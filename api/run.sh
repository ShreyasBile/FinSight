#!/usr/bin/env bash
# One command to run the FinSight OCR service (Git Bash / macOS / Linux).
# First run auto-creates the venv + installs deps; later runs just start the server.
set -e
cd "$(dirname "$0")"

# venv python lives in Scripts/ on Windows, bin/ elsewhere
PY=".venv/Scripts/python.exe"; [ -f "$PY" ] || PY=".venv/bin/python"

if [ ! -d ".venv" ]; then
  echo "[setup] First run - creating virtual environment..."
  python -m venv .venv
  PY=".venv/Scripts/python.exe"; [ -f "$PY" ] || PY=".venv/bin/python"
  echo "[setup] Installing dependencies..."
  "$PY" -m pip install -q --upgrade pip
  "$PY" -m pip install -q -r requirements.txt
fi

[ -f ".env" ] || cp ".env.example" ".env"

echo ""
echo "FinSight OCR running on http://localhost:8000  (docs: /docs)"
echo "Provider: see OCR_PROVIDER in api/.env  (default: mock)"
echo ""
"$PY" -m uvicorn app.main:app --reload --port 8000
