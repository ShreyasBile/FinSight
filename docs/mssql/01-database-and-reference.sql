/* ============================================================================
   FinSight AI  ·  SQL Server (SSMS) Schema
   FILE 1 of 5 — Database, schemas, and reference (lookup) tables
   ----------------------------------------------------------------------------
   Target   : SQL Server 2017+  (2019+ recommended; STRING_AGG needs 2017,
              ISJSON needs 2016, PERCENTILE_CONT needs 2012)
   Run order: 01 → 02 → 03 → 04 → 05
   Run in   : SSMS, with "SQLCMD Mode" OFF. Each GO is a batch separator.

   WHY LOOKUP TABLES INSTEAD OF ENUMS
   PostgreSQL ENUMs have no direct T-SQL equivalent. The two options are a
   CHECK constraint (cheap, but the list is frozen in DDL and carries no
   metadata) or a reference table (a real FK, extensible without ALTER, and —
   the deciding factor here — it can carry PROCESS-MINING METADATA. e.g.
   ref.application_status.sort_order gives the normative order of the loan
   process, and is_terminal defines when a case ends. Conformance checking and
   case-duration calculation both need exactly that, and a CHECK constraint
   cannot hold it.

   ALL TIMESTAMPS ARE UTC (DATETIME2(3), DEFAULT SYSUTCDATETIME()).
   Process mining breaks badly on mixed timezones; convert to local only in
   the presentation layer or via pm.business_calendar.
============================================================================ */

IF DB_ID('FinSight') IS NULL
    CREATE DATABASE FinSight;
GO

-- Run from master (before USE): readers never block writers, which matters
-- once the event log is being written continuously and queried for dashboards.
ALTER DATABASE FinSight SET READ_COMMITTED_SNAPSHOT ON WITH ROLLBACK IMMEDIATE;
GO

USE FinSight;
GO

-- SSMS sets these ON by default; sqlcmd does not. Filtered indexes, indexed
-- views and JSON checks all require QUOTED_IDENTIFIER ON, so set it explicitly
-- and the script behaves identically however it is run.
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

/* ---------------------------------------------------------------------------
   1. SCHEMAS  (SSMS Object Explorer groups by schema — with ~50 tables this
      is the difference between a usable tree and a wall of names)
--------------------------------------------------------------------------- */
IF SCHEMA_ID('ref')   IS NULL EXEC('CREATE SCHEMA ref');    -- lookup / reference data
GO
IF SCHEMA_ID('core')  IS NULL EXEC('CREATE SCHEMA core');   -- customers, applications, documents
GO
IF SCHEMA_ID('fraud') IS NULL EXEC('CREATE SCHEMA fraud');  -- fraud cases, signals, rings
GO
IF SCHEMA_ID('comp')  IS NULL EXEC('CREATE SCHEMA comp');   -- RBI compliance / knowledge base
GO
IF SCHEMA_ID('ops')   IS NULL EXEC('CREATE SCHEMA ops');    -- users, notifications, products
GO
IF SCHEMA_ID('risk')  IS NULL EXEC('CREATE SCHEMA risk');   -- risk & pricing
GO
IF SCHEMA_ID('audit') IS NULL EXEC('CREATE SCHEMA audit');  -- row-level audit trail
GO
IF SCHEMA_ID('pm')    IS NULL EXEC('CREATE SCHEMA pm');     -- process mining
GO


/* ###########################################################################
   2. REFERENCE TABLES  (replacing the 14 PostgreSQL ENUMs)
########################################################################### */

-- 2.1 · application_status ---------------------------------------------------
-- sort_order      : normative position in the loan journey (conformance base)
-- stage_group     : coarse phase, for high-level process maps
-- is_terminal     : marks case completion (case_ended_at / duration)
-- is_happy_path   : part of the designed "golden path"; deviations are the
--                   thing process mining is meant to surface
-- target_hours    : default SLA for time-in-state analysis
CREATE TABLE ref.application_status (
    status_code     VARCHAR(40)   NOT NULL PRIMARY KEY,
    display_name    NVARCHAR(80)  NOT NULL,
    stage_group     NVARCHAR(40)  NOT NULL,
    sort_order      SMALLINT      NOT NULL,
    is_terminal     BIT           NOT NULL DEFAULT 0,
    is_happy_path   BIT           NOT NULL DEFAULT 1,
    is_waiting_state BIT          NOT NULL DEFAULT 0,   -- clock runs but nobody is working
    target_hours    DECIMAL(8,2)  NULL
);
GO

INSERT INTO ref.application_status
    (status_code, display_name, stage_group, sort_order, is_terminal, is_happy_path, is_waiting_state, target_hours)
VALUES
    ('draft',                  N'Draft',                     N'Intake',        10, 0, 1, 1, NULL),
    ('submitted',              N'Submitted',                 N'Intake',        20, 0, 1, 0,   2),
    ('documentsProcessing',    N'Documents Processing',      N'Documents',     30, 0, 1, 0,   1),
    ('documentsVerified',      N'Documents Verified',        N'Documents',     40, 0, 1, 0,   4),
    ('goldValuationScheduled', N'Gold Valuation Scheduled',  N'Verification',  50, 0, 0, 1,  24),
    ('goldValued',             N'Gold Valued',               N'Verification',  55, 0, 0, 0,   4),
    ('fieldVerification',      N'Field Verification',        N'Verification',  60, 0, 0, 0,  48),
    ('propertyValuation',      N'Property Valuation',        N'Verification',  65, 0, 0, 0,  72),
    ('legalVerification',      N'Legal Verification',        N'Verification',  70, 0, 0, 0,  72),
    ('incomeVerification',     N'Income Verification',       N'Verification',  75, 0, 1, 0,  24),
    ('propertyDocReview',      N'Property Document Review',  N'Verification',  80, 0, 0, 0,  48),
    ('legalCheck',             N'Legal Check',               N'Verification',  85, 0, 0, 0,  48),
    ('technicalValuation',     N'Technical Valuation',       N'Verification',  90, 0, 0, 0,  72),
    ('bankGstAnalysis',        N'Bank / GST Analysis',       N'Verification',  95, 0, 0, 0,  24),
    ('vehicleInspection',      N'Vehicle Inspection',        N'Verification', 100, 0, 0, 0,  48),
    ('underwritingReview',     N'Underwriting Review',       N'Underwriting', 110, 0, 1, 0,  24),
    ('underReview',            N'Under Review',              N'Underwriting', 115, 0, 0, 0,  24),
    ('offerGenerated',         N'Offer Generated',           N'Decision',     120, 0, 1, 0,   4),
    ('accepted',               N'Offer Accepted',            N'Decision',     130, 0, 1, 1, 168),
    ('sanctioned',             N'Sanctioned',                N'Fulfilment',   140, 0, 1, 0,  24),
    ('disbursed',              N'Disbursed',                 N'Fulfilment',   150, 1, 1, 0,  48),
    ('rejected',               N'Rejected',                  N'Closed',       200, 1, 0, 0, NULL),
    ('cancelled',              N'Cancelled',                 N'Closed',       210, 1, 0, 0, NULL),
    ('expired',                N'Expired',                   N'Closed',       220, 1, 0, 0, NULL);
GO

-- 2.2 · loan_product_type ----------------------------------------------------
CREATE TABLE ref.loan_product_type (
    product_code    VARCHAR(40)   NOT NULL PRIMARY KEY,
    display_name    NVARCHAR(80)  NOT NULL,
    is_secured      BIT           NOT NULL DEFAULT 0
);
GO
INSERT INTO ref.loan_product_type (product_code, display_name, is_secured) VALUES
    ('gold',               N'Gold Loan',           1),
    ('home_loan',          N'Home Loan',           1),
    ('lap',                N'Loan Against Property',1),
    ('tractor',            N'Tractor Loan',        1),
    ('microfinance',       N'Microfinance',        0),
    ('msme',               N'MSME Loan',           0),
    ('working_capital',    N'Working Capital',     0),
    ('commercial_vehicle', N'Commercial Vehicle',  1),
    ('used_vehicle',       N'Used Vehicle Loan',   1),
    ('two_wheeler',        N'Two Wheeler Loan',    1),
    ('personal_loan',      N'Personal Loan',       0),
    ('education_loan',     N'Education Loan',      0);
GO

-- 2.3 · user_role ------------------------------------------------------------
CREATE TABLE ref.user_role (
    role_code       VARCHAR(20)   NOT NULL PRIMARY KEY,
    display_name    NVARCHAR(60)  NOT NULL,
    hierarchy_level SMALLINT      NOT NULL DEFAULT 0   -- org mining: who escalates to whom
);
GO
INSERT INTO ref.user_role (role_code, display_name, hierarchy_level) VALUES
    ('user',    N'Borrower',             0),
    ('officer', N'Underwriting Officer', 1),
    ('head',    N'Head Manager',         2),
    ('admin',   N'Administrator',        3);
GO

-- 2.4 · document_type --------------------------------------------------------
-- The extra flags mirror api/app/doc_types.py + the tampering config, so the
-- event log can answer "how long do IDENTITY documents take vs INCOME ones?"
CREATE TABLE ref.document_type (
    document_type_code  VARCHAR(40)  NOT NULL PRIMARY KEY,
    display_name        NVARCHAR(80) NOT NULL,
    is_identity_doc     BIT          NOT NULL DEFAULT 0,
    expects_face        BIT          NOT NULL DEFAULT 0,
    is_income_doc       BIT          NOT NULL DEFAULT 0,
    always_escalate     BIT          NOT NULL DEFAULT 0,  -- never fast-tracked by the tampering gate
    typical_processing_ms INT        NULL                 -- baseline for anomaly detection
);
GO
INSERT INTO ref.document_type
    (document_type_code, display_name, is_identity_doc, expects_face, is_income_doc, always_escalate, typical_processing_ms)
VALUES
    ('aadhaar',                N'Aadhaar Card',           1,1,0,1, 12000),
    ('pan',                    N'PAN Card',               1,1,0,1,  9000),
    ('passport',               N'Passport',               1,1,0,1, 12000),
    ('voter_id',               N'Voter ID',               1,1,0,1, 10000),
    ('driving_license',        N'Driving Licence',        1,1,0,1, 10000),
    ('bank_statement',         N'Bank Statement',         0,0,1,0, 30000),
    ('salary_slip',            N'Salary Slip',            0,0,1,0, 18000),
    ('itr',                    N'Income Tax Return',      0,0,1,0, 25000),
    ('property_document',      N'Property Document',      0,0,0,0, 30000),
    ('valuation_report',       N'Valuation Report',       0,0,0,0, 25000),
    ('vehicle_rc',             N'Vehicle RC',             0,0,0,0, 12000),
    ('insurance_certificate',  N'Insurance Certificate',  0,0,0,0, 12000),
    ('business_proof',         N'Business Proof',         0,0,0,0, 15000),
    ('gst_certificate',        N'GST Certificate',        0,0,1,0, 15000),
    ('photograph',             N'Photograph',             1,1,0,0,  5000),
    ('other',                  N'Other',                  0,0,0,0, 15000);
GO

-- 2.5 · document_status ------------------------------------------------------
CREATE TABLE ref.document_status (
    status_code   VARCHAR(30)  NOT NULL PRIMARY KEY,
    display_name  NVARCHAR(60) NOT NULL,
    is_terminal   BIT          NOT NULL DEFAULT 0,
    is_rework_trigger BIT      NOT NULL DEFAULT 0   -- 'rejected'/'resubmitted' start a rework loop
);
GO
INSERT INTO ref.document_status (status_code, display_name, is_terminal, is_rework_trigger) VALUES
    ('pending',     N'Pending',     0,0),
    ('processing',  N'Processing',  0,0),
    ('verified',    N'Verified',    1,0),
    ('rejected',    N'Rejected',    0,1),
    ('resubmitted', N'Resubmitted', 0,1),
    ('expired',     N'Expired',     1,0);
GO

-- 2.6 · risk_level / fraud_severity / fraud_status ---------------------------
CREATE TABLE ref.risk_level (
    risk_level_code VARCHAR(20)  NOT NULL PRIMARY KEY,
    display_name    NVARCHAR(40) NOT NULL,
    sort_order      SMALLINT     NOT NULL
);
GO
INSERT INTO ref.risk_level VALUES
    ('low',N'Low',1),('medium',N'Medium',2),('high',N'High',3),('critical',N'Critical',4);
GO

CREATE TABLE ref.fraud_severity (
    severity_code VARCHAR(20)  NOT NULL PRIMARY KEY,
    display_name  NVARCHAR(40) NOT NULL,
    sort_order    SMALLINT     NOT NULL,
    target_resolution_hours DECIMAL(8,2) NULL
);
GO
INSERT INTO ref.fraud_severity VALUES
    ('low',N'Low',1,168),('medium',N'Medium',2,72),('high',N'High',3,24),('critical',N'Critical',4,4);
GO

CREATE TABLE ref.fraud_status (
    status_code  VARCHAR(20)  NOT NULL PRIMARY KEY,
    display_name NVARCHAR(40) NOT NULL,
    is_terminal  BIT          NOT NULL DEFAULT 0
);
GO
INSERT INTO ref.fraud_status VALUES
    ('open',N'Open',0),('investigating',N'Investigating',0),
    ('confirmed',N'Confirmed',1),('dismissed',N'Dismissed',1),('escalated',N'Escalated',0);
GO

-- 2.7 · compliance reference -------------------------------------------------
CREATE TABLE ref.rule_type (
    rule_type_code VARCHAR(30)  NOT NULL PRIMARY KEY,
    display_name   NVARCHAR(60) NOT NULL,
    blocks_approval BIT         NOT NULL DEFAULT 0
);
GO
INSERT INTO ref.rule_type VALUES
    ('mandatory',N'Mandatory (Regulatory)',1),
    ('internal_policy',N'Internal Policy',0),
    ('advisory',N'Advisory',0);
GO

CREATE TABLE ref.rule_category (
    rule_category_code VARCHAR(30)  NOT NULL PRIMARY KEY,
    display_name       NVARCHAR(60) NOT NULL
);
GO
INSERT INTO ref.rule_category VALUES
    ('kyc',N'KYC'),('digital_lending',N'Digital Lending'),
    ('fair_practices',N'Fair Practices'),('credit_risk',N'Credit Risk'),
    ('fraud_risk',N'Fraud Risk'),('general',N'General');
GO

CREATE TABLE ref.compliance_check_status (
    status_code  VARCHAR(30)  NOT NULL PRIMARY KEY,
    display_name NVARCHAR(60) NOT NULL,
    is_failure   BIT          NOT NULL DEFAULT 0
);
GO
INSERT INTO ref.compliance_check_status VALUES
    ('pass',N'Pass',0),('fail',N'Fail',1),('warning',N'Warning',0),
    ('inconclusive',N'Inconclusive',0),('not_applicable',N'Not Applicable',0);
GO

-- 2.8 · notification reference -----------------------------------------------
CREATE TABLE ref.notification_channel (
    channel_code VARCHAR(20)  NOT NULL PRIMARY KEY,
    display_name NVARCHAR(40) NOT NULL
);
GO
INSERT INTO ref.notification_channel VALUES
    ('in_app',N'In-App'),('email',N'Email'),('sms',N'SMS'),('whatsapp',N'WhatsApp');
GO

CREATE TABLE ref.notification_priority (
    priority_code VARCHAR(20)  NOT NULL PRIMARY KEY,
    display_name  NVARCHAR(40) NOT NULL,
    sort_order    SMALLINT     NOT NULL
);
GO
INSERT INTO ref.notification_priority VALUES
    ('low',N'Low',1),('medium',N'Medium',2),('high',N'High',3),('urgent',N'Urgent',4);
GO

-- 2.9 · business_event_type --------------------------------------------------
CREATE TABLE ref.business_event_type (
    event_type_code VARCHAR(40)  NOT NULL PRIMARY KEY,
    display_name    NVARCHAR(80) NOT NULL
);
GO
INSERT INTO ref.business_event_type (event_type_code, display_name) VALUES
    ('application_created',N'Application Created'),
    ('application_submitted',N'Application Submitted'),
    ('document_uploaded',N'Document Uploaded'),
    ('document_verified',N'Document Verified'),
    ('document_rejected',N'Document Rejected'),
    ('offer_generated',N'Offer Generated'),
    ('offer_accepted',N'Offer Accepted'),
    ('offer_rejected',N'Offer Rejected'),
    ('disbursed',N'Disbursed'),
    ('fraud_detected',N'Fraud Detected'),
    ('fraud_escalated',N'Fraud Escalated'),
    ('compliance_check',N'Compliance Check'),
    ('rule_proposed',N'Rule Proposed'),
    ('rule_approved',N'Rule Approved'),
    ('rule_rejected',N'Rule Rejected'),
    ('user_login',N'User Login'),
    ('user_logout',N'User Logout'),
    ('threshold_changed',N'Threshold Changed'),
    ('pricing_override',N'Pricing Override'),
    ('report_generated',N'Report Generated');
GO


/* ###########################################################################
   3. PROCESS-MINING REFERENCE TABLES  (new — no PostgreSQL equivalent)
########################################################################### */

-- 3.1 · lifecycle_transition -------------------------------------------------
-- The XES standard lifecycle model. WHY IT MATTERS: with only "complete"
-- events you can measure the gap BETWEEN activities, but never how long an
-- activity itself took. Logging schedule → assign → start → complete splits
-- that gap into QUEUE time, ASSIGNMENT time, and actual WORK time — which is
-- the whole point of a bottleneck analysis.
CREATE TABLE ref.lifecycle_transition (
    transition_code VARCHAR(20)  NOT NULL PRIMARY KEY,
    display_name    NVARCHAR(40) NOT NULL,
    sort_order      SMALLINT     NOT NULL,
    is_completion   BIT          NOT NULL DEFAULT 0
);
GO
INSERT INTO ref.lifecycle_transition VALUES
    ('schedule',   N'Scheduled',    1,0),
    ('assign',     N'Assigned',     2,0),
    ('start',      N'Started',      3,0),
    ('suspend',    N'Suspended',    4,0),
    ('resume',     N'Resumed',      5,0),
    ('complete',   N'Completed',    6,1),
    ('abort',      N'Aborted',      7,1),
    ('manualskip', N'Skipped (Manual)', 8,1),
    ('autoskip',   N'Skipped (Auto)',   9,1);
GO

-- 3.2 · resource_type --------------------------------------------------------
-- FinSight is a HUMAN + AI process. Separating a Qwen2.5-VL extraction from an
-- officer's review is what lets you answer "how much of the cycle time is
-- machine, how much is human, and where do humans override the machine?"
CREATE TABLE ref.resource_type (
    resource_type_code VARCHAR(30)  NOT NULL PRIMARY KEY,
    display_name       NVARCHAR(60) NOT NULL,
    is_automated       BIT          NOT NULL DEFAULT 0
);
GO
INSERT INTO ref.resource_type VALUES
    ('human',        N'Human (Staff)',        0),
    ('customer',     N'Customer / Borrower',  0),
    ('ml_model',     N'ML / Vision Model',    1),
    ('rule_engine',  N'Rule Engine',          1),
    ('system',       N'System / Scheduler',   1),
    ('external_api', N'External API',         1);
GO

-- 3.3 · automation_component -------------------------------------------------
-- One row per FinSight backend module that performs work. This is what turns
-- "processing took 41s" into "preprocess 6s, extract 19s, tampering 14s".
CREATE TABLE ref.automation_component (
    component_code   VARCHAR(40)  NOT NULL PRIMARY KEY,
    display_name     NVARCHAR(80) NOT NULL,
    module_path      NVARCHAR(200) NULL,
    resource_type_code VARCHAR(30) NOT NULL
        REFERENCES ref.resource_type(resource_type_code),
    is_blocking      BIT          NOT NULL DEFAULT 1,  -- does the borrower wait on it?
    typical_ms       INT          NULL
);
GO
INSERT INTO ref.automation_component
    (component_code, display_name, module_path, resource_type_code, is_blocking, typical_ms)
VALUES
    ('preprocess',   N'Image Preprocessing',      N'api/app/preprocess.py',            'system',      1,  7000),
    ('ocr_extract',  N'Document Extraction (VLM)',N'api/app/providers/',               'ml_model',    1, 19000),
    ('guardrails',   N'Field Guardrails',         N'api/app/guardrails.py',            'rule_engine', 1,   150),
    ('tampering',    N'Tampering Cascade',        N'api/app/tampering/service.py',     'ml_model',    0, 45000),
    ('authenticity', N'Authenticity (Vision LLM)',N'api/app/tampering/signals/authenticity.py','ml_model',0, 12000),
    ('face_match',   N'Face Verification',        N'api/app/face/service.py',          'ml_model',    0,  4000),
    ('pii_mask',     N'PII Masking',              N'api/app/mask.py',                  'system',      0,  3000),
    ('cross_verify', N'Cross-Document Verification',N'src/utils/docVerification.js',   'rule_engine', 1,    50),
    ('compliance',   N'RBI Compliance Engine',    N'api/app/compliance/rules_engine.py','rule_engine',1,   400),
    ('rule_extract', N'Rule Candidate Extraction',N'api/app/compliance/extraction.py', 'ml_model',    0, 20000),
    ('risk_model',   N'Risk Scoring',             N'(planned)',                        'ml_model',    1,  1000),
    ('fraud_fusion', N'Fraud Fusion',             N'(planned)',                        'ml_model',    1,  2000),
    ('storage',      N'Audit Store Write',        N'api/app/storage.py',               'system',      0,  1200),
    ('notifier',     N'Notification Dispatch',    N'(planned)',                        'system',      0,   300);
GO

-- 3.4 · wait_reason ----------------------------------------------------------
-- Bottleneck analysis is only actionable if you know WHY the clock was
-- running. "3 days in fieldVerification" is a fact; "3 days waiting on the
-- external valuation agency" is a decision.
CREATE TABLE ref.wait_reason (
    wait_reason_code VARCHAR(40)  NOT NULL PRIMARY KEY,
    display_name     NVARCHAR(80) NOT NULL,
    is_customer_side BIT          NOT NULL DEFAULT 0,  -- excluded from internal SLA
    is_external      BIT          NOT NULL DEFAULT 0
);
GO
INSERT INTO ref.wait_reason VALUES
    ('none',                 N'Not Waiting',                   0,0),
    ('awaiting_customer_doc',N'Awaiting Customer Document',    1,0),
    ('awaiting_customer_ack',N'Awaiting Customer Acceptance',  1,0),
    ('officer_queue',        N'In Officer Queue',              0,0),
    ('head_approval_queue',  N'Awaiting Head Approval',        0,0),
    ('fraud_investigation',  N'Held for Fraud Investigation',  0,0),
    ('compliance_hold',      N'Compliance Hold',               0,0),
    ('external_valuation',   N'Awaiting External Valuation',   0,1),
    ('external_legal',       N'Awaiting Legal Opinion',        0,1),
    ('external_bureau',      N'Awaiting Credit Bureau',        0,1),
    ('model_queue',          N'Queued for Model Inference',    0,0),
    ('after_hours',          N'Outside Business Hours',        0,0),
    ('system_retry',         N'System Retry / Error Backoff',  0,0);
GO

-- 3.5 · channel --------------------------------------------------------------
CREATE TABLE ref.channel (
    channel_code VARCHAR(20)  NOT NULL PRIMARY KEY,
    display_name NVARCHAR(40) NOT NULL
);
GO
INSERT INTO ref.channel VALUES
    ('web',N'Web App'),('mobile',N'Mobile'),('branch',N'Branch Walk-In'),
    ('api',N'Partner API'),('batch',N'Batch / Import'),('system',N'System');
GO

-- 3.6 · timestamp_source -----------------------------------------------------
-- DATA QUALITY. Process-mining output is only as trustworthy as its clocks.
-- An "inferred" timestamp (back-filled during migration) must be visibly
-- distinguishable from a server-stamped one, or every duration you compute is
-- quietly fiction.
CREATE TABLE ref.timestamp_source (
    source_code  VARCHAR(20)  NOT NULL PRIMARY KEY,
    display_name NVARCHAR(60) NOT NULL,
    is_trusted   BIT          NOT NULL DEFAULT 1
);
GO
INSERT INTO ref.timestamp_source VALUES
    ('server',   N'Server Clock (trusted)',       1),
    ('client',   N'Client / Browser Clock',       0),
    ('imported', N'Imported from External System',0),
    ('inferred', N'Inferred / Back-filled',       0);
GO

-- 3.7 · environment ----------------------------------------------------------
-- The app currently runs in "DEMO MODE · ALL DATA IS PLACEHOLDER". Demo cases
-- must never contaminate a real process model, so every case and event carries
-- an environment tag and every mining view filters on it.
CREATE TABLE ref.environment (
    environment_code VARCHAR(20)  NOT NULL PRIMARY KEY,
    display_name     NVARCHAR(40) NOT NULL,
    include_in_mining BIT         NOT NULL DEFAULT 0
);
GO
INSERT INTO ref.environment VALUES
    ('prod', N'Production', 1),
    ('uat',  N'UAT',        0),
    ('demo', N'Demo',       0),
    ('dev',  N'Development',0),
    ('test', N'Test Data',  0);
GO

PRINT 'FILE 1 complete — database, 8 schemas, 20 reference tables seeded.';
GO
