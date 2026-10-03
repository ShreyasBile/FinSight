@echo off
REM One command to run the FinSight API (document intelligence + agents).
REM First run auto-creates the agents venv + installs deps; later runs just start the server.
cd /d "%~dp0"

if not exist ".venv-agents" (
  echo [setup] First run - creating virtual environment...
  python -m venv .venv-agents
  echo [setup] Installing dependencies...
  .venv-agents\Scripts\python -m pip install -q --upgrade pip
  .venv-agents\Scripts\python -m pip install -q -r requirements-agents.txt
  REM rapidocr pulls the GUI build of OpenCV; the service uses the headless one
  .venv-agents\Scripts\python -m pip uninstall -y -q opencv-python
  .venv-agents\Scripts\python -m pip install -q --force-reinstall --no-deps opencv-python-headless==4.10.0.84
)

if not exist ".env" copy ".env.example" ".env" >nul

REM telemetry off: this service handles applicants' financial data
set CREWAI_DISABLE_TELEMETRY=true
set CREWAI_TRACING_ENABLED=false
set OTEL_SDK_DISABLED=true
set ANONYMIZED_TELEMETRY=False
set PYTHONIOENCODING=utf-8

echo.
echo FinSight API running on http://localhost:8000  (docs: /docs)
echo AI model: choose it in the app (Head Manager - Settings - AI Models)
echo Press Ctrl+C to stop.
echo.
REM --reload-dir app: watch the service's code only, not the ~52,000 files of .venv-agents
.venv-agents\Scripts\python -m uvicorn app.main:app --reload --reload-dir app --port 8000
