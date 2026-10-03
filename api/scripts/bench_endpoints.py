"""Latency benchmark for the hot document endpoints, in-process (TestClient,
no network), model-free (mock extraction, authenticity + explain off) so the
numbers isolate the code path — before vs after the agent layer.

    # baseline (pre-agent commit) — from a worktree of ef3d045, its old venv:
    .venv/Scripts/python scripts/bench_endpoints.py --app-dir <worktree>/api --label baseline
    # current build:
    .venv-agents/Scripts/python scripts/bench_endpoints.py --label agents

Writes api/eval/results/latency_<label>.json. Fixtures are the synthetic
documents in api/eval/datasets/synth/out (never real documents).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]           # this checkout's api/
SYNTH = HERE / "eval" / "datasets" / "synth" / "out"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app-dir", default=str(HERE))
    ap.add_argument("--label", required=True)
    ap.add_argument("-n", type=int, default=12)
    ap.add_argument("--provider", default="mock", choices=["mock", "ollama"])
    ap.add_argument("--cases", default="", help="comma-separated subset")
    a = ap.parse_args()
    data_dir = tempfile.mkdtemp(prefix="finsight-bench-")
    os.environ.update({"OCR_PROVIDER": a.provider, "TAMPERING_AUTHENTICITY_ENABLED": "false",
                       "TAMPERING_EXPLAIN_ENABLED": "false", "DB_ENABLED": "false", "AGENTS_SQL_SINK": "false",
                       "FINSIGHT_DATA_DIR": data_dir, "FINSIGHT_FORBID_HOSTED": "true",
                       "PYTHONIOENCODING": "utf-8"})
    sys.path.insert(0, a.app_dir)
    os.chdir(a.app_dir)
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    tamper = json.loads((SYNTH / "tamper" / "manifest.json").read_text())
    genuine = next(r for r in tamper if r["variant"] == "salary_genuine")
    photo = next(r for r in tamper if r["variant"] == "salary_photo_genuine")
    f = lambda rel: (Path(rel).name, (SYNTH / rel).read_bytes(),  # noqa: E731
                     "application/pdf" if rel.endswith(".pdf") else "image/jpeg")
    cases = {
        "extract_pan_photo": ("/extract", {"doc_type": "pan"}, [("file", f("clean/ID000/pan.jpg"))]),
        "extract_statement_pdf": ("/extract", {"doc_type": "bank_statement"},
                                  [("file", f("clean/ID000/bank_statement.pdf"))]),
        "integrity_pdf": ("/document/integrity", {"doc_type": "salary_slip",
                                                   "parsed_json": json.dumps(genuine["ocr_fields"])},
                          [("file", f(genuine["path"]))]),
        "integrity_photo": ("/document/integrity", {"doc_type": "salary_slip",
                                                     "parsed_json": json.dumps(photo["ocr_fields"])},
                            [("file", f(photo["path"]))]),
        "mask_pan": ("/document/mask", {"doc_type": "pan", "parsed_json": "{}"}, [("file", f("clean/ID000/pan.jpg"))]),
        "face_verify": ("/face/verify", {"doc_types": "pan,aadhaar"},
                        [("files", f("clean/ID000/pan.jpg")), ("files", f("clean/ID000/aadhaar.jpg"))]),
    }
    if a.cases:
        cases = {k: v for k, v in cases.items() if k in a.cases.split(",")}
    out = {}
    for name, (path, data, files) in cases.items():
        client.post(path, data=data, files=files)                 # warm-up (model loads, caches)
        ms = []
        for _ in range(a.n):
            t = time.perf_counter()
            r = client.post(path, data=data, files=files)
            ms.append((time.perf_counter() - t) * 1000)
            assert r.status_code == 200, (name, r.status_code, r.text[:200])
        ms.sort()
        out[name] = {"p50_ms": round(statistics.median(ms), 1),
                     "p95_ms": round(ms[min(len(ms) - 1, int(len(ms) * 0.95))], 1), "n": a.n}
        print(name, out[name], flush=True)
    dest = HERE / "eval" / "results" / f"latency_{a.label}.json"
    dest.write_text(json.dumps({"label": a.label, "app_dir": a.app_dir, "results": out}, indent=1))
    print("->", dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
