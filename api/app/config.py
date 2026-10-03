from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

API_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    # absolute path: the API reads api/.env no matter which directory uvicorn,
    # pytest or an eval script was started from
    model_config = SettingsConfigDict(env_file=str(API_DIR / ".env"), extra="ignore")

    # provider: mock | ollama | anthropic
    ocr_provider: str = "mock"

    # ollama (local VLM)
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5vl:7b"
    # Ollama's default runtime context (4096 tokens) is too small once a
    # multi-page document (e.g. Aadhaar front+back) turns into 2+ image
    # payloads plus the prompt — that alone can hit ~4300 tokens. Model
    # supports up to 128k; this just raises the per-request allocation.
    ollama_num_ctx: int = 8192

    # anthropic (server-side)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"
    anthropic_version: str = "2023-06-01"

    # image preprocessing (OpenCV + EasyOCR) — cleans phone-photo scans
    # before the model: auto-orient, deskew, warp, IQA. Adds latency + heavy deps.
    ocr_preprocess: bool = True
    preprocess_segment: bool = False  # rembg background removal (extra install)

    # document-integrity (tampering) analysis after OCR — forensic signals +
    # business-rule validation of the extracted fields. Adds CPU latency
    # (copy-move + CNN); set false to disable.
    tampering_enabled: bool = True

    # plain-language explanation of tampering findings via the local Qwen model.
    # TRANSLATION ONLY — detection stays 100% deterministic; the LLM just
    # restates the findings in simple language. Runs only for MEDIUM/HIGH docs.
    tampering_explain_enabled: bool = True
    tampering_explain_timeout: int = 90
    # let Qwen SEE the document image so it can say WHERE each already-detected
    # issue is (grounded by the findings, never free-form detection). Adds a
    # vision call's latency; set false for text-only explanations.
    tampering_explain_vision: bool = True

    # AI authenticity check (tampering/signals/authenticity.py). The ONE
    # non-deterministic DETECTION signal: a vision pass that judges whether a
    # document looks genuine or fabricated/AI-generated — the layer that catches
    # a from-scratch fake the forensic signals are blind to. Fenced in: runs only
    # for the fraud-prone doc types below, flags for human review only (never
    # auto-rejects), and is capped in fusion so it alone cannot reach HIGH.
    tampering_authenticity_enabled: bool = True
    tampering_authenticity_doc_types: str = "salary_slip,payslip,bank_statement,itr,form16,pan,aadhaar"
    tampering_authenticity_timeout: int = 60

    # ── SQL Server (the lending database) ────────────────────────────────
    # Everything the borrower and officer screens display is read from here;
    # the frontend no longer carries hardcoded data. Default targets the
    # LocalDB instance the docs/mssql scripts build.
    db_enabled: bool = True
    db_driver: str = "ODBC Driver 17 for SQL Server"
    db_server: str = r"(localdb)\MSSQLLocalDB"
    db_name: str = "FinSight"
    db_trusted_connection: bool = True   # Windows auth
    db_user: str = ""
    db_password: str = ""
    db_timeout: int = 15

    @property
    def db_connection_string(self) -> str:
        parts = [
            f"DRIVER={{{self.db_driver}}}",
            f"SERVER={self.db_server}",
            f"DATABASE={self.db_name}",
            f"Connection Timeout={self.db_timeout}",
        ]
        if self.db_trusted_connection:
            parts.append("Trusted_Connection=yes")
        else:
            parts.append(f"UID={self.db_user}")
            parts.append(f"PWD={self.db_password}")
        # Driver 18 defaults Encrypt=yes, which LocalDB has no certificate for.
        if "18" in self.db_driver:
            parts.append("Encrypt=no")
            parts.append("TrustServerCertificate=yes")
        return ";".join(parts)

    # ── Agents (app/agents) ──────────────────────────────────────────────
    # kill switch: false = every endpoint calls its module directly, exactly as
    # before the agent layer existed (no tracing, no case evidence)
    agents_enabled: bool = True
    # comma-separated agent ids to bypass individually (e.g. "credit_memo")
    agents_disabled: str = ""
    # process-mining environment tag for agent events (demo runs must never
    # pollute a production process model)
    agents_env: str = "prod"
    # write agent steps to SQL Server pm.event_log (falls back to JSON traces)
    agents_sql_sink: bool = True
    # tests/benchmarks set this so no document can ever reach a hosted model
    finsight_forbid_hosted: bool = False
    # key for hashing PAN/Aadhaar into dedupe keys (HMAC) — set in api/.env
    dedupe_hmac_key: str = "finsight-dev-dedupe-key"
    # runtime state (LLM settings, dedupe index, run index); tests point this
    # at a temp dir so they never read or write the real one
    finsight_data_dir: str = ""
    # The lender's standard loan terms, as declared in every Key Facts Statement (agents/tools/kfs.py).
    # These are the lender's own policy, not something an application proves: change them here (or in
    # api/.env) only when the actual product terms change.
    kfs_cooling_off_days: int = 3                 # borrower may exit without penalty (RBI: at least 1 day)
    kfs_disbursal_to_borrower_account: bool = True   # no LSP / third-party pool account in the fund flow
    kfs_penalty_as_charges: bool = True           # penal charges, never penal interest (RBI, 2024)
    kfs_processing_fee_pct: float = 1.0           # the only fee charged; itemised in the KFS
    # concurrent requests the local Ollama server can serve (OLLAMA_NUM_PARALLEL)
    ollama_parallel: int = 1
    # extra LLM calls allowed at once to a hosted provider
    anthropic_parallel: int = 4

    @property
    def data_dir(self) -> Path:
        return Path(self.finsight_data_dir) if self.finsight_data_dir else API_DIR / "data"

    # service
    allowed_origins: str = "http://localhost:5173,http://localhost:4173"
    max_file_mb: int = 15
    pdf_max_pages: int = 4

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


settings = Settings()
