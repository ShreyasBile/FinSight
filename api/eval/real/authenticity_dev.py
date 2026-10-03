"""Development experiment: can the local vision model spot a LOCAL edit if it is asked about one?

The authenticity check asks whether a whole document was fabricated; the forgeries in the test sets are a
few characters changed in a genuine document, and it flagged 11% of them. This asks the model, on
development documents only, the question a reviewer of an edited document asks - which characters look
different from their neighbours - and records how often it says yes on forged and on genuine documents.
Per-document answers are cached (development data), so prompts can be compared without re-running.

    python -m eval.real.authenticity_dev [n_per_class]

Second experiment (`zoom`): the whole page is shrunk to the model's input size, where a few edited characters
are a handful of pixels. Here the model is shown the edited area itself, magnified - for forged documents the
marked edit with the text around it, for genuine documents an area of the same size and place - and asked the
same question. It is the best case for any design that would hand the model the suspicious region.

    python -m eval.real.authenticity_dev zoom [n_per_class]
"""
from __future__ import annotations

import io
import json
import sys

from eval import common as C
from eval.real import core as R

SYSTEM = ("You review scanned documents for a lender. A forger takes a GENUINE document and changes a few "
          "characters - an amount, a date, a name, an ID number - with an image editor. You look for the "
          "traces such an edit leaves. Most documents you see are genuine; say 'no' unless you can point at "
          "the edited text.")

PROMPT = ("Look closely at every printed word and number in this image.\n"
          "An edited value usually shows at least one of these, compared with the text around it:\n"
          "- characters in a different font, weight, size or letter spacing than the same kind of characters nearby\n"
          "- a baseline that is higher, lower or tilted against the rest of its line\n"
          "- characters sharper, blurrier, darker or lighter than their neighbours\n"
          "- a patch of background around a word with a different colour, texture or noise, or a visible box\n"
          "- a value that overlaps or is squeezed into the space of the original\n"
          "Answer as strict JSON only:\n"
          '{"edited": "yes" | "no" | "unclear", "where": "the text that looks edited, quoted exactly, or empty", '
          '"why": "which of the signs above you see"}\n'
          "Say 'yes' only if you can quote the edited text.")


def main(n: int = 40):
    C.setup_env(provider="ollama", db=False)
    from app.agents.llm.gateway import chat
    from eval.real import evidence_real as E
    f = [it for it in E.findit_items("dev") if it["path"].exists() and it["path"].parent.name == "val"]
    s = E.sidtd_items("dev", 10000)
    items = (R.sample([x for x in f if x["forged"]], n, salt="auth") + R.sample([x for x in f if not x["forged"]], n, salt="auth")
             + R.sample([x for x in s if x["forged"]], n // 2, salt="auth") + R.sample([x for x in s if not x["forged"]], n // 2, salt="auth"))
    cache = R.ItemCache("authenticity", "local-edit-prompt-v1-dev")

    def one(it):
        from PIL import Image
        img = Image.open(it["path"]).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=92)
        res = chat("authenticity", system=SYSTEM, user=PROMPT, images=[buf.getvalue()], timeout=120)
        p = res.parsed if isinstance(res.parsed, dict) else {}
        where = str(p.get("where") or "")
        return {"edited": str(p.get("edited") or "").lower(), "quoted": bool(where.strip()), "where_len": len(where)}
    rows = cache.run(items, one, label="local-edit question", every=10, on_error={"edited": None})
    out = {}
    for name, keep in (("receipts", lambda it: it["set"] == "findit"), ("ids", lambda it: it["set"] == "sidtd")):
        for forged in (True, False):
            rs = [r for it, r in zip(items, rows) if keep(it) and it["forged"] == forged and r.get("edited") is not None]
            out[f"{name}_{'forged' if forged else 'genuine'}"] = {
                "n": len(rs), "yes": sum(r["edited"] == "yes" for r in rs),
                "yes_with_quote": sum(r["edited"] == "yes" and r["quoted"] for r in rs)}
    print(json.dumps(out, indent=1))
    (R.RESULTS / "authenticity_local_edit_dev.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


ZOOM_PROMPT = "This is a magnified part of a scanned document.\n" + PROMPT.split("\n", 1)[1]


def _crop(img, box):
    """The box with the text around it (one and a half box-widths each side, three box-heights above and
    below), magnified so its short side is at least 448 px."""
    x0, y0, x1, y1 = box
    mx, my = max(1.5 * (x1 - x0), 160), max(3 * (y1 - y0), 60)
    c = img.crop((int(max(x0 - mx, 0)), int(max(y0 - my, 0)), int(min(x1 + mx, img.width)), int(min(y1 + my, img.height))))
    k = min(max(448 / max(min(c.size), 1), 1.0), 1344 / max(c.size))
    return c.resize((max(int(c.width * k), 1), max(int(c.height * k), 1))) if k > 1.01 else c


def zoom(n: int = 40):
    C.setup_env(provider="ollama", db=False)
    from PIL import Image
    from app.agents.llm.gateway import chat
    from eval.real import evidence_real as E
    f = [it for it in E.findit_items("dev") if it["path"].exists() and it["path"].parent.name == "val"]
    s = E.sidtd_items("dev", 10000)
    fields = E._field_boxes()
    items = []
    forged = R.sample([x for x in f if x["forged"] and x["regions"]], n, salt="zoom")
    for it, g in zip(forged, R.sample([x for x in f if not x["forged"]], n, salt="zoom")):
        w, h = Image.open(it["path"]).size
        items.append({**it, "box": it["regions"][0]})
        items.append({**g, "rel": [it["regions"][0][0] / w, it["regions"][0][1] / h, it["regions"][0][2] / w, it["regions"][0][3] / h]})
    for it in R.sample([x for x in s if x["forged"] and x["regions"]], n // 2, salt="zoom"):
        items.append({**it, "box": it["regions"][0]})
    for it in R.sample([x for x in s if not x["forged"] and fields.get(x["path"].name)], n // 2, salt="zoom"):
        box = sorted(fields[it["path"].name].items())[0][1]                # the same kind of area: a printed field
        items.append({**it, "box": box})
    cache = R.ItemCache("authenticity", "zoomed-edit-v1-dev")

    def one(it):
        img = Image.open(it["path"]).convert("RGB")
        box = it.get("box") or [it["rel"][0] * img.width, it["rel"][1] * img.height, it["rel"][2] * img.width, it["rel"][3] * img.height]
        buf = io.BytesIO()
        _crop(img, box).save(buf, "JPEG", quality=95)
        res = chat("authenticity", system=SYSTEM, user=ZOOM_PROMPT, images=[buf.getvalue()], timeout=120)
        p = res.parsed if isinstance(res.parsed, dict) else {}
        return {"edited": str(p.get("edited") or "").lower(), "quoted": bool(str(p.get("where") or "").strip())}
    rows = cache.run(items, one, label="zoomed edit question", every=10, on_error={"edited": None})
    out = {}
    for name, keep in (("receipts", lambda it: it["set"] == "findit"), ("ids", lambda it: it["set"] == "sidtd")):
        for fg in (True, False):
            rs = [r for it, r in zip(items, rows) if keep(it) and it["forged"] == fg and r.get("edited") is not None]
            out[f"{name}_{'forged' if fg else 'genuine'}"] = {"n": len(rs), **{a: sum(r["edited"] == a for r in rs) for a in ("yes", "no", "unclear")}}
    print(json.dumps(out, indent=1))
    (R.RESULTS / "authenticity_zoom_dev.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    if sys.argv[1:2] == ["zoom"]:
        zoom(*(int(a) for a in sys.argv[2:3]))
    else:
        main(*(int(a) for a in sys.argv[1:2]))
