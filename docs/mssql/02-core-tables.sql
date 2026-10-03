/* ============================================================================
   FinSight AI  ·  SQL Server (SSMS) Schema
   FILE 2 of 5 — The 31 operational tables, translated to T-SQL
   ----------------------------------------------------------------------------
   Every column carried over from finsight-database-schema.sql is present.
   Columns added for process mining are marked  -- [PM]  with the reason.

   TYPE MAPPING USED THROUGHOUT
     UUID          -> UNIQUEIDENTIFIER (PK default NEWSEQUENTIALID for index
                      locality; NEWID() elsewhere)
     TIMESTAMPTZ   -> DATETIME2(3), UTC, DEFAULT SYSUTCDATETIME()
     JSONB         -> NVARCHAR(MAX) + CHECK (ISJSON(col) = 1)
     BOOLEAN       -> BIT
     NUMERIC(p,s)  -> DECIMAL(p,s)
     TEXT          -> NVARCHAR(MAX), or sized NVARCHAR when indexed
     INET          -> NVARCHAR(45)          (IPv6-safe)
     VECTOR(384)   -> VARBINARY(MAX)        (see note on compliance chunks)
     BIGSERIAL     -> BIGINT IDENTITY(1,1)
     partial index -> filtered index (SQL Server supports WHERE on indexes)
============================================================================ */

USE FinSight;
GO

-- SSMS sets these ON by default; sqlcmd does not. Filtered indexes, indexed
-- views and JSON checks all require QUOTED_IDENTIFIER ON, so set it explicitly
-- and the script behaves identically however it is run.
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

/* ###########################################################################
   SECTION A — OPERATIONS: users first, because everything references them
########################################################################### */

-- 19 · ops.users -------------------------------------------------------------
CREATE TABLE ops.users (
    user_id             UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_users_id DEFAULT NEWSEQUENTIALID(),
    email               NVARCHAR(255)    NOT NULL,
    password_hash       NVARCHAR(255)    NOT NULL,
    full_name           NVARCHAR(150)    NOT NULL,
    role_code           VARCHAR(20)      NOT NULL,
    branch_code         NVARCHAR(20)     NULL,
    is_active           BIT              NOT NULL DEFAULT 1,
    last_login_at       DATETIME2(3)     NULL,
    created_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),

    -- [PM] ORGANISATIONAL MINING. A social-network / handover-of-work analysis
    -- needs the org structure, not just a user id: who reports to whom, which
    -- team, and whether the "resource" is even a person.
    team_name           NVARCHAR(80)     NULL,
    manager_user_id     UNIQUEIDENTIFIER NULL,
    resource_type_code  VARCHAR(30)      NOT NULL DEFAULT 'human',
    is_bot              BIT              NOT NULL DEFAULT 0,
    -- [PM] Working-time calculation: a case sitting overnight is not a
    -- bottleneck. Links the resource to a shift so durations can be measured
    -- in WORKING hours rather than wall-clock hours.
    calendar_code       VARCHAR(40)      NULL,
    fte_cost_per_hour   DECIMAL(10,2)    NULL,   -- cost mining
    hired_at            DATETIME2(3)     NULL,   -- experience curves ("new joiners are slower")
    environment_code    VARCHAR(20)      NOT NULL DEFAULT 'prod',

    CONSTRAINT PK_users PRIMARY KEY CLUSTERED (user_id),
    CONSTRAINT FK_users_role      FOREIGN KEY (role_code)          REFERENCES ref.user_role(role_code),
    CONSTRAINT FK_users_restype   FOREIGN KEY (resource_type_code) REFERENCES ref.resource_type(resource_type_code),
    CONSTRAINT FK_users_env       FOREIGN KEY (environment_code)   REFERENCES ref.environment(environment_code),
    CONSTRAINT FK_users_manager   FOREIGN KEY (manager_user_id)    REFERENCES ops.users(user_id)
);
GO
CREATE UNIQUE INDEX UX_users_email ON ops.users(email);
CREATE INDEX IX_users_role_branch  ON ops.users(role_code, branch_code);
GO

-- 20 · ops.user_sessions -----------------------------------------------------
CREATE TABLE ops.user_sessions (
    session_id          UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_sessions_id DEFAULT NEWSEQUENTIALID(),
    user_id             UNIQUEIDENTIFIER NOT NULL,
    token               NVARCHAR(400)    NOT NULL,
    ip_address          NVARCHAR(45)     NULL,
    user_agent          NVARCHAR(500)    NULL,
    is_active           BIT              NOT NULL DEFAULT 1,
    logged_in_at        DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    logged_out_at       DATETIME2(3)     NULL,
    expires_at          DATETIME2(3)     NOT NULL,

    -- [PM] Ties every event back to one login session. This is what enables
    -- "task-level" mining: how many applications does an officer touch per
    -- session, and how often do they switch between cases (context switching
    -- is a measurable, and usually large, hidden cost).
    channel_code        VARCHAR(20)      NOT NULL DEFAULT 'web',
    device_type         NVARCHAR(40)     NULL,
    cases_touched       INT              NOT NULL DEFAULT 0,
    events_logged       INT              NOT NULL DEFAULT 0,

    CONSTRAINT PK_user_sessions PRIMARY KEY CLUSTERED (session_id),
    CONSTRAINT FK_sessions_user    FOREIGN KEY (user_id)      REFERENCES ops.users(user_id),
    CONSTRAINT FK_sessions_channel FOREIGN KEY (channel_code) REFERENCES ref.channel(channel_code)
);
GO
CREATE UNIQUE INDEX UX_sessions_token ON ops.user_sessions(token);
CREATE INDEX IX_sessions_user ON ops.user_sessions(user_id, logged_in_at DESC);
GO


/* ###########################################################################
   SECTION B — CORE LENDING  (tables 01–08)
########################################################################### */

-- 01 · core.customers --------------------------------------------------------
CREATE TABLE core.customers (
    customer_id         UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_customers_id DEFAULT NEWSEQUENTIALID(),
    first_name          NVARCHAR(100)    NOT NULL,
    last_name           NVARCHAR(100)    NOT NULL,
    date_of_birth       DATE             NOT NULL,
    mobile              NVARCHAR(15)     NOT NULL,
    email               NVARCHAR(255)    NULL,
    pan                 VARCHAR(10)      NULL,
    aadhaar             VARCHAR(12)      NULL,
    address_line1       NVARCHAR(255)    NULL,
    address_line2       NVARCHAR(255)    NULL,
    city                NVARCHAR(100)    NULL,
    state               NVARCHAR(100)    NULL,
    pincode             VARCHAR(6)       NULL,
    occupation          NVARCHAR(120)    NULL,
    annual_income       DECIMAL(14,2)    NULL,
    credit_score        SMALLINT         NULL,
    is_active           BIT              NOT NULL DEFAULT 1,
    created_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),

    -- [PM] Customer-level process context. Repeat borrowers follow measurably
    -- shorter paths (documents already on file); "first application" vs
    -- "nth application" is one of the strongest variant predictors in lending.
    first_application_at DATETIME2(3)    NULL,
    application_count    INT             NOT NULL DEFAULT 0,
    environment_code     VARCHAR(20)     NOT NULL DEFAULT 'prod',

    CONSTRAINT PK_customers PRIMARY KEY CLUSTERED (customer_id),
    CONSTRAINT CK_customers_credit_score CHECK (credit_score IS NULL OR credit_score BETWEEN 300 AND 900),
    CONSTRAINT FK_customers_env FOREIGN KEY (environment_code) REFERENCES ref.environment(environment_code)
);
GO
CREATE UNIQUE INDEX UX_customers_pan     ON core.customers(pan)     WHERE pan IS NOT NULL;
CREATE UNIQUE INDEX UX_customers_aadhaar ON core.customers(aadhaar) WHERE aadhaar IS NOT NULL;
CREATE INDEX IX_customers_mobile ON core.customers(mobile);
GO

-- 02 · core.loan_applications ------------------------------------------------
-- THE CASE TABLE. In process-mining terms every row here is one "case", so
-- this table carries the case-level attributes every analysis slices by.
CREATE TABLE core.loan_applications (
    application_id      UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_apps_id DEFAULT NEWSEQUENTIALID(),
    customer_id         UNIQUEIDENTIFIER NOT NULL,
    loan_product        VARCHAR(40)      NOT NULL,
    status_code         VARCHAR(40)      NOT NULL DEFAULT 'draft',
    loan_amount         DECIMAL(14,2)    NULL,
    tenure_months       SMALLINT         NULL,
    interest_rate       DECIMAL(5,3)     NULL,
    purpose             NVARCHAR(500)    NULL,
    form_data           NVARCHAR(MAX)    NULL,
    assigned_officer_id UNIQUEIDENTIFIER NULL,
    assigned_head_id    UNIQUEIDENTIFIER NULL,
    branch_code         NVARCHAR(20)     NULL,
    applied_at          DATETIME2(3)     NULL,
    submitted_at        DATETIME2(3)     NULL,
    completed_at        DATETIME2(3)     NULL,
    created_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),

    /* ---- [PM] CASE BOUNDARIES ------------------------------------------
       A case needs an unambiguous start and end. `applied_at`/`completed_at`
       are nullable and business-defined; these two are the mining anchors and
       are maintained by trigger, so throughput time is never ambiguous. */
    case_started_at         DATETIME2(3) NULL,
    case_ended_at           DATETIME2(3) NULL,
    case_duration_sec       BIGINT       NULL,   -- wall-clock
    case_business_sec       BIGINT       NULL,   -- working-calendar adjusted
    is_case_closed          BIT          NOT NULL DEFAULT 0,

    /* ---- [PM] PROCESS SHAPE -------------------------------------------
       Precomputed per-case aggregates. Recomputing these from the event log
       on every dashboard load is what makes naive process-mining setups
       unusably slow; they are refreshed by pm.usp_refresh_case_metrics. */
    variant_id              BIGINT       NULL,   -- FK added in file 03
    event_count             INT          NOT NULL DEFAULT 0,
    activity_count          INT          NOT NULL DEFAULT 0,  -- distinct activities
    touch_count             INT          NOT NULL DEFAULT 0,  -- human touches only
    rework_count            INT          NOT NULL DEFAULT 0,  -- repeated activities
    handover_count          INT          NOT NULL DEFAULT 0,  -- resource changes
    reopened_count          INT          NOT NULL DEFAULT 0,
    automation_ratio        DECIMAL(5,4) NULL,   -- automated events / all events
    is_straight_through     BIT          NOT NULL DEFAULT 0,  -- zero human touches
    deviation_count         INT          NOT NULL DEFAULT 0,  -- vs the normative model

    /* ---- [PM] SLA & QUEUEING ------------------------------------------ */
    sla_due_at              DATETIME2(3) NULL,
    sla_breached            BIT          NOT NULL DEFAULT 0,
    sla_breach_sec          BIGINT       NULL,
    priority                SMALLINT     NOT NULL DEFAULT 3,  -- 1 highest
    queue_entered_at        DATETIME2(3) NULL,
    current_owner_id        UNIQUEIDENTIFIER NULL,
    current_owner_since     DATETIME2(3) NULL,
    current_wait_reason     VARCHAR(40)  NULL,
    last_activity_at        DATETIME2(3) NULL,   -- staleness / "stuck case" alerts

    /* ---- [PM] CONTEXT / SEGMENTATION ---------------------------------- */
    channel_code            VARCHAR(20)  NOT NULL DEFAULT 'web',
    process_version         NVARCHAR(20) NULL,   -- which workflow version this case ran under
    environment_code        VARCHAR(20)  NOT NULL DEFAULT 'prod',
    cancellation_reason_code NVARCHAR(60) NULL,

    CONSTRAINT PK_loan_applications PRIMARY KEY CLUSTERED (application_id),
    CONSTRAINT FK_apps_customer  FOREIGN KEY (customer_id)         REFERENCES core.customers(customer_id),
    CONSTRAINT FK_apps_product   FOREIGN KEY (loan_product)        REFERENCES ref.loan_product_type(product_code),
    CONSTRAINT FK_apps_status    FOREIGN KEY (status_code)         REFERENCES ref.application_status(status_code),
    CONSTRAINT FK_apps_officer   FOREIGN KEY (assigned_officer_id) REFERENCES ops.users(user_id),
    CONSTRAINT FK_apps_head      FOREIGN KEY (assigned_head_id)    REFERENCES ops.users(user_id),
    CONSTRAINT FK_apps_owner     FOREIGN KEY (current_owner_id)    REFERENCES ops.users(user_id),
    CONSTRAINT FK_apps_wait      FOREIGN KEY (current_wait_reason) REFERENCES ref.wait_reason(wait_reason_code),
    CONSTRAINT FK_apps_channel   FOREIGN KEY (channel_code)        REFERENCES ref.channel(channel_code),
    CONSTRAINT FK_apps_env       FOREIGN KEY (environment_code)    REFERENCES ref.environment(environment_code),
    CONSTRAINT CK_apps_form_json CHECK (form_data IS NULL OR ISJSON(form_data) = 1)
);
GO
CREATE INDEX IX_apps_customer ON core.loan_applications(customer_id);
CREATE INDEX IX_apps_status   ON core.loan_applications(status_code) INCLUDE (loan_product, branch_code);
CREATE INDEX IX_apps_product  ON core.loan_applications(loan_product);
CREATE INDEX IX_apps_officer  ON core.loan_applications(assigned_officer_id) WHERE assigned_officer_id IS NOT NULL;
CREATE INDEX IX_apps_created  ON core.loan_applications(created_at DESC);
-- [PM] The two indexes mining actually hammers: open cases by age, and the
-- variant/product/duration slice behind every performance dashboard.
CREATE INDEX IX_apps_open_cases ON core.loan_applications(is_case_closed, last_activity_at)
    WHERE is_case_closed = 0;
CREATE INDEX IX_apps_variant    ON core.loan_applications(variant_id, loan_product)
    INCLUDE (case_duration_sec, sla_breached, rework_count);
GO

-- 03 · core.application_status_history ---------------------------------------
-- This table is already an implicit event log; the added columns are what make
-- it a USABLE one.
CREATE TABLE core.application_status_history (
    history_id          UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_hist_id DEFAULT NEWSEQUENTIALID(),
    application_id      UNIQUEIDENTIFIER NOT NULL,
    from_status         VARCHAR(40)      NULL,
    to_status           VARCHAR(40)      NOT NULL,
    changed_by          UNIQUEIDENTIFIER NULL,
    change_reason       NVARCHAR(1000)   NULL,
    created_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),

    -- [PM] ORDERING. Two status changes can land in the same millisecond
    -- (e.g. a cascade of automated transitions). Without an explicit sequence
    -- the directly-follows graph — the backbone of every process map — becomes
    -- non-deterministic and the discovered model changes between refreshes.
    event_seq                   INT          NULL,
    -- [PM] TIME-IN-STATE. Computing this on the fly with LAG() over millions
    -- of rows is the single most expensive query in a process-mining workload;
    -- it is written once, at insert, by trigger.
    duration_in_previous_sec    BIGINT       NULL,
    business_duration_sec       BIGINT       NULL,
    -- [PM] Who/what moved it, and was anyone actually working during the wait
    is_automated                BIT          NOT NULL DEFAULT 0,
    component_code              VARCHAR(40)  NULL,
    wait_reason_code            VARCHAR(40)  NULL,
    -- [PM] Structured reason beats free text: change_reason is unqueryable
    -- prose, reason_code is groupable ("28% of returns were reason=blurry_doc")
    reason_code                 NVARCHAR(60) NULL,
    is_rework                   BIT          NOT NULL DEFAULT 0,   -- returning to an earlier status
    is_backward_transition      BIT          NOT NULL DEFAULT 0,   -- to_status.sort_order < from_status.sort_order
    sla_due_at                  DATETIME2(3) NULL,
    sla_met                     BIT          NULL,
    correlation_id              UNIQUEIDENTIFIER NULL,

    CONSTRAINT PK_status_history PRIMARY KEY CLUSTERED (application_id, created_at, history_id),
    CONSTRAINT UQ_status_history_id UNIQUE (history_id),
    CONSTRAINT FK_hist_app    FOREIGN KEY (application_id)   REFERENCES core.loan_applications(application_id),
    CONSTRAINT FK_hist_from   FOREIGN KEY (from_status)      REFERENCES ref.application_status(status_code),
    CONSTRAINT FK_hist_to     FOREIGN KEY (to_status)        REFERENCES ref.application_status(status_code),
    CONSTRAINT FK_hist_user   FOREIGN KEY (changed_by)       REFERENCES ops.users(user_id),
    CONSTRAINT FK_hist_comp   FOREIGN KEY (component_code)   REFERENCES ref.automation_component(component_code),
    CONSTRAINT FK_hist_wait   FOREIGN KEY (wait_reason_code) REFERENCES ref.wait_reason(wait_reason_code)
);
GO
CREATE INDEX IX_status_history_ts ON core.application_status_history(created_at DESC);
GO

-- 04 · core.uploaded_documents -----------------------------------------------
CREATE TABLE core.uploaded_documents (
    document_id         UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_docs_id DEFAULT NEWSEQUENTIALID(),
    application_id      UNIQUEIDENTIFIER NOT NULL,
    document_type       VARCHAR(40)      NOT NULL,
    file_name           NVARCHAR(255)    NOT NULL,
    file_path           NVARCHAR(500)    NOT NULL,
    file_size_bytes     INT              NULL,
    mime_type           NVARCHAR(100)    NULL,
    checksum_sha256     CHAR(64)         NULL,
    ocr_status          VARCHAR(30)      NOT NULL DEFAULT 'pending',
    verification_status VARCHAR(30)      NOT NULL DEFAULT 'pending',
    extracted_data      NVARCHAR(MAX)    NULL,
    uploaded_by         UNIQUEIDENTIFIER NULL,
    uploaded_at         DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),

    /* ---- [PM] THE DOCUMENT IS ITS OWN PROCESS -------------------------
       Today the table has only uploaded_at and updated_at, so every question
       about the document pipeline ("is OCR the bottleneck, or is it the queue
       in front of OCR?") is unanswerable. These split the lifecycle. */
    queued_at               DATETIME2(3) NULL,
    processing_started_at   DATETIME2(3) NULL,
    processing_completed_at DATETIME2(3) NULL,
    verified_at             DATETIME2(3) NULL,
    queue_wait_ms           INT          NULL,   -- queued_at -> processing_started_at
    processing_ms           INT          NULL,   -- start -> complete

    /* ---- [PM] PER-STAGE TIMINGS ---------------------------------------
       api/app/main.py ALREADY measures every one of these (the `timings` dict
       returned by /extract, /document/integrity, /face/verify, /document/mask)
       and then throws them away. Persisting them turns the existing
       instrumentation into a mineable sub-process for free. */
    preprocess_ms           INT          NULL,
    extract_ms              INT          NULL,
    tampering_ms            INT          NULL,
    face_ms                 INT          NULL,
    mask_ms                 INT          NULL,
    storage_ms              INT          NULL,

    /* ---- [PM] REWORK LOOPS --------------------------------------------
       Document resubmission is the #1 rework loop in retail lending. Without
       a link from the new upload to the one it replaces, a resubmission looks
       like a brand-new document and the loop is invisible. */
    attempt_no                  SMALLINT NOT NULL DEFAULT 1,
    resubmission_of_document_id UNIQUEIDENTIFIER NULL,
    rejection_reason_code       NVARCHAR(60) NULL,
    rejected_by                 UNIQUEIDENTIFIER NULL,
    rejected_at                 DATETIME2(3) NULL,

    /* ---- [PM] OUTCOME SNAPSHOT (denormalised for slicing) -------------- */
    page_count              SMALLINT     NULL,
    ocr_confidence          DECIMAL(5,4) NULL,
    needs_review            BIT          NOT NULL DEFAULT 0,
    tampering_risk_tier     VARCHAR(10)  NULL,   -- LOW / MEDIUM / HIGH
    was_preprocessed        BIT          NOT NULL DEFAULT 0,
    provider_used           NVARCHAR(40) NULL,   -- mock / ollama / anthropic
    environment_code        VARCHAR(20)  NOT NULL DEFAULT 'prod',

    CONSTRAINT PK_uploaded_documents PRIMARY KEY CLUSTERED (document_id),
    CONSTRAINT FK_docs_app     FOREIGN KEY (application_id)      REFERENCES core.loan_applications(application_id),
    CONSTRAINT FK_docs_type    FOREIGN KEY (document_type)       REFERENCES ref.document_type(document_type_code),
    CONSTRAINT FK_docs_ocr_st  FOREIGN KEY (ocr_status)          REFERENCES ref.document_status(status_code),
    CONSTRAINT FK_docs_ver_st  FOREIGN KEY (verification_status) REFERENCES ref.document_status(status_code),
    CONSTRAINT FK_docs_user    FOREIGN KEY (uploaded_by)         REFERENCES ops.users(user_id),
    CONSTRAINT FK_docs_prev    FOREIGN KEY (resubmission_of_document_id) REFERENCES core.uploaded_documents(document_id),
    CONSTRAINT FK_docs_env     FOREIGN KEY (environment_code)    REFERENCES ref.environment(environment_code),
    CONSTRAINT CK_docs_json    CHECK (extracted_data IS NULL OR ISJSON(extracted_data) = 1),
    CONSTRAINT CK_docs_tier    CHECK (tampering_risk_tier IS NULL OR tampering_risk_tier IN ('LOW','MEDIUM','HIGH'))
);
GO
CREATE INDEX IX_docs_app  ON core.uploaded_documents(application_id) INCLUDE (document_type, ocr_status);
CREATE INDEX IX_docs_type ON core.uploaded_documents(document_type);
CREATE INDEX IX_docs_rework ON core.uploaded_documents(resubmission_of_document_id)
    WHERE resubmission_of_document_id IS NOT NULL;
GO

-- 05 · core.document_extraction_results --------------------------------------
CREATE TABLE core.document_extraction_results (
    extraction_id       UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_extract_id DEFAULT NEWSEQUENTIALID(),
    document_id         UNIQUEIDENTIFIER NOT NULL,
    extraction_source   NVARCHAR(40)     NOT NULL DEFAULT 'ocr',
    raw_text            NVARCHAR(MAX)    NULL,
    structured_data     NVARCHAR(MAX)    NULL,
    confidence_scores   NVARCHAR(MAX)    NULL,
    processing_time_ms  INT              NULL,
    model_version       NVARCHAR(80)     NULL,
    created_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),

    -- [PM] MODEL-VERSION MINING. The tampering CNN alone has ten versions
    -- (v001..v010, v009 live). Being able to ask "did the process get faster
    -- or slower after v009 went live?" requires the version on the event, and
    -- retries/fallbacks are themselves process steps worth counting.
    provider_name       NVARCHAR(40)     NULL,   -- mock / ollama / anthropic
    prompt_version      NVARCHAR(40)     NULL,
    retry_count         SMALLINT         NOT NULL DEFAULT 0,
    fell_back_to        NVARCHAR(40)     NULL,   -- provider fallback path
    preprocess_applied  BIT              NOT NULL DEFAULT 0,
    quality_gate_result NVARCHAR(30)     NULL,   -- kept_cleaned / reverted_to_original
    started_at          DATETIME2(3)     NULL,
    completed_at        DATETIME2(3)     NULL,

    CONSTRAINT PK_extraction PRIMARY KEY CLUSTERED (extraction_id),
    CONSTRAINT FK_extract_doc FOREIGN KEY (document_id) REFERENCES core.uploaded_documents(document_id),
    CONSTRAINT CK_extract_json1 CHECK (structured_data   IS NULL OR ISJSON(structured_data) = 1),
    CONSTRAINT CK_extract_json2 CHECK (confidence_scores IS NULL OR ISJSON(confidence_scores) = 1)
);
GO
CREATE INDEX IX_extraction_doc ON core.document_extraction_results(document_id);
CREATE INDEX IX_extraction_model ON core.document_extraction_results(model_version, created_at);
GO

-- 06 · core.field_verification_log -------------------------------------------
CREATE TABLE core.field_verification_log (
    verification_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_fieldver_id DEFAULT NEWSEQUENTIALID(),
    application_id      UNIQUEIDENTIFIER NOT NULL,
    field_name          NVARCHAR(100)    NOT NULL,
    field_value         NVARCHAR(500)    NULL,
    source_document_id  UNIQUEIDENTIFIER NULL,
    verification_method NVARCHAR(60)     NOT NULL,
    is_valid            BIT              NOT NULL,
    confidence          DECIMAL(4,3)     NULL,
    discrepancy_detail  NVARCHAR(1000)   NULL,
    verified_by         UNIQUEIDENTIFIER NULL,
    verified_at         DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),

    -- [PM] HUMAN-OVERRIDES-MACHINE is the highest-value metric in an
    -- AI-assisted process. It needs the machine's answer, the human's answer,
    -- and the moment the human intervened — three columns that don't exist yet.
    is_automated        BIT              NOT NULL DEFAULT 1,
    component_code      VARCHAR(40)      NULL,
    started_at          DATETIME2(3)     NULL,
    duration_ms         INT              NULL,
    machine_value       NVARCHAR(500)    NULL,
    was_overridden      BIT              NOT NULL DEFAULT 0,
    overridden_by       UNIQUEIDENTIFIER NULL,
    overridden_at       DATETIME2(3)     NULL,
    override_reason     NVARCHAR(500)    NULL,
    attempt_no          SMALLINT         NOT NULL DEFAULT 1,

    CONSTRAINT PK_field_verification PRIMARY KEY CLUSTERED (verification_id),
    CONSTRAINT CK_fieldver_conf CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    CONSTRAINT FK_fieldver_app  FOREIGN KEY (application_id)     REFERENCES core.loan_applications(application_id),
    CONSTRAINT FK_fieldver_doc  FOREIGN KEY (source_document_id) REFERENCES core.uploaded_documents(document_id),
    CONSTRAINT FK_fieldver_user FOREIGN KEY (verified_by)        REFERENCES ops.users(user_id),
    CONSTRAINT FK_fieldver_ovr  FOREIGN KEY (overridden_by)      REFERENCES ops.users(user_id),
    CONSTRAINT FK_fieldver_comp FOREIGN KEY (component_code)     REFERENCES ref.automation_component(component_code)
);
GO
CREATE INDEX IX_field_verification_app ON core.field_verification_log(application_id);
CREATE INDEX IX_field_verification_ovr ON core.field_verification_log(was_overridden, component_code)
    WHERE was_overridden = 1;
GO

-- 07 · core.loan_offers ------------------------------------------------------
CREATE TABLE core.loan_offers (
    offer_id            UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_offers_id DEFAULT NEWSEQUENTIALID(),
    application_id      UNIQUEIDENTIFIER NOT NULL,
    offered_amount      DECIMAL(14,2)    NOT NULL,
    interest_rate       DECIMAL(5,3)     NOT NULL,
    tenure_months       SMALLINT         NOT NULL,
    processing_fee      DECIMAL(10,2)    NULL,
    emi_amount          DECIMAL(10,2)    NULL,
    offer_valid_until   DATETIME2(3)     NOT NULL,
    terms_conditions    NVARCHAR(MAX)    NULL,
    status              VARCHAR(20)      NOT NULL DEFAULT 'pending',
    accepted_at         DATETIME2(3)     NULL,
    created_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),

    -- [PM] The offer stage hides two very different delays: how long the BANK
    -- took to produce the offer, and how long the CUSTOMER sat on it. Only the
    -- first is a bottleneck you can fix; separating them needs these columns.
    generated_by_type   VARCHAR(30)      NOT NULL DEFAULT 'system',
    generated_by        UNIQUEIDENTIFIER NULL,
    sent_at             DATETIME2(3)     NULL,
    first_viewed_at     DATETIME2(3)     NULL,
    decided_at          DATETIME2(3)     NULL,
    customer_decision_sec BIGINT         NULL,   -- sent_at -> decided_at
    revision_no         SMALLINT         NOT NULL DEFAULT 1,
    revision_of_offer_id UNIQUEIDENTIFIER NULL,  -- renegotiation loop
    revision_reason     NVARCHAR(200)    NULL,

    CONSTRAINT PK_loan_offers PRIMARY KEY CLUSTERED (offer_id),
    CONSTRAINT CK_offers_status CHECK (status IN ('pending','accepted','rejected','expired')),
    CONSTRAINT FK_offers_app  FOREIGN KEY (application_id)       REFERENCES core.loan_applications(application_id),
    CONSTRAINT FK_offers_user FOREIGN KEY (generated_by)         REFERENCES ops.users(user_id),
    CONSTRAINT FK_offers_prev FOREIGN KEY (revision_of_offer_id) REFERENCES core.loan_offers(offer_id),
    CONSTRAINT FK_offers_rtype FOREIGN KEY (generated_by_type)   REFERENCES ref.resource_type(resource_type_code)
);
GO
CREATE INDEX IX_offers_app ON core.loan_offers(application_id);
GO

-- 08 · core.disbursements ----------------------------------------------------
CREATE TABLE core.disbursements (
    disbursement_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_disb_id DEFAULT NEWSEQUENTIALID(),
    offer_id            UNIQUEIDENTIFIER NOT NULL,
    application_id      UNIQUEIDENTIFIER NOT NULL,
    disbursed_amount    DECIMAL(14,2)    NOT NULL,
    disbursed_at        DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    disbursement_method NVARCHAR(60)     NULL,
    reference_number    NVARCHAR(80)     NULL,
    settlement_account  NVARCHAR(60)     NULL,
    created_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),

    -- [PM] Disbursement is the case's terminal milestone; initiated→settled is
    -- a measurable external-dependency delay (NEFT/IMPS/bank rails), and
    -- failures here are a real, currently-invisible rework loop.
    initiated_at        DATETIME2(3)     NULL,
    settled_at          DATETIME2(3)     NULL,
    settlement_lag_sec  BIGINT           NULL,
    attempt_no          SMALLINT         NOT NULL DEFAULT 1,
    failure_reason_code NVARCHAR(60)     NULL,

    CONSTRAINT PK_disbursements PRIMARY KEY CLUSTERED (disbursement_id),
    CONSTRAINT FK_disb_offer FOREIGN KEY (offer_id)       REFERENCES core.loan_offers(offer_id),
    CONSTRAINT FK_disb_app   FOREIGN KEY (application_id) REFERENCES core.loan_applications(application_id)
);
GO
CREATE INDEX IX_disbursements_app   ON core.disbursements(application_id);
CREATE INDEX IX_disbursements_offer ON core.disbursements(offer_id);
GO


/* ###########################################################################
   SECTION C — FRAUD  (tables 09–13)
########################################################################### */

-- 09 · fraud.fraud_cases -----------------------------------------------------
-- [PM] A fraud case is a SECOND case notion (its own process: open →
-- investigating → confirmed/dismissed). The added timestamps let it be mined
-- as a process in its own right, not just as an attribute of the loan.
CREATE TABLE fraud.fraud_cases (
    case_id             UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_fcase_id DEFAULT NEWSEQUENTIALID(),
    application_id      UNIQUEIDENTIFIER NULL,
    customer_id         UNIQUEIDENTIFIER NULL,
    severity            VARCHAR(20)      NOT NULL DEFAULT 'low',
    status              VARCHAR(20)      NOT NULL DEFAULT 'open',
    fraud_category      NVARCHAR(80)     NULL,
    description         NVARCHAR(MAX)    NOT NULL,
    detection_method    NVARCHAR(80)     NULL,
    ai_confidence       DECIMAL(4,3)     NULL,
    flagged_by          UNIQUEIDENTIFIER NULL,
    assigned_to         UNIQUEIDENTIFIER NULL,
    resolved_at         DATETIME2(3)     NULL,
    created_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),

    -- [PM] The two numbers a fraud team is actually judged on — MTTD and MTTR
    -- — cannot be derived from created_at/resolved_at alone, because
    -- created_at is when the SYSTEM noticed, not when the risk first appeared.
    first_signal_at         DATETIME2(3) NULL,
    detected_at             DATETIME2(3) NULL,
    assigned_at             DATETIME2(3) NULL,
    triage_started_at       DATETIME2(3) NULL,
    triage_completed_at     DATETIME2(3) NULL,
    time_to_detect_sec      BIGINT       NULL,
    time_to_assign_sec      BIGINT       NULL,
    time_to_resolve_sec     BIGINT       NULL,
    reopened_count          SMALLINT     NOT NULL DEFAULT 0,
    escalation_count        SMALLINT     NOT NULL DEFAULT 0,
    sla_due_at              DATETIME2(3) NULL,
    sla_breached            BIT          NOT NULL DEFAULT 0,
    is_false_positive       BIT          NULL,   -- feeds model retraining
    environment_code        VARCHAR(20)  NOT NULL DEFAULT 'prod',

    CONSTRAINT PK_fraud_cases PRIMARY KEY CLUSTERED (case_id),
    CONSTRAINT CK_fraud_conf   CHECK (ai_confidence IS NULL OR ai_confidence BETWEEN 0 AND 1),
    CONSTRAINT CK_fraud_target CHECK (application_id IS NOT NULL OR customer_id IS NOT NULL),
    CONSTRAINT FK_fcase_app  FOREIGN KEY (application_id) REFERENCES core.loan_applications(application_id),
    CONSTRAINT FK_fcase_cust FOREIGN KEY (customer_id)    REFERENCES core.customers(customer_id),
    CONSTRAINT FK_fcase_sev  FOREIGN KEY (severity)       REFERENCES ref.fraud_severity(severity_code),
    CONSTRAINT FK_fcase_stat FOREIGN KEY (status)         REFERENCES ref.fraud_status(status_code),
    CONSTRAINT FK_fcase_flag FOREIGN KEY (flagged_by)     REFERENCES ops.users(user_id),
    CONSTRAINT FK_fcase_asgn FOREIGN KEY (assigned_to)    REFERENCES ops.users(user_id),
    CONSTRAINT FK_fcase_env  FOREIGN KEY (environment_code) REFERENCES ref.environment(environment_code)
);
GO
CREATE INDEX IX_fraud_cases_app    ON fraud.fraud_cases(application_id);
CREATE INDEX IX_fraud_cases_cust   ON fraud.fraud_cases(customer_id);
CREATE INDEX IX_fraud_cases_status ON fraud.fraud_cases(status, severity);
GO

-- 10 · fraud.fraud_signals ---------------------------------------------------
CREATE TABLE fraud.fraud_signals (
    signal_id           UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_fsig_id DEFAULT NEWSEQUENTIALID(),
    case_id             UNIQUEIDENTIFIER NOT NULL,
    signal_name         NVARCHAR(80)     NOT NULL,
    signal_value        DECIMAL(10,4)    NULL,
    signal_threshold    DECIMAL(10,4)    NULL,
    signal_score        DECIMAL(4,3)     NULL,
    signal_category     NVARCHAR(60)     NULL,
    metadata            NVARCHAR(MAX)    NULL,
    detected_at         DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),

    -- [PM] Maps a signal back to the module that produced it and how long that
    -- module took, so the fraud cascade (10 signals, 2 stages) is mineable as
    -- a sub-process: which signal fires first, which one is slowest, which one
    -- never contributes to a confirmed case.
    component_code      VARCHAR(40)      NULL,
    document_id         UNIQUEIDENTIFIER NULL,
    stage_no            SMALLINT         NULL,   -- tampering cascade stage 1 / 2
    compute_ms          INT              NULL,
    model_version       NVARCHAR(40)     NULL,
    contributed_to_verdict BIT           NOT NULL DEFAULT 0,

    CONSTRAINT PK_fraud_signals PRIMARY KEY CLUSTERED (signal_id),
    CONSTRAINT CK_fsig_score CHECK (signal_score IS NULL OR signal_score BETWEEN 0 AND 1),
    CONSTRAINT CK_fsig_json  CHECK (metadata IS NULL OR ISJSON(metadata) = 1),
    CONSTRAINT FK_fsig_case  FOREIGN KEY (case_id)        REFERENCES fraud.fraud_cases(case_id),
    CONSTRAINT FK_fsig_doc   FOREIGN KEY (document_id)    REFERENCES core.uploaded_documents(document_id),
    CONSTRAINT FK_fsig_comp  FOREIGN KEY (component_code) REFERENCES ref.automation_component(component_code)
);
GO
CREATE INDEX IX_fraud_signals_case ON fraud.fraud_signals(case_id);
GO

-- 11 · fraud.fraud_rings -----------------------------------------------------
CREATE TABLE fraud.fraud_rings (
    ring_id             UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_ring_id DEFAULT NEWSEQUENTIALID(),
    ring_name           NVARCHAR(150)    NOT NULL,
    description         NVARCHAR(MAX)    NULL,
    severity            VARCHAR(20)      NOT NULL DEFAULT 'medium',
    status              VARCHAR(20)      NOT NULL DEFAULT 'open',
    detected_at         DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    -- [PM] Ring cases share a process signature (same officer, same branch,
    -- same variant, submitted minutes apart). member_count + first/last seen
    -- make that pattern queryable.
    member_count        INT              NOT NULL DEFAULT 0,
    first_case_at       DATETIME2(3)     NULL,
    last_case_at        DATETIME2(3)     NULL,

    CONSTRAINT PK_fraud_rings PRIMARY KEY CLUSTERED (ring_id),
    CONSTRAINT FK_ring_sev  FOREIGN KEY (severity) REFERENCES ref.fraud_severity(severity_code),
    CONSTRAINT FK_ring_stat FOREIGN KEY (status)   REFERENCES ref.fraud_status(status_code)
);
GO

-- 12 · fraud.fraud_ring_members ----------------------------------------------
CREATE TABLE fraud.fraud_ring_members (
    ring_id             UNIQUEIDENTIFIER NOT NULL,
    customer_id         UNIQUEIDENTIFIER NOT NULL,
    linked_at           DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    link_evidence       NVARCHAR(MAX)    NULL,
    link_type           NVARCHAR(60)     NULL,   -- [PM] shared_device / shared_face / shared_ifsc
    link_confidence     DECIMAL(4,3)     NULL,   -- [PM]

    CONSTRAINT PK_fraud_ring_members PRIMARY KEY CLUSTERED (ring_id, customer_id),
    CONSTRAINT FK_frm_ring FOREIGN KEY (ring_id)     REFERENCES fraud.fraud_rings(ring_id),
    CONSTRAINT FK_frm_cust FOREIGN KEY (customer_id) REFERENCES core.customers(customer_id)
);
GO

-- 13 · fraud.fraud_escalations -----------------------------------------------
CREATE TABLE fraud.fraud_escalations (
    escalation_id       UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_fesc_id DEFAULT NEWSEQUENTIALID(),
    case_id             UNIQUEIDENTIFIER NOT NULL,
    escalated_by        UNIQUEIDENTIFIER NOT NULL,
    escalated_to        UNIQUEIDENTIFIER NOT NULL,
    reason              NVARCHAR(MAX)    NOT NULL,
    previous_severity   VARCHAR(20)      NULL,
    new_severity        VARCHAR(20)      NOT NULL,
    resolved_at         DATETIME2(3)     NULL,
    created_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),

    -- [PM] An escalation IS a handover. Level + acknowledgement time turn the
    -- escalation chain into a measurable path ("critical cases bounce through
    -- 2.4 people before someone acts").
    escalation_level    SMALLINT         NOT NULL DEFAULT 1,
    acknowledged_at     DATETIME2(3)     NULL,
    ack_latency_sec     BIGINT           NULL,
    reason_code         NVARCHAR(60)     NULL,

    CONSTRAINT PK_fraud_escalations PRIMARY KEY CLUSTERED (escalation_id),
    CONSTRAINT FK_fesc_case FOREIGN KEY (case_id)           REFERENCES fraud.fraud_cases(case_id),
    CONSTRAINT FK_fesc_by   FOREIGN KEY (escalated_by)      REFERENCES ops.users(user_id),
    CONSTRAINT FK_fesc_to   FOREIGN KEY (escalated_to)      REFERENCES ops.users(user_id),
    CONSTRAINT FK_fesc_prev FOREIGN KEY (previous_severity) REFERENCES ref.fraud_severity(severity_code),
    CONSTRAINT FK_fesc_new  FOREIGN KEY (new_severity)      REFERENCES ref.fraud_severity(severity_code)
);
GO
CREATE INDEX IX_fraud_esc_case ON fraud.fraud_escalations(case_id);
GO


/* ###########################################################################
   SECTION D — COMPLIANCE / KNOWLEDGE  (tables 14–18)
########################################################################### */

-- 14 · comp.compliance_documents ---------------------------------------------
CREATE TABLE comp.compliance_documents (
    document_id         UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_cdoc_id DEFAULT NEWSEQUENTIALID(),
    title               NVARCHAR(300)    NOT NULL,
    category            NVARCHAR(60)     NOT NULL,
    source_type         NVARCHAR(60)     NOT NULL,
    source_url          NVARCHAR(500)    NULL,
    effective_date      DATE             NULL,
    text_content        NVARCHAR(MAX)    NOT NULL,
    chunk_count         INT              NOT NULL DEFAULT 0,
    status              VARCHAR(20)      NOT NULL DEFAULT 'active',
    uploaded_by         UNIQUEIDENTIFIER NULL,
    created_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    -- [PM] Regulatory ingestion is its own process: ingest → chunk → extract
    -- → review → approve. These make its lead time measurable.
    ingested_at         DATETIME2(3)     NULL,
    indexed_at          DATETIME2(3)     NULL,
    indexing_ms         INT              NULL,

    CONSTRAINT PK_compliance_documents PRIMARY KEY CLUSTERED (document_id),
    CONSTRAINT CK_cdoc_status CHECK (status IN ('active','archived','draft')),
    CONSTRAINT FK_cdoc_user   FOREIGN KEY (uploaded_by) REFERENCES ops.users(user_id)
);
GO
CREATE INDEX IX_compliance_docs_category ON comp.compliance_documents(category);
GO

-- 15 · comp.compliance_document_chunks ---------------------------------------
-- NOTE ON `embedding`: PostgreSQL used pgvector's VECTOR(384). SQL Server 2025
-- has a native VECTOR type; on 2017–2022 store the raw float32 array as
-- VARBINARY(MAX) (384 * 4 = 1536 bytes) and do similarity in the app layer.
-- The live FinSight compliance module uses BM25, not embeddings, so this
-- column is forward-looking either way.
CREATE TABLE comp.compliance_document_chunks (
    chunk_id            UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_chunk_id DEFAULT NEWSEQUENTIALID(),
    document_id         UNIQUEIDENTIFIER NOT NULL,
    chunk_index         INT              NOT NULL,
    chunk_text          NVARCHAR(MAX)    NOT NULL,
    embedding           VARBINARY(MAX)   NULL,   -- SQL Server 2025: VECTOR(384)
    created_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    -- [PM] Retrieval telemetry: which chunks actually get retrieved and cited
    -- tells you whether the knowledge base is working or just large.
    retrieval_count     INT              NOT NULL DEFAULT 0,
    last_retrieved_at   DATETIME2(3)     NULL,

    CONSTRAINT PK_compliance_chunks PRIMARY KEY CLUSTERED (chunk_id),
    CONSTRAINT UQ_chunk_doc_index UNIQUE (document_id, chunk_index),
    CONSTRAINT FK_chunk_doc FOREIGN KEY (document_id)
        REFERENCES comp.compliance_documents(document_id) ON DELETE CASCADE
);
GO

-- 16 · comp.compliance_rule_candidates (maker-checker draft queue) ------------
-- [PM] THIRD CASE NOTION: rule authoring (extract → review → approve/reject).
-- This is a genuine human workflow with a queue and a reviewer, and it is one
-- of the easiest, highest-value processes to mine in this system.
CREATE TABLE comp.compliance_rule_candidates (
    candidate_id        UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_cand_id DEFAULT NEWSEQUENTIALID(),
    document_id         UNIQUEIDENTIFIER NOT NULL,
    proposed_rule_name  NVARCHAR(200)    NOT NULL,
    rule_type           VARCHAR(30)      NOT NULL,
    rule_category       VARCHAR(30)      NOT NULL,
    condition_expr      NVARCHAR(MAX)    NOT NULL,   -- 'condition' renamed: clearer in T-SQL
    regulation          NVARCHAR(300)    NULL,
    obligation_text     NVARCHAR(MAX)    NULL,
    confidence          DECIMAL(4,3)     NULL,
    needs_field_mapping BIT              NOT NULL DEFAULT 0,
    status              VARCHAR(20)      NOT NULL DEFAULT 'pending',
    reviewed_by         UNIQUEIDENTIFIER NULL,
    reviewed_at         DATETIME2(3)     NULL,
    rejection_note      NVARCHAR(MAX)    NULL,
    created_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),

    -- [PM] Queue time is the whole story here: drafts sit unreviewed for days.
    extracted_at        DATETIME2(3)     NULL,
    queued_at           DATETIME2(3)     NULL,
    review_started_at   DATETIME2(3)     NULL,
    review_duration_sec BIGINT           NULL,
    queue_wait_sec      BIGINT           NULL,
    extraction_method   NVARCHAR(40)     NULL,   -- llm / heuristic fallback
    extraction_ms       INT              NULL,
    revision_no         SMALLINT         NOT NULL DEFAULT 1,

    CONSTRAINT PK_rule_candidates PRIMARY KEY CLUSTERED (candidate_id),
    CONSTRAINT CK_cand_status CHECK (status IN ('pending','approved','rejected')),
    CONSTRAINT CK_cand_conf   CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    CONSTRAINT FK_cand_doc    FOREIGN KEY (document_id)   REFERENCES comp.compliance_documents(document_id),
    CONSTRAINT FK_cand_rtype  FOREIGN KEY (rule_type)     REFERENCES ref.rule_type(rule_type_code),
    CONSTRAINT FK_cand_rcat   FOREIGN KEY (rule_category) REFERENCES ref.rule_category(rule_category_code),
    CONSTRAINT FK_cand_user   FOREIGN KEY (reviewed_by)   REFERENCES ops.users(user_id)
);
GO
CREATE INDEX IX_candidates_doc    ON comp.compliance_rule_candidates(document_id);
CREATE INDEX IX_candidates_status ON comp.compliance_rule_candidates(status, created_at);
GO

-- 17 · comp.compliance_rules -------------------------------------------------
CREATE TABLE comp.compliance_rules (
    rule_id             UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_rule_id DEFAULT NEWSEQUENTIALID(),
    candidate_id        UNIQUEIDENTIFIER NULL,
    rule_name           NVARCHAR(200)    NOT NULL,
    rule_type           VARCHAR(30)      NOT NULL,
    rule_category       VARCHAR(30)      NOT NULL,
    condition_expr      NVARCHAR(MAX)    NOT NULL,
    regulation          NVARCHAR(300)    NULL,
    is_active           BIT              NOT NULL DEFAULT 1,
    version             INT              NOT NULL DEFAULT 1,
    approved_by         UNIQUEIDENTIFIER NOT NULL,
    approved_at         DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    superseded_by       UNIQUEIDENTIFIER NULL,
    created_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    -- [PM] Rule-change impact analysis: "the approval rate dropped 8% the week
    -- KYC_003 went live" is only answerable if effective dates and firing
    -- counts live on the rule.
    effective_from      DATETIME2(3)     NULL,
    effective_until     DATETIME2(3)     NULL,
    evaluation_count    BIGINT           NOT NULL DEFAULT 0,
    failure_count       BIGINT           NOT NULL DEFAULT 0,
    avg_eval_ms         DECIMAL(10,2)    NULL,

    CONSTRAINT PK_compliance_rules PRIMARY KEY CLUSTERED (rule_id),
    CONSTRAINT FK_rule_cand  FOREIGN KEY (candidate_id)  REFERENCES comp.compliance_rule_candidates(candidate_id),
    CONSTRAINT FK_rule_rtype FOREIGN KEY (rule_type)     REFERENCES ref.rule_type(rule_type_code),
    CONSTRAINT FK_rule_rcat  FOREIGN KEY (rule_category) REFERENCES ref.rule_category(rule_category_code),
    CONSTRAINT FK_rule_user  FOREIGN KEY (approved_by)   REFERENCES ops.users(user_id),
    CONSTRAINT FK_rule_super FOREIGN KEY (superseded_by) REFERENCES comp.compliance_rules(rule_id)
);
GO
CREATE UNIQUE INDEX UX_rules_name_active ON comp.compliance_rules(rule_name) WHERE is_active = 1;
GO

-- 18 · comp.compliance_check_results -----------------------------------------
CREATE TABLE comp.compliance_check_results (
    check_id            UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_check_id DEFAULT NEWSEQUENTIALID(),
    application_id      UNIQUEIDENTIFIER NOT NULL,
    rule_id             UNIQUEIDENTIFIER NOT NULL,
    status              VARCHAR(30)      NOT NULL,
    evidence            NVARCHAR(MAX)    NULL,
    remediation         NVARCHAR(MAX)    NULL,
    checked_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),

    -- [PM] A compliance run is an ACTIVITY with a duration and an outcome that
    -- can be overridden. rule_version pins which version of the rule produced
    -- the result — without it, re-running history gives different answers.
    check_started_at    DATETIME2(3)     NULL,
    duration_ms         INT              NULL,
    rule_version        INT              NULL,
    run_id              UNIQUEIDENTIFIER NULL,   -- groups all rules in one evaluation pass
    is_automated        BIT              NOT NULL DEFAULT 1,
    was_overridden      BIT              NOT NULL DEFAULT 0,
    overridden_by       UNIQUEIDENTIFIER NULL,
    override_reason     NVARCHAR(500)    NULL,
    triggered_rework    BIT              NOT NULL DEFAULT 0,

    CONSTRAINT PK_compliance_check_results PRIMARY KEY CLUSTERED (check_id),
    CONSTRAINT FK_ccheck_app  FOREIGN KEY (application_id) REFERENCES core.loan_applications(application_id),
    CONSTRAINT FK_ccheck_rule FOREIGN KEY (rule_id)        REFERENCES comp.compliance_rules(rule_id),
    CONSTRAINT FK_ccheck_stat FOREIGN KEY (status)         REFERENCES ref.compliance_check_status(status_code),
    CONSTRAINT FK_ccheck_ovr  FOREIGN KEY (overridden_by)  REFERENCES ops.users(user_id)
);
GO
CREATE INDEX IX_compliance_check_app  ON comp.compliance_check_results(application_id, checked_at DESC);
CREATE INDEX IX_compliance_check_rule ON comp.compliance_check_results(rule_id, status);
GO


/* ###########################################################################
   SECTION E — OPERATIONS / CONFIG  (tables 21–24)
########################################################################### */

-- 21 · ops.notifications -----------------------------------------------------
CREATE TABLE ops.notifications (
    notification_id     UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_notif_id DEFAULT NEWSEQUENTIALID(),
    user_id             UNIQUEIDENTIFIER NOT NULL,
    title               NVARCHAR(200)    NOT NULL,
    body                NVARCHAR(MAX)    NULL,
    channel             VARCHAR(20)      NOT NULL DEFAULT 'in_app',
    priority            VARCHAR(20)      NOT NULL DEFAULT 'medium',
    is_read             BIT              NOT NULL DEFAULT 0,
    reference_type      NVARCHAR(60)     NULL,
    reference_id        UNIQUEIDENTIFIER NULL,
    sent_at             DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    read_at             DATETIME2(3)     NULL,

    -- [PM] Notifications are process INTERVENTIONS. Linking them to a case and
    -- recording whether an action followed lets you measure whether a nudge
    -- actually moves a stalled case — the classic "intervention effectiveness"
    -- question process mining is used for.
    application_id      UNIQUEIDENTIFIER NULL,
    responded_at        DATETIME2(3)     NULL,
    response_latency_sec BIGINT          NULL,
    action_taken        NVARCHAR(60)     NULL,
    reminder_no         SMALLINT         NOT NULL DEFAULT 0,
    template_id         UNIQUEIDENTIFIER NULL,

    CONSTRAINT PK_notifications PRIMARY KEY CLUSTERED (notification_id),
    CONSTRAINT FK_notif_user    FOREIGN KEY (user_id)        REFERENCES ops.users(user_id),
    CONSTRAINT FK_notif_channel FOREIGN KEY (channel)        REFERENCES ref.notification_channel(channel_code),
    CONSTRAINT FK_notif_prio    FOREIGN KEY (priority)       REFERENCES ref.notification_priority(priority_code),
    CONSTRAINT FK_notif_app     FOREIGN KEY (application_id) REFERENCES core.loan_applications(application_id)
);
GO
CREATE INDEX IX_notifications_user   ON ops.notifications(user_id, sent_at DESC);
CREATE INDEX IX_notifications_unread ON ops.notifications(user_id) WHERE is_read = 0;
GO

-- 22 · ops.notification_templates --------------------------------------------
CREATE TABLE ops.notification_templates (
    template_id         UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_tmpl_id DEFAULT NEWSEQUENTIALID(),
    template_name       NVARCHAR(120)    NOT NULL,
    channel             VARCHAR(20)      NOT NULL,
    subject_template    NVARCHAR(300)    NULL,
    body_template       NVARCHAR(MAX)    NOT NULL,
    variables           NVARCHAR(MAX)    NULL,   -- was TEXT[]; now a JSON array
    is_active           BIT              NOT NULL DEFAULT 1,
    created_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT PK_notification_templates PRIMARY KEY CLUSTERED (template_id),
    CONSTRAINT UQ_template_name UNIQUE (template_name),
    CONSTRAINT FK_tmpl_channel  FOREIGN KEY (channel) REFERENCES ref.notification_channel(channel_code),
    CONSTRAINT CK_tmpl_vars_json CHECK (variables IS NULL OR ISJSON(variables) = 1)
);
GO
ALTER TABLE ops.notifications
    ADD CONSTRAINT FK_notif_template FOREIGN KEY (template_id) REFERENCES ops.notification_templates(template_id);
GO

-- 23 · ops.loan_products -----------------------------------------------------
CREATE TABLE ops.loan_products (
    product_id          UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_prod_id DEFAULT NEWSEQUENTIALID(),
    product_code        VARCHAR(40)      NOT NULL,
    display_name        NVARCHAR(120)    NOT NULL,
    description         NVARCHAR(MAX)    NULL,
    min_amount          DECIMAL(14,2)    NOT NULL,
    max_amount          DECIMAL(14,2)    NOT NULL,
    min_tenure_months   SMALLINT         NOT NULL,
    max_tenure_months   SMALLINT         NOT NULL,
    base_interest_rate  DECIMAL(5,3)     NOT NULL,
    processing_fee_pct  DECIMAL(5,3)     NOT NULL DEFAULT 0,
    is_active           BIT              NOT NULL DEFAULT 1,
    created_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    -- [PM] The target the discovered process is compared against.
    target_tat_hours    DECIMAL(8,2)     NULL,
    process_definition_id BIGINT         NULL,   -- FK added in file 03

    CONSTRAINT PK_loan_products PRIMARY KEY CLUSTERED (product_id),
    CONSTRAINT UQ_loan_products_code UNIQUE (product_code),
    CONSTRAINT FK_prod_type FOREIGN KEY (product_code) REFERENCES ref.loan_product_type(product_code)
);
GO

-- 24 · ops.loan_product_document_requirements --------------------------------
CREATE TABLE ops.loan_product_document_requirements (
    product_id          UNIQUEIDENTIFIER NOT NULL,
    document_type       VARCHAR(40)      NOT NULL,
    is_mandatory        BIT              NOT NULL DEFAULT 1,
    max_age_days        INT              NULL,
    display_order       SMALLINT         NULL,   -- [PM] expected collection order

    CONSTRAINT PK_product_doc_req PRIMARY KEY CLUSTERED (product_id, document_type),
    CONSTRAINT FK_pdr_product FOREIGN KEY (product_id)    REFERENCES ops.loan_products(product_id),
    CONSTRAINT FK_pdr_doctype FOREIGN KEY (document_type) REFERENCES ref.document_type(document_type_code)
);
GO


/* ###########################################################################
   SECTION F — RISK / PRICING  (tables 25–27)
########################################################################### */

-- 25 · risk.risk_assessments -------------------------------------------------
CREATE TABLE risk.risk_assessments (
    assessment_id       UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_risk_id DEFAULT NEWSEQUENTIALID(),
    application_id      UNIQUEIDENTIFIER NOT NULL,
    overall_risk_level  VARCHAR(20)      NOT NULL,
    risk_score          DECIMAL(4,3)     NULL,
    assessment_data     NVARCHAR(MAX)    NULL,
    model_version       NVARCHAR(80)     NULL,
    assessed_by         UNIQUEIDENTIFIER NULL,
    assessed_at         DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),

    -- [PM] Re-assessment is a rework loop (score recomputed after new docs
    -- arrive). assessment_no + previous_score make score DRIFT within a single
    -- case visible, and the override columns capture human disagreement.
    assessment_no       SMALLINT         NOT NULL DEFAULT 1,
    previous_score      DECIMAL(4,3)     NULL,
    trigger_reason      NVARCHAR(80)     NULL,   -- initial / doc_added / manual_recheck
    started_at          DATETIME2(3)     NULL,
    duration_ms         INT              NULL,
    is_automated        BIT              NOT NULL DEFAULT 1,
    was_overridden      BIT              NOT NULL DEFAULT 0,
    overridden_score    DECIMAL(4,3)     NULL,
    overridden_by       UNIQUEIDENTIFIER NULL,
    override_reason     NVARCHAR(500)    NULL,
    feature_snapshot    NVARCHAR(MAX)    NULL,   -- inputs, for reproducibility

    CONSTRAINT PK_risk_assessments PRIMARY KEY CLUSTERED (assessment_id),
    CONSTRAINT CK_risk_score CHECK (risk_score IS NULL OR risk_score BETWEEN 0 AND 1),
    CONSTRAINT CK_risk_json  CHECK (assessment_data IS NULL OR ISJSON(assessment_data) = 1),
    CONSTRAINT FK_risk_app   FOREIGN KEY (application_id)     REFERENCES core.loan_applications(application_id),
    CONSTRAINT FK_risk_level FOREIGN KEY (overall_risk_level) REFERENCES ref.risk_level(risk_level_code),
    CONSTRAINT FK_risk_user  FOREIGN KEY (assessed_by)        REFERENCES ops.users(user_id),
    CONSTRAINT FK_risk_ovr   FOREIGN KEY (overridden_by)      REFERENCES ops.users(user_id)
);
GO
CREATE INDEX IX_risk_assessments_app ON risk.risk_assessments(application_id, assessed_at DESC);
GO

-- 26 · risk.risk_thresholds --------------------------------------------------
CREATE TABLE risk.risk_thresholds (
    threshold_id        UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_thr_id DEFAULT NEWSEQUENTIALID(),
    product_code        VARCHAR(40)      NULL,
    threshold_type      NVARCHAR(60)     NOT NULL,
    min_value           DECIMAL(10,4)    NULL,
    max_value           DECIMAL(10,4)    NULL,
    risk_level          VARCHAR(20)      NOT NULL,
    is_active           BIT              NOT NULL DEFAULT 1,
    effective_from      DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    effective_until     DATETIME2(3)     NULL,
    created_by          UNIQUEIDENTIFIER NULL,
    created_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT PK_risk_thresholds PRIMARY KEY CLUSTERED (threshold_id),
    CONSTRAINT CK_thr_bounds CHECK (min_value IS NOT NULL OR max_value IS NOT NULL),
    CONSTRAINT FK_thr_product FOREIGN KEY (product_code) REFERENCES ref.loan_product_type(product_code),
    CONSTRAINT FK_thr_level   FOREIGN KEY (risk_level)   REFERENCES ref.risk_level(risk_level_code),
    CONSTRAINT FK_thr_user    FOREIGN KEY (created_by)   REFERENCES ops.users(user_id)
);
GO
CREATE INDEX IX_risk_thresholds_product ON risk.risk_thresholds(product_code, is_active);
GO

-- 27 · risk.pricing_models ---------------------------------------------------
CREATE TABLE risk.pricing_models (
    model_id            UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_pm_id DEFAULT NEWSEQUENTIALID(),
    product_code        VARCHAR(40)      NOT NULL,
    model_name          NVARCHAR(120)    NOT NULL,
    base_rate           DECIMAL(5,3)     NOT NULL,
    risk_premium_min    DECIMAL(5,3)     NOT NULL DEFAULT 0,
    risk_premium_max    DECIMAL(5,3)     NOT NULL DEFAULT 0,
    tenure_premium      DECIMAL(5,3)     NOT NULL DEFAULT 0,
    parameters          NVARCHAR(MAX)    NULL,
    is_active           BIT              NOT NULL DEFAULT 1,
    effective_from      DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    effective_until     DATETIME2(3)     NULL,
    created_by          UNIQUEIDENTIFIER NULL,
    created_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT PK_pricing_models PRIMARY KEY CLUSTERED (model_id),
    CONSTRAINT CK_pricing_json  CHECK (parameters IS NULL OR ISJSON(parameters) = 1),
    CONSTRAINT FK_pricing_prod  FOREIGN KEY (product_code) REFERENCES ref.loan_product_type(product_code),
    CONSTRAINT FK_pricing_user  FOREIGN KEY (created_by)   REFERENCES ops.users(user_id)
);
GO
CREATE INDEX IX_pricing_models_product ON risk.pricing_models(product_code, is_active);
GO


/* ###########################################################################
   SECTION G — AUDIT  (tables 28–31)
########################################################################### */

-- Partition function/scheme for the audit log (monthly ranges).
-- SQL Server needs these declared before the partitioned table.
IF NOT EXISTS (SELECT 1 FROM sys.partition_functions WHERE name = 'PF_AuditMonthly')
    CREATE PARTITION FUNCTION PF_AuditMonthly (DATETIME2(3))
    AS RANGE RIGHT FOR VALUES
        ('2026-07-01','2026-08-01','2026-09-01','2026-10-01','2026-11-01','2026-12-01','2027-01-01');
GO
IF NOT EXISTS (SELECT 1 FROM sys.partition_schemes WHERE name = 'PS_AuditMonthly')
    CREATE PARTITION SCHEME PS_AuditMonthly
    AS PARTITION PF_AuditMonthly ALL TO ([PRIMARY]);
GO

-- 28 · audit.audit_log -------------------------------------------------------
CREATE TABLE audit.audit_log (
    log_id              BIGINT           IDENTITY(1,1) NOT NULL,
    event_id            UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    table_name          NVARCHAR(128)    NOT NULL,
    operation           VARCHAR(10)      NOT NULL,
    entity_id           UNIQUEIDENTIFIER NULL,
    old_data            NVARCHAR(MAX)    NULL,
    new_data            NVARCHAR(MAX)    NULL,
    changed_by          UNIQUEIDENTIFIER NULL,
    session_id          UNIQUEIDENTIFIER NULL,
    ip_address          NVARCHAR(45)     NULL,
    occurred_at         DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),

    -- [PM] TWO CRITICAL ADDITIONS.
    -- 1) application_id: audit_log records entity_id but no CASE id, so audit
    --    rows cannot be correlated to a loan without a join per source table.
    --    Denormalising the case id makes the audit trail directly mineable.
    -- 2) txn_id: one user action (e.g. "approve") fires N row-level triggers.
    --    Without a transaction grouping key those N rows look like N separate
    --    events and inflate every activity count.
    application_id      UNIQUEIDENTIFIER NULL,
    txn_id              BIGINT           NULL,
    changed_columns     NVARCHAR(MAX)    NULL,   -- JSON array of column names
    source_system       NVARCHAR(40)     NULL,

    CONSTRAINT PK_audit_log PRIMARY KEY CLUSTERED (occurred_at, log_id)
        ON PS_AuditMonthly(occurred_at),
    CONSTRAINT CK_audit_op CHECK (operation IN ('INSERT','UPDATE','DELETE','TRUNCATE'))
) ON PS_AuditMonthly(occurred_at);
GO
CREATE INDEX IX_audit_log_table  ON audit.audit_log(table_name, occurred_at) ON PS_AuditMonthly(occurred_at);
CREATE INDEX IX_audit_log_entity ON audit.audit_log(entity_id, occurred_at)  ON PS_AuditMonthly(occurred_at);
CREATE INDEX IX_audit_log_actor  ON audit.audit_log(changed_by, occurred_at) ON PS_AuditMonthly(occurred_at);
CREATE INDEX IX_audit_log_app    ON audit.audit_log(application_id, occurred_at) ON PS_AuditMonthly(occurred_at);
GO

-- 29 · audit.audit_log_details -----------------------------------------------
-- NOTE: the FK is composite (occurred_at, log_id) because a partitioned
-- table's PK must include the partitioning column.
CREATE TABLE audit.audit_log_details (
    detail_id           BIGINT           IDENTITY(1,1) NOT NULL,
    log_occurred_at     DATETIME2(3)     NOT NULL,
    log_id              BIGINT           NOT NULL,
    detail_key          NVARCHAR(100)    NOT NULL,
    detail_value        NVARCHAR(MAX)    NULL,
    detail_type         NVARCHAR(40)     NULL,

    CONSTRAINT PK_audit_log_details PRIMARY KEY CLUSTERED (detail_id),
    CONSTRAINT FK_audit_details_log FOREIGN KEY (log_occurred_at, log_id)
        REFERENCES audit.audit_log(occurred_at, log_id)
);
GO
CREATE INDEX IX_audit_details_log ON audit.audit_log_details(log_id);
GO

-- 30 · audit.business_events -------------------------------------------------
-- Kept for application compatibility. File 04 adds a trigger that PROJECTS
-- every row here into pm.event_log, so the app can keep writing business
-- events while process mining reads from one unified log.
CREATE TABLE audit.business_events (
    event_id            UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_bizev_id DEFAULT NEWSEQUENTIALID(),
    application_id      UNIQUEIDENTIFIER NULL,
    customer_id         UNIQUEIDENTIFIER NULL,
    event_type          VARCHAR(40)      NOT NULL,
    event_data          NVARCHAR(MAX)    NULL,
    source              NVARCHAR(60)     NULL,
    created_by          UNIQUEIDENTIFIER NULL,
    created_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    -- [PM] Correlation across the FastAPI modules (one HTTP request can touch
    -- preprocess + extract + guardrails); ties them into one activity instance.
    correlation_id      UNIQUEIDENTIFIER NULL,
    projected_to_pm     BIT              NOT NULL DEFAULT 0,

    CONSTRAINT PK_business_events PRIMARY KEY CLUSTERED (event_id),
    CONSTRAINT CK_bizev_json CHECK (event_data IS NULL OR ISJSON(event_data) = 1),
    CONSTRAINT FK_bizev_type FOREIGN KEY (event_type)     REFERENCES ref.business_event_type(event_type_code),
    CONSTRAINT FK_bizev_app  FOREIGN KEY (application_id) REFERENCES core.loan_applications(application_id),
    CONSTRAINT FK_bizev_cust FOREIGN KEY (customer_id)    REFERENCES core.customers(customer_id),
    CONSTRAINT FK_bizev_user FOREIGN KEY (created_by)     REFERENCES ops.users(user_id)
);
GO
CREATE INDEX IX_biz_events_app  ON audit.business_events(application_id, created_at);
CREATE INDEX IX_biz_events_type ON audit.business_events(event_type, created_at DESC);
GO

-- 31 · core.application_form_responses ---------------------------------------
CREATE TABLE core.application_form_responses (
    response_id         UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_formresp_id DEFAULT NEWSEQUENTIALID(),
    application_id      UNIQUEIDENTIFIER NOT NULL,
    field_key           NVARCHAR(100)    NOT NULL,
    field_value         NVARCHAR(MAX)    NULL,
    field_type          NVARCHAR(40)     NULL,
    section             NVARCHAR(60)     NULL,
    is_validated        BIT              NOT NULL DEFAULT 0,
    validation_result   NVARCHAR(MAX)    NULL,
    created_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),

    -- [PM] FORM-FILLING BEHAVIOUR. FinSight autofills fields from extracted
    -- documents; whether the borrower ACCEPTED the autofill or corrected it is
    -- a direct measure of OCR quality, and edit_count/time_to_fill expose the
    -- form fields that actually cause abandonment.
    was_autofilled      BIT              NOT NULL DEFAULT 0,
    autofill_source_document_id UNIQUEIDENTIFIER NULL,
    was_corrected       BIT              NOT NULL DEFAULT 0,
    edit_count          SMALLINT         NOT NULL DEFAULT 0,
    first_filled_at     DATETIME2(3)     NULL,
    time_to_fill_ms     INT              NULL,

    CONSTRAINT PK_form_responses PRIMARY KEY CLUSTERED (response_id),
    CONSTRAINT UQ_form_app_field UNIQUE (application_id, field_key),
    CONSTRAINT CK_formresp_json CHECK (validation_result IS NULL OR ISJSON(validation_result) = 1),
    CONSTRAINT FK_formresp_app  FOREIGN KEY (application_id) REFERENCES core.loan_applications(application_id),
    CONSTRAINT FK_formresp_doc  FOREIGN KEY (autofill_source_document_id) REFERENCES core.uploaded_documents(document_id)
);
GO
CREATE INDEX IX_form_responses_app ON core.application_form_responses(application_id);
GO

PRINT 'FILE 2 complete — 31 operational tables created with process-mining columns.';
GO
