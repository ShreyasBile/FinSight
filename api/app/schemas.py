from typing import Any

from pydantic import BaseModel


class FieldResult(BaseModel):
    key: str
    label: str
    value: Any
    flag: str  # ok | warn | error


class PlainExplanation(BaseModel):
    summary: str                    # 1-2 plain-language sentences
    issues: list[str]               # each concern in simple, non-technical words


class TamperHeatmap(BaseModel):
    signal: str                     # which signal produced it (copy_move, cnn, ...)
    trust: str                      # "precise" | "supporting"
    caption: str                    # plain-language caption for the image
    image: str                      # base64 PNG data URI (downscaled)


class IssueAnnotation(BaseModel):
    n: int                          # matches the numbered box on the annotated image
    text: str                       # the concrete finding, cleaned for display
    located: bool                   # true = boxed on the page; false = in file data only


class LocateResult(BaseModel):
    annotations: list[IssueAnnotation]  # one row per concrete finding
    # document render with numbered boxes over each located finding; null when
    # none of the findings name a value that's printed on the page
    annotated_image: str | None = None
    located_count: int                  # how many findings got a box
    ocr_tokens: int                     # OCR word count (for diagnostics)


class TamperingResult(BaseModel):
    risk_tier: str                  # LOW | MEDIUM | HIGH
    risk_score: float               # 0..1 fused tamper score
    fast_tracked: bool              # true = cleared at the cheap stage-1 gate
    source_format: str              # camera_image | pdf_scan | pdf_digital | ...
    needs_review: bool              # tier is MEDIUM or HIGH
    explanations: list[str]         # human-readable reasons (technical)
    signals: list[dict[str, Any]]   # per-signal {name, available, score, findings}
    # plain-language restatement of the findings via Qwen (MEDIUM/HIGH only);
    # null when clean, disabled, or the model was unavailable
    plain_explanation: PlainExplanation | None = None
    heatmaps: list[TamperHeatmap] = []  # visual "where it's wrong" overlays (flagged docs)
    document_preview: str | None = None  # clean downscaled render (data URI) for hover-preview


class ExtractResponse(BaseModel):
    doc_type: str
    provider: str
    parsed: dict[str, Any]          # what the frontend renders/edits
    raw: str                        # raw model text (for the "show raw OCR" panel)
    fields: list[FieldResult]       # per-field validation
    confidence: float               # 0..1
    needs_review: bool              # force human review when true
    tampering: TamperingResult | None = None   # document-integrity analysis
    # the auto-oriented / deskewed / contrast-corrected image actually sent to
    # the OCR model (data URI). Null for PDFs and when preprocessing is off,
    # failed, or made no change — the frontend falls back to the raw upload.
    cleaned_preview: str | None = None
    # per-stage wall-clock, milliseconds: {preprocess_ms, extract_ms} — collected
    # by the frontend into the application audit trail (see /application/store).
    timings: dict[str, float] = {}
    # Document Identity agent: {ok, rule} — "is this the document we asked for?"
    # (server-side twin of the browser's wrong-document gate). Additive field.
    document_identity: dict[str, Any] | None = None


class IntegrityResult(BaseModel):
    """Deferred document-integrity bundle for the Review & Submit page: the
    tampering analysis plus the cleaned (auto-oriented/deskewed) render. Split
    out of /extract so the upload step only pays for VLM field extraction —
    the VLM reads rotated/raw images fine (verified on real sideways and
    upside-down ID cards), so preprocessing isn't needed until this step."""
    tampering: TamperingResult | None = None
    cleaned_preview: str | None = None
    timings: dict[str, float] = {}   # {tampering_ms}


class DocTypeInfo(BaseModel):
    id: str
    label: str
    required: bool


class MaskResult(BaseModel):
    masked_image: str            # data URI of the document with PII redacted
    masked_count: int            # how many sensitive regions were covered
    labels: list[str]            # which kinds (Aadhaar / PAN / Account)
    complete: bool | None = None  # PAN / Aadhaar cards: the card's own number was covered
    withheld: bool = False        # the number was not located - the image is blurred instead of masked
    timings: dict[str, float] = {}   # {mask_ms}


class FaceBox(BaseModel):
    bbox: list[int]              # x, y, w, h in the upright render
    score: float                 # detector confidence
    rotation_to_upright: int     # 0/90/180/270 — how the face sits in the document
    roll_degrees: float          # residual in-plane tilt (eye line)
    quality: str = "ok"          # "low" = found only via contrast enhancement / weak
    thumb: str | None = None     # small JPEG data URI of the face crop


class FaceDocResult(BaseModel):
    doc_type: str
    face_count: int
    faces: list[FaceBox]
    error: str | None = None     # unreadable file etc.; face_count 0 + no error = simply no face


class FacePair(BaseModel):
    a: str                       # doc_type of first document
    b: str                       # doc_type of second document
    similarity: float            # SFace cosine similarity (>= 0.363 = same person)
    verdict: str                 # match | uncertain | different
    note: str | None = None      # e.g. low-quality downgrade explanation
    low_quality: bool = False    # one of the two faces actually compared was barely detectable


class FaceVerifyResult(BaseModel):
    documents: list[FaceDocResult]
    pairs: list[FacePair]
    overall: str                 # match | mismatch | uncertain | insufficient
    summary: str
    timings: dict[str, float] = {}   # {face_ms}


# ── Application audit store (see storage.py) ────────────────────────────────
class StoredDocument(BaseModel):
    doc_type: str
    label: str = ""
    file_name: str = ""
    mime: str = ""
    uploaded_at: str = ""
    fields: dict[str, Any] = {}          # extracted fields (full/unmasked)
    original_b64: str | None = None      # raw uploaded file (data URI or bare base64)
    masked_b64: str | None = None        # PII-redacted render (data URI or bare base64)
    timings: dict[str, float] = {}       # {preprocess_ms, extract_ms, mask_ms, tampering_ms}
    tampering: dict[str, Any] | None = None


class ApplicationStoreRequest(BaseModel):
    app_id: str
    # wizard-minted case GUID (X-Case-Id); links agent traces + evidence to the
    # submitted application. Optional so older clients keep working.
    case_id: str | None = None
    applicant_name: str = ""
    loan_type: str = ""
    submitted_at: str = ""
    face_check: dict[str, Any] | None = None   # {overall, summary, time_ms}
    documents: list[StoredDocument] = []
    # what the borrower typed into the wizard; optional so older clients keep working
    loan: dict[str, Any] = {}        # amount, tenure_months, purpose, monthly_income, existing_emi, employment_type
    applicant: dict[str, Any] = {}   # name, mobile, email, dob, city, state, pincode
    form: dict[str, Any] = {}        # product-specific answers (kept as JSON on the application)
    # the wizard's Consent & Declaration step: {data_collection, credit_bureau_pull, communication,
    # information_correct, no_guarantee_understood, accepted_at, captured_by}
    consent: dict[str, Any] = {}


class ApplicationStoreResult(BaseModel):
    ok: bool
    rows: int = 0
    dir: str | None = None
    xlsx: str | None = None
    error: str | None = None
    # the database write (core.loan_applications): what every officer / head screen reads
    application_no: str | None = None
    sql_ok: bool | None = None
    sql_error: str | None = None
    masking_in_background: int = 0
