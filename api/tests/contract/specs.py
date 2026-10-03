"""Contract specs: the exact requests whose responses must not change when the
endpoints move behind agents. Shared by capture.py (records goldens from the
pre-agent code) and test_contract.py (replays them against the current code).

Only synthetic documents are used (api/eval/datasets/synth/out) — no real
applicant data ever lands in a golden file.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

API = Path(__file__).resolve().parents[2]
SYNTH = API / "eval" / "datasets" / "synth" / "out"
GOLDEN_DIR = Path(__file__).resolve().parent / "goldens"

# Deterministic, model-free configuration for contract runs: mock extraction,
# no vision-LLM authenticity/explain calls. Set BEFORE importing the app.
CONTRACT_ENV = {
    "OCR_PROVIDER": "mock",
    "TAMPERING_AUTHENTICITY_ENABLED": "false",
    "TAMPERING_EXPLAIN_ENABLED": "false",
    "DB_ENABLED": "false",
    "AGENTS_SQL_SINK": "false",
    "FINSIGHT_FORBID_HOSTED": "true",
    # a throwaway data dir: never read the real Settings/LLM choice or dedupe index
    "FINSIGHT_DATA_DIR": tempfile.mkdtemp(prefix="finsight-contract-"),
}


def apply_env():
    os.environ.update(CONTRACT_ENV)


def isolate_compliance():
    """Use a freshly seeded compliance DB (the 14 seed rules), never the live
    knowledge base — rules an operator adds must not change the contract."""
    from app.compliance import db as dbm
    from app.compliance import startup as compliance_startup
    dbm.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="finsight-contract-comp-"), "compliance.db")
    compliance_startup()


_DATA_URI = re.compile(r"^data:[\w/+.-]+;base64,")


def normalize(obj, key: str = ""):
    """Make a response comparable: drop wall-clock timings, replace embedded
    images with a short content hash, round floats."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "timings" or k.endswith("_ms"):
                out[k] = sorted(v.keys()) if isinstance(v, dict) else "<ms>"
                continue
            out[k] = normalize(v, k)
        return out
    if isinstance(obj, list):
        return [normalize(v, key) for v in obj]
    if isinstance(obj, str) and _DATA_URI.match(obj):
        return "img:" + hashlib.sha256(obj.encode()).hexdigest()[:16]
    if isinstance(obj, float):
        return round(obj, 3)
    return obj


def _file(rel: str) -> tuple[str, bytes, str]:
    p = SYNTH / rel
    mime = "application/pdf" if p.suffix == ".pdf" else "image/jpeg"
    return p.name, p.read_bytes(), mime


def specs() -> list[dict]:
    """[{name, method, path, data?, files?, json?}] — files as (field, rel_path)."""
    s: list[dict] = [
        {"name": "doc_types", "method": "GET", "path": "/doc-types"},
        {"name": "compliance_rules", "method": "GET", "path": "/compliance/rules"},
        {"name": "compliance_rag", "method": "POST", "path": "/compliance/rag/query",
         "json": {"query": "What is the cooling-off period for digital loans?", "top_k": 3}},
    ]
    for doc, rel in [("pan", "clean/ID000/pan.jpg"), ("aadhaar", "clean/ID000/aadhaar.jpg"),
                     ("salary_slip", "clean/ID000/salary_slip.pdf"),
                     ("salary_slip", "clean/ID000/salary_slip_photo.jpg"),
                     ("bank_statement", "clean/ID000/bank_statement.pdf")]:
        s.append({"name": f"extract_{Path(rel).stem}", "method": "POST", "path": "/extract",
                  "data": {"doc_type": doc}, "files": [("file", rel)]})
    tamper = json.loads((SYNTH / "tamper" / "manifest.json").read_text())
    for row in [r for r in tamper if r["identity"] == "ID000"]:
        s.append({"name": f"integrity_{row['variant']}", "method": "POST",
                  "path": "/document/integrity",
                  "data": {"doc_type": row["doc_type"], "parsed_json": json.dumps(row["ocr_fields"])},
                  "files": [("file", row["path"])], "seed_torch": True})
    for doc, rel in [("pan", "clean/ID000/pan.jpg"), ("aadhaar", "clean/ID000/aadhaar.jpg")]:
        s.append({"name": f"mask_{doc}", "method": "POST", "path": "/document/mask",
                  "data": {"doc_type": doc, "parsed_json": "{}"}, "files": [("file", rel)]})
    s.append({"name": "face_verify_silhouettes", "method": "POST", "path": "/face/verify",
              "data": {"doc_types": "pan,aadhaar"},
              "files": [("files", "clean/ID000/pan.jpg"), ("files", "clean/ID000/aadhaar.jpg")]})
    s.append({"name": "policy_check", "method": "POST", "path": "/compliance/policy/check",
              "json": sample_application()})
    return s


def sample_application() -> dict:
    return {
        "application_id": "APP-CONTRACT-1", "loan_type": "personal_loan",
        "customer": {"kyc_status": "complete", "ovd_verified": True, "pan_available": True,
                     "consent": {"data_collection": True, "credit_bureau_pull": True},
                     "existing_emi": 5000, "proposed_emi": 12000, "monthly_income": 60000,
                     "credit_score": 720},
        "documents": {},
        "loan_terms": {"kfs_provided": True, "apr_disclosed": True, "all_fees_in_kfs": True,
                       "cooling_off_period_days": 3, "disbursal_to_borrower_account": True,
                       "penal_charge_is_interest": False},
        "risk": {"tamper_score": 10, "fraud_score": 12},
    }


def run_spec(client, spec: dict):
    files = None
    if spec.get("files"):
        files = [(field, _file(rel)) for field, rel in spec["files"]]
    if spec.get("seed_torch"):
        try:
            import torch
            torch.manual_seed(0)
        except Exception:  # noqa: BLE001
            pass
    if spec["method"] == "GET":
        r = client.get(spec["path"])
    elif spec.get("json") is not None:
        r = client.post(spec["path"], json=spec["json"])
    else:
        r = client.post(spec["path"], data=spec.get("data"), files=files)
    body = r.json()
    return r.status_code, normalize(body)
