"""Latency of the main upload paths with NOTHING else running on the machine.

The test runs shared the PC with two or three other evaluations, so their latencies are upper bounds.
This times a few development documents per path, one at a time, on an otherwise idle machine and
writes eval/results_real/latency_idle.json for the report. Timing only - nothing is scored.

    python -m eval.real.timing_idle
"""
from __future__ import annotations

import json
import statistics

from eval import common as C
from eval.real import core as R


def _time(items, fn) -> dict:
    ms = []
    for it in items:
        with C.Timer() as t:
            fn(it)
        ms.append(t.ms)
    ms.sort()
    return {"n": len(ms), "median_s": round(statistics.median(ms) / 1000, 1), "max_s": round(ms[-1] / 1000, 1)}


def main():
    C.setup_env(provider="ollama", db=False)
    from app.agents.flows import document_flows as flows
    from app.agents.registry import get_agent
    from eval.real import evidence_real as E
    from eval.real import intake_real as I
    flows._parse_statement_later = lambda *a, **k: None
    pre, mask = get_agent("preprocessing"), get_agent("pii_masking")
    pans = I.pan_items("dev", 8)
    stm = I.statement_items("dev", None)
    digital = [s for s in stm if s["kind"] == "digital"][:4]
    scanned = [s for s in stm if s["kind"] == "scanned"][:4]
    frames = I.midv_items("dev")[:12]
    scans = I.sroie_items("dev", 12)
    receipts = [r for r in E.findit_items("dev") if not r["forged"]][:6]
    flows.run_extract(pans[0]["path"].read_bytes(), "image/png", "pan", "warm-up.png")      # model load not timed
    out = {
        "Cleanup of a phone photo of an ID card": _time(frames, lambda it: pre.execute(
            {"data": I._png(it["image"]), "mime": "image/png", "doc_type": "pan"})),
        "Cleanup of a scanned page": _time(scans, lambda it: pre.execute(
            {"data": I._png(it["image"]), "mime": "image/png", "doc_type": "salary_slip"})),
        "PAN card upload (cleanup + reading model + checks)": _time(pans, lambda it: flows.run_extract(
            it["path"].read_bytes(), "image/png", "pan", it["path"].name)),
        "Bank statement upload, digital PDF": _time(digital, lambda it: flows.run_extract(
            it["path"].read_bytes(), "application/pdf", "bank_statement", it["path"].name)),
        "Bank statement upload, scanned PDF": _time(scanned, lambda it: flows.run_extract(
            it["path"].read_bytes(), "application/pdf", "bank_statement", it["path"].name)),
        "Masking a PAN card": _time(pans, lambda it: mask.execute(
            {"data": it["path"].read_bytes(), "doc_type": "pan", "parsed": {}})),
        "Integrity check of a scanned receipt": _time(receipts, lambda it: flows.run_integrity(
            it["path"].read_bytes(), it["path"].name, "vehicle_quotation", {})),
    }
    (R.RESULTS / "latency_idle.json").write_text(json.dumps(
        {"note": "development documents, one at a time, nothing else running; local model already loaded",
         "machine": R.machine() if hasattr(R, "machine") else None, "paths": out}, indent=1), encoding="utf-8")
    for k, v in out.items():
        print(f"{k:55s} {v}")


if __name__ == "__main__":
    main()
