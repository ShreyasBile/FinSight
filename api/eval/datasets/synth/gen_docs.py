"""Build the synthetic evaluation datasets.

    cd api
    .venv-agents/Scripts/python -m eval.datasets.synth.gen_docs --all

Writes api/eval/datasets/synth/out/<set>/ (gitignored — rebuilt from the seed)
plus one manifest.json per set. Sets:
  clean     — one full document pack per identity, with field-level ground truth
  degraded  — ID cards rotated / photographed on a desk / blurred / dark / glare
  tamper    — genuine documents and labelled forgeries of each attack type
"""
from __future__ import annotations

import argparse
import json
import random
from dataclasses import asdict
from pathlib import Path

from . import forge, render
from .identities import expected_fields, fmt_inr, make_identity, stable

OUT = Path(__file__).resolve().parent / "out"
SEED = 20260925


def _write(path: Path, data: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path.relative_to(OUT)).replace("\\", "/")


def identities(n: int, seed: int = SEED):
    rng = random.Random(seed)
    return [make_identity(rng, f"ID{k:03d}") for k in range(n)]


def build_clean(n: int = 12, seed: int = SEED) -> list[dict]:
    """Full document pack per identity. PDFs are born-digital (genuine producer,
    one save generation); cards are camera-like JPEGs."""
    rows = []
    for idt in identities(n, seed):
        base = OUT / "clean" / idt.uid
        docs = {
            "pan": ("image/jpeg", render.to_jpeg(render.pan_card(idt, seed=stable(idt.uid) % 999), 92)),
            "aadhaar": ("image/jpeg", render.to_jpeg(render.aadhaar_card(idt, seed=stable(idt.uid) % 998), 92)),
            "salary_slip": ("application/pdf", render.lines_to_pdf(render.salary_slip_lines(idt))),
            "bank_statement": ("application/pdf", render.lines_to_pdf(
                render.statement_lines(idt), producer="Finacle Statement Engine 11.2",
                creator="Finacle Core Banking")),
            "itr": ("application/pdf", render.lines_to_pdf(render.itr_lines(idt),
                                                          producer="Income Tax e-Filing Portal",
                                                          creator="ITD e-Filing 2.0")),
            "form16": ("application/pdf", render.lines_to_pdf(render.form16_lines(idt),
                                                             producer="TRACES Form16 Generator",
                                                             creator="TRACES")),
            "gst_return": ("application/pdf", render.lines_to_pdf(render.gst_lines(idt),
                                                                 producer="GSTN Returns Offline Tool",
                                                                 creator="GST Portal")),
        }
        # a photographed paper payslip too — the image path through preprocessing
        docs["salary_slip_photo"] = ("image/jpeg", render.to_jpeg(
            render.paper_document(render.salary_slip_lines(idt), seed=stable(idt.uid) % 997), 88))
        for key, (mime, data) in docs.items():
            doc_type = "salary_slip" if key == "salary_slip_photo" else key
            ext = "pdf" if mime == "application/pdf" else "jpg"
            rel = _write(base / f"{key}.{ext}", data)
            rows.append({"id": f"{idt.uid}:{key}", "set": "clean", "identity": idt.uid,
                         "doc_type": doc_type, "variant": key, "path": rel, "mime": mime,
                         "expected": expected_fields(idt, doc_type)})
        _write(base / "identity.json", json.dumps(idt.to_dict(), indent=1).encode())
    return rows


def build_degraded(n: int = 10, seed: int = SEED + 1) -> list[dict]:
    """ID cards under real-world capture problems. Ground truth = the upright
    orientation plus the fields that must still be read."""
    rng = random.Random(seed)
    rows = []
    for idt in identities(n, seed):
        for doc_type, maker in (("pan", render.pan_card), ("aadhaar", render.aadhaar_card)):
            card = maker(idt, seed=rng.randint(0, 999))
            variants = []
            k = rng.choice([1, 2, 3])
            variants.append((f"rot{90 * k}", *forge.rotate(card, k, skew_deg=rng.uniform(-4, 4))))
            variants.append(("desk", *forge.on_desk(card, rng)))
            desk2, m2 = forge.on_desk(card, rng)
            rot2, mr = forge.rotate(desk2, rng.choice([1, 3]))
            variants.append(("desk_rot", rot2, {**m2, **mr}))
            variants.append(("blur", *forge.blur(card, rng.uniform(1.2, 2.2))))
            variants.append(("dark", *forge.low_light(card, rng.uniform(0.35, 0.55))))
            variants.append(("glare", *forge.glare(card, rng)))
            for name, im, meta in variants:
                rel = _write(OUT / "degraded" / idt.uid / f"{doc_type}_{name}.jpg",
                             render.to_jpeg(im, rng.randint(70, 90)))
                rows.append({"id": f"{idt.uid}:{doc_type}:{name}", "set": "degraded",
                             "identity": idt.uid, "doc_type": doc_type, "variant": name,
                             "path": rel, "mime": "image/jpeg", "degradation": meta,
                             "expected": expected_fields(idt, doc_type)})
    return rows


def build_tamper(n: int = 10, seed: int = SEED + 2) -> list[dict]:
    """Genuine documents and one forgery of each attack family per identity.
    label.forged = ground truth; label.attack = which family; expected_min_tier
    = the least a correct detector should say (MEDIUM for anything forged)."""
    rng = random.Random(seed)
    rows = []

    def add(idt, name, doc_type, mime, data, forged, attack, extra=None, fields=None):
        ext = "pdf" if mime == "application/pdf" else "jpg"
        rel = _write(OUT / "tamper" / idt.uid / f"{name}.{ext}", data)
        rows.append({"id": f"{idt.uid}:{name}", "set": "tamper", "identity": idt.uid,
                     "doc_type": doc_type, "variant": name, "path": rel, "mime": mime,
                     "label": {"forged": forged, "attack": attack, **(extra or {})},
                     "ocr_fields": fields if fields is not None else expected_fields(idt, doc_type)})

    for idt in identities(n, seed):
        slip_fields = expected_fields(idt, "salary_slip")
        stmt_fields = expected_fields(idt, "bank_statement")
        # --- genuine controls ---
        add(idt, "salary_genuine", "salary_slip", "application/pdf",
            render.lines_to_pdf(render.salary_slip_lines(idt)), False, "none")
        add(idt, "statement_genuine", "bank_statement", "application/pdf",
            render.lines_to_pdf(render.statement_lines(idt), producer="Finacle Statement Engine 11.2",
                                creator="Finacle Core Banking"), False, "none")
        pan = render.pan_card(idt, seed=rng.randint(0, 999))
        add(idt, "pan_genuine", "pan", "image/jpeg", render.to_jpeg(pan, 90), False, "none")
        slip_photo = render.paper_document(render.salary_slip_lines(idt), seed=rng.randint(0, 999))
        add(idt, "salary_photo_genuine", "salary_slip", "image/jpeg",
            render.to_jpeg(slip_photo, 88), False, "none")

        # --- born-digital PDF attacks ---
        inflated = int(idt.net * rng.uniform(1.6, 2.4))
        fields = dict(slip_fields, net_salary=inflated)
        add(idt, "salary_net_inflated", "salary_slip", "application/pdf",
            render.lines_to_pdf(render.salary_slip_lines(idt, overrides={"net": inflated})),
            True, "arithmetic", {"detail": "net salary raised above gross"}, fields=fields)
        add(idt, "salary_editor_resave", "salary_slip", "application/pdf",
            render.append_incremental_update(render.lines_to_pdf(render.salary_slip_lines(idt)),
                                             new_producer="Wondershare PDFelement 10"),
            True, "pdf_edit", {"detail": "re-saved by a PDF editor after issue"})
        add(idt, "statement_utility_resave", "bank_statement", "application/pdf",
            render.append_incremental_update(
                render.lines_to_pdf(render.statement_lines(idt), producer="Finacle Statement Engine 11.2"),
                new_producer="iLovePDF"),
            True, "pdf_utility", {"detail": "passed through iLovePDF (unlock or edit — must surface MEDIUM)"})
        add(idt, "salary_jspdf_fabricated", "salary_slip", "application/pdf",
            render.lines_to_pdf(render.salary_slip_lines(idt, placeholders=True),
                                producer="jsPDF 2.5.1", creator="jsPDF"),
            True, "fabricated", {"detail": "generated by a browser library with placeholder values"},
            fields=dict(slip_fields, employee_name="John Doe", employer_name="ABC Company Pvt Ltd"))
        closing_fake = round(idt.closing_balance + rng.randint(200_000, 900_000), 2)
        add(idt, "statement_closing_edited", "bank_statement", "application/pdf",
            render.lines_to_pdf(render.statement_lines(idt, summary={"closing_balance": closing_fake}),
                                producer="Finacle Statement Engine 11.2"),
            True, "arithmetic", {"detail": "closing balance inflated; summary no longer reconciles"},
            fields=dict(stmt_fields, closing_balance=closing_fake))

        # --- image attacks ---
        W = slip_photo.size[0]
        # the net-salary figure row sits at y=762 in the layout (see salary_slip_lines)
        net_box = (398, 758, 720, 800)
        spliced, meta = forge.splice_text(slip_photo, net_box, fmt_inr(inflated), 28)
        add(idt, "salary_photo_spliced", "salary_slip", "image/jpeg", render.to_jpeg(spliced, 90),
            True, "splice", meta, fields=dict(slip_fields, net_salary=inflated))
        cm, meta = forge.copy_move(pan, (290, 158, 560, 210), (290, 470))
        add(idt, "pan_copy_move", "pan", "image/jpeg", render.to_jpeg(cm, 90), True, "copy_move", meta)
        del W
    return rows


_LFW = None


def lfw_faces():
    """LFW test pairs (public research set) as PIL images:
    [(face_a, face_b, same_person)], downloaded once into datasets/external/lfw."""
    global _LFW
    if _LFW is None:
        import numpy as np
        from PIL import Image
        from sklearn.datasets import fetch_lfw_pairs
        home = Path(__file__).resolve().parents[1] / "external" / "lfw"
        d = fetch_lfw_pairs(subset="test", color=True, resize=1.0, data_home=str(home),
                            download_if_missing=False)
        px = d.pairs
        if px.max() <= 1.0:
            px = px * 255.0
        _LFW = [(Image.fromarray(px[i, 0].astype("uint8")), Image.fromarray(px[i, 1].astype("uint8")),
                 bool(d.target[i])) for i in range(len(d.target))]
    return _LFW


def build_tamper_cam(n: int = 12, seed: int = SEED + 5) -> list[dict]:
    """Camera-realistic tamper set used by the tampering/authenticity/locator
    evaluation: every IMAGE document (genuine or forged) is a simulated phone
    capture of a printed page (forge.camera_capture), and forgeries are edits
    made to that captured photo — as a real forger would. PDFs as in build_tamper.
    Adds image-borne fabrications (a placeholder payslip, photographed)."""
    rng = random.Random(seed)
    rows = []

    def add(idt, name, doc_type, mime, data, forged, attack, extra=None, fields=None):
        ext = "pdf" if mime == "application/pdf" else "jpg"
        rel = _write(OUT / "tamper_cam" / idt.uid / f"{name}.{ext}", data)
        rows.append({"id": f"{idt.uid}:{name}", "set": "tamper_cam", "identity": idt.uid,
                     "doc_type": doc_type, "variant": name, "path": rel, "mime": mime,
                     "label": {"forged": forged, "attack": attack, **(extra or {})},
                     "ocr_fields": fields if fields is not None else expected_fields(idt, doc_type)})

    faces = lfw_faces()
    for k, idt in enumerate(identities(n, seed)):
        slip_f = expected_fields(idt, "salary_slip")
        stmt_f = expected_fields(idt, "bank_statement")
        own_face = faces[2 * k][0]                   # this applicant's (public LFW) face
        donor_face = faces[2 * k + 501][1]           # a different person's face
        pan = forge.camera_capture(render.pan_card(idt, face=own_face, seed=rng.randint(0, 999)), rng)
        aad = forge.camera_capture(render.aadhaar_card(idt, face=own_face, seed=rng.randint(0, 999)), rng)
        slip = forge.camera_capture(render.paper_document(render.salary_slip_lines(idt), seed=rng.randint(0, 999)), rng)
        stmt_photo = forge.camera_capture(render.paper_document(render.statement_lines(idt), seed=rng.randint(0, 999)), rng)
        # genuine controls
        add(idt, "pan_genuine", "pan", "image/jpeg", render.to_jpeg(pan, 90), False, "none")
        add(idt, "aadhaar_genuine", "aadhaar", "image/jpeg", render.to_jpeg(aad, 90), False, "none")
        add(idt, "salary_photo_genuine", "salary_slip", "image/jpeg", render.to_jpeg(slip, 90), False, "none")
        add(idt, "statement_photo_genuine", "bank_statement", "image/jpeg", render.to_jpeg(stmt_photo, 90),
            False, "none")
        add(idt, "salary_pdf_genuine", "salary_slip", "application/pdf",
            render.lines_to_pdf(render.salary_slip_lines(idt)), False, "none")
        add(idt, "statement_pdf_genuine", "bank_statement", "application/pdf",
            render.lines_to_pdf(render.statement_lines(idt), producer="Finacle Statement Engine 11.2",
                                creator="Finacle Core Banking"), False, "none")
        # image forgeries on the captured photo
        inflated = int(idt.net * rng.uniform(1.6, 2.4))
        sp, meta = forge.splice_text(slip, (398, 758, 720, 800), fmt_inr(inflated), 28)
        add(idt, "salary_photo_spliced", "salary_slip", "image/jpeg", render.to_jpeg(sp, 90), True, "splice",
            meta, fields=dict(slip_f, net_salary=inflated))
        cm, meta = forge.copy_move(pan, (290, 158, 560, 210), (290, 470))
        add(idt, "pan_copy_move", "pan", "image/jpeg", render.to_jpeg(cm, 90), True, "copy_move", meta)
        ps, meta = forge.photo_swap(pan, (40, 128, 250, 388), donor_face)
        add(idt, "pan_photo_swap", "pan", "image/jpeg", render.to_jpeg(ps, 90), True, "photo_swap", meta)
        fab = forge.camera_capture(render.paper_document(render.salary_slip_lines(idt, placeholders=True),
                                                         seed=rng.randint(0, 999)), rng)
        add(idt, "salary_photo_fabricated", "salary_slip", "image/jpeg", render.to_jpeg(fab, 90), True,
            "fabricated", {"detail": "placeholder payslip, photographed"},
            fields=dict(slip_f, employee_name="John Doe", employer_name="ABC Company Pvt Ltd"))
        # PDF forgeries
        add(idt, "salary_net_inflated", "salary_slip", "application/pdf",
            render.lines_to_pdf(render.salary_slip_lines(idt, overrides={"net": inflated})), True, "arithmetic",
            fields=dict(slip_f, net_salary=inflated))
        add(idt, "salary_editor_resave", "salary_slip", "application/pdf",
            render.append_incremental_update(render.lines_to_pdf(render.salary_slip_lines(idt)),
                                             new_producer="Wondershare PDFelement 10"), True, "pdf_edit")
        add(idt, "statement_utility_resave", "bank_statement", "application/pdf",
            render.append_incremental_update(
                render.lines_to_pdf(render.statement_lines(idt), producer="Finacle Statement Engine 11.2"),
                new_producer="iLovePDF"), True, "pdf_utility")
        add(idt, "salary_jspdf_fabricated", "salary_slip", "application/pdf",
            render.lines_to_pdf(render.salary_slip_lines(idt, placeholders=True), producer="jsPDF 2.5.1",
                                creator="jsPDF"), True, "fabricated",
            fields=dict(slip_f, employee_name="John Doe", employer_name="ABC Company Pvt Ltd"))
        closing_fake = round(idt.closing_balance + rng.randint(200_000, 900_000), 2)
        add(idt, "statement_closing_edited", "bank_statement", "application/pdf",
            render.lines_to_pdf(render.statement_lines(idt, summary={"closing_balance": closing_fake}),
                                producer="Finacle Statement Engine 11.2"), True, "arithmetic",
            fields=dict(stmt_f, closing_balance=closing_fake))
    return rows


PATTERN_SCENARIOS = ("clean", "cash_cycling", "sudden_deposit", "window_dressing",
                     "income_mismatch", "gst_inflation")


def _rebalance(tx: list[dict], opening: float) -> list[dict]:
    tx.sort(key=lambda t: (t["date"][6:], t["date"][3:5], t["date"][:2], t.get("_o", 0)))
    bal = opening
    for t in tx:
        bal = round(bal - t["debit"] + t["credit"], 2)
        t["balance"] = bal
    return tx


def _d(day) -> str:
    return day.strftime("%d/%m/%Y")


def build_patterns(n_per: int = 6, seed: int = SEED + 7) -> list[dict]:
    """Bank statements with ONE injected transaction-level fraud pattern each
    (plus clean controls), for the Fraud Patterns agent. Every statement is a
    born-digital PDF; every other one also as a phone photo of the printout so
    the OCR row parser is exercised. label.patterns = the patterns the
    injection legitimately creates (window dressing IS a late sudden deposit,
    so both are correct for it)."""
    from datetime import date, timedelta
    rng = random.Random(seed)
    rows = []
    idts = identities(n_per * len(PATTERN_SCENARIOS), seed)
    for k, idt in enumerate(idts):
        scen = PATTERN_SCENARIOS[k % len(PATTERN_SCENARIOS)]
        tx = [dict(t) for t in idt.transactions]
        opening = idt.opening_balance
        start, end = date(2026, 4, 1), date(2026, 6, 29)
        payslip_net, gst_monthly = float(idt.net), None
        expected = set()
        if scen == "cash_cycling":
            for j in range(rng.randint(3, 4)):
                day = start + timedelta(days=rng.randint(3, 80))
                amt = float(rng.randrange(150_000, 450_000, 5_000))
                tx.append({"date": _d(day), "description": f"IMPS/CR/{rng.randint(10**5, 10**6)}/TRADERS",
                           "debit": 0.0, "credit": amt, "_o": 1})
                tx.append({"date": _d(day + timedelta(days=rng.randint(0, 2))),
                           "description": f"IMPS/DR/{rng.randint(10**5, 10**6)}/ENTERPRISE",
                           "debit": amt - rng.choice([0, 0, 50, 100]), "credit": 0.0, "_o": 2})
            expected = {"cash_cycling"}
        elif scen == "sudden_deposit":
            day = end - timedelta(days=rng.randint(18, 27))
            amt = float(rng.randrange(6, 12) * idt.net)
            tx.append({"date": _d(day), "description": "CASH DEPOSIT BRANCH", "debit": 0.0, "credit": amt, "_o": 1})
            tx.append({"date": _d(day + timedelta(days=rng.randint(2, 5))), "description": "RTGS TO BUILDER",
                       "debit": round(amt * 0.95, 2), "credit": 0.0, "_o": 2})
            expected = {"sudden_deposit"}
        elif scen == "window_dressing":
            day = end - timedelta(days=rng.randint(1, 4))
            amt = float(rng.randrange(4, 8) * max(idt.net, 60_000))
            tx.append({"date": _d(day), "description": "NEFT FROM RELATIVE", "debit": 0.0, "credit": amt, "_o": 1})
            expected = {"window_dressing", "sudden_deposit"}
        elif scen == "income_mismatch":
            real = round(idt.net * rng.uniform(0.45, 0.7))
            for t in tx:
                if t["description"].startswith("SALARY"):
                    t["credit"] = float(real)
            expected = {"income_mismatch"}
        elif scen == "gst_inflation":
            credits = sum(t["credit"] for t in tx) / 3
            gst_monthly = float(round(credits * rng.uniform(3, 6), -3))
            expected = {"gst_inflation"}
        else:  # clean control — half with a consistent GST return
            if k % 2:
                gst_monthly = float(round(sum(t["credit"] for t in tx) / 3 * 0.8, -3))
        tx = _rebalance(tx, opening)
        for t in tx:
            t.pop("_o", None)
        summ = {"opening_balance": opening, "closing_balance": tx[-1]["balance"],
                "total_credits": round(sum(t["credit"] for t in tx), 2),
                "total_debits": round(sum(t["debit"] for t in tx), 2),
                "average_balance": round(sum(t["balance"] for t in tx) / len(tx), 2),
                "statement_period": f"{_d(start)} to {_d(end)}"}
        lines = render.statement_lines(idt, tx=tx, summary=summ)
        label = {"scenario": scen, "patterns": sorted(expected), "payslip_net": payslip_net,
                 "gst_monthly_turnover": gst_monthly, "transactions": tx}
        variants = [("pdf", "application/pdf", render.lines_to_pdf(lines, producer="Finacle Statement Engine 11.2",
                                                                    creator="Finacle Core Banking"))]
        if k % 2 == 0:
            ph = forge.camera_capture(render.paper_document(lines, seed=rng.randint(0, 999)), rng)
            variants.append(("photo", "image/jpeg", render.to_jpeg(ph, 90)))
        for v, mime, data in variants:
            rel = _write(OUT / "patterns" / idt.uid / f"statement_{v}.{'pdf' if v == 'pdf' else 'jpg'}", data)
            rows.append({"id": f"{idt.uid}:{scen}:{v}", "set": "patterns", "identity": idt.uid,
                         "doc_type": "bank_statement", "variant": v, "path": rel, "mime": mime, "label": label})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--set", choices=["clean", "degraded", "tamper", "tamper_cam", "patterns"], action="append")
    ap.add_argument("-n", type=int, default=None)
    a = ap.parse_args()
    sets = ["clean", "degraded", "tamper"] if a.all or not a.set else a.set
    builders = {"clean": build_clean, "degraded": build_degraded, "tamper": build_tamper,
                "tamper_cam": build_tamper_cam, "patterns": build_patterns}
    for s in sets:
        rows = builders[s](a.n) if a.n else builders[s]()
        (OUT / s).mkdir(parents=True, exist_ok=True)
        (OUT / s / "manifest.json").write_text(json.dumps(rows, indent=1))
        print(f"{s}: {len(rows)} documents -> {OUT / s}")


if __name__ == "__main__":
    main()
