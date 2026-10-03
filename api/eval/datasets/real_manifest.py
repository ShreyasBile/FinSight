"""Local-only manifests of real documents found on this machine.

  real      the developer's own documents (storage/, document_tampering/document
            tests, document_tampering/data/archive) with labels assigned by
            inspection: genuine / fabricated (documents the developer made to
            test fraud detection) / unknown. NEVER sent to a hosted model;
            results are reported only as counts. Written to datasets/real/
            (gitignored).
  external  the paired original/tampered paystub + bank-statement images in
            D:/novacinternship/multimodal-document-tampering-detection — an
            independent pixel-edit test set (not used to train FinSight's CNN).
"""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

NOVAC = Path(__file__).resolve().parents[4]          # D:/novacinternship
OUT = Path(__file__).resolve().parent / "real"

# archive files (document_tampering/data/archive), labelled by looking at them
ARCHIVE = {
    "08ebfea9": ("bank_statement", "genuine", "unlocked with iLovePDF — design: surface as MEDIUM"),
    "0db981de": ("aadhaar", "genuine", ""), "27a6463f": ("aadhaar", "genuine", ""),
    "44203e5a": ("vehicle_quotation", "genuine", ""), "531de264": ("aadhaar", "genuine", ""),
    "bf16fec1": ("aadhaar", "genuine", "phone screenshot"), "e78c8eeb": ("pan", "genuine", "Adobe Scan PDF"),
    "ac372d30": ("bank_statement", "fabricated", "developer-made statement (fictional holder)"),
    "e52f429c": ("bank_statement", "fabricated", "developer-made statement (fictional holder)"),
    "b9624a9e": ("salary_slip", "fabricated", "jsPDF-generated payslip"),
    "bd6e4363": ("pan", "unknown", "provenance unclear — excluded from scores"),
    "42e616aa": ("salary_slip", "not_a_document", "Canva project proposal — wrong-document probe"),
}
TESTS = {
    "62988XXXX_DownloadStatement_1783510941_unlocked.pdf": ("bank_statement", "genuine", "unlocked (utility re-save)"),
    "Screenshot 2026-06-08 113017.png": ("aadhaar", "genuine", "phone screenshot"),
    "WhatsApp Image 2026-06-05 at 4.22.07 PM.jpeg": ("aadhaar", "genuine", ""),
    "WhatsApp Image 2026-06-09 at 3.49.59 PM.jpeg": ("aadhaar", "genuine", ""),
    "WhatsApp Image 2026-07-08 at 5.38.06 PM.jpeg": ("vehicle_quotation", "genuine", ""),
    "pan card shreyas.pdf": ("pan", "genuine", ""),
    "PaySlip_shreyas_bile_July_2026.pdf": ("salary_slip", "fabricated", "developer-made payslip"),
}


def _mime(p: Path) -> str:
    return "application/pdf" if p.suffix.lower() == ".pdf" else "image/png" if p.suffix.lower() == ".png" else "image/jpeg"


def build() -> dict:
    rows, seen = [], {}
    def add(p: Path, doc_type, label, note, source):
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
        if sha in seen:                      # the same file stored in two places
            seen[sha]["also_at"].append(source)
            return
        row = {"id": f"real{len(rows):02d}", "path": str(p), "doc_type": doc_type, "mime": _mime(p),
               "label": label, "note": note, "source": source, "sha12": sha[:12], "also_at": []}
        seen[sha] = row
        rows.append(row)
    for p in sorted((NOVAC / "document_tampering" / "document tests").glob("*/*")):
        if p.name in TESTS:
            add(p, *TESTS[p.name], "document tests")
    for p in sorted((NOVAC / "document_tampering" / "data" / "archive").glob("*")):
        key = p.name[:8]
        if key in ARCHIVE:
            add(p, *ARCHIVE[key], "archive")
    for p in sorted((NOVAC / "finsight" / "storage").glob("APP-*/*_original.*")):
        dt = p.name.split("_original")[0]
        head = p.read_bytes()[:200_000]
        if b"Canva" in head:
            # the image file carries Canva XMP metadata: it was exported from a
            # design tool, so "genuine" cannot be asserted — excluded from scores
            add(p, dt, "edited_unknown", "stored application; Canva export (XMP) — provenance unclear",
                f"storage/{p.parent.name}")
            continue
        add(p, dt, "genuine" if dt != "salary_slip" else "fabricated",
            "stored application" + (" (the developer's test payslip)" if dt == "salary_slip" else ""),
            f"storage/{p.parent.name}")
    rng = random.Random(11)
    base = NOVAC / "multimodal-document-tampering-detection" / "images" / "training"
    ext = []
    for kind, dt, n in (("Paystub", "salary_slip", 25), ("BankStatement", "bank_statement", 20)):
        idx = rng.sample(range(100), n)
        for i in idx:
            for folder, prefix, forged in (("original", "", False), ("forged", "Tampered", True)):
                p = base / folder / f"{prefix}{kind}_{i}.jpg"
                if p.exists():
                    ext.append({"id": f"ext:{prefix}{kind}_{i}", "path": str(p), "doc_type": dt,
                                "mime": "image/jpeg", "label": {"forged": forged, "attack": "pixel_edit" if forged else "none"}})
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "manifest.json").write_text(json.dumps(rows, indent=1))
    (OUT / "external_manifest.json").write_text(json.dumps(ext, indent=1))
    return {"real": len(rows), "external": len(ext)}


if __name__ == "__main__":
    print(build())
