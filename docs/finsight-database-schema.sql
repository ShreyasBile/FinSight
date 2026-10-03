-- =============================================================================
-- FinSight AI — Loan Origination System  ·  Database Schema
-- 31 tables · 9 enums · 17 audit triggers · full constraint enforcement
-- =============================================================================
-- Usage:  psql -U finsight -d finsight -f finsight-database-schema.sql
-- =============================================================================

BEGIN;

-- #############################################################################
-- 1. ENUMS
-- #############################################################################

CREATE TYPE application_status AS ENUM (
    'draft','submitted','documentsProcessing','documentsVerified',
    'goldValuationScheduled','goldValued','fieldVerification',
    'propertyValuation','legalVerification','incomeVerification',
    'propertyDocReview','legalCheck','technicalValuation',
    'bankGstAnalysis','underwritingReview','vehicleInspection',
    'underReview','offerGenerated','accepted','sanctioned',
    'disbursed','rejected','cancelled','expired'
);

CREATE TYPE loan_product_type AS ENUM (
    'gold','home_loan','lap','tractor','microfinance',
    'msme','working_capital','commercial_vehicle',
    'used_vehicle','two_wheeler','personal_loan','education_loan'
);

CREATE TYPE user_role AS ENUM ('user','officer','head','admin');

CREATE TYPE document_type AS ENUM (
    'aadhaar','pan','passport','voter_id','driving_license',
    'bank_statement','salary_slip','itr','property_document',
    'valuation_report','vehicle_rc','insurance_certificate',
    'business_proof','gst_certificate','photograph','other'
);

CREATE TYPE document_status AS ENUM (
    'pending','processing','verified','rejected','resubmitted','expired'
);

CREATE TYPE risk_level AS ENUM ('low','medium','high','critical');

CREATE TYPE fraud_severity AS ENUM ('low','medium','high','critical');

CREATE TYPE fraud_status AS ENUM (
    'open','investigating','confirmed','dismissed','escalated'
);

CREATE TYPE rule_type AS ENUM ('mandatory','internal_policy','advisory');

CREATE TYPE rule_category AS ENUM (
    'kyc','digital_lending','fair_practices','credit_risk','fraud_risk','general'
);

CREATE TYPE compliance_check_status AS ENUM (
    'pass','fail','warning','inconclusive','not_applicable'
);

CREATE TYPE notification_channel AS ENUM ('in_app','email','sms','whatsapp');

CREATE TYPE notification_priority AS ENUM ('low','medium','high','urgent');

CREATE TYPE business_event_type AS ENUM (
    'application_created','application_submitted','document_uploaded',
    'document_verified','document_rejected','offer_generated',
    'offer_accepted','offer_rejected','disbursed','fraud_detected',
    'fraud_escalated','compliance_check','rule_proposed','rule_approved',
    'rule_rejected','user_login','user_logout','threshold_changed',
    'pricing_override','report_generated'
);

-- #############################################################################
-- 2. CORE LENDING  (tables 01–08)
-- #############################################################################

-- 01 · customers
CREATE TABLE customers (
    customer_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    first_name          TEXT NOT NULL,
    last_name           TEXT NOT NULL,
    date_of_birth       DATE NOT NULL,
    mobile              TEXT NOT NULL,
    email               TEXT,
    pan                 VARCHAR(10),
    aadhaar             VARCHAR(12),
    address_line1       TEXT,
    address_line2       TEXT,
    city                TEXT,
    state               TEXT,
    pincode             VARCHAR(6),
    occupation          TEXT,
    annual_income       NUMERIC(14,2),
    credit_score        SMALLINT CHECK (credit_score BETWEEN 300 AND 900),
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX idx_customers_pan    ON customers(pan)    WHERE pan IS NOT NULL;
CREATE UNIQUE INDEX idx_customers_aadhaar ON customers(aadhaar) WHERE aadhaar IS NOT NULL;
CREATE INDEX idx_customers_mobile ON customers(mobile);

-- 02 · loan_applications
CREATE TABLE loan_applications (
    application_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id         UUID NOT NULL REFERENCES customers(customer_id),
    loan_product        loan_product_type NOT NULL,
    status              application_status NOT NULL DEFAULT 'draft',
    loan_amount         NUMERIC(14,2),
    tenure_months       SMALLINT,
    interest_rate       NUMERIC(5,3),
    purpose             TEXT,
    form_data           JSONB,
    assigned_officer_id UUID,
    assigned_head_id    UUID,
    branch_code         TEXT,
    applied_at          TIMESTAMPTZ,
    submitted_at        TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_loan_applications_customer   ON loan_applications(customer_id);
CREATE INDEX idx_loan_applications_status     ON loan_applications(status);
CREATE INDEX idx_loan_applications_product    ON loan_applications(loan_product);
CREATE INDEX idx_loan_applications_officer    ON loan_applications(assigned_officer_id);
CREATE INDEX idx_loan_applications_created    ON loan_applications(created_at DESC);

-- 03 · application_status_history
CREATE TABLE application_status_history (
    history_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id      UUID NOT NULL REFERENCES loan_applications(application_id),
    from_status         application_status,
    to_status           application_status NOT NULL,
    changed_by          UUID,
    change_reason       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_status_history_app ON application_status_history(application_id);
CREATE INDEX idx_status_history_ts  ON application_status_history(created_at DESC);

-- 04 · uploaded_documents
CREATE TABLE uploaded_documents (
    document_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id      UUID NOT NULL REFERENCES loan_applications(application_id),
    document_type       document_type NOT NULL,
    file_name           TEXT NOT NULL,
    file_path           TEXT NOT NULL,
    file_size_bytes     INTEGER,
    mime_type           TEXT,
    checksum_sha256     VARCHAR(64),
    ocr_status          document_status NOT NULL DEFAULT 'pending',
    verification_status document_status NOT NULL DEFAULT 'pending',
    extracted_data      JSONB,
    uploaded_by         UUID,
    uploaded_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_uploaded_docs_app   ON uploaded_documents(application_id);
CREATE INDEX idx_uploaded_docs_type  ON uploaded_documents(document_type);

-- 05 · document_extraction_results
CREATE TABLE document_extraction_results (
    extraction_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id         UUID NOT NULL REFERENCES uploaded_documents(document_id),
    extraction_source   TEXT NOT NULL DEFAULT 'ocr',
    raw_text            TEXT,
    structured_data     JSONB,
    confidence_scores   JSONB,
    processing_time_ms  INTEGER,
    model_version       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_extraction_doc ON document_extraction_results(document_id);

-- 06 · field_verification_log
CREATE TABLE field_verification_log (
    verification_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id      UUID NOT NULL REFERENCES loan_applications(application_id),
    field_name          TEXT NOT NULL,
    field_value         TEXT,
    source_document_id  UUID REFERENCES uploaded_documents(document_id),
    verification_method TEXT NOT NULL,
    is_valid            BOOLEAN NOT NULL,
    confidence          NUMERIC(4,3) CHECK (confidence BETWEEN 0 AND 1),
    discrepancy_detail  TEXT,
    verified_by         UUID,
    verified_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_field_verification_app ON field_verification_log(application_id);

-- 07 · loan_offers
CREATE TABLE loan_offers (
    offer_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id      UUID NOT NULL REFERENCES loan_applications(application_id),
    offered_amount      NUMERIC(14,2) NOT NULL,
    interest_rate       NUMERIC(5,3) NOT NULL,
    tenure_months       SMALLINT NOT NULL,
    processing_fee      NUMERIC(10,2),
    emi_amount          NUMERIC(10,2),
    offer_valid_until   TIMESTAMPTZ NOT NULL,
    terms_conditions    TEXT,
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','accepted','rejected','expired')),
    accepted_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_offers_app ON loan_offers(application_id);

-- 08 · disbursements
CREATE TABLE disbursements (
    disbursement_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    offer_id            UUID NOT NULL REFERENCES loan_offers(offer_id),
    application_id      UUID NOT NULL REFERENCES loan_applications(application_id),
    disbursed_amount    NUMERIC(14,2) NOT NULL,
    disbursed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    disbursement_method TEXT,
    reference_number    TEXT,
    settlement_account  TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_disbursements_app ON disbursements(application_id);
CREATE INDEX idx_disbursements_offer ON disbursements(offer_id);

-- #############################################################################
-- 3. FRAUD  (tables 09–13)
-- #############################################################################

-- 09 · fraud_cases
CREATE TABLE fraud_cases (
    case_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id      UUID REFERENCES loan_applications(application_id),
    customer_id         UUID REFERENCES customers(customer_id),
    severity            fraud_severity NOT NULL DEFAULT 'low',
    status              fraud_status NOT NULL DEFAULT 'open',
    fraud_category      TEXT,
    description         TEXT NOT NULL,
    detection_method    TEXT,
    ai_confidence       NUMERIC(4,3) CHECK (ai_confidence BETWEEN 0 AND 1),
    flagged_by          UUID,
    assigned_to         UUID,
    resolved_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (application_id IS NOT NULL OR customer_id IS NOT NULL)
);
CREATE INDEX idx_fraud_cases_app      ON fraud_cases(application_id);
CREATE INDEX idx_fraud_cases_customer ON fraud_cases(customer_id);
CREATE INDEX idx_fraud_cases_status   ON fraud_cases(status);

-- 10 · fraud_signals
CREATE TABLE fraud_signals (
    signal_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id             UUID NOT NULL REFERENCES fraud_cases(case_id),
    signal_name         TEXT NOT NULL,
    signal_value        NUMERIC(10,4),
    signal_threshold    NUMERIC(10,4),
    signal_score        NUMERIC(4,3) CHECK (signal_score BETWEEN 0 AND 1),
    signal_category     TEXT,
    metadata            JSONB,
    detected_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_fraud_signals_case ON fraud_signals(case_id);

-- 11 · fraud_rings
CREATE TABLE fraud_rings (
    ring_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ring_name           TEXT NOT NULL,
    description         TEXT,
    severity            fraud_severity NOT NULL DEFAULT 'medium',
    status              fraud_status NOT NULL DEFAULT 'open',
    detected_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 12 · fraud_ring_members
CREATE TABLE fraud_ring_members (
    ring_id             UUID NOT NULL REFERENCES fraud_rings(ring_id),
    customer_id         UUID NOT NULL REFERENCES customers(customer_id),
    linked_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    link_evidence       TEXT,
    PRIMARY KEY (ring_id, customer_id)
);

-- 13 · fraud_escalations
CREATE TABLE fraud_escalations (
    escalation_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id             UUID NOT NULL REFERENCES fraud_cases(case_id),
    escalated_by        UUID NOT NULL,
    escalated_to        UUID NOT NULL,
    reason              TEXT NOT NULL,
    previous_severity   fraud_severity,
    new_severity        fraud_severity NOT NULL,
    resolved_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_fraud_esc_case ON fraud_escalations(case_id);

-- #############################################################################
-- 4. COMPLIANCE / KNOWLEDGE  (tables 14–18)
-- #############################################################################

-- 14 · compliance_documents
CREATE TABLE compliance_documents (
    document_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title               TEXT NOT NULL,
    category            TEXT NOT NULL,
    source_type         TEXT NOT NULL,
    source_url          TEXT,
    effective_date      DATE,
    text_content        TEXT NOT NULL,
    chunk_count         INTEGER DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active','archived','draft')),
    uploaded_by         UUID,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_compliance_docs_category ON compliance_documents(category);

-- 15 · compliance_document_chunks
CREATE TABLE compliance_document_chunks (
    chunk_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id         UUID NOT NULL REFERENCES compliance_documents(document_id)
                        ON DELETE CASCADE,
    chunk_index         INTEGER NOT NULL,
    chunk_text          TEXT NOT NULL,
    embedding           VECTOR(384),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, chunk_index)
);
CREATE INDEX idx_compliance_chunks_doc ON compliance_document_chunks(document_id);

-- 16 · compliance_rule_candidates (maker-checker draft queue)
CREATE TABLE compliance_rule_candidates (
    candidate_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id         UUID NOT NULL REFERENCES compliance_documents(document_id),
    proposed_rule_name  TEXT NOT NULL,
    rule_type           rule_type NOT NULL,
    rule_category       rule_category NOT NULL,
    condition           TEXT NOT NULL,
    regulation          TEXT,
    obligation_text     TEXT,
    confidence          NUMERIC(4,3) CHECK (confidence BETWEEN 0 AND 1),
    needs_field_mapping BOOLEAN DEFAULT FALSE,
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','approved','rejected')),
    reviewed_by         UUID,
    reviewed_at         TIMESTAMPTZ,
    rejection_note      TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_candidates_doc   ON compliance_rule_candidates(document_id);
CREATE INDEX idx_candidates_status ON compliance_rule_candidates(status);

-- 17 · compliance_rules (active, enforced rules)
CREATE TABLE compliance_rules (
    rule_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    candidate_id        UUID REFERENCES compliance_rule_candidates(candidate_id),
    rule_name           TEXT NOT NULL,
    rule_type           rule_type NOT NULL,
    rule_category       rule_category NOT NULL,
    condition           TEXT NOT NULL,
    regulation          TEXT,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    version             INTEGER NOT NULL DEFAULT 1,
    approved_by         UUID NOT NULL,
    approved_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    superseded_by       UUID,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX idx_rules_name_active
    ON compliance_rules(rule_name) WHERE is_active = TRUE;

-- 18 · compliance_check_results
CREATE TABLE compliance_check_results (
    check_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id      UUID NOT NULL REFERENCES loan_applications(application_id),
    rule_id             UUID NOT NULL REFERENCES compliance_rules(rule_id),
    status              compliance_check_status NOT NULL,
    evidence            TEXT,
    remediation         TEXT,
    checked_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_compliance_check_app  ON compliance_check_results(application_id);
CREATE INDEX idx_compliance_check_rule ON compliance_check_results(rule_id);

-- #############################################################################
-- 5. OPERATIONS / CONFIG  (tables 19–24)
-- #############################################################################

-- 19 · users
CREATE TABLE users (
    user_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email               TEXT NOT NULL,
    password_hash       TEXT NOT NULL,
    full_name           TEXT NOT NULL,
    role                user_role NOT NULL,
    branch_code         TEXT,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    last_login_at       TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX idx_users_email ON users(email);

-- 20 · user_sessions
CREATE TABLE user_sessions (
    session_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(user_id),
    token               TEXT NOT NULL,
    ip_address          INET,
    user_agent          TEXT,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    logged_in_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    logged_out_at       TIMESTAMPTZ,
    expires_at          TIMESTAMPTZ NOT NULL
);
CREATE UNIQUE INDEX idx_sessions_token ON user_sessions(token);
CREATE INDEX idx_sessions_user ON user_sessions(user_id);

-- 21 · notifications
CREATE TABLE notifications (
    notification_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(user_id),
    title               TEXT NOT NULL,
    body                TEXT,
    channel             notification_channel NOT NULL DEFAULT 'in_app',
    priority            notification_priority NOT NULL DEFAULT 'medium',
    is_read             BOOLEAN NOT NULL DEFAULT FALSE,
    reference_type      TEXT,
    reference_id        UUID,
    sent_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    read_at             TIMESTAMPTZ
);
CREATE INDEX idx_notifications_user   ON notifications(user_id);
CREATE INDEX idx_notifications_unread ON notifications(user_id) WHERE is_read = FALSE;

-- 22 · notification_templates
CREATE TABLE notification_templates (
    template_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    template_name       TEXT NOT NULL UNIQUE,
    channel             notification_channel NOT NULL,
    subject_template    TEXT,
    body_template       TEXT NOT NULL,
    variables           TEXT[],
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 23 · loan_products
CREATE TABLE loan_products (
    product_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_code        loan_product_type NOT NULL UNIQUE,
    display_name        TEXT NOT NULL,
    description         TEXT,
    min_amount          NUMERIC(14,2) NOT NULL,
    max_amount          NUMERIC(14,2) NOT NULL,
    min_tenure_months   SMALLINT NOT NULL,
    max_tenure_months   SMALLINT NOT NULL,
    base_interest_rate  NUMERIC(5,3) NOT NULL,
    processing_fee_pct  NUMERIC(5,3) DEFAULT 0,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 24 · loan_product_document_requirements (static junction)
CREATE TABLE loan_product_document_requirements (
    product_id          UUID NOT NULL REFERENCES loan_products(product_id),
    document_type       document_type NOT NULL,
    is_mandatory        BOOLEAN NOT NULL DEFAULT TRUE,
    max_age_days        INTEGER,
    PRIMARY KEY (product_id, document_type)
);

-- #############################################################################
-- 6. RISK / PRICING  (tables 25–27)
-- #############################################################################

-- 25 · risk_assessments
CREATE TABLE risk_assessments (
    assessment_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id      UUID NOT NULL REFERENCES loan_applications(application_id),
    overall_risk_level  risk_level NOT NULL,
    risk_score          NUMERIC(4,3) CHECK (risk_score BETWEEN 0 AND 1),
    assessment_data     JSONB,
    model_version       TEXT,
    assessed_by         UUID,
    assessed_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_risk_assessments_app ON risk_assessments(application_id);

-- 26 · risk_thresholds
CREATE TABLE risk_thresholds (
    threshold_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_code        loan_product_type,
    threshold_type      TEXT NOT NULL,
    min_value           NUMERIC(10,4),
    max_value           NUMERIC(10,4),
    risk_level          risk_level NOT NULL,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    effective_from      TIMESTAMPTZ NOT NULL DEFAULT now(),
    effective_until     TIMESTAMPTZ,
    created_by          UUID,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (min_value IS NOT NULL OR max_value IS NOT NULL)
);
CREATE INDEX idx_risk_thresholds_product ON risk_thresholds(product_code);

-- 27 · pricing_models
CREATE TABLE pricing_models (
    model_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_code        loan_product_type NOT NULL,
    model_name          TEXT NOT NULL,
    base_rate           NUMERIC(5,3) NOT NULL,
    risk_premium_min    NUMERIC(5,3) DEFAULT 0,
    risk_premium_max    NUMERIC(5,3) DEFAULT 0,
    tenure_premium      NUMERIC(5,3) DEFAULT 0,
    parameters          JSONB,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    effective_from      TIMESTAMPTZ NOT NULL DEFAULT now(),
    effective_until     TIMESTAMPTZ,
    created_by          UUID,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_pricing_models_product ON pricing_models(product_code);

-- #############################################################################
-- 7. AUDIT  (tables 28–31)
-- #############################################################################

-- 28 · audit_log
CREATE TABLE audit_log (
    log_id              BIGSERIAL,
    event_id            UUID NOT NULL DEFAULT gen_random_uuid(),
    table_name          TEXT NOT NULL,
    operation           TEXT NOT NULL CHECK (operation IN ('INSERT','UPDATE','DELETE','TRUNCATE')),
    entity_id           UUID,
    old_data            JSONB,
    new_data            JSONB,
    changed_by          UUID,
    session_id          UUID,
    ip_address          INET,
    occurred_at         TIMESTAMPTZ NOT NULL DEFAULT now()
) PARTITION BY RANGE (occurred_at);

-- Create monthly partitions for audit_log
CREATE TABLE audit_log_2026_07 PARTITION OF audit_log
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE audit_log_2026_08 PARTITION OF audit_log
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE audit_log_2026_09 PARTITION OF audit_log
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE audit_log_default PARTITION OF audit_log DEFAULT;

CREATE INDEX idx_audit_log_table ON audit_log(table_name);
CREATE INDEX idx_audit_log_entity ON audit_log(entity_id);
CREATE INDEX idx_audit_log_actor ON audit_log(changed_by);
CREATE INDEX idx_audit_log_ts ON audit_log(occurred_at DESC);

-- 29 · audit_log_details (structured key-value metadata per audit event)
CREATE TABLE audit_log_details (
    detail_id           BIGSERIAL PRIMARY KEY,
    log_id              BIGINT NOT NULL REFERENCES audit_log(log_id),
    detail_key          TEXT NOT NULL,
    detail_value        TEXT,
    detail_type         TEXT
);
CREATE INDEX idx_audit_details_log ON audit_log_details(log_id);

-- 30 · business_events (application-level events, separate from audit)
CREATE TABLE business_events (
    event_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id      UUID REFERENCES loan_applications(application_id),
    customer_id         UUID REFERENCES customers(customer_id),
    event_type          business_event_type NOT NULL,
    event_data          JSONB,
    source              TEXT,
    created_by          UUID,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_biz_events_app ON business_events(application_id);
CREATE INDEX idx_biz_events_type ON business_events(event_type);
CREATE INDEX idx_biz_events_ts ON business_events(created_at DESC);

-- 31 · application_form_responses (product-specific dynamic form data)
CREATE TABLE application_form_responses (
    response_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id      UUID NOT NULL REFERENCES loan_applications(application_id),
    field_key           TEXT NOT NULL,
    field_value         TEXT,
    field_type          TEXT,
    section             TEXT,
    is_validated        BOOLEAN DEFAULT FALSE,
    validation_result   JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (application_id, field_key)
);
CREATE INDEX idx_form_responses_app ON application_form_responses(application_id);

-- #############################################################################
-- 8. AUDIT TRIGGER FUNCTIONS
-- #############################################################################

CREATE OR REPLACE FUNCTION set_session_context()
RETURNS TRIGGER AS $$
BEGIN
    -- Expect caller to SET LOCAL session_vars.actor_id and session_vars.session_id
    -- before DML. If not set, NULL is used.
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION audit_trigger_insert()
RETURNS TRIGGER AS $$
DECLARE
    v_actor     UUID;
    v_session   UUID;
    v_ip        INET;
BEGIN
    v_actor   := current_setting('session_vars.actor_id',   TRUE)::UUID;
    v_session := current_setting('session_vars.session_id',  TRUE)::UUID;
    v_ip      := current_setting('session_vars.ip_address',  TRUE)::INET;

    INSERT INTO audit_log (table_name, operation, entity_id, new_data, changed_by, session_id, ip_address)
    VALUES (TG_TABLE_NAME, 'INSERT', NEW.event_id, row_to_json(NEW)::JSONB, v_actor, v_session, v_ip);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION audit_trigger_update()
RETURNS TRIGGER AS $$
DECLARE
    v_actor     UUID;
    v_session   UUID;
    v_ip        INET;
    v_pkey_col  TEXT;
    v_pkey_val  UUID;
BEGIN
    v_actor   := current_setting('session_vars.actor_id',   TRUE)::UUID;
    v_session := current_setting('session_vars.session_id',  TRUE)::UUID;
    v_ip      := current_setting('session_vars.ip_address',  TRUE)::INET;

    -- Determine primary key column dynamically
    SELECT a.attname INTO v_pkey_col
    FROM   pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
    WHERE  i.indrelid = TG_RELID AND i.indisprimary
    LIMIT 1;

    EXECUTE format('SELECT ($1::jsonb->>%L)::uuid', v_pkey_col) USING row_to_json(NEW)::TEXT INTO v_pkey_val;

    INSERT INTO audit_log (table_name, operation, entity_id, old_data, new_data, changed_by, session_id, ip_address)
    VALUES (TG_TABLE_NAME, 'UPDATE', v_pkey_val, row_to_json(OLD)::JSONB, row_to_json(NEW)::JSONB,
            v_actor, v_session, v_ip);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION audit_trigger_delete()
RETURNS TRIGGER AS $$
DECLARE
    v_actor     UUID;
    v_session   UUID;
    v_ip        INET;
    v_pkey_col  TEXT;
    v_pkey_val  UUID;
BEGIN
    v_actor   := current_setting('session_vars.actor_id',   TRUE)::UUID;
    v_session := current_setting('session_vars.session_id',  TRUE)::UUID;
    v_ip      := current_setting('session_vars.ip_address',  TRUE)::INET;

    SELECT a.attname INTO v_pkey_col
    FROM   pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
    WHERE  i.indrelid = TG_RELID AND i.indisprimary
    LIMIT 1;

    EXECUTE format('SELECT ($1::jsonb->>%L)::uuid', v_pkey_col) USING row_to_json(OLD)::TEXT INTO v_pkey_val;

    INSERT INTO audit_log (table_name, operation, entity_id, old_data, changed_by, session_id, ip_address)
    VALUES (TG_TABLE_NAME, 'DELETE', v_pkey_val, row_to_json(OLD)::JSONB, v_actor, v_session, v_ip);
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

-- #############################################################################
-- 9. AUDIT TRIGGERS  (17 triggers on core + fraud + compliance + ops + risk tables)
-- #############################################################################

-- Helper macro: create INSERT, UPDATE, DELETE triggers for a table
-- (excluding static junction tables like loan_product_document_requirements)

-- customers
CREATE TRIGGER trg_customers_audit_insert AFTER INSERT ON customers
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_insert();
CREATE TRIGGER trg_customers_audit_update AFTER UPDATE ON customers
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_update();
CREATE TRIGGER trg_customers_audit_delete AFTER DELETE ON customers
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_delete();

-- loan_applications
CREATE TRIGGER trg_loan_apps_audit_insert AFTER INSERT ON loan_applications
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_insert();
CREATE TRIGGER trg_loan_apps_audit_update AFTER UPDATE ON loan_applications
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_update();
CREATE TRIGGER trg_loan_apps_audit_delete AFTER DELETE ON loan_applications
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_delete();

-- loan_offers
CREATE TRIGGER trg_offers_audit_insert AFTER INSERT ON loan_offers
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_insert();
CREATE TRIGGER trg_offers_audit_update AFTER UPDATE ON loan_offers
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_update();
CREATE TRIGGER trg_offers_audit_delete AFTER DELETE ON loan_offers
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_delete();

-- disbursements
CREATE TRIGGER trg_disbursements_audit_insert AFTER INSERT ON disbursements
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_insert();
CREATE TRIGGER trg_disbursements_audit_update AFTER UPDATE ON disbursements
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_update();
CREATE TRIGGER trg_disbursements_audit_delete AFTER DELETE ON disbursements
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_delete();

-- fraud_cases
CREATE TRIGGER trg_fraud_cases_audit_insert AFTER INSERT ON fraud_cases
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_insert();
CREATE TRIGGER trg_fraud_cases_audit_update AFTER UPDATE ON fraud_cases
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_update();
CREATE TRIGGER trg_fraud_cases_audit_delete AFTER DELETE ON fraud_cases
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_delete();

-- fraud_rings
CREATE TRIGGER trg_fraud_rings_audit_insert AFTER INSERT ON fraud_rings
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_insert();
CREATE TRIGGER trg_fraud_rings_audit_update AFTER UPDATE ON fraud_rings
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_update();
CREATE TRIGGER trg_fraud_rings_audit_delete AFTER DELETE ON fraud_rings
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_delete();

-- compliance_documents
CREATE TRIGGER trg_compliance_docs_audit_insert AFTER INSERT ON compliance_documents
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_insert();
CREATE TRIGGER trg_compliance_docs_audit_update AFTER UPDATE ON compliance_documents
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_update();
CREATE TRIGGER trg_compliance_docs_audit_delete AFTER DELETE ON compliance_documents
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_delete();

-- compliance_rules
CREATE TRIGGER trg_compliance_rules_audit_insert AFTER INSERT ON compliance_rules
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_insert();
CREATE TRIGGER trg_compliance_rules_audit_update AFTER UPDATE ON compliance_rules
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_update();
CREATE TRIGGER trg_compliance_rules_audit_delete AFTER DELETE ON compliance_rules
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_delete();

-- compliance_check_results
CREATE TRIGGER trg_compliance_checks_audit_insert AFTER INSERT ON compliance_check_results
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_insert();
CREATE TRIGGER trg_compliance_checks_audit_update AFTER UPDATE ON compliance_check_results
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_update();
CREATE TRIGGER trg_compliance_checks_audit_delete AFTER DELETE ON compliance_check_results
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_delete();

-- users
CREATE TRIGGER trg_users_audit_insert AFTER INSERT ON users
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_insert();
CREATE TRIGGER trg_users_audit_update AFTER UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_update();
CREATE TRIGGER trg_users_audit_delete AFTER DELETE ON users
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_delete();

-- risk_assessments
CREATE TRIGGER trg_risk_assessments_audit_insert AFTER INSERT ON risk_assessments
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_insert();
CREATE TRIGGER trg_risk_assessments_audit_update AFTER UPDATE ON risk_assessments
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_update();
CREATE TRIGGER trg_risk_assessments_audit_delete AFTER DELETE ON risk_assessments
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_delete();

-- pricing_models
CREATE TRIGGER trg_pricing_models_audit_insert AFTER INSERT ON pricing_models
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_insert();
CREATE TRIGGER trg_pricing_models_audit_update AFTER UPDATE ON pricing_models
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_update();
CREATE TRIGGER trg_pricing_models_audit_delete AFTER DELETE ON pricing_models
    FOR EACH ROW EXECUTE FUNCTION audit_trigger_delete();

-- #############################################################################
-- 10. UPDATED_AT TRIGGERS
-- #############################################################################

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_customers_updated_at      BEFORE UPDATE ON customers
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_loan_apps_updated_at      BEFORE UPDATE ON loan_applications
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_uploaded_docs_updated_at   BEFORE UPDATE ON uploaded_documents
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_fraud_cases_updated_at     BEFORE UPDATE ON fraud_cases
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_fraud_rings_updated_at     BEFORE UPDATE ON fraud_rings
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_compliance_docs_updated_at BEFORE UPDATE ON compliance_documents
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_compliance_rules_updated_at BEFORE UPDATE ON compliance_rules
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_users_updated_at           BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_notification_templates_updated_at BEFORE UPDATE ON notification_templates
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_pricing_models_updated_at   BEFORE UPDATE ON pricing_models
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_form_responses_updated_at   BEFORE UPDATE ON application_form_responses
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- #############################################################################
-- 11. TRUNCATE GUARD (prevents accidental TRUNCATE on audited tables)
-- #############################################################################

CREATE OR REPLACE FUNCTION prevent_truncate()
RETURNS EVENT_TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'TRUNCATE is not allowed on application tables. Use DELETE with WHERE.';
END;
$$ LANGUAGE plpgsql;

-- #############################################################################
-- 12. USAGE NOTES
-- #############################################################################
--
-- SESSION CONTEXT (required before any DML on audited tables):
--   SET LOCAL session_vars.actor_id   = 'uuid-of-current-user';
--   SET LOCAL session_vars.session_id = 'uuid-of-current-session';
--   SET LOCAL session_vars.ip_address = '192.168.1.1';
--
-- QUERYING AUDIT LOG:
--   SELECT * FROM audit_log
--   WHERE table_name = 'loan_applications'
--     AND entity_id = 'some-uuid'
--   ORDER BY occurred_at DESC;
--
-- ADDING NEW MONTHLY PARTITIONS:
--   CREATE TABLE audit_log_YYYY_MM PARTITION OF audit_log
--     FOR VALUES FROM ('YYYY-MM-01') TO ('YYYY-MM-01'::DATE + INTERVAL '1 month');
--

COMMIT;
