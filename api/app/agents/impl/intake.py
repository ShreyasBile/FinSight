"""Intake crew — agents 1-4 (diagram 1, OCR pipeline)."""
from __future__ import annotations

import asyncio
import json
import time
from functools import partial

from ...config import settings
from ...utils import to_b64
from ..base import AgentOutcome, FinSightAgent
from ..registry import implements
from ..tracing.redact import digest, field_names


def _expected_keys(prompt: str) -> list[str]:
    """Field names the doc-type prompt asks for (its JSON template)."""
    start, end = prompt.find("{"), prompt.rfind("}")
    if start == -1 or end <= start:
        return []
    try:
        return list(json.loads(prompt[start:end + 1]).keys())
    except ValueError:
        return []


def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


@implements("preprocessing")
class PreprocessingAgent(FinSightAgent):
    """Warp-first cleanup (EXIF, contour warp/deskew, orientation, adaptive
    quality repair, revert guard). Required for extraction accuracy on skewed
    or rotated photos; PDFs are left to the provider's rasterizer."""

    def input_digest(self, inputs):
        return {"doc_type": inputs["doc_type"], "sha12": digest(inputs["data"]),
                "bytes": len(inputs["data"]), "mime": inputs["mime"]}

    def execute(self, inputs):
        data, mime, doc_type = inputs["data"], inputs["mime"], inputs["doc_type"]
        if not (settings.ocr_preprocess and mime.startswith("image/")):
            reason = "PDF — rendered by the provider" if mime == "application/pdf" else "preprocessing disabled"
            return AgentOutcome(payload={"data": data, "mime": mime, "cleaned_preview": None, "ms": None},
                                status="skipped", summary=f"Skipped: {reason}")
        from ...preprocess import doc_langs, is_page_doc, preprocess_image
        t0 = time.perf_counter()
        info: dict = {}
        try:
            out = self.tool("preprocess_image", partial(preprocess_image, data,
                                                         segment=settings.preprocess_segment,
                                                         langs=doc_langs(doc_type), page=is_page_doc(doc_type),
                                                         info=info))
        except Exception as exc:  # noqa: BLE001 - never fail extraction because cleanup did
            return AgentOutcome(payload={"data": data, "mime": mime, "cleaned_preview": None, "ms": None},
                                status="degraded", warnings=[f"preprocessing failed: {type(exc).__name__}"],
                                summary="Cleanup failed — original image used")
        ms = round((time.perf_counter() - t0) * 1000, 1)
        changed = out != data
        size = _px(out)
        # unchanged = the cleanup kept the upload as it was (a clean, upright scan, or a revert):
        # the bytes are still the uploaded file, so they keep its own type
        out_mime = "image/jpeg" if changed else mime
        return AgentOutcome(
            payload={"data": out, "mime": out_mime,
                     "cleaned_preview": f"data:{out_mime};base64," + to_b64(out), "ms": ms,
                     # words read while choosing the orientation (reused by Document Identity)
                     "page_text": info.get("page_text")},
            ai_outcome="pass",
            summary=(f"Image cleaned ({size[0]}x{size[1]})" if size else "Image cleaned") if changed
                    else "Clean scan — used as uploaded",
            output_summary={"changed": changed, "output_px": size, "input_bytes": len(data),
                            "output_bytes": len(out)})


def _px(img_bytes: bytes):
    try:
        import io
        from PIL import Image
        return list(Image.open(io.BytesIO(img_bytes)).size)
    except Exception:  # noqa: BLE001
        return None


def _first_page(data: bytes) -> bytes | None:
    """Page 1 of a PDF as an image, for the one retry. A multi-page statement fills the local model's
    whole context, and the call can outlast its time limit when the machine is busy (measured on real
    statement layouts: 12 of 60 reads timed out and returned nothing). The summary fields the prompt
    asks for are printed on the first page; the rows come from the deterministic parser."""
    try:
        from ...utils import render_pdf_to_images
        pages = render_pdf_to_images(data, max_pages=1, max_dimension=1600)
        return pages[0] if pages else None
    except Exception:  # noqa: BLE001
        return None


def _summary_pages(data: bytes) -> bytes | None:
    """A bank statement reduced to its first and last page (a new PDF), or None when it has at most two
    pages or cannot be rebuilt. The prompt asks for the statement SUMMARY - holder, bank, account, period,
    opening and closing balance - which banks print on the first page and, for the closing figures, the
    last; the rows in between are read by the deterministic parser, not by the model."""
    try:
        import io
        import pypdfium2 as pdfium
        from ...pdfium_lock import PDFIUM_LOCK
        with PDFIUM_LOCK:
            src = pdfium.PdfDocument(data)
            try:
                n = len(src)
                if n <= 2:
                    return None
                dst = pdfium.PdfDocument.new()
                try:
                    dst.import_pages(src, pages=[0, n - 1])
                    buf = io.BytesIO()
                    dst.save(buf)
                    return buf.getvalue()
                finally:
                    dst.close()
            finally:
                src.close()
    except Exception:  # noqa: BLE001 - the whole statement is sent instead
        return None


def _shrink(data: bytes, mime: str, scale: float = 0.6) -> bytes | None:
    """A smaller JPEG of an image page for the one retry (a PDF is retried on its first page)."""
    if (mime or "").endswith("pdf"):
        return None
    try:
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert("RGB")
        im = im.resize((max(28, int(im.width * scale)), max(28, int(im.height * scale))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=90)
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        return None


@implements("extraction")
class ExtractionAgent(FinSightAgent):
    """The VLM reads the document into the doc type's JSON template. Provider
    and model come from the Settings page; the call goes through the gateway."""

    def input_digest(self, inputs):
        return {"doc_type": inputs["doc_type"], "sha12": digest(inputs["data"]), "mime": inputs["mime"]}

    def execute(self, inputs):
        from ...providers import get_provider
        from ..llm.gateway import LLMError
        provider = get_provider()
        t0 = time.perf_counter()
        warnings, error = [], None
        data = inputs["data"]
        if inputs["doc_type"] == "bank_statement" and (inputs["mime"] or "").endswith("pdf"):
            # Only the first and last page go to the model. Measured on real statement layouts: the
            # four-page read fills the local model's context, took two minutes and timed out on 12 of 60
            # statements; first + last page read every summary field right on 10 of 10 development
            # statements in about 30 s.
            data = _summary_pages(data) or data
        try:
            raw, parsed = asyncio.run(provider.extract(data, inputs["mime"], inputs["doc_type"],
                                                       inputs["prompt"]))
        except LLMError as exc:
            # Seen on real scanned receipts: the local model refused a page ("Failed to tokenize prompt")
            # when memory was short; seen on real statements: the call timed out. One retry on less input
            # (an image at reduced resolution, a PDF on its first page); if that fails too, report the
            # page as unreadable instead of failing the whole upload.
            is_pdf = (inputs["mime"] or "").endswith("pdf")
            small, small_mime = ((_first_page(inputs["data"]), "image/png") if is_pdf
                                 else (_shrink(inputs["data"], inputs["mime"]), "image/jpeg"))
            warnings.append(f"reading model did not answer ({str(exc)[:120]}); retried "
                            + ("on the first page only" if is_pdf else "at lower resolution"))
            try:
                raw, parsed = (asyncio.run(provider.extract(small, small_mime, inputs["doc_type"], inputs["prompt"]))
                               if small else (None, None))
            except LLMError as exc2:
                raw, parsed, error = None, None, str(exc2)[:200]
        ms = round((time.perf_counter() - t0) * 1000, 1)
        if error:
            return AgentOutcome(payload={"raw": None, "parsed": None, "provider": provider.name, "ms": ms,
                                         "error": error},
                                ai_outcome="fail", confidence=0.0, status="degraded", warnings=warnings,
                                summary="The reading model could not process this page — ask for a clearer or smaller upload",
                                output_summary={"fields": [], "filled": 0, "json_ok": False, "error": error})
        expected = _expected_keys(inputs["prompt"])
        got = [k for k in expected if parsed and not _blank(parsed.get(k))]
        conf = round(len(got) / len(expected), 3) if expected else (1.0 if parsed else 0.0)
        outcome = "pass" if parsed and conf >= 0.75 else ("uncertain" if parsed else "fail")
        from ..llm.gateway import resolve
        r = resolve("extraction")
        return AgentOutcome(
            payload={"raw": raw, "parsed": parsed, "provider": provider.name, "ms": ms},
            ai_outcome=outcome, confidence=conf, warnings=warnings,
            summary=(f"Read {len(got)}/{len(expected)} fields" if expected else "Read document")
                    + ("" if parsed else " — no valid JSON returned"),
            output_summary={"fields": field_names(parsed), "filled": len(got), "expected": len(expected),
                            "json_ok": parsed is not None},
            model={"provider": r.provider, "model": r.model, "source": r.source})


@implements("validation")
class ValidationAgent(FinSightAgent):
    """Structural checks on every field: PAN/IFSC/GSTIN formats, the Aadhaar
    Verhoeff checksum, CIBIL and salary ranges."""

    def execute(self, inputs):
        from ...guardrails import validate
        check = self.tool("guardrails.validate", validate, inputs["doc_type"], inputs.get("parsed"))
        flags = [f["flag"] for f in check["fields"]]
        n_err, n_warn = flags.count("error"), flags.count("warn")
        outcome = "fail" if n_err else ("flag" if check["needs_review"] else "pass")
        return AgentOutcome(
            payload=check, ai_outcome=outcome, confidence=check["confidence"],
            summary=f"{flags.count('ok')} ok · {n_warn} warn · {n_err} error",
            output_summary={"flags": {f["key"]: f["flag"] for f in check["fields"]},
                            "needs_review": check["needs_review"]})


@implements("doc_identity")
class DocumentIdentityAgent(FinSightAgent):
    """'Is this the document we asked for?' — the wrong-document gate, ported
    from the browser (docVerification.js) so the server can enforce it too."""

    def execute(self, inputs):
        from ...docrules import check_document_identity
        from ..tools.doc_keywords import contradiction
        res = self.tool("check_document_identity", check_document_identity,
                        inputs["doc_type"], inputs.get("parsed"))
        conflict = None
        if inputs.get("page_text") is not None:
            conflict = self.tool("doc_keywords.contradiction", contradiction,
                                 inputs["doc_type"], inputs["page_text"])
        silent = False
        if inputs.get("page_text") is not None and res["ok"] and conflict is None:
            # the fields came back, nothing contradicts - but does the page say it is this document at all?
            from ..tools.doc_keywords import lacks_own_words
            silent = self.tool("doc_keywords.lacks_own_words", lacks_own_words, inputs["doc_type"], inputs["page_text"])
        ok = res["ok"] and conflict is None and not silent
        payload = {"ok": ok, "rule": res.get("rule"), "fields_ok": res["ok"]}
        if conflict:
            payload["looks_like"] = conflict["looks_like"]
        if silent:
            payload["no_own_words"] = True
        warnings = []
        if ok and inputs.get("page_text") is not None:
            from ..tools.doc_keywords import unconfirmed
            if self.tool("doc_keywords.unconfirmed", unconfirmed, inputs["doc_type"], inputs["page_text"]):
                # accepted on its fields, but nothing OCR can read on the page says it is this card
                payload["unconfirmed"] = True
                warnings.append(f"no {inputs['doc_type'].upper()} heading or number could be read on the page - "
                                "check that it is the requested card")
        why = (f"looks like a {conflict['looks_like']}" if conflict
               else "nothing on the page says it is one" if silent
               else "its key field was not found")
        return AgentOutcome(payload=payload, ai_outcome="pass" if ok else "fail", warnings=warnings,
                            summary=("Matches the requested document type" + (" by its fields - not confirmed by the "
                                     "page's own text" if warnings else "")) if ok
                            else f"Not a {inputs['doc_type']} — {why}; ask for the right document",
                            output_summary={"ok": ok, "fields_ok": res["ok"], "conflict": conflict,
                                            "unconfirmed": bool(warnings), "no_own_words": silent})
