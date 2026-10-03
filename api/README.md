<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="../docs/brand/logo-lockup-light.png">
    <img src="../docs/brand/logo-lockup.png" alt="FinSight AI" width="260">
  </picture>
</p>

# FinSight OCR Service

Local document-extraction (IDP) microservice for FinSight AI. Takes an uploaded
document + a `doc_type`, runs OCR/extraction, validates fields with real checksums,
and returns structured JSON the frontend renders.

This replaces the original browser → Anthropic call (which had no auth and hit CORS)
with a proper server-side service that keeps the API key / PII off the client.

## Run

```bash
cd api
python -m venv .venv
# Windows: .venv\Scripts\activate    |   macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000/docs for the interactive API.

## Providers (set `OCR_PROVIDER` in `.env`)

| value | what it does | needs |
|-------|--------------|-------|
| `mock` | returns canned, schema-valid data — wire/test the whole app with no model | nothing (default) |
| `ollama` | local Qwen2.5-VL (recommended local) | `ollama pull qwen2.5vl:7b` |
| `anthropic` | server-side Claude vision (no GPU) | `ANTHROPIC_API_KEY` |

Switching provider requires **no code or frontend changes** — same `/extract` contract.

## Endpoints
- `POST /extract` — `multipart/form-data`: `doc_type` (e.g. `pan`), `file`. Returns
  `{ parsed, raw, fields[], confidence, needs_review, provider }`.
- `GET /doc-types` — supported document types.
- `GET /health` — liveness + active provider.

## Guardrails
Every field is structurally validated (not just OCR confidence):
PAN/IFSC/GSTIN format, **Aadhaar Verhoeff checksum** (masked Aadhaar accepted),
PIN/CIBIL range, salary sanity. Each field → `ok | warn | error`; the document gets
a `confidence` and a `needs_review` flag that the UI uses to force manual review.

## Image preprocessing
Raster uploads are cleaned before the model sees them (`app/preprocess.py`), which
is a big accuracy win on real phone photos:

1. **EXIF** orientation fix
2. **Auto-orient** — 12 angles (0°…330°) scored by EasyOCR confidence, rotate the winner
3. **Warp** — document contour detection (4 brightness variants × 3 Canny configs) → perspective flatten; falls back to morphological text-line **deskew**
4. **Adaptive IQA** — ordered denoise → brightness → contrast → sharpen (each gated on measured metrics)
5. **Quality gate** — re-OCR; if the cleaned image reads > 30 % fewer chars, keep the original

Toggle with `OCR_PREPROCESS` (default `true`). If it fails for any reason the raw
image is sent to the model — extraction never fails just because cleanup did. PDFs
use the provider's own rasterization path and skip this step. Optional background
removal (`PREPROCESS_SEGMENT=true`) needs `pip install rembg onnxruntime`.

> Preprocessing pulls in `opencv-python-headless`, `numpy`, and `easyocr` (which
> brings **torch, ~2 GB**). EasyOCR also downloads its detection/recognition models
> (~170 MB) on first run. Set `OCR_PREPROCESS=false` to run lean without them.

## Frontend wiring
`src/utils/ocr.js` posts to `${VITE_OCR_API_URL || http://localhost:8000}/extract`.
Set `VITE_OCR_API_URL` in the web app's environment to point elsewhere.

## Agent layer (CrewAI) — `app/agents/`

Every feature is one of 29 agents in six crews (intake, privacy, identity & fraud
evidence, compliance, underwriting, operations). The endpoints above keep their
exact contracts; behind them each call runs as a traced agent step.

```bash
cd api
python -m venv .venv-agents
.venv-agents\Scripts\pip install -r requirements-agents.txt   # crewai 1.15.22, xgboost, pytest …
run.bat                                                        # uvicorn on :8000 with telemetry off
```

**How an agent works.** Each agent runs its deterministic tool first
(`execute()` → score, tier, verdict). An LLM is used only to narrate or judge, in
ONE structured call, and its text must pass the grounding gate: every number of
10 or more it states must exist in the evidence, otherwise the deterministic text
is kept. The underwriting supervisor may tighten the rule baseline, never loosen
it. Nothing auto-rejects; loans above Rs 5 lakh need the Head. No agent has an
approve / reject / file / send tool (`registry.FORBIDDEN_SIDE_EFFECTS`, tested).

**LLM choice (Settings → AI Models, Head only).** `GET/PUT /settings/llm` stores
the provider/model per default, lane (vision/text) and agent in
`api/data/llm_settings.json`; every run takes a snapshot. A hosted provider needs
the Head's acknowledgement and is refused outright when `FINSIGHT_FORBID_HOSTED=true`;
hosted text lanes receive pseudonymised evidence. All calls go through
`app/agents/llm/gateway.py` (Ollama or the Anthropic SDK), which also scales images
to fit the model's context window.

**Tracing.** Runs and steps are kept in memory, as JSON under
`storage/agent_traces/`, and (when `AGENTS_SQL_SINK=true`) in SQL Server's `agent.*`
tables and `pm.event_log` via `pm.usp_log_event` — migration
`docs/mssql/08-agentic-layer.sql`. Environment `prod` (evaluations write `demo`, which no screen lists); a failing database never
fails a request (spool + replay).

| Endpoint | What |
|---|---|
| `GET /agents`, `/agents/health`, `/agents/stats` | roster, model per agent, p50/p95 |
| `GET /agents/runs?case_id=`, `/agents/runs/{id}` | traces |
| `POST /agents/cases/{id}/assess` | cross-check · dedupe · statement patterns → fraud fusion → policy (0 LLM calls) |
| `POST /agents/cases/{id}/underwrite` | income → risk → Underwriting Crew (supervisor + memo) → offer → next step → notifications |
| `POST /agents/applications/{no}/assess` · `/underwrite` | the same for applications stored in SQL Server |
| `GET /agents/fraud/triage` | ring and case PROPOSALS (never filed) |
| `GET /agents/process/insights?narrate=` | process-mining facts from `pm.vw_*` (+ grounded briefing) |
| `POST /agents/notify/{event}` | in-app notification drafts (no external channel exists) |
| `POST /liveness/check` | selfie: anti-spoof (MiniFASNet) + match to the case's ID photo |

`AGENTS_ENABLED=false` is the kill switch: the same tools run directly, untraced.
Wizard submissions are also written to `core.customers / loan_applications /
uploaded_documents` (`app/agents/sql_persist.py`) with the loan terms and contact details: a
borrower's application is attached to their own customer record and queued for the default
officer; an officer's goes to that officer's queue.

**Clearing test data.** `scripts/purge_mock_data.py --keep APP-...` removes every other
application and everything that hangs off it (dry run by default; `--apply` first takes a full
database backup under `api/data/backups/`).

**Tests and evaluation.**

```bash
.venv-agents\Scripts\python -m pytest tests/unit tests/agents tests/contract
.venv-agents\Scripts\python -m eval.datasets.synth.gen_docs --all --set patterns --set tamper_cam
.venv-agents\Scripts\python -m eval.run_eval --all        # results → eval/results/<agent>.json
```

Targets are fixed in `eval/targets.yaml` before a run; the evaluation uses local
models only and a throwaway data directory. Results are written up in
`docs/reports/06-FinSight-Agent-Evaluation-Report.docx`.

**Real-data evaluation** (`eval/real/`). Each agent on its own, on real or public production-like
datasets kept outside the repository (`D:\novacinternship\finsight-eval-data\raw`, sources and
licences in `DATASETS.md` there). Targets are fixed in `eval/targets_real.yaml` before a run; every
dataset is split 30 / 70 into development and test by a fixed seed, fixes are developed on the
development split only and the test split is scored again once.

```bash
.venv-agents\Scripts\python -m eval.real.run_real --agent extraction --split test   # → eval/results_real/extraction.test.json
.venv-agents\Scripts\python -m eval.real.report_real                                # → docs/reports/08-FinSight-Real-Data-Evaluation.pdf / .docx
```

Per-item results are cached under `finsight-eval-data\work\cache`, so an interrupted run resumes.
Caches and logs hold scores and verdicts only, never a value read from a document.

**Patch detector** (`app/tampering/signals/patch_cnn.py`, models in `app/tampering/models/patch_real_v1/`).
A stage-2 tampering signal trained on real forged documents — receipts edited by people (Find it again!)
and ID images with replaced or repainted fields (SIDTD), development splits only. Two small models per
document family (`id`, `page`), each trained on one half of the development documents and calibrated on
the other; a document is evidence only when both models put it above the score 95% of genuine documents
stay under, and the finding names up to three areas, which the locator draws. Alone it gives MEDIUM,
never HIGH. To retrain (needs the datasets) or only recalibrate from the saved held-out scores:

```bash
.venv-agents\Scripts\python -m eval.real.train_patch_forensics ids        # or: receipts  → finsight-eval-data\work\patch_models
.venv-agents\Scripts\python -m eval.real.train_patch_forensics calibrate id   # or: page
```

Copy `<family>_A.pt`, `<family>_B.pt` and `<family>.json` into `app/tampering/models/patch_real_v1/`;
without them the signal reports itself unavailable and the cascade runs as before.

## Roadmap hooks
- Swap local storage for **S3** (raw uploads) + push results to the Delta lakehouse.
- Add a **batch** path (Databricks/Spark) reusing the same prompts + guardrails.
- Add cross-document validation + a LayoutLM/Donut fine-tune for high-volume templates.

---

## RBI Compliance module (`app/compliance/`)

Checks a loan application against current RBI regulations and internal
underwriting policy, and answers regulatory questions over a knowledge base.
This is the [RBI Compliance Checker](../../projects/rbi%20compliance%20checker/)
mounted as a module — same engine, same seed content, its own SQLite DB
(`app/compliance/data/compliance.db`, created + seeded automatically on
startup), served under `/compliance/*` on this same app (port 8000). The
standalone project keeps working independently on its own port; the two are
copies, not a shared deployment, so changes to one don't reach the other
automatically.

### Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/compliance/policy/check` | POST | Evaluate a loan application; per-rule results + overall decision. Powers the **RBI Compliance** tab in Customer 360. |
| `/compliance/rag/query` | POST | BM25 query over the regulatory KB → ranked passages with citations. Powers the **AI Policy Query** chat embedded in both Policy Engine pages. |
| `/compliance/kb/documents` | GET/POST | List / ingest regulatory documents |
| `/compliance/kb/documents/{id}/chunks` | GET | View a document's indexed chunks |
| `/compliance/kb/documents/{id}` | DELETE | Remove a document |
| `/compliance/rules` | GET/POST | List / create rules |
| `/compliance/rules/{rule_id}` | DELETE | Deactivate a rule |
| `/compliance/kb/documents/{id}/extract` | POST | Extract **draft** rule candidates from a document (local-LLM via Ollama, falls back to a deterministic heuristic) |
| `/compliance/rules/candidates` | GET | List draft candidates (`?status=pending\|approved\|rejected\|all`) |
| `/compliance/rules/candidates/{id}/approve` | POST | Reviewer maps the field placeholder + approves → creates a live rule |
| `/compliance/rules/candidates/{id}/reject` | POST | Reviewer rejects a draft |
| `/compliance/audit` | GET | Rule-lifecycle audit trail (extract / approve / reject / create / deactivate) |
| `/compliance/stats` | GET | Dashboard counts, incl. pending-review count |

### Frontend surfaces

| Page | Role(s) | What it does |
|------|---------|--------------|
| `src/pages/head/HeadCompliance.jsx` | Head Manager only | Knowledge-base management (ingest/extract/delete), rule authoring, the maker-checker Review Queue, audit log |
| `src/components/ComplianceCheckPanel.jsx` | Officer + Head | "RBI Compliance" tab on Customer 360 — runs the applicant against every active rule, shows citations + remediation per rule |
| `src/components/RegulatoryChat.jsx` | Officer + Head | Embedded chat, used inside both Policy Engine pages (`PagePolicy.jsx`, `officer/OfficerPolicy.jsx`) — **not** a separate page; the Policy Engine mock query box *is* this chat now |
| `src/utils/compliance.js` | — | Fetch wrapper for all `/compliance/*` calls, same convention as `utils/ocr.js` |

### Rule authoring: extraction + maker-checker review

Same model as the standalone project — an obligation extractor (local Ollama,
falling back to a modal-verb heuristic) only ever proposes **draft** rules;
nothing becomes an active rule until a reviewer maps the condition's
placeholder field and approves. See the standalone README's
["Rule authoring"](../../projects/rbi%20compliance%20checker/README.md#rule-authoring-extraction--maker-checker-review)
section for the full design rationale — it applies unchanged here.

### Known limitations (as integrated)

- Chat answers are the top retrieved passage verbatim, not an LLM-synthesized
  answer — real retrieval, not yet real generation.
- The Customer 360 compliance check runs against a hardcoded payload
  (`COMPLIANCE_PAYLOAD` in `PageCustomer360.jsx`), not a persisted applicant
  record — FinSight has no per-applicant database yet (see the pending
  database-schema design) to draw real values from.
- Role gating (Head-only management) is frontend-only; the API itself does not
  authenticate or authorize the caller.
- `reviewer`/`actor` fields in approvals and the audit log are client-supplied
  strings, not derived from a real session.
