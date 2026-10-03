/* ============================================================================
   FinSight AI  ·  SQL Server (SSMS) Schema
   FILE 3 of 5 — THE PROCESS-MINING LAYER  (entirely new)
   ----------------------------------------------------------------------------
   WHAT PROCESS MINING NEEDS, IN ONE PARAGRAPH
   Every process-mining algorithm — process discovery, conformance checking,
   performance/bottleneck analysis, variant analysis — consumes ONE structure:
   an EVENT LOG. An event log is a table where each row says
       "in CASE x, ACTIVITY y happened at TIME t, performed by RESOURCE r".
   Those four columns are the minimum. Everything beyond them (lifecycle,
   durations, wait reasons, costs, object links) is what turns a picture of the
   process into an explanation of it.

   The existing FinSight schema has none of this. It stores CURRENT STATE
   (application.status = 'underReview') plus a partial history table. State
   tells you where a case is; an event log tells you how it got there, how long
   each hop took, who did it, and how often the path deviates from the design.

   THREE CASE NOTIONS live in this database, and all three are worth mining:
     1. loan_application  — the main journey (draft → disbursed)
     2. fraud_case        — detect → triage → resolve
     3. rule_authoring    — extract → review → approve/reject (maker-checker)
     4. document          — upload → preprocess → extract → verify (sub-process)

   Run AFTER 02-core-tables.sql.
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
   1. PROCESS DEFINITIONS AND THE ACTIVITY DICTIONARY
########################################################################### */

-- 1.1 · pm.process_definition ------------------------------------------------
-- A named, VERSIONED process. Versioning matters: if you change the workflow
-- in September, cases started in August must still be compared against the
-- model they actually ran under, or every one of them looks non-conformant.
CREATE TABLE pm.process_definition (
    process_definition_id   BIGINT        IDENTITY(1,1) NOT NULL,
    process_code            VARCHAR(50)   NOT NULL,
    process_name            NVARCHAR(150) NOT NULL,
    process_version         NVARCHAR(20)  NOT NULL DEFAULT '1.0',
    case_id_source          NVARCHAR(100) NOT NULL,   -- which table/column is the case id
    description             NVARCHAR(MAX) NULL,
    is_active               BIT           NOT NULL DEFAULT 1,
    effective_from          DATETIME2(3)  NOT NULL DEFAULT SYSUTCDATETIME(),
    effective_until         DATETIME2(3)  NULL,
    created_at              DATETIME2(3)  NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT PK_process_definition PRIMARY KEY CLUSTERED (process_definition_id),
    CONSTRAINT UQ_process_code_version UNIQUE (process_code, process_version)
);
GO

INSERT INTO pm.process_definition (process_code, process_name, process_version, case_id_source, description) VALUES
    ('loan_application', N'Loan Origination',        '1.0', 'core.loan_applications.application_id',
     N'Borrower journey from draft to disbursement or rejection.'),
    ('document',         N'Document Processing',     '1.0', 'core.uploaded_documents.document_id',
     N'Per-document sub-process: upload, preprocess, extract, validate, verify.'),
    ('fraud_case',       N'Fraud Investigation',     '1.0', 'fraud.fraud_cases.case_id',
     N'Signal to verdict: detection, triage, escalation, resolution.'),
    ('rule_authoring',   N'Regulatory Rule Authoring','1.0','comp.compliance_rule_candidates.candidate_id',
     N'Maker-checker: ingest regulation, extract candidate, review, approve or reject.');
GO

ALTER TABLE ops.loan_products
    ADD CONSTRAINT FK_prod_processdef FOREIGN KEY (process_definition_id)
        REFERENCES pm.process_definition(process_definition_id);
GO

-- 1.2 · pm.activity ----------------------------------------------------------
-- THE ACTIVITY DICTIONARY. Free-text activity names are the most common way a
-- process-mining project fails: "Doc Verified", "document verified" and
-- "Verify Document" become three nodes on the map for one real step. A
-- dictionary with stable codes prevents that, and carries the metadata
-- (expected duration, is_milestone, is_value_adding) that performance and
-- waste analyses need.
CREATE TABLE pm.activity (
    activity_id             INT           IDENTITY(1,1) NOT NULL,
    activity_code           VARCHAR(60)   NOT NULL,
    activity_name           NVARCHAR(120) NOT NULL,   -- the label drawn on the process map
    process_code            VARCHAR(50)   NOT NULL,
    activity_category       NVARCHAR(60)  NOT NULL,   -- Intake / Documents / AI / Review / Decision
    default_resource_type   VARCHAR(30)   NOT NULL DEFAULT 'human',
    component_code          VARCHAR(40)   NULL,       -- which FinSight module performs it
    is_milestone            BIT           NOT NULL DEFAULT 0,  -- shown on the executive map
    is_value_adding         BIT           NOT NULL DEFAULT 1,  -- lean analysis: waste vs work
    is_optional             BIT           NOT NULL DEFAULT 0,  -- absence is not a deviation
    is_rework_indicator     BIT           NOT NULL DEFAULT 0,  -- its presence means something went wrong
    expected_duration_sec   INT           NULL,
    normative_order         SMALLINT      NULL,       -- rough position in the happy path
    sort_order              SMALLINT      NULL,
    is_active               BIT           NOT NULL DEFAULT 1,

    CONSTRAINT PK_activity PRIMARY KEY CLUSTERED (activity_id),
    CONSTRAINT UQ_activity_code UNIQUE (activity_code),
    CONSTRAINT FK_activity_rtype FOREIGN KEY (default_resource_type) REFERENCES ref.resource_type(resource_type_code),
    CONSTRAINT FK_activity_comp  FOREIGN KEY (component_code)        REFERENCES ref.automation_component(component_code)
);
GO

-- Seeded from what the FinSight code ACTUALLY does today (api/app/main.py
-- endpoints, the 4-step wizard in UserNewApplication.jsx, the compliance
-- maker-checker flow) plus the planned risk/fraud-fusion steps.
INSERT INTO pm.activity
    (activity_code, activity_name, process_code, activity_category, default_resource_type,
     component_code, is_milestone, is_value_adding, is_optional, is_rework_indicator,
     expected_duration_sec, normative_order)
VALUES
 -- ── Loan application: intake ───────────────────────────────────────────────
 ('APP_CREATE',        N'Application Created',           'loan_application', N'Intake',      'customer',    NULL,          1,1,0,0,   60,  10),
 ('PRODUCT_SELECT',    N'Loan Product Selected',         'loan_application', N'Intake',      'customer',    NULL,          0,1,0,0,   30,  20),
 ('REQUIREMENTS_VIEW', N'Requirements Reviewed',         'loan_application', N'Intake',      'customer',    NULL,          0,1,1,0,   45,  30),
 -- ── Loan application: documents ────────────────────────────────────────────
 ('DOC_UPLOAD',        N'Document Uploaded',             'loan_application', N'Documents',   'customer',    NULL,          1,1,0,0,   90,  40),
 ('DOC_PREPROCESS',    N'Document Preprocessed',         'document',         N'AI',          'system',      'preprocess',  0,1,0,0,    7,  50),
 ('DOC_EXTRACT',       N'Fields Extracted (OCR/VLM)',    'document',         N'AI',          'ml_model',    'ocr_extract', 1,1,0,0,   19,  60),
 ('DOC_GUARDRAILS',    N'Field Validation Executed',     'document',         N'AI',          'rule_engine', 'guardrails',  0,1,0,0,    1,  70),
 ('DOC_IDENTITY_CHECK',N'Document Identity Confirmed',   'document',         N'AI',          'rule_engine', 'cross_verify',0,1,0,0,    1,  75),
 ('DOC_TAMPER_SCAN',   N'Tampering Analysis Executed',   'document',         N'AI',          'ml_model',    'tampering',   1,1,0,0,   45,  80),
 ('DOC_TAMPER_EXPLAIN',N'Tampering Explanation Requested','document',        N'Review',      'human',       'tampering',   0,1,1,1,   10,  85),
 ('DOC_TAMPER_LOCATE', N'Tampering Locations Rendered',  'document',         N'Review',      'system',      'tampering',   0,1,1,1,    8,  86),
 ('DOC_MASK',          N'PII Masked',                    'document',         N'AI',          'system',      'pii_mask',    0,1,0,0,    3,  90),
 ('DOC_VERIFY',        N'Document Verified',             'document',         N'Documents',   'human',       NULL,          1,1,0,0,  120, 100),
 ('DOC_REJECT',        N'Document Rejected',             'document',         N'Documents',   'human',       NULL,          0,0,1,1,   90, 105),
 ('DOC_REQUEST_AGAIN', N'Document Re-requested',         'loan_application', N'Documents',   'human',       NULL,          0,0,1,1,   60, 106),
 ('DOC_RESUBMIT',      N'Document Resubmitted',          'loan_application', N'Documents',   'customer',    NULL,          0,0,1,1,   90, 107),
 -- ── Loan application: identity & cross-checks ──────────────────────────────
 ('FACE_VERIFY',       N'Face Verification Executed',    'loan_application', N'AI',          'ml_model',    'face_match',  1,1,0,0,    4, 110),
 ('CROSS_VERIFY',      N'Cross-Document Check Executed', 'loan_application', N'AI',          'rule_engine', 'cross_verify',0,1,0,0,    1, 115),
 ('DETAILS_CONFIRM',   N'Personal Details Confirmed',    'loan_application', N'Intake',      'customer',    NULL,          0,1,0,0,  300, 120),
 ('CONSENT_ACCEPT',    N'Consent & Declaration Accepted','loan_application', N'Intake',      'customer',    NULL,          0,1,0,0,   60, 125),
 ('APP_SUBMIT',        N'Application Submitted',         'loan_application', N'Intake',      'customer',    NULL,          1,1,0,0,   10, 130),
 -- ── Loan application: assessment ───────────────────────────────────────────
 ('APP_ASSIGN',        N'Assigned to Officer',           'loan_application', N'Review',      'system',      NULL,          1,1,0,0,   30, 140),
 ('APP_REASSIGN',      N'Reassigned to Another Officer', 'loan_application', N'Review',      'human',       NULL,          0,0,1,1,   60, 141),
 ('RISK_SCORE',        N'Risk Assessment Computed',      'loan_application', N'AI',          'ml_model',    'risk_model',  1,1,0,0,    1, 150),
 ('RISK_RESCORE',      N'Risk Re-assessed',              'loan_application', N'AI',          'ml_model',    'risk_model',  0,0,1,1,    1, 151),
 ('FRAUD_FUSION',      N'Fraud Score Fused',             'loan_application', N'AI',          'ml_model',    'fraud_fusion',1,1,0,0,    2, 155),
 ('COMPLIANCE_CHECK',  N'RBI Compliance Check Run',      'loan_application', N'Compliance',  'rule_engine', 'compliance',  1,1,0,0,    1, 160),
 ('COMPLIANCE_OVERRIDE',N'Compliance Result Overridden', 'loan_application', N'Compliance',  'human',       NULL,          0,0,1,1,  180, 161),
 ('OFFICER_REVIEW',    N'Officer Review',                'loan_application', N'Review',      'human',       NULL,          1,1,0,0,  900, 170),
 ('OFFICER_NOTE',      N'Officer Remark Added',          'loan_application', N'Review',      'human',       NULL,          0,1,1,0,  120, 171),
 ('HEAD_APPROVAL_REQ', N'Head Approval Requested',       'loan_application', N'Decision',    'human',       NULL,          0,1,1,0,   60, 180),
 ('HEAD_APPROVE',      N'Head Approved',                 'loan_application', N'Decision',    'human',       NULL,          1,1,1,0,  600, 181),
 ('HEAD_RETURN',       N'Returned by Head for Rework',   'loan_application', N'Decision',    'human',       NULL,          0,0,1,1,  300, 182),
 -- ── Loan application: outcome ──────────────────────────────────────────────
 ('OFFER_GENERATE',    N'Offer Generated',               'loan_application', N'Decision',    'system',      NULL,          1,1,0,0,   30, 190),
 ('OFFER_SEND',        N'Offer Sent to Customer',        'loan_application', N'Decision',    'system',      'notifier',    0,1,0,0,    5, 191),
 ('OFFER_VIEW',        N'Offer Viewed by Customer',      'loan_application', N'Decision',    'customer',    NULL,          0,1,1,0,   60, 192),
 ('OFFER_ACCEPT',      N'Offer Accepted',                'loan_application', N'Decision',    'customer',    NULL,          1,1,1,0,  120, 193),
 ('OFFER_REJECT',      N'Offer Rejected by Customer',    'loan_application', N'Decision',    'customer',    NULL,          1,0,1,0,  120, 194),
 ('OFFER_REVISE',      N'Offer Revised',                 'loan_application', N'Decision',    'human',       NULL,          0,0,1,1,  600, 195),
 ('APP_SANCTION',      N'Sanctioned',                    'loan_application', N'Fulfilment',  'human',       NULL,          1,1,1,0,  600, 200),
 ('DISBURSE_INITIATE', N'Disbursement Initiated',        'loan_application', N'Fulfilment',  'system',      NULL,          1,1,1,0,   60, 210),
 ('DISBURSE_SETTLE',   N'Disbursement Settled',          'loan_application', N'Fulfilment',  'external_api',NULL,          1,1,1,0, 3600, 211),
 ('DISBURSE_FAIL',     N'Disbursement Failed',           'loan_application', N'Fulfilment',  'external_api',NULL,          0,0,1,1,   60, 212),
 ('APP_REJECT',        N'Application Rejected',          'loan_application', N'Closed',      'human',       NULL,          1,1,1,0,  300, 220),
 ('APP_CANCEL',        N'Application Cancelled',         'loan_application', N'Closed',      'customer',    NULL,          1,0,1,0,   60, 221),
 ('APP_EXPIRE',        N'Application Expired',           'loan_application', N'Closed',      'system',      NULL,          1,0,1,0,    1, 222),
 -- ── Fraud investigation ────────────────────────────────────────────────────
 ('FRAUD_SIGNAL',      N'Fraud Signal Raised',           'fraud_case',       N'Fraud',       'ml_model',    'fraud_fusion',1,1,0,0,    1,  10),
 ('FRAUD_CASE_OPEN',   N'Fraud Case Opened',             'fraud_case',       N'Fraud',       'system',      NULL,          1,1,0,0,   10,  20),
 ('FRAUD_ASSIGN',      N'Fraud Case Assigned',           'fraud_case',       N'Fraud',       'system',      NULL,          0,1,0,0,   30,  30),
 ('FRAUD_TRIAGE',      N'Fraud Case Triaged',            'fraud_case',       N'Fraud',       'human',       NULL,          1,1,0,0, 1800,  40),
 ('FRAUD_ESCALATE',    N'Fraud Case Escalated',          'fraud_case',       N'Fraud',       'human',       NULL,          1,1,1,1,  300,  50),
 ('FRAUD_RESOLVE',     N'Fraud Case Resolved',           'fraud_case',       N'Fraud',       'human',       NULL,          1,1,0,0,  600,  60),
 ('FRAUD_REOPEN',      N'Fraud Case Reopened',           'fraud_case',       N'Fraud',       'human',       NULL,          0,0,1,1,  300,  61),
 -- ── Rule authoring (maker-checker) ─────────────────────────────────────────
 ('KB_INGEST',         N'Regulatory Document Ingested',  'rule_authoring',   N'Compliance',  'human',       NULL,          1,1,0,0,  300,  10),
 ('KB_INDEX',          N'Document Chunked & Indexed',    'rule_authoring',   N'Compliance',  'system',      NULL,          0,1,0,0,    5,  20),
 ('RULE_EXTRACT',      N'Rule Candidate Extracted',      'rule_authoring',   N'Compliance',  'ml_model',    'rule_extract',1,1,0,0,   20,  30),
 ('RULE_REVIEW_START', N'Candidate Review Started',      'rule_authoring',   N'Compliance',  'human',       NULL,          0,1,0,0,   60,  40),
 ('RULE_FIELD_MAP',    N'Condition Field Mapped',        'rule_authoring',   N'Compliance',  'human',       NULL,          0,1,0,0,  300,  50),
 ('RULE_APPROVE',      N'Rule Approved',                 'rule_authoring',   N'Compliance',  'human',       NULL,          1,1,1,0,  120,  60),
 ('RULE_REJECT',       N'Rule Rejected',                 'rule_authoring',   N'Compliance',  'human',       NULL,          1,1,1,0,  120,  61),
 ('RULE_DEACTIVATE',   N'Rule Deactivated',              'rule_authoring',   N'Compliance',  'human',       NULL,          0,1,1,0,   60,  70),
 -- ── Cross-cutting ──────────────────────────────────────────────────────────
 ('NOTIFY_SENT',       N'Notification Sent',             'loan_application', N'Communication','system',     'notifier',    0,0,1,0,    1, 900),
 ('CUSTOMER_CONTACT',  N'Customer Contacted',            'loan_application', N'Communication','human',      NULL,          0,1,1,0,  600, 901),
 ('CASE_SUSPEND',      N'Case Put On Hold',              'loan_application', N'Review',      'human',       NULL,          0,0,1,1,   60, 902),
 ('CASE_RESUME',       N'Case Resumed',                  'loan_application', N'Review',      'human',       NULL,          0,0,1,1,   60, 903);
GO

-- 1.3 · pm.process_edge ------------------------------------------------------
-- THE NORMATIVE MODEL, as a directly-follows edge set. Conformance checking =
-- comparing the edges the log actually contains against the edges declared
-- here. Anything in the log but not here is a deviation.
CREATE TABLE pm.process_edge (
    edge_id                 BIGINT      IDENTITY(1,1) NOT NULL,
    process_definition_id   BIGINT      NOT NULL,
    from_activity_id        INT         NULL,      -- NULL = process start
    to_activity_id          INT         NULL,      -- NULL = process end
    is_allowed              BIT         NOT NULL DEFAULT 1,
    is_happy_path           BIT         NOT NULL DEFAULT 0,
    max_expected_wait_sec   INT         NULL,      -- SLA on the TRANSITION itself
    notes                   NVARCHAR(300) NULL,

    CONSTRAINT PK_process_edge PRIMARY KEY CLUSTERED (edge_id),
    CONSTRAINT UQ_process_edge UNIQUE (process_definition_id, from_activity_id, to_activity_id),
    CONSTRAINT FK_edge_proc FOREIGN KEY (process_definition_id) REFERENCES pm.process_definition(process_definition_id),
    CONSTRAINT FK_edge_from FOREIGN KEY (from_activity_id)      REFERENCES pm.activity(activity_id),
    CONSTRAINT FK_edge_to   FOREIGN KEY (to_activity_id)        REFERENCES pm.activity(activity_id)
);
GO

-- The designed loan journey, as consecutive pairs. Everything a case does that
-- is NOT in this list comes back as a deviation — which is the point: this is
-- the "as designed" process that the "as executed" log gets compared against.
-- Adjust it as the real workflow settles; it is data, not code.
DECLARE @happy TABLE (ord INT IDENTITY(1,1), code VARCHAR(60));
INSERT INTO @happy (code) VALUES
    ('APP_CREATE'),('PRODUCT_SELECT'),('REQUIREMENTS_VIEW'),('DOC_UPLOAD'),
    ('DOC_PREPROCESS'),('DOC_EXTRACT'),('DOC_GUARDRAILS'),('DOC_IDENTITY_CHECK'),
    ('DOC_TAMPER_SCAN'),('DOC_MASK'),('DOC_VERIFY'),('FACE_VERIFY'),('CROSS_VERIFY'),
    ('DETAILS_CONFIRM'),('CONSENT_ACCEPT'),('APP_SUBMIT'),('APP_ASSIGN'),
    ('RISK_SCORE'),('FRAUD_FUSION'),('COMPLIANCE_CHECK'),('OFFICER_REVIEW'),
    ('HEAD_APPROVAL_REQ'),('HEAD_APPROVE'),('OFFER_GENERATE'),('OFFER_SEND'),
    ('OFFER_VIEW'),('OFFER_ACCEPT'),('APP_SANCTION'),('DISBURSE_INITIATE'),
    ('DISBURSE_SETTLE');

DECLARE @pd BIGINT = (SELECT process_definition_id FROM pm.process_definition
                      WHERE process_code = 'loan_application' AND process_version = '1.0');

INSERT INTO pm.process_edge (process_definition_id, from_activity_id, to_activity_id, is_allowed, is_happy_path)
SELECT @pd, af.activity_id, at2.activity_id, 1, 1
FROM   @happy h1
JOIN   @happy h2  ON h2.ord = h1.ord + 1
JOIN   pm.activity af  ON af.activity_code  = h1.code
JOIN   pm.activity at2 ON at2.activity_code = h2.code;

-- Start and end markers.
INSERT INTO pm.process_edge (process_definition_id, from_activity_id, to_activity_id, is_allowed, is_happy_path)
SELECT @pd, NULL, activity_id, 1, 1 FROM pm.activity WHERE activity_code = 'APP_CREATE'
UNION ALL
SELECT @pd, activity_id, NULL, 1, 1 FROM pm.activity
WHERE activity_code IN ('DISBURSE_SETTLE','APP_REJECT','APP_CANCEL','APP_EXPIRE');

-- Legitimate loops and alternative endings: allowed, but NOT happy path, so
-- they show up as tolerated variation rather than as violations.
INSERT INTO pm.process_edge (process_definition_id, from_activity_id, to_activity_id, is_allowed, is_happy_path, notes)
SELECT @pd, af.activity_id, at2.activity_id, 1, 0, m.note
FROM (VALUES
        ('DOC_VERIFY',      'DOC_UPLOAD',        N'Next document in the set'),
        ('DOC_UPLOAD',      'DOC_UPLOAD',        N'Multiple documents uploaded back to back'),
        ('DOC_TAMPER_SCAN', 'DOC_TAMPER_EXPLAIN',N'Reviewer asks for an explanation'),
        ('DOC_TAMPER_EXPLAIN','DOC_TAMPER_LOCATE',N'Reviewer asks where the issue is'),
        ('DOC_TAMPER_LOCATE','DOC_VERIFY',       N'Reviewer clears the document'),
        ('DOC_REJECT',      'DOC_REQUEST_AGAIN', N'Rework loop: bad document'),
        ('DOC_REQUEST_AGAIN','DOC_RESUBMIT',     N'Rework loop: borrower resubmits'),
        ('DOC_RESUBMIT',    'DOC_PREPROCESS',    N'Resubmitted document re-enters the pipeline'),
        ('OFFICER_REVIEW',  'DOC_REQUEST_AGAIN', N'Officer sends a document back'),
        ('OFFICER_REVIEW',  'OFFER_GENERATE',    N'Approved without head sign-off'),
        ('OFFICER_REVIEW',  'APP_REJECT',        N'Declined at review'),
        ('HEAD_RETURN',     'OFFICER_REVIEW',    N'Head returns for rework'),
        ('HEAD_APPROVAL_REQ','HEAD_RETURN',      N'Head returns instead of approving'),
        ('COMPLIANCE_CHECK','APP_REJECT',        N'Blocked by a mandatory rule'),
        ('FRAUD_FUSION',    'FRAUD_SIGNAL',      N'Fraud case spawned'),
        ('OFFER_VIEW',      'OFFER_REJECT',      N'Customer declines'),
        ('OFFER_REJECT',    'OFFER_REVISE',      N'Renegotiation'),
        ('OFFER_REVISE',    'OFFER_SEND',        N'Revised offer sent'),
        ('DISBURSE_INITIATE','DISBURSE_FAIL',    N'Payment rail failure'),
        ('DISBURSE_FAIL',   'DISBURSE_INITIATE', N'Retry after failure'),
        ('CASE_SUSPEND',    'CASE_RESUME',       N'On hold and resumed')
     ) AS m(from_code, to_code, note)
JOIN pm.activity af  ON af.activity_code  = m.from_code
JOIN pm.activity at2 ON at2.activity_code = m.to_code
-- Guard so the seed stays idempotent and an edge that is already part of the
-- happy path above is never inserted twice.
WHERE NOT EXISTS (SELECT 1 FROM pm.process_edge pe
                  WHERE pe.process_definition_id = @pd
                    AND pe.from_activity_id = af.activity_id
                    AND pe.to_activity_id   = at2.activity_id);
GO


/* ###########################################################################
   2. THE EVENT LOG  — the single most important table in this file
########################################################################### */

CREATE TABLE pm.event_log (
    /* ---- identity & ordering ------------------------------------------ */
    event_id                BIGINT           IDENTITY(1,1) NOT NULL,
    event_uid               UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    -- Idempotency key: the API can retry a call without double-logging an
    -- event. Duplicate events silently corrupt every frequency metric.
    idempotency_key         NVARCHAR(120)    NULL,

    process_code            VARCHAR(50)      NOT NULL,   -- which of the 4 processes
    process_definition_id   BIGINT           NULL,
    case_id                 UNIQUEIDENTIFIER NOT NULL,   -- application / doc / fraud case / candidate
    case_key                NVARCHAR(60)     NULL,       -- human-readable, e.g. 'APP-14261706'
    -- Explicit ordinal within the case. Timestamps tie (automated cascades can
    -- emit five events in the same millisecond); without this the discovered
    -- directly-follows graph is non-deterministic between runs.
    event_seq               INT              NOT NULL,

    /* ---- what happened ------------------------------------------------ */
    activity_id             INT              NOT NULL,
    -- Denormalised label. Renaming an activity in the dictionary must NOT
    -- silently rewrite history; the log keeps the name as it was logged.
    activity_name           NVARCHAR(120)    NOT NULL,
    lifecycle_transition    VARCHAR(20)      NOT NULL DEFAULT 'complete',
    -- Groups the start/complete pair of ONE execution. Required to pair them
    -- correctly when the same activity repeats within a case (XES concept:instance).
    activity_instance_id    UNIQUEIDENTIFIER NULL,

    /* ---- when -------------------------------------------------------- */
    occurred_at             DATETIME2(3)     NOT NULL,   -- business time (UTC)
    recorded_at             DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    timestamp_source        VARCHAR(20)      NOT NULL DEFAULT 'server',
    -- Interval form: many FinSight activities are logged once, on completion,
    -- with a measured duration (the `timings` dict). Storing the interval
    -- avoids forcing two rows per activity while keeping durations exact.
    queued_at               DATETIME2(3)     NULL,
    started_at              DATETIME2(3)     NULL,
    completed_at            DATETIME2(3)     NULL,
    duration_ms             BIGINT           NULL,   -- started -> completed (work)
    waiting_ms              BIGINT           NULL,   -- queued  -> started   (queue)
    business_duration_ms    BIGINT           NULL,   -- working-calendar adjusted
    -- Gap since the previous event of the same case. Precomputing it is the
    -- difference between an instant bottleneck query and a 40-second LAG scan.
    since_previous_ms       BIGINT           NULL,

    /* ---- who --------------------------------------------------------- */
    resource_id             UNIQUEIDENTIFIER NULL,      -- ops.users
    resource_name           NVARCHAR(150)    NULL,      -- snapshot
    resource_type_code      VARCHAR(30)      NOT NULL DEFAULT 'system',
    -- Snapshots: an officer promoted to head next month must not retroactively
    -- change who performed last month's activity.
    resource_role_snapshot  VARCHAR(20)      NULL,
    resource_team_snapshot  NVARCHAR(80)     NULL,
    resource_branch_snapshot NVARCHAR(20)    NULL,
    previous_resource_id    UNIQUEIDENTIFIER NULL,      -- handover-of-work edge
    is_handover             BIT              NOT NULL DEFAULT 0,
    on_behalf_of_id         UNIQUEIDENTIFIER NULL,      -- delegation
    session_id              UNIQUEIDENTIFIER NULL,      -- ops.user_sessions

    /* ---- automation / AI (FinSight-specific) -------------------------- */
    is_automated            BIT              NOT NULL DEFAULT 0,
    component_code          VARCHAR(40)      NULL,
    model_name              NVARCHAR(80)     NULL,      -- qwen2.5vl:7b, sface, cnn
    model_version           NVARCHAR(40)     NULL,      -- v009, claude-sonnet-4…
    ai_confidence           DECIMAL(5,4)     NULL,
    ai_outcome              NVARCHAR(40)     NULL,      -- pass / flag / fail / uncertain
    -- The headline metric for an AI-assisted process: how often does a human
    -- disagree with the machine, on which activity, and does it cost time?
    human_overrode_ai       BIT              NOT NULL DEFAULT 0,
    override_reason         NVARCHAR(300)    NULL,

    /* ---- process control --------------------------------------------- */
    from_status             VARCHAR(40)      NULL,
    to_status               VARCHAR(40)      NULL,
    wait_reason_code        VARCHAR(40)      NULL,
    is_rework               BIT              NOT NULL DEFAULT 0,
    attempt_no              SMALLINT         NOT NULL DEFAULT 1,
    rework_of_event_id      BIGINT           NULL,
    is_exception            BIT              NOT NULL DEFAULT 0,
    exception_type          NVARCHAR(60)     NULL,
    is_deviation            BIT              NOT NULL DEFAULT 0,   -- set by conformance run
    deviation_type          NVARCHAR(40)     NULL,                 -- unexpected / skipped / out_of_order

    /* ---- SLA & cost --------------------------------------------------- */
    sla_id                  BIGINT           NULL,
    sla_due_at              DATETIME2(3)     NULL,
    sla_met                 BIT              NULL,
    sla_breach_ms           BIGINT           NULL,
    activity_cost           DECIMAL(12,4)    NULL,      -- staff minutes x rate, or compute cost

    /* ---- context ------------------------------------------------------ */
    channel_code            VARCHAR(20)      NULL,
    source_system           NVARCHAR(40)     NULL,      -- frontend / api / tampering_module …
    correlation_id          UNIQUEIDENTIFIER NULL,      -- one HTTP request / trace
    parent_event_id         BIGINT           NULL,      -- nested activity
    environment_code        VARCHAR(20)      NOT NULL DEFAULT 'prod',
    is_valid_for_mining     BIT              NOT NULL DEFAULT 1,   -- exclude demo/broken rows
    attributes              NVARCHAR(MAX)    NULL,      -- free-form JSON event payload
    schema_version          SMALLINT         NOT NULL DEFAULT 1,

    CONSTRAINT PK_event_log PRIMARY KEY NONCLUSTERED (event_id),
    CONSTRAINT UQ_event_uid UNIQUE (event_uid),
    CONSTRAINT CK_event_attrs_json CHECK (attributes IS NULL OR ISJSON(attributes) = 1),
    CONSTRAINT FK_event_activity  FOREIGN KEY (activity_id)          REFERENCES pm.activity(activity_id),
    CONSTRAINT FK_event_lifecycle FOREIGN KEY (lifecycle_transition) REFERENCES ref.lifecycle_transition(transition_code),
    CONSTRAINT FK_event_tssource  FOREIGN KEY (timestamp_source)     REFERENCES ref.timestamp_source(source_code),
    CONSTRAINT FK_event_rtype     FOREIGN KEY (resource_type_code)   REFERENCES ref.resource_type(resource_type_code),
    CONSTRAINT FK_event_resource  FOREIGN KEY (resource_id)          REFERENCES ops.users(user_id),
    CONSTRAINT FK_event_prevres   FOREIGN KEY (previous_resource_id) REFERENCES ops.users(user_id),
    CONSTRAINT FK_event_session   FOREIGN KEY (session_id)           REFERENCES ops.user_sessions(session_id),
    CONSTRAINT FK_event_comp      FOREIGN KEY (component_code)       REFERENCES ref.automation_component(component_code),
    CONSTRAINT FK_event_wait      FOREIGN KEY (wait_reason_code)     REFERENCES ref.wait_reason(wait_reason_code),
    CONSTRAINT FK_event_channel   FOREIGN KEY (channel_code)         REFERENCES ref.channel(channel_code),
    CONSTRAINT FK_event_env       FOREIGN KEY (environment_code)     REFERENCES ref.environment(environment_code),
    CONSTRAINT FK_event_fromst    FOREIGN KEY (from_status)          REFERENCES ref.application_status(status_code),
    CONSTRAINT FK_event_tost      FOREIGN KEY (to_status)            REFERENCES ref.application_status(status_code),
    CONSTRAINT FK_event_procdef   FOREIGN KEY (process_definition_id) REFERENCES pm.process_definition(process_definition_id)
);
GO

/* CLUSTERED INDEX = the trace read pattern.
   Process mining almost never reads one event; it reads WHOLE CASES in order.
   Clustering on (process, case, time, seq) makes each trace one sequential
   range scan instead of a random-IO gather. This choice matters more than any
   other index in the schema. */
CREATE UNIQUE CLUSTERED INDEX CX_event_log_trace
    ON pm.event_log (process_code, case_id, occurred_at, event_seq, event_id);
GO

CREATE INDEX IX_event_activity  ON pm.event_log (activity_id, occurred_at)
    INCLUDE (case_id, duration_ms, waiting_ms, resource_id);
CREATE INDEX IX_event_resource  ON pm.event_log (resource_id, occurred_at)
    WHERE resource_id IS NOT NULL;
CREATE INDEX IX_event_time      ON pm.event_log (occurred_at);
CREATE INDEX IX_event_corr      ON pm.event_log (correlation_id) WHERE correlation_id IS NOT NULL;
CREATE INDEX IX_event_instance  ON pm.event_log (activity_instance_id) WHERE activity_instance_id IS NOT NULL;
CREATE INDEX IX_event_deviation ON pm.event_log (is_deviation, activity_id) WHERE is_deviation = 1;
CREATE UNIQUE INDEX UX_event_idempotency ON pm.event_log (idempotency_key) WHERE idempotency_key IS NOT NULL;
GO

/* COLUMNSTORE for the analytical side.
   Process-mining queries are overwhelmingly aggregations across millions of
   events (average duration per activity, counts per variant). A nonclustered
   columnstore index typically gives 5–20x on exactly that shape while the
   rowstore clustered index keeps trace reads fast. NVARCHAR(MAX) columns
   (attributes) cannot participate, so the column list is explicit. */
CREATE NONCLUSTERED COLUMNSTORE INDEX NCCI_event_log ON pm.event_log
(
    event_id, process_code, case_id, event_seq, activity_id, lifecycle_transition,
    occurred_at, duration_ms, waiting_ms, business_duration_ms, since_previous_ms,
    resource_id, resource_type_code, resource_role_snapshot, resource_branch_snapshot,
    is_automated, component_code, ai_confidence, human_overrode_ai,
    is_rework, attempt_no, is_exception, is_deviation, is_handover,
    sla_met, sla_breach_ms, activity_cost, environment_code, is_valid_for_mining
);
GO

/* PARTITIONING (optional, do it when the log passes ~50M rows):
   CREATE PARTITION FUNCTION PF_EventMonthly (DATETIME2(3)) AS RANGE RIGHT FOR VALUES (...);
   then rebuild CX_event_log_trace ON PS_EventMonthly(occurred_at) with
   occurred_at already in the key (it is). Left out by default because
   partitioning a small table only adds operational overhead. */


/* ###########################################################################
   3. OBJECT-CENTRIC LAYER (OCEL 2.0)
   ---------------------------------------------------------------------------
   Classic process mining forces every event into exactly ONE case. That is a
   poor fit here: "Document Uploaded" belongs to the application AND to the
   document; "Face Verification" spans SEVERAL documents at once. Flattening it
   to one case id either duplicates events (inflating counts) or loses the
   relationship — the well-known "convergence and divergence" problem.

   The object-centric model fixes it: an event links to any number of objects.
   You can still flatten to a classic log for tools that need one (that is what
   pm.event_log.case_id is), but the full truth is preserved here.
########################################################################### */

CREATE TABLE pm.object_type (
    object_type_code    VARCHAR(40)   NOT NULL PRIMARY KEY,
    display_name        NVARCHAR(80)  NOT NULL,
    source_table        NVARCHAR(128) NULL
);
GO
INSERT INTO pm.object_type VALUES
    ('application',     N'Loan Application',      N'core.loan_applications'),
    ('customer',        N'Customer',              N'core.customers'),
    ('document',        N'Uploaded Document',     N'core.uploaded_documents'),
    ('offer',           N'Loan Offer',            N'core.loan_offers'),
    ('disbursement',    N'Disbursement',          N'core.disbursements'),
    ('fraud_case',      N'Fraud Case',            N'fraud.fraud_cases'),
    ('fraud_ring',      N'Fraud Ring',            N'fraud.fraud_rings'),
    ('compliance_rule', N'Compliance Rule',       N'comp.compliance_rules'),
    ('rule_candidate',  N'Rule Candidate',        N'comp.compliance_rule_candidates'),
    ('regulation_doc',  N'Regulatory Document',   N'comp.compliance_documents'),
    ('resource',        N'Staff Resource',        N'ops.users');
GO

CREATE TABLE pm.object_instance (
    object_id           BIGINT           IDENTITY(1,1) NOT NULL,
    object_type_code    VARCHAR(40)      NOT NULL,
    natural_key         UNIQUEIDENTIFIER NOT NULL,   -- the PK in the source table
    display_key         NVARCHAR(80)     NULL,       -- e.g. 'APP-14261706'
    created_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    attributes          NVARCHAR(MAX)    NULL,

    CONSTRAINT PK_object_instance PRIMARY KEY CLUSTERED (object_id),
    CONSTRAINT UQ_object_natural UNIQUE (object_type_code, natural_key),
    CONSTRAINT FK_object_type FOREIGN KEY (object_type_code) REFERENCES pm.object_type(object_type_code),
    CONSTRAINT CK_object_json CHECK (attributes IS NULL OR ISJSON(attributes) = 1)
);
GO

CREATE TABLE pm.event_object_link (
    event_id            BIGINT       NOT NULL,
    object_id           BIGINT       NOT NULL,
    qualifier           NVARCHAR(40) NOT NULL DEFAULT 'related',  -- primary / input / output / affected

    CONSTRAINT PK_event_object_link PRIMARY KEY CLUSTERED (event_id, object_id, qualifier),
    CONSTRAINT FK_eol_event  FOREIGN KEY (event_id)  REFERENCES pm.event_log(event_id),
    CONSTRAINT FK_eol_object FOREIGN KEY (object_id) REFERENCES pm.object_instance(object_id)
);
GO
CREATE INDEX IX_eol_object ON pm.event_object_link(object_id, event_id);
GO

CREATE TABLE pm.object_relation (
    source_object_id    BIGINT       NOT NULL,
    target_object_id    BIGINT       NOT NULL,
    relation_type       NVARCHAR(40) NOT NULL,   -- belongs_to / derived_from / linked_by_face
    created_at          DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT PK_object_relation PRIMARY KEY CLUSTERED (source_object_id, target_object_id, relation_type),
    CONSTRAINT FK_orel_src FOREIGN KEY (source_object_id) REFERENCES pm.object_instance(object_id),
    CONSTRAINT FK_orel_tgt FOREIGN KEY (target_object_id) REFERENCES pm.object_instance(object_id)
);
GO


/* ###########################################################################
   4. VARIANTS, CASE METRICS, SLAs, CALENDAR, CONFORMANCE
########################################################################### */

-- 4.1 · pm.case_variant ------------------------------------------------------
-- A VARIANT is one distinct end-to-end path (the ordered list of activities).
-- Typically 80% of cases follow ~5 variants and the remaining 20% explode into
-- hundreds — finding that long tail is usually the first real insight a
-- process-mining project produces.
CREATE TABLE pm.case_variant (
    variant_id          BIGINT        IDENTITY(1,1) NOT NULL,
    process_code        VARCHAR(50)   NOT NULL,
    variant_hash        BINARY(32)    NOT NULL,   -- SHA2_256 of the activity sequence
    activity_sequence   NVARCHAR(MAX) NOT NULL,   -- 'APP_CREATE>DOC_UPLOAD>…'
    activity_count      INT           NOT NULL,
    case_count          INT           NOT NULL DEFAULT 0,
    avg_duration_sec    BIGINT        NULL,
    median_duration_sec BIGINT        NULL,
    min_duration_sec    BIGINT        NULL,
    max_duration_sec    BIGINT        NULL,
    approval_rate       DECIMAL(5,4)  NULL,   -- outcome per variant: the money question
    rework_rate         DECIMAL(5,4)  NULL,
    is_happy_path       BIT           NOT NULL DEFAULT 0,
    is_conformant       BIT           NULL,
    first_seen_at       DATETIME2(3)  NULL,
    last_seen_at        DATETIME2(3)  NULL,

    CONSTRAINT PK_case_variant PRIMARY KEY CLUSTERED (variant_id),
    CONSTRAINT UQ_variant_hash UNIQUE (process_code, variant_hash)
);
GO

ALTER TABLE core.loan_applications
    ADD CONSTRAINT FK_apps_variant FOREIGN KEY (variant_id) REFERENCES pm.case_variant(variant_id);
GO

-- 4.2 · pm.case_metrics ------------------------------------------------------
-- Per-case aggregates for the NON-loan processes (the loan case keeps its
-- metrics on core.loan_applications so officer screens don't need a join).
CREATE TABLE pm.case_metrics (
    process_code        VARCHAR(50)      NOT NULL,
    case_id             UNIQUEIDENTIFIER NOT NULL,
    variant_id          BIGINT           NULL,
    started_at          DATETIME2(3)     NULL,
    ended_at            DATETIME2(3)     NULL,
    duration_sec        BIGINT           NULL,
    business_duration_sec BIGINT         NULL,
    event_count         INT              NOT NULL DEFAULT 0,
    activity_count      INT              NOT NULL DEFAULT 0,
    touch_count         INT              NOT NULL DEFAULT 0,
    rework_count        INT              NOT NULL DEFAULT 0,
    handover_count      INT              NOT NULL DEFAULT 0,
    deviation_count     INT              NOT NULL DEFAULT 0,
    automation_ratio    DECIMAL(5,4)     NULL,
    total_cost          DECIMAL(14,4)    NULL,
    sla_breach_count    INT              NOT NULL DEFAULT 0,
    is_closed           BIT              NOT NULL DEFAULT 0,
    refreshed_at        DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT PK_case_metrics PRIMARY KEY CLUSTERED (process_code, case_id),
    CONSTRAINT FK_casemetrics_variant FOREIGN KEY (variant_id) REFERENCES pm.case_variant(variant_id)
);
GO

-- 4.3 · pm.sla_definition ----------------------------------------------------
CREATE TABLE pm.sla_definition (
    sla_id              BIGINT       IDENTITY(1,1) NOT NULL,
    sla_name            NVARCHAR(120) NOT NULL,
    process_code        VARCHAR(50)  NOT NULL,
    activity_id         INT          NULL,    -- NULL = whole-case SLA
    status_code         VARCHAR(40)  NULL,    -- or an SLA on time-in-status
    product_code        VARCHAR(40)  NULL,
    priority            SMALLINT     NULL,
    target_sec          BIGINT       NOT NULL,
    warning_sec         BIGINT       NULL,
    use_business_hours  BIT          NOT NULL DEFAULT 1,
    calendar_code       VARCHAR(40)  NULL,
    exclude_customer_wait BIT        NOT NULL DEFAULT 1,   -- don't penalise staff for customer delay
    is_active           BIT          NOT NULL DEFAULT 1,
    effective_from      DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
    effective_until     DATETIME2(3) NULL,

    CONSTRAINT PK_sla_definition PRIMARY KEY CLUSTERED (sla_id),
    CONSTRAINT FK_sla_activity FOREIGN KEY (activity_id)  REFERENCES pm.activity(activity_id),
    CONSTRAINT FK_sla_status   FOREIGN KEY (status_code)  REFERENCES ref.application_status(status_code),
    CONSTRAINT FK_sla_product  FOREIGN KEY (product_code) REFERENCES ref.loan_product_type(product_code)
);
GO
ALTER TABLE pm.event_log
    ADD CONSTRAINT FK_event_sla FOREIGN KEY (sla_id) REFERENCES pm.sla_definition(sla_id);
GO

-- 4.4 · pm.business_calendar + holidays --------------------------------------
-- WHY THIS EXISTS: a case submitted Friday 6pm and picked up Monday 9am shows
-- a 63-hour delay in wall-clock terms and ~1 working hour in reality. Without
-- a calendar, every weekend turns into a fake bottleneck and the real ones
-- disappear into the noise.
CREATE TABLE pm.calendar (
    calendar_code   VARCHAR(40)  NOT NULL,
    display_name    NVARCHAR(80) NOT NULL,
    timezone_name   NVARCHAR(60) NOT NULL DEFAULT 'India Standard Time',

    CONSTRAINT PK_calendar PRIMARY KEY CLUSTERED (calendar_code)
);
GO
INSERT INTO pm.calendar (calendar_code, display_name, timezone_name) VALUES
    ('IN_BRANCH', N'Indian Branch Hours', 'India Standard Time'),
    ('IN_24X7',   N'Always On (System)',  'India Standard Time');
GO

CREATE TABLE pm.business_calendar (
    calendar_code       VARCHAR(40)  NOT NULL,
    day_of_week         TINYINT      NOT NULL,   -- 1 = Sunday … 7 = Saturday (T-SQL DATEPART(dw) default)
    start_time          TIME(0)      NOT NULL,
    end_time            TIME(0)      NOT NULL,

    CONSTRAINT PK_business_calendar PRIMARY KEY CLUSTERED (calendar_code, day_of_week),
    CONSTRAINT FK_bizcal_calendar FOREIGN KEY (calendar_code) REFERENCES pm.calendar(calendar_code),
    CONSTRAINT CK_bizcal_dow CHECK (day_of_week BETWEEN 1 AND 7),
    CONSTRAINT CK_bizcal_span CHECK (end_time > start_time)
);
GO
INSERT INTO pm.business_calendar (calendar_code, day_of_week, start_time, end_time) VALUES
    ('IN_BRANCH', 2, '09:30', '18:00'),   -- Monday
    ('IN_BRANCH', 3, '09:30', '18:00'),
    ('IN_BRANCH', 4, '09:30', '18:00'),
    ('IN_BRANCH', 5, '09:30', '18:00'),
    ('IN_BRANCH', 6, '09:30', '18:00'),   -- Friday
    ('IN_BRANCH', 7, '09:30', '13:30'),   -- Saturday half day
    ('IN_24X7',   1, '00:00', '23:59'),
    ('IN_24X7',   2, '00:00', '23:59'),
    ('IN_24X7',   3, '00:00', '23:59'),
    ('IN_24X7',   4, '00:00', '23:59'),
    ('IN_24X7',   5, '00:00', '23:59'),
    ('IN_24X7',   6, '00:00', '23:59'),
    ('IN_24X7',   7, '00:00', '23:59');
GO

CREATE TABLE pm.calendar_holiday (
    calendar_code       VARCHAR(40)  NOT NULL,
    holiday_date        DATE         NOT NULL,
    holiday_name        NVARCHAR(100) NULL,

    CONSTRAINT PK_calendar_holiday PRIMARY KEY CLUSTERED (calendar_code, holiday_date),
    CONSTRAINT FK_holiday_calendar FOREIGN KEY (calendar_code) REFERENCES pm.calendar(calendar_code)
);
GO

ALTER TABLE ops.users
    ADD CONSTRAINT FK_users_calendar FOREIGN KEY (calendar_code) REFERENCES pm.calendar(calendar_code);
ALTER TABLE pm.sla_definition
    ADD CONSTRAINT FK_sla_calendar FOREIGN KEY (calendar_code) REFERENCES pm.calendar(calendar_code);
GO

-- 4.5 · pm.conformance_result + pm.deviation ---------------------------------
CREATE TABLE pm.conformance_result (
    conformance_id      BIGINT           IDENTITY(1,1) NOT NULL,
    process_definition_id BIGINT         NOT NULL,
    case_id             UNIQUEIDENTIFIER NOT NULL,
    fitness             DECIMAL(5,4)     NULL,   -- how much of the trace the model explains
    precision_score     DECIMAL(5,4)     NULL,   -- how much extra behaviour the model allows
    deviation_count     INT              NOT NULL DEFAULT 0,
    missing_activities  NVARCHAR(MAX)    NULL,   -- JSON array
    extra_activities    NVARCHAR(MAX)    NULL,   -- JSON array
    is_conformant       BIT              NOT NULL DEFAULT 1,
    computed_at         DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT PK_conformance_result PRIMARY KEY CLUSTERED (conformance_id),
    CONSTRAINT UQ_conformance_case UNIQUE (process_definition_id, case_id),
    CONSTRAINT FK_conf_proc FOREIGN KEY (process_definition_id) REFERENCES pm.process_definition(process_definition_id)
);
GO

CREATE TABLE pm.deviation (
    deviation_id        BIGINT           IDENTITY(1,1) NOT NULL,
    conformance_id      BIGINT           NOT NULL,
    event_id            BIGINT           NULL,
    deviation_type      NVARCHAR(40)     NOT NULL,   -- unexpected_activity / skipped / out_of_order / sla_breach
    expected_activity_id INT             NULL,
    actual_activity_id  INT              NULL,
    severity            VARCHAR(20)      NOT NULL DEFAULT 'medium',
    detail              NVARCHAR(500)    NULL,

    CONSTRAINT PK_deviation PRIMARY KEY CLUSTERED (deviation_id),
    CONSTRAINT FK_dev_conf  FOREIGN KEY (conformance_id) REFERENCES pm.conformance_result(conformance_id),
    CONSTRAINT FK_dev_event FOREIGN KEY (event_id)       REFERENCES pm.event_log(event_id),
    CONSTRAINT FK_dev_exp   FOREIGN KEY (expected_activity_id) REFERENCES pm.activity(activity_id),
    CONSTRAINT FK_dev_act   FOREIGN KEY (actual_activity_id)   REFERENCES pm.activity(activity_id),
    CONSTRAINT FK_dev_sev   FOREIGN KEY (severity)       REFERENCES ref.risk_level(risk_level_code)
);
GO

-- 4.6 · pm.dfg_snapshot ------------------------------------------------------
-- A materialised directly-follows graph. Recomputing the DFG from scratch on
-- every dashboard render is the usual reason process-mining dashboards feel
-- slow; refresh this nightly (or per filter set) and read it instantly.
CREATE TABLE pm.dfg_snapshot (
    snapshot_id         BIGINT       IDENTITY(1,1) NOT NULL,
    process_code        VARCHAR(50)  NOT NULL,
    filter_hash         BINARY(32)   NOT NULL,   -- identifies the slice (product, branch, period)
    filter_json         NVARCHAR(MAX) NULL,
    from_activity_id    INT          NULL,
    to_activity_id      INT          NULL,
    transition_count    BIGINT       NOT NULL,
    case_count          BIGINT       NOT NULL,
    avg_wait_sec        BIGINT       NULL,
    median_wait_sec     BIGINT       NULL,
    p90_wait_sec        BIGINT       NULL,
    max_wait_sec        BIGINT       NULL,
    is_deviation_edge   BIT          NOT NULL DEFAULT 0,
    computed_at         DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),

    CONSTRAINT PK_dfg_snapshot PRIMARY KEY CLUSTERED (snapshot_id),
    CONSTRAINT FK_dfg_from FOREIGN KEY (from_activity_id) REFERENCES pm.activity(activity_id),
    CONSTRAINT FK_dfg_to   FOREIGN KEY (to_activity_id)   REFERENCES pm.activity(activity_id)
);
GO
CREATE INDEX IX_dfg_lookup ON pm.dfg_snapshot(process_code, filter_hash, from_activity_id, to_activity_id);
GO

-- 4.7 · pm.export_run --------------------------------------------------------
-- Bookkeeping for extracts to external tools (PM4Py, Celonis, Disco, ProM).
-- Also gives incremental export a watermark instead of re-exporting everything.
CREATE TABLE pm.export_run (
    export_id           BIGINT       IDENTITY(1,1) NOT NULL,
    process_code        VARCHAR(50)  NOT NULL,
    export_format       NVARCHAR(20) NOT NULL,   -- xes / csv / ocel / parquet
    target_tool         NVARCHAR(40) NULL,
    filter_json         NVARCHAR(MAX) NULL,
    from_event_id       BIGINT       NULL,
    to_event_id         BIGINT       NULL,
    case_count          INT          NULL,
    event_count         INT          NULL,
    exported_by         UNIQUEIDENTIFIER NULL,
    started_at          DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
    completed_at        DATETIME2(3) NULL,
    status              NVARCHAR(20) NOT NULL DEFAULT 'running',

    CONSTRAINT PK_export_run PRIMARY KEY CLUSTERED (export_id),
    CONSTRAINT FK_export_user FOREIGN KEY (exported_by) REFERENCES ops.users(user_id)
);
GO

PRINT 'FILE 3 complete — process-mining layer: event log, OCEL objects, variants, SLA, calendar, conformance.';
GO
