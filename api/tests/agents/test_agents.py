"""Cross-cutting agent guarantees (plan: "Hard rules").

* every roster agent is registered and implemented;
* no agent declares a forbidden side effect (approving, filing, sending …);
* the hot endpoints keep today's LLM-call budget — /extract 1, everything
  else on the upload path and the case assessment 0;
* the tighten-only supervisor, the grounding gate and advisory-only outputs.
"""
import json

import pytest

from tests.contract.specs import SYNTH, apply_env, isolate_compliance

apply_env()


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    isolate_compliance()
    return TestClient(app)


def test_roster_complete_and_implemented():
    from app.agents.registry import IMPLEMENTATIONS, REGISTRY
    import app.agents.impl  # noqa: F401 - registers
    assert len(REGISTRY) == 29
    missing = sorted(set(REGISTRY) - set(IMPLEMENTATIONS))
    assert missing == [], f"agents without an implementation: {missing}"


def test_no_forbidden_side_effects():
    from app.agents.registry import FORBIDDEN_SIDE_EFFECTS, REGISTRY
    bad = {a: set(s.side_effects) & FORBIDDEN_SIDE_EFFECTS for a, s in REGISTRY.items()}
    assert not any(bad.values()), bad


def _llm_calls(client, run_id):
    run = client.get(f"/agents/runs/{run_id}").json()
    return sum(len(s.get("llm_calls") or []) for s in run["steps"]), [s["agent_id"] for s in run["steps"]]


def _post(client, path, data, files):
    fl = [(field, (p.name, p.read_bytes(), "application/pdf" if p.suffix == ".pdf" else "image/jpeg"))
          for field, p in files]
    return client.post(path, data=data, files=fl, headers={"X-Case-Id": "11111111-2222-3333-4444-555555555555"})


def test_hot_path_llm_budget(client):
    pan, aad = SYNTH / "clean/ID000/pan.jpg", SYNTH / "clean/ID000/aadhaar.jpg"
    r = _post(client, "/extract", {"doc_type": "pan"}, [("file", pan)])
    assert r.status_code == 200
    n, agents = _llm_calls(client, r.headers["X-Finsight-Run-Id"])
    assert n <= 1 and "extraction" in agents
    for path, data, files in [("/document/mask", {"doc_type": "pan", "parsed_json": "{}"}, [("file", pan)]),
                              ("/face/verify", {"doc_types": "pan,aadhaar"}, [("files", pan), ("files", aad)])]:
        r = _post(client, path, data, files)
        assert r.status_code == 200, path
        n, _ = _llm_calls(client, r.headers["X-Finsight-Run-Id"])
        assert n == 0, f"{path} made {n} LLM calls"


def test_case_assessment_makes_no_llm_calls(client):
    cid = "11111111-2222-3333-4444-555555555555"
    r = client.post(f"/agents/cases/{cid}/assess", json={"no_notify": True})
    assert r.status_code == 200, r.text
    body = r.json()
    n, agents = _llm_calls(client, body["run_id"])
    assert n == 0 and "fraud_fusion" in agents and "policy_check" in agents
    assert body["fraud"]["tier"] in ("LOW", "MEDIUM", "HIGH")
    assert body["route"] in ("continue", "refer")          # advisory route, never a status change


def test_supervisor_can_only_tighten():
    from app.agents.crews.underwriting import STRICTNESS
    from app.agents.impl.underwriting import enforce_floor
    order = sorted(STRICTNESS, key=STRICTNESS.get)
    for base in order:
        for llm in order:
            final, blocked = enforce_floor({"recommendation": base}, llm)
            assert STRICTNESS[final] >= STRICTNESS[base]
            assert blocked == (STRICTNESS[llm] < STRICTNESS[base])


def test_grounding_gate():
    from app.agents.base import grounded
    ok, _ = grounded("Net salary Rs 65,820 matches the payslip.", {"net": 65820})
    assert ok
    ok, bad = grounded("Net salary Rs 95,000 matches the payslip.", {"net": 65820})
    assert not ok and bad


# ── Phase 5 tools ───────────────────────────────────────────────────────────
def test_amount_parsing_tolerates_ocr_separators():
    from app.agents.tools.transactions import _AMT, _amount
    vals = [_amount(a) for a in _AMT.findall("RENT 16,011.00 1,36,131.0 x 1,48978.00 y 1,47.715.00")]
    assert vals == [16011.0, 136131.0, 148978.0, 147715.0]
    assert _AMT.findall("DOB 27/09/1984 UPI/554125/GROCERY") == []


def test_reconcile_corrects_a_misread_balance():
    from app.agents.tools.transactions import _reconcile
    raw = [{"date": "01/04/2026", "description": "SALARY", "txn": 50000.0, "balance": 150000.0},
           {"date": "05/04/2026", "description": "UPI", "txn": 2000.0, "balance": 48000.0},   # misread 1,48,000
           {"date": "10/04/2026", "description": "BILL", "txn": 1000.0, "balance": 147000.0}]
    rows = _reconcile(raw, 100000.0)
    assert rows[1]["balance"] == 148000.0 and rows[1]["debit"] == 2000.0 and rows[1]["reconciled"]
    assert rows[0]["credit"] == 50000.0 and rows[2]["debit"] == 1000.0


def _tx(day, desc, dr, cr, bal, ok=True):
    return {"date": f"{day:02d}/05/2026", "description": desc, "debit": dr, "credit": cr, "balance": bal,
            "reconciled": ok}


def test_patterns_cash_cycling_and_no_flag_on_clean():
    from app.agents.tools.fraud_patterns import scan
    clean = [_tx(1, "SALARY CREDIT", 0, 60000, 160000), _tx(5, "UPI", 2000, 0, 158000),
             _tx(15, "RENT", 15000, 0, 143000), _tx(25, "UPI", 3000, 0, 140000)]
    assert scan(clean, payslip_net=60000)["flags"] == []
    cyc = clean + [_tx(26, "IMPS CR", 0, 200000, 340000), _tx(27, "IMPS DR", 200000, 0, 140000),
                   _tx(28, "IMPS CR", 0, 300000, 440000), _tx(29, "IMPS DR", 300000, 0, 140000)]
    assert "cash_cycling" in {f["pattern"] for f in scan(cyc)["flags"]}


def test_patterns_ignore_unreconciled_rows():
    from app.agents.tools.fraud_patterns import scan
    rows = [_tx(1, "SALARY CREDIT", 0, 60000, 160000), _tx(5, "UPI", 2000, 0, 158000),
            _tx(20, "MISREAD", 0, 900000, 1058000, ok=False), _tx(25, "UPI", 3000, 0, 155000)]
    assert "sudden_deposit" not in {f["pattern"] for f in scan(rows)["flags"]}


def test_triage_links_skip_repeat_applicants():
    import numpy as np
    from app.agents.tools.fraud_triage import links
    face = list(np.ones(128) / np.sqrt(128))
    other = list(np.r_[np.ones(64), -np.ones(64)] / np.sqrt(128))
    keys = {"a": {"pan_hmac": {"P1"}}, "b": {"pan_hmac": {"P1"}},        # same PAN + same face: customer
            "c": {"pan_hmac": {"P2"}}, "d": {"pan_hmac": {"P3"}}}        # same face, different PANs: ring
    faces = {"a": [np.asarray(face)], "b": [np.asarray(face)], "c": [np.asarray(face)], "d": [np.asarray(face)]}
    got = {(e["a"], e["b"]): e["kinds"] for e in links(keys, faces)}
    assert ("a", "b") not in got
    assert got[("c", "d")] == ["face_across_ids"]
    faces["b"] = [np.asarray(other)]
    got = {(e["a"], e["b"]): e["kinds"] for e in links(keys, faces)}
    assert "id_different_face" in got[("a", "b")]


def test_notifier_renders_in_app_only_and_never_half_filled():
    from app.agents.tools import notifier
    ctx = {"application_no": "APP-1", "applicant": "A", "product": "Home Loan", "recommendation": "refer",
           "next_step": "underReview", "requires_head": True, "amount": 650000}
    drafts = notifier.render("underwriting_completed", ctx)
    assert {d["role"] for d in drafts} == {"officer", "head"}
    assert all(d["channel"] == "in_app" and "{" not in d["body"] for d in drafts)
    assert "Rs 6,50,000" in next(d for d in drafts if d["role"] == "head")["body"]
    assert all(d.get("skipped") for d in notifier.render("offer_drafted", {"application_no": "APP-1"}))


def test_sql_persist_mappings():
    from app.agents.sql_persist import applicant_from, doc_code, product_code
    assert product_code("Two Wheeler Loan") == "two_wheeler" and product_code("Car Loan") == "car"
    assert doc_code("gst_return") == "gst_certificate" and doc_code("cibil") == "other"
    a = applicant_from({"applicant_name": "Asha Nair", "documents": [
        {"doc_type": "pan", "fields": {"pan_number": "abcde1234f", "dob": "05/06/1990"}}]})
    assert a["pan"] == "ABCDE1234F" and a["dob"] == "1990-06-05" and a["last_name"] == "Nair"


def test_low_quality_face_never_asserts_a_mismatch(monkeypatch):
    """Two very different faces: a confident 'mismatch' only when both photos
    are good; if either face was barely detectable the verdict is 'uncertain'."""
    import numpy as np
    from app.face import service
    a = np.r_[np.ones(64), np.zeros(64)].astype(np.float32).reshape(1, -1)
    b = np.r_[np.zeros(64), np.ones(64)].astype(np.float32).reshape(1, -1)
    faces = {}
    # fresh dicts per call: verify() pops the embeddings out of what it is given
    monkeypatch.setattr(service, "analyze_document",
                        lambda data: {"faces": [dict(f) for f in faces[data]], "error": None})
    monkeypatch.setattr(service, "_similarity", lambda x, y: float((x @ y.T).ravel()[0] / 64))
    face = lambda emb, q: {"bbox": [0, 0, 10, 10], "score": 0.9, "rotation_to_upright": 0,  # noqa: E731
                           "roll_degrees": 0.0, "quality": q, "thumb": None, "embedding": emb}
    faces.update({b"pan": [face(a, "ok")], b"aad": [face(b, "ok")]})
    assert service.verify([("pan", b"pan"), ("aadhaar", b"aad")])["overall"] == "mismatch"
    faces.update({b"aad": [face(b, "low")]})
    res = service.verify([("pan", b"pan"), ("aadhaar", b"aad")])
    assert res["overall"] != "mismatch" and res["pairs"][0]["verdict"] == "uncertain"


def test_pdfium_calls_survive_concurrency():
    """PDFium is not thread-safe; the intake flow reads a statement's text and
    rows beside the model call that rasterises the same PDF. Unserialised, two
    threads inside PDFium crashed the process outright (no traceback)."""
    from concurrent.futures import ThreadPoolExecutor
    from app.agents.tools.doc_keywords import page_text
    from app.agents.tools.transactions import parse
    from app.utils import render_pdf_to_images
    pdf = (SYNTH / "clean/ID000/bank_statement.pdf").read_bytes()
    jobs = [lambda: render_pdf_to_images(pdf, max_pages=2), lambda: page_text(pdf, "application/pdf"),
            lambda: parse(pdf)] * 12
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = [f.result() for f in [ex.submit(j) for j in jobs]]
    assert all(r for r in results)
    assert len(parse(pdf)["transactions"]) > 10


def test_router_accepts_either_review_status():
    """Diagram 7's review node is 'underReview / underwritingReview' for every
    product; a seeded commercial-vehicle case in underwritingReview must get a
    next step, not 'not on the path'."""
    from app.agents.tools.workflow import next_step
    r = next_step("commercial_vehicle", "underwritingReview", {}, 1_800_000)
    assert r["next"] == "offerGenerated" and r["status"] == "underwritingReview" and r["requires_head_approval"]
