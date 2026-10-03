/* ============================================================================
   FinSight AI  ·  SQL Server (SSMS) Schema
   FILE 8 — The agentic layer (CrewAI agents) and what it writes
   ----------------------------------------------------------------------------
   Every FinSight feature now runs as a separate agent (api/app/agents). Each
   agent step becomes one process-mining event through pm.usp_log_event, so the
   event log finally receives the per-stage timings the API always measured and
   discarded. This file adds what those events and the new agents need:

     1. agent schema — run / step / tool_call rows for the Agent Activity views,
        plus an audit trail of LLM-selection changes made on the Settings page
     2. ref.resource_type 'ai_agent' and the new ref.automation_component rows
        (module paths for components that used to be "(planned)")
     3. pm.process_definition 'agent_ops' — agent work with no loan case
        (regulatory Q&A, process-mining analysis)
     4. pm.activity codes for the new agents (all optional: an application that
        never runs, say, a credit memo is not a deviation) + allowed edges
     5. fraud.fraud_weight_sets / fraud.fraud_assessments — the fused fraud
        score (weighted sum + dominant floors), snapshotted with its weights
     6. fraud.identity_keys / fraud.identity_matches — dedupe (hashed keys only;
        face embeddings stay in the API's local index)
     7. views agent.vw_step_stats / agent.vw_llm_usage

   Idempotent: safe to run more than once. Agent events are written with
   environment_code = 'demo' (include_in_mining = 0) until the agents are
   promoted, so demo runs never pollute a production process model.

   Run AFTER 07-seed-demo-data.sql:
     sqlcmd -S "(localdb)\MSSQLLocalDB" -E -f 65001 -b -i 08-agentic-layer.sql
============================================================================ */

USE FinSight;
GO

SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

/* ---------------------------------------------------------------------------
   1. agent schema
--------------------------------------------------------------------------- */
IF SCHEMA_ID('agent') IS NULL EXEC('CREATE SCHEMA agent');
GO

IF OBJECT_ID('agent.run') IS NULL
CREATE TABLE agent.run (
    run_id              UNIQUEIDENTIFIER NOT NULL,
    flow                VARCHAR(60)      NOT NULL,     -- extract | integrity | assess | underwrite | ...
    case_id             UNIQUEIDENTIFIER NULL,          -- wizard case / application GUID
    case_key            NVARCHAR(60)     NULL,          -- APP-... when known
    status              VARCHAR(20)      NOT NULL,      -- running | ok | degraded | error
    started_at          DATETIME2(3)     NOT NULL,
    ended_at            DATETIME2(3)     NULL,
    duration_ms         INT              NULL,
    actor_role          VARCHAR(20)      NULL,
    actor_id            NVARCHAR(60)     NULL,
    settings_revision   INT              NULL,          -- LLM settings revision in force
    environment_code    VARCHAR(20)      NOT NULL DEFAULT 'demo',
    step_count          SMALLINT         NOT NULL DEFAULT 0,
    created_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT PK_agent_run PRIMARY KEY CLUSTERED (run_id),
    CONSTRAINT CK_agent_run_status CHECK (status IN ('running','ok','degraded','error'))
);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_agent_run_case')
    CREATE INDEX IX_agent_run_case ON agent.run(case_id, started_at);
GO

IF OBJECT_ID('agent.step') IS NULL
CREATE TABLE agent.step (
    step_id             UNIQUEIDENTIFIER NOT NULL,
    run_id              UNIQUEIDENTIFIER NOT NULL,
    seq                 SMALLINT         NOT NULL,
    agent_id            VARCHAR(40)      NOT NULL,
    activity_code       VARCHAR(60)      NULL,
    component_code      VARCHAR(40)      NULL,
    status              VARCHAR(20)      NOT NULL,      -- ok | degraded | skipped | cached | error
    ai_outcome          VARCHAR(20)      NULL,          -- pass | flag | fail | uncertain | info
    ai_confidence       DECIMAL(5,4)     NULL,
    score               DECIMAL(6,4)     NULL,
    started_at          DATETIME2(3)     NOT NULL,
    duration_ms         INT              NULL,
    model_provider      VARCHAR(20)      NULL,
    model_name          NVARCHAR(80)     NULL,
    llm_calls           SMALLINT         NOT NULL DEFAULT 0,
    llm_ms              INT              NULL,
    llm_queue_ms        INT              NULL,
    tool_calls          SMALLINT         NOT NULL DEFAULT 0,
    summary             NVARCHAR(400)    NULL,          -- PII-free one-liner
    output_json         NVARCHAR(MAX)    NULL,          -- PII-free output summary
    event_id            BIGINT           NULL,          -- pm.event_log row it produced
    created_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT PK_agent_step PRIMARY KEY CLUSTERED (step_id),
    CONSTRAINT FK_agent_step_run FOREIGN KEY (run_id) REFERENCES agent.run(run_id),
    CONSTRAINT CK_agent_step_json CHECK (output_json IS NULL OR ISJSON(output_json) = 1)
);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_agent_step_agent')
    CREATE INDEX IX_agent_step_agent ON agent.step(agent_id, started_at) INCLUDE (duration_ms, status, ai_outcome);
GO

IF OBJECT_ID('agent.tool_call') IS NULL
CREATE TABLE agent.tool_call (
    tool_call_id        BIGINT           IDENTITY(1,1) NOT NULL,
    step_id             UNIQUEIDENTIFIER NOT NULL,
    tool                VARCHAR(60)      NOT NULL,
    duration_ms         INT              NULL,
    status              VARCHAR(20)      NULL,
    detail              NVARCHAR(300)    NULL,
    CONSTRAINT PK_agent_tool_call PRIMARY KEY CLUSTERED (tool_call_id),
    CONSTRAINT FK_agent_tool_step FOREIGN KEY (step_id) REFERENCES agent.step(step_id)
);
GO

IF OBJECT_ID('agent.llm_setting_audit') IS NULL
CREATE TABLE agent.llm_setting_audit (
    audit_id            BIGINT           IDENTITY(1,1) NOT NULL,
    changed_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    actor_id            NVARCHAR(60)     NULL,
    revision_from       INT              NULL,
    revision_to         INT              NOT NULL,
    hosted_in_use       BIT              NOT NULL DEFAULT 0,
    change_json         NVARCHAR(MAX)    NOT NULL,
    CONSTRAINT PK_llm_setting_audit PRIMARY KEY CLUSTERED (audit_id),
    CONSTRAINT CK_llm_audit_json CHECK (ISJSON(change_json) = 1)
);
GO

/* ---------------------------------------------------------------------------
   2. Resource type + automation components
--------------------------------------------------------------------------- */
IF NOT EXISTS (SELECT 1 FROM ref.resource_type WHERE resource_type_code = 'ai_agent')
    INSERT INTO ref.resource_type VALUES ('ai_agent', N'AI Agent (CrewAI)', 1);
GO

MERGE ref.automation_component AS t
USING (VALUES
    ('doc_identity',    N'Document Identity Gate',       N'api/app/docrules.py',                        'rule_engine', 1,   20),
    ('tamper_explain',  N'Tamper Explainer (LLM)',       N'api/app/tampering/explain.py',               'ai_agent',    0, 4000),
    ('tamper_locate',   N'Tamper Locator',               N'api/app/tampering/locate.py',                'system',      0, 3000),
    ('dedupe',          N'Duplicate Identity Check',     N'api/app/agents/tools/dedupe.py',             'rule_engine', 0,  200),
    ('fraud_pattern',   N'Statement Fraud Patterns',     N'api/app/agents/tools/fraud_patterns.py',     'rule_engine', 0,  300),
    ('fraud_triage',    N'Fraud Case/Ring Triage',       N'api/app/agents/tools/fraud_triage.py',       'ai_agent',    0, 1500),
    ('income_analysis', N'Income Triangulation',         N'api/app/agents/tools/income.py',             'rule_engine', 0,  100),
    ('credit_memo',     N'Credit Memo Writer (LLM)',     N'api/app/agents/crews/underwriting.py',       'ai_agent',    0, 20000),
    ('uw_supervisor',   N'Underwriting Supervisor (LLM)',N'api/app/agents/crews/underwriting.py',       'ai_agent',    0, 15000),
    ('offer_gen',       N'Offer Generation',             N'api/app/agents/vendor/risk_engine/pricing_engine.py','rule_engine',0, 50),
    ('workflow_router', N'Workflow Router',              N'api/app/agents/tools/workflow.py',           'rule_engine', 0,   20),
    ('reg_qa',          N'Regulatory Q&A (RAG)',         N'api/app/agents/tools/regulatory_qa.py',      'ai_agent',    0, 6000),
    ('kb_ingest',       N'KB Ingestion',                 N'api/app/compliance/db.py',                   'system',      0,  500),
    ('pm_analyst',      N'Process-Mining Analyst',       N'api/app/agents/tools/process_mining.py',     'ai_agent',    0, 3000),
    ('liveness',        N'Selfie Liveness Check',        N'api/app/agents/tools/liveness.py',           'ml_model',    1,  800)
) AS s(component_code, display_name, module_path, resource_type_code, is_blocking, typical_ms)
ON t.component_code = s.component_code
WHEN MATCHED THEN UPDATE SET display_name = s.display_name, module_path = s.module_path,
                             resource_type_code = s.resource_type_code
WHEN NOT MATCHED THEN INSERT (component_code, display_name, module_path, resource_type_code, is_blocking, typical_ms)
                      VALUES (s.component_code, s.display_name, s.module_path, s.resource_type_code, s.is_blocking, s.typical_ms);
GO

-- components that used to be JavaScript-only or "(planned)"
UPDATE ref.automation_component SET module_path = N'api/app/docrules.py'                 WHERE component_code = 'cross_verify';
UPDATE ref.automation_component SET module_path = N'api/app/agents/vendor/risk_engine/'   WHERE component_code = 'risk_model';
UPDATE ref.automation_component SET module_path = N'api/app/agents/tools/fraud_fusion.py' WHERE component_code = 'fraud_fusion';
UPDATE ref.automation_component SET module_path = N'api/app/agents/tools/notifier.py'     WHERE component_code = 'notifier';
GO

/* ---------------------------------------------------------------------------
   3. Process definition for case-less agent work
--------------------------------------------------------------------------- */
IF NOT EXISTS (SELECT 1 FROM pm.process_definition WHERE process_code = 'agent_ops')
    INSERT INTO pm.process_definition (process_code, process_name, process_version, case_id_source, description)
    VALUES ('agent_ops', N'Agent Operations', '1.0', 'agent.run.run_id',
            N'Agent work that belongs to no loan case: regulatory Q&A, process-mining analysis.');
GO

/* ---------------------------------------------------------------------------
   4. Activities for the new agents
--------------------------------------------------------------------------- */
MERGE pm.activity AS t
USING (VALUES
 ('DOC_AUTHENTICITY',   N'Authenticity Assessed (AI)',        'document',         N'AI',        'ai_agent',    'authenticity',   0,1,1,0,   12,  79),
 ('DEDUPE_CHECK',       N'Duplicate Identity Check Executed', 'loan_application', N'AI',        'rule_engine', 'dedupe',         0,1,1,0,    1, 116),
 ('FRAUD_PATTERN_SCAN', N'Statement Fraud Patterns Scanned',  'loan_application', N'AI',        'rule_engine', 'fraud_pattern',  0,1,1,0,    1, 117),
 ('INCOME_ANALYSIS',    N'Income Triangulated',               'loan_application', N'AI',        'rule_engine', 'income_analysis',0,1,1,0,    1, 148),
 ('UW_RECOMMEND',       N'Underwriting Recommendation (AI)',  'loan_application', N'AI',        'ai_agent',    'uw_supervisor',  1,1,1,0,   15, 165),
 ('CREDIT_MEMO_GENERATE',N'Credit Memo Drafted (AI)',         'loan_application', N'AI',        'ai_agent',    'credit_memo',    0,1,1,0,   20, 166),
 ('WORKFLOW_ROUTE',     N'Next Workflow Step Recommended',    'loan_application', N'AI',        'rule_engine', 'workflow_router',0,1,1,0,    1, 167),
 ('APP_STORE',          N'Application Written to Audit Store','loan_application', N'Intake',    'system',      'storage',        0,1,1,0,    2, 131),
 ('LIVENESS_CHECK',     N'Selfie Liveness Checked',           'loan_application', N'AI',        'ml_model',    'liveness',       0,1,1,0,    1, 112),
 ('FRAUD_TRIAGE_AI',    N'Fraud Case Proposed (AI)',          'fraud_case',       N'Fraud',     'ai_agent',    'fraud_triage',   0,1,1,0,    2,  15),
 ('FRAUD_RING_SCAN',    N'Fraud Ring Links Scanned (AI)',     'fraud_case',       N'Fraud',     'ai_agent',    'fraud_triage',   0,1,1,0,    2,  16),
 ('REG_QA_ANSWER',      N'Regulatory Question Answered',      'agent_ops',        N'Compliance','ai_agent',    'reg_qa',         0,1,1,0,    6,  10),
 ('PM_ANALYZE',         N'Process-Mining Analysis Run',       'agent_ops',        N'Analytics', 'ai_agent',    'pm_analyst',     0,1,1,0,    3,  20)
) AS s(activity_code, activity_name, process_code, activity_category, default_resource_type,
       component_code, is_milestone, is_value_adding, is_optional, is_rework_indicator,
       expected_duration_sec, normative_order)
ON t.activity_code = s.activity_code
WHEN NOT MATCHED THEN INSERT
    (activity_code, activity_name, process_code, activity_category, default_resource_type,
     component_code, is_milestone, is_value_adding, is_optional, is_rework_indicator,
     expected_duration_sec, normative_order)
VALUES (s.activity_code, s.activity_name, s.process_code, s.activity_category, s.default_resource_type,
        s.component_code, s.is_milestone, s.is_value_adding, s.is_optional, s.is_rework_indicator,
        s.expected_duration_sec, s.normative_order);
GO

-- Allowed (non-happy-path) edges so conformance checking treats the agent
-- steps as tolerated variation, not violations.
DECLARE @pd BIGINT = (SELECT TOP 1 process_definition_id FROM pm.process_definition
                      WHERE process_code = 'loan_application' AND is_active = 1 ORDER BY process_definition_id DESC);
INSERT INTO pm.process_edge (process_definition_id, from_activity_id, to_activity_id, is_allowed, is_happy_path, notes)
SELECT @pd, af.activity_id, at2.activity_id, 1, 0, m.note
FROM (VALUES
        ('FACE_VERIFY',        'LIVENESS_CHECK',      N'Selfie liveness after face match'),
        ('LIVENESS_CHECK',     'CROSS_VERIFY',        N'Then cross-document checks'),
        ('CROSS_VERIFY',       'DEDUPE_CHECK',        N'Agents: duplicate identity check'),
        ('DEDUPE_CHECK',       'FRAUD_PATTERN_SCAN',  N'Agents: statement pattern scan'),
        ('FRAUD_PATTERN_SCAN', 'FRAUD_FUSION',        N'Agents: fraud fusion'),
        ('DEDUPE_CHECK',       'FRAUD_FUSION',        N'Agents: fraud fusion (no statement)'),
        ('CROSS_VERIFY',       'FRAUD_FUSION',        N'Agents: fraud fusion'),
        ('FRAUD_FUSION',       'COMPLIANCE_CHECK',    N'Agents: evidence-backed policy check'),
        ('APP_SUBMIT',         'APP_STORE',           N'Audit store write'),
        ('APP_STORE',          'APP_ASSIGN',          N'Then assignment'),
        ('APP_ASSIGN',         'INCOME_ANALYSIS',     N'Underwriting flow'),
        ('INCOME_ANALYSIS',    'RISK_SCORE',          N'Underwriting flow'),
        ('RISK_SCORE',         'UW_RECOMMEND',        N'Underwriting crew'),
        ('UW_RECOMMEND',       'CREDIT_MEMO_GENERATE',N'Underwriting crew'),
        ('CREDIT_MEMO_GENERATE','OFFER_GENERATE',     N'Draft offer'),
        ('OFFER_GENERATE',     'WORKFLOW_ROUTE',      N'Next-step recommendation'),
        ('WORKFLOW_ROUTE',     'NOTIFY_SENT',         N'Notification drafted'),
        ('COMPLIANCE_CHECK',   'OFFICER_REVIEW',      N'Human review follows the agents'),
        ('CREDIT_MEMO_GENERATE','OFFICER_REVIEW',     N'Human review follows the memo'),
        ('WORKFLOW_ROUTE',     'OFFICER_REVIEW',      N'Human review follows routing')
     ) AS m(from_code, to_code, note)
JOIN pm.activity af  ON af.activity_code  = m.from_code
JOIN pm.activity at2 ON at2.activity_code = m.to_code
WHERE NOT EXISTS (SELECT 1 FROM pm.process_edge pe
                  WHERE pe.process_definition_id = @pd
                    AND pe.from_activity_id = af.activity_id
                    AND pe.to_activity_id   = at2.activity_id);
GO

/* ---------------------------------------------------------------------------
   5. Fraud fusion: versioned weights + snapshotted assessments
--------------------------------------------------------------------------- */
IF OBJECT_ID('fraud.fraud_weight_sets') IS NULL
CREATE TABLE fraud.fraud_weight_sets (
    weights_version     VARCHAR(20)      NOT NULL,
    weights_json        NVARCHAR(MAX)    NOT NULL,    -- {"tampering":0.30,...,"tiers":{...},"floors":{...}}
    is_active           BIT              NOT NULL DEFAULT 0,
    notes               NVARCHAR(400)    NULL,
    created_by          NVARCHAR(60)     NULL,
    created_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT PK_fraud_weight_sets PRIMARY KEY CLUSTERED (weights_version),
    CONSTRAINT CK_fws_json CHECK (ISJSON(weights_json) = 1)
);
GO

IF OBJECT_ID('fraud.fraud_assessments') IS NULL
CREATE TABLE fraud.fraud_assessments (
    assessment_id          UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_fass_id DEFAULT NEWSEQUENTIALID(),
    case_id                UNIQUEIDENTIFIER NOT NULL,   -- wizard case / application GUID
    application_no         NVARCHAR(20)     NULL,
    weights_version        VARCHAR(20)      NOT NULL,
    fused_score            DECIMAL(6,4)     NOT NULL,
    tier                   VARCHAR(10)      NOT NULL,
    face_component         DECIMAL(6,4)     NULL,
    tampering_component    DECIMAL(6,4)     NULL,
    cross_check_component  DECIMAL(6,4)     NULL,
    validation_component   DECIMAL(6,4)     NULL,
    dedupe_component       DECIMAL(6,4)     NULL,
    patterns_component     DECIMAL(6,4)     NULL,
    floors_json            NVARCHAR(MAX)    NULL,
    evidence_json          NVARCHAR(MAX)    NULL,
    run_id                 UNIQUEIDENTIFIER NULL,
    created_at             DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT PK_fraud_assessments PRIMARY KEY CLUSTERED (assessment_id),
    CONSTRAINT CK_fass_tier CHECK (tier IN ('LOW','MEDIUM','HIGH')),
    CONSTRAINT CK_fass_evidence CHECK (evidence_json IS NULL OR ISJSON(evidence_json) = 1),
    CONSTRAINT FK_fass_weights FOREIGN KEY (weights_version) REFERENCES fraud.fraud_weight_sets(weights_version)
);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_fass_case')
    CREATE INDEX IX_fass_case ON fraud.fraud_assessments(case_id, created_at);
GO

IF NOT EXISTS (SELECT 1 FROM fraud.fraud_weight_sets WHERE weights_version = 'v1')
    INSERT INTO fraud.fraud_weight_sets (weights_version, weights_json, is_active, notes, created_by)
    VALUES ('v1',
      N'{"weights":{"tampering":0.30,"face":0.20,"cross_check":0.20,"validation":0.10,"dedupe":0.10,"patterns":0.10},"tiers":{"medium":0.30,"high":0.60},"floors":["face_mismatch_confident","exact_duplicate_identity","tamper_high"]}',
      1, N'Initial weights. Weighted sum renormalised over available components; any one floor forces >= MEDIUM, two force HIGH. Advisory only.',
      N'agents');
GO

/* ---------------------------------------------------------------------------
   6. Dedupe: hashed identity keys and matches
--------------------------------------------------------------------------- */
IF OBJECT_ID('fraud.identity_keys') IS NULL
CREATE TABLE fraud.identity_keys (
    key_id              BIGINT           IDENTITY(1,1) NOT NULL,
    case_id             UNIQUEIDENTIFIER NOT NULL,
    key_type            VARCHAR(20)      NOT NULL,     -- file_sha256 | pan_hmac | aadhaar_hmac | account_hmac
    key_value           CHAR(64)         NOT NULL,     -- hex digest, never the raw number
    doc_type            VARCHAR(40)      NULL,
    created_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT PK_identity_keys PRIMARY KEY CLUSTERED (key_id),
    CONSTRAINT CK_idk_type CHECK (key_type IN ('file_sha256','pan_hmac','aadhaar_hmac','account_hmac'))
);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_idk_lookup')
    CREATE INDEX IX_idk_lookup ON fraud.identity_keys(key_type, key_value) INCLUDE (case_id);
GO

IF OBJECT_ID('fraud.identity_matches') IS NULL
CREATE TABLE fraud.identity_matches (
    match_id            BIGINT           IDENTITY(1,1) NOT NULL,
    case_id             UNIQUEIDENTIFIER NOT NULL,
    matched_case_id     UNIQUEIDENTIFIER NOT NULL,
    key_type            VARCHAR(20)      NOT NULL,     -- incl. 'face' (similarity only)
    similarity          DECIMAL(6,4)     NULL,
    created_at          DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT PK_identity_matches PRIMARY KEY CLUSTERED (match_id)
);
GO

/* ---------------------------------------------------------------------------
   7. Views
--------------------------------------------------------------------------- */
CREATE OR ALTER VIEW agent.vw_step_stats AS
WITH s AS (
    SELECT agent_id, duration_ms, status, ai_outcome,
           PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY duration_ms) OVER (PARTITION BY agent_id) AS p50_ms,
           PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms) OVER (PARTITION BY agent_id) AS p95_ms
    FROM agent.step WHERE status <> 'cached'
)
SELECT agent_id,
       COUNT(*)                                             AS runs,
       MAX(p50_ms)                                          AS p50_ms,
       MAX(p95_ms)                                          AS p95_ms,
       SUM(CASE WHEN status = 'degraded' THEN 1 ELSE 0 END) AS degraded,
       SUM(CASE WHEN status = 'error'    THEN 1 ELSE 0 END) AS errors,
       SUM(CASE WHEN ai_outcome IN ('flag','fail') THEN 1 ELSE 0 END) AS flagged
FROM s GROUP BY agent_id;
GO

CREATE OR ALTER VIEW agent.vw_llm_usage AS
SELECT model_provider, model_name, agent_id,
       COUNT(*) AS steps, SUM(llm_calls) AS llm_calls,
       AVG(CAST(llm_ms AS FLOAT)) AS avg_llm_ms, AVG(CAST(llm_queue_ms AS FLOAT)) AS avg_queue_ms
FROM agent.step WHERE llm_calls > 0
GROUP BY model_provider, model_name, agent_id;
GO

/* ---------------------------------------------------------------------------
   8. Reference data used by wizard submissions (Audit/Storage agent writes
      them to core.* since the agentic layer) — the wizard offers a Car Loan
      product that the seeded ref table did not know.
--------------------------------------------------------------------------- */
IF NOT EXISTS (SELECT 1 FROM ref.loan_product_type WHERE product_code = 'car')
    INSERT INTO ref.loan_product_type (product_code, display_name, is_secured) VALUES ('car', 'Car Loan', 1);
GO

PRINT 'FinSight agentic layer installed.';
GO
