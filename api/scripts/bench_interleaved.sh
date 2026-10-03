#!/usr/bin/env bash
# Baseline vs agent build, real local model, interleaved rounds so GPU load
# (shared with other programs) hits both builds alike. Run from api/.
# Needs a worktree of the pre-agent commit at ../../finsight-baseline.
set -u
CASES=extract_pan_photo,extract_statement_pdf
for r in 1 2 3; do
  (cd ../../finsight-baseline/api && ../../finsight/api/.venv/Scripts/python.exe \
     ../../finsight/api/scripts/bench_endpoints.py --app-dir "$(pwd)" --label "baseline_ollama_r$r" \
     --provider ollama --cases "$CASES" -n 4) >/dev/null 2>&1
  .venv-agents/Scripts/python.exe scripts/bench_endpoints.py --label "agents_ollama_r$r" \
     --provider ollama --cases "$CASES" -n 4 >/dev/null 2>&1
done
echo done
