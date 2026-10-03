<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/logo-lockup-light.png">
    <img src="docs/brand/logo-lockup.png" alt="FinSight AI" width="260">
  </picture>
</p>

# How to Run FinSight AI

This project has two parts that both need to be running at the same time:

1. **Frontend** — the website you see in the browser (React + Vite)
2. **Backend** — the OCR/document-extraction service (Python + FastAPI)

You'll use two terminal windows — one for each. **Or skip all of that:** on Windows, double-click the
**FinSight AI** icon on your Desktop (see [One-click start](#one-click-start-windows)).

---

## One-click start (Windows)

The `launcher/` folder starts the whole project with one click. Create the Desktop icon once:

```
powershell -ExecutionPolicy Bypass -File launcher\create-desktop-shortcut.ps1
```

Then double-click **FinSight AI** on the Desktop. It starts, in order, whatever is not already running:
the local model server (Ollama), the database (SQL Server LocalDB — checked with a real query, and
repaired automatically if an earlier copy is stuck), the API (`api\run.bat`, port 8000) and the web
app (`npm run dev`, port 5173). It waits until the whole chain answers — web app, API, database —
compiles the app, and only then opens FinSight in **its own app window** (Edge with a private
profile, so your everyday browser — its extensions, tabs or a stuck session — can't stop it loading). That takes about 15–25 seconds;
running it again while FinSight is up just opens the browser.

- The API and web app run in two minimised windows named **FinSight API** and **FinSight Web**.
  Run `launcher\stop-finsight.bat` (it also closes the app window), or close them, to stop the app. Ollama and the database are left running.
- Options (run `launcher\start-finsight.bat` from a terminal): `-NoBrowser`; `-DefaultBrowser` opens your normal browser instead; `-Warmup` also loads the
  vision model into GPU memory so the first upload is faster (skipped automatically when Windows is
  short of memory — the model takes about 8 GB).
- If it reports that Windows is low on memory, close a few programs (browsers with many tabs,
  WSL / Docker, games) and start it again.
- Moved the project folder? Run `create-desktop-shortcut.ps1` again.

---

## 1. Prerequisites (install these first)

| Tool | Version | Check with | Get it from |
|---|---|---|---|
| Node.js | 18.18+ (or 20+) | `node -v` | https://nodejs.org |
| Python | 3.10 – 3.12 | `python --version` | https://python.org |

Optional, only if you want real AI extraction instead of test data:

| Tool | Needed for | Get it from |
|---|---|---|
| Ollama | Local AI model (`ollama` provider) | https://ollama.com |

You don't need Ollama or any API key to get the app running — it works out of the box with fake/sample data (see [Step 4](#4-choose-an-ocr-provider)).

---

## 2. Run the frontend

Open a terminal in the project folder (`finsight`) and run:

```bash
npm install
npm run dev
```

Leave this running. It will print a local address, usually:

```
http://localhost:5173
```

Open that link in your browser — this is the app.

---

## 3. Run the backend (OCR service)

Open a **second** terminal in the same project folder and run:

```bash
npm run ocr
```

That's it — the first time you run this, it will automatically:
- create a Python virtual environment (an isolated space for this project's Python packages, kept in `api/.venv`)
- install all required Python packages
- create your local config file (`api/.env`) from the template

Every time after that, it just starts the server. Leave this terminal running too. You should see:

```
FinSight OCR running on http://localhost:8000  (docs: /docs)
```

You can open `http://localhost:8000/docs` in your browser to see and test the API directly.

> **Manual setup (if `npm run ocr` doesn't work on your machine):**
> ```bash
> cd api
> python -m venv .venv
> .venv\Scripts\activate          (macOS/Linux: source .venv/bin/activate)
> pip install -r requirements.txt
> copy .env.example .env          (macOS/Linux: cp .env.example .env)
> uvicorn app.main:app --reload --port 8000
> ```

---

## 4. Choose an OCR provider

Open `api/.env` and set `OCR_PROVIDER` to one of:

| Value | What it does | Extra setup needed |
|---|---|---|
| `mock` (default) | Returns realistic sample data — good for trying out the app, no AI involved | None |
| `ollama` | Runs a real AI model on your own computer | Install Ollama, then run: `ollama pull qwen2.5vl:7b` |
| `anthropic` | Uses Claude (cloud AI), no GPU needed | Add your key to `ANTHROPIC_API_KEY` in `api/.env` |

You can switch providers any time by editing `api/.env` and restarting the backend terminal (`Ctrl+C`, then `npm run ocr` again). No code changes needed.

Image cleanup (auto-straightening, brightness/contrast fixes) is on by default (`OCR_PREPROCESS=true`) and works with any provider. It downloads some extra packages (~2GB) the first time — set it to `false` in `api/.env` if you want a lighter, faster setup.

---

## 5. You're running

With both terminals open:

- Frontend: http://localhost:5173 ← open this one
- Backend: http://localhost:8000/docs ← optional, for testing the API directly

---

## 6. RBI Compliance features

No extra setup — the compliance engine (rules, a regulatory knowledge base,
and a Q&A chat) starts automatically with the backend and seeds its own data
the first time it runs. Where to find it, by role (pick a role on the app's
first screen):

| Role | Where |
|---|---|
| Underwriting Officer | **Policy Engine** — ask the chat a regulation question, or open **Customer 360° → RBI Compliance** to see an applicant checked against every active rule |
| Head Manager | Same as above, plus a dedicated **RBI Compliance** page — manage the knowledge base, write rules, and review/approve rules the system drafted from a regulatory document |

If a compliance panel shows "service unreachable," the backend is still
starting up (see the [Troubleshooting](#troubleshooting) note about `--reload`
below) — click **Retry**.

---

## 7. Taking a loan from application to disbursement

Every step is saved to the database and appears in each role's view, with a
notification to whoever acts next (the bell / **Notifications** page).

| Step | Who | Where |
|---|---|---|
| Apply: upload documents, fill in details, **Submit** | Borrower | **Apply for Loan**. Submitting takes a couple of seconds; masked copies of the documents are made in the background |
| **Verify documents** → **Start underwriting review** | Officer | **My Applications → Open Case**, *Officer Actions* card on the right |
| Run the fraud & policy assessment, **Generate Credit Memo** | Officer | *Fraud Report* tab and the header button. Approval stays locked until the fraud assessment has run |
| **Approve & send offer** (up to ₹5 lakh, fraud not HIGH) or **Refer to Head** | Officer | *Officer Actions* |
| **Approve & send offer**, **Send back to officer** or **Reject** | Head Manager | **Dashboard → Approval Queue → Decide**. Approving over a HIGH fraud flag needs a written reason |
| **Accept offer** / **Decline offer** | Borrower | **My Applications → View Offer** |
| **Sanction loan** → **Disburse loan** | Officer (the Head above ₹5 lakh) | *Officer Actions* |

**Request more documents** (officer or Head) asks the borrower for a file; the
borrower uploads it from the application page, then the officer verifies again.
**View** on a document opens it: officers always get the masked copy (PAN and
Aadhaar numbers hidden), the Head and the borrower the original.

---

## Troubleshooting

**"Port already in use"** — something else is already using 5173 or 8000. Close the other program, or stop whatever is running on that port, then try again.

**`npm run ocr` fails on the first run** — use the manual setup steps in [Step 3](#3-run-the-backend-ocr-service) instead, which show each step individually so you can see where it's failing.

**Backend starts but extraction fails** — check `OCR_PROVIDER` in `api/.env`. If it's set to `ollama`, make sure Ollama is installed and running, and that you've run `ollama pull qwen2.5vl:7b`. If it's set to `anthropic`, make sure `ANTHROPIC_API_KEY` is filled in.

**Changes to `api/.env` don't seem to apply** — stop the backend terminal (`Ctrl+C`) and run `npm run ocr` again; it only reads the file on startup.

**A compliance panel (Policy Engine, RBI Compliance, Customer 360's compliance tab) briefly says "service unreachable"** — the backend runs with auto-reload and re-imports everything (including the compliance module's own database setup) whenever a backend file changes, which takes a few seconds. Click **Retry**.

---

## Quick reference

```bash
# Terminal 1 — frontend
npm install
npm run dev

# Terminal 2 — backend
npm run ocr
```
