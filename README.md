<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/logo-lockup-light.png">
    <img src="docs/brand/logo-lockup.png" alt="FinSight AI" width="300">
  </picture>
</p>

# FinSight AI

Agentic loan underwriting for Indian NBFCs. 29 CrewAI agents read the documents of a loan file, check
them for fraud and RBI policy, score the risk and draft the credit memo — on a local model, with a
person making every decision.

- **Presentation:** [docs/reports/07-FinSight-Project-Presentation.pdf](docs/reports/07-FinSight-Project-Presentation.pdf) (also as `.pptx`)
- **Real-data evaluation of every agent:** [docs/reports/08-FinSight-Real-Data-Evaluation.pdf](docs/reports/08-FinSight-Real-Data-Evaluation.pdf)
- **How to run it:** [howtouse.md](howtouse.md) · **API and evaluation details:** [api/README.md](api/README.md) · **UI design system:** [DESIGN.md](DESIGN.md)

## What it does

| Crew | Agents | Job |
|---|---|---|
| Intake | Preprocessing, Extraction, Validation, Document Identity | Turn each upload into checked, structured fields |
| Privacy | PII Masking | Officers only ever see PAN / Aadhaar masked to the last four |
| Identity & fraud evidence | Face Verification, Liveness, Tampering Forensics, Authenticity, Tamper Explainer, Tamper Locator, Cross-Doc Verification, Dedupe, Fraud Fusion, Fraud Patterns, Fraud Triage | One advisory fraud tier per case, with the evidence behind it |
| Compliance | KB Ingestion, Rule Authoring, Regulatory Q&A, Policy Check | RBI and internal rules as data; answers with citations |
| Underwriting | Income Analysis, Risk Scoring, Credit Memo, Underwriting Supervisor, Offer Generation, Workflow Router | Risk grade, drafted memo, offer and next step |
| Operations | Notifier, Audit / Storage, Process-Mining Analyst | In-app notifications, the record, process figures |

Design rules that hold everywhere: a deterministic tool sets every score, the language model only
explains (and its numbers are checked against the evidence); nothing is auto-rejected; every model call
goes through one gateway and stays on the local machine by default; every step is traced.

## How well it works

Every agent was tested on its own, on real or public production-like data, against a target written
down before the run. Fixes were developed on a development split and the held-out test split was
scored once afterwards.

| Rating | Agents |
|---|---|
| Strong — target met | 25 |
| Acceptable — within 10% | 2 (PII Masking 98.4%, Tamper Locator 74.6%) |
| Weak — below | 2 (Authenticity 11%, card outline in phone photos 69%) |

The numbers, confidence intervals, datasets and limits are in the
[evaluation report](docs/reports/08-FinSight-Real-Data-Evaluation.pdf); the result files are in
`api/eval/results_real/`. The datasets themselves are public and are not part of this repository.

## Layout

```
src/        React + Vite front end (borrower, underwriting officer, head manager)
api/        FastAPI service: OCR and extraction, masking, tampering, faces, compliance, agents, tests
api/eval/   evaluation harnesses, targets and results
docs/       architecture page, SQL Server schema (docs/mssql), reports, brand assets
launcher/   Windows start / stop scripts
```

## Stack

React 18 · Vite 5 · FastAPI · CrewAI · Ollama (`qwen2.5vl:7b`) · OpenCV · RapidOCR · YuNet + SFace ·
MiniFASNet · XGBoost · PyTorch · SQL Server LocalDB · pytest

## Notes

- No real identity documents or applicant data are in this repository. Screenshots and fixtures use a
  generated applicant; uploads and the database stay on the machine that runs the app (`storage/`,
  `api/data/` are ignored).
- Trained components were fitted on public datasets (Find it again!, SIDTD, the L&T vehicle-loan set);
  check those datasets' terms before reusing the weights.
- Presentation photos: Dimitri Karastelev and Wesley Tingey, Unsplash.
