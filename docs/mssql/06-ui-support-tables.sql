/* ============================================================================
   FinSight AI  ·  SQL Server (SSMS) Schema
   FILE 6 of 7 — Columns and tables the application screens need
   ----------------------------------------------------------------------------
   Everything here exists because a borrower or officer screen displays it and
   the schema had nowhere to store it. Adding these is what lets the frontend
   drop its hardcoded arrays entirely.

     1. core.loan_applications.application_no  — the "APP-2024-0891" reference
        shown on every screen. The schema only had a GUID, which is unusable as
        a human reference and is not what officers quote to each other.
     2. ref.application_status.*_next_action   — "what happens next" per status,
        as data. The UI was deriving this from a hardcoded map; a status added
        later would silently show a blank next step.
     3. ops.policy_tickets                     — the officer's Policy Engine queue.
     4. ops.generated_reports                  — credit memos, risk/fraud reports.

   Run AFTER 05-views-process-mining.sql.
============================================================================ */

USE FinSight;
GO

SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

/* ---------------------------------------------------------------------------
   1. Human-readable application number
--------------------------------------------------------------------------- */
IF COL_LENGTH('core.loan_applications', 'application_no') IS NULL
    ALTER TABLE core.loan_applications ADD application_no NVARCHAR(20) NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UX_apps_application_no')
    CREATE UNIQUE INDEX UX_apps_application_no
        ON core.loan_applications(application_no) WHERE application_no IS NOT NULL;
GO

/* ---------------------------------------------------------------------------
   2. "Next action" per status, for each audience
   The officer queue and the borrower's list both answer "what now?" — from the
   same status, but with different answers. Storing both keeps the two screens
   consistent without either of them hardcoding a mapping.
--------------------------------------------------------------------------- */
IF COL_LENGTH('ref.application_status', 'officer_next_action') IS NULL
    ALTER TABLE ref.application_status ADD
        officer_next_action  NVARCHAR(60) NULL,
        borrower_next_action NVARCHAR(60) NULL;
GO

UPDATE ref.application_status SET officer_next_action = v.oa, borrower_next_action = v.ba
FROM ref.application_status s
JOIN (VALUES
    ('draft',                  N'—',                    N'Continue application'),
    ('submitted',              N'Assign & Review',      N'Wait for assignment'),
    ('documentsProcessing',    N'Review Documents',     N'Documents being checked'),
    ('documentsVerified',      N'Policy Check',         N'Wait for officer review'),
    ('goldValuationScheduled', N'Await Valuation',      N'Attend gold valuation'),
    ('goldValued',             N'Review Valuation',     N'Wait for officer review'),
    ('fieldVerification',      N'Await Field Report',   N'Field visit scheduled'),
    ('propertyValuation',      N'Await Valuation',      N'Property visit scheduled'),
    ('legalVerification',      N'Await Legal Opinion',  N'Legal check in progress'),
    ('incomeVerification',     N'Verify Income',        N'Income being verified'),
    ('propertyDocReview',      N'Review Property Docs', N'Documents being checked'),
    ('legalCheck',             N'Await Legal Opinion',  N'Legal check in progress'),
    ('technicalValuation',     N'Await Technical Report',N'Technical visit scheduled'),
    ('bankGstAnalysis',        N'Review Bank/GST',      N'Statements being analysed'),
    ('vehicleInspection',      N'Await Inspection',     N'Vehicle inspection pending'),
    ('underwritingReview',     N'Approve/Reject',       N'Wait for officer review'),
    ('underReview',            N'Review Documents',     N'Wait for officer review'),
    ('offerGenerated',         N'Await Customer',       N'Review & accept loan offer'),
    ('accepted',               N'Sanction',             N'Wait for sanction'),
    ('sanctioned',             N'Disburse',             N'Await disbursement'),
    ('disbursed',              N'—',                    N'Check disbursement status'),
    ('rejected',               N'—',                    N'Contact support for details'),
    ('cancelled',              N'—',                    N'Start a new application'),
    ('expired',                N'—',                    N'Start a new application')
) AS v(code, oa, ba) ON v.code = s.status_code;
GO

/* ---------------------------------------------------------------------------
   3. ops.policy_tickets — the officer's Policy Engine queue
--------------------------------------------------------------------------- */
IF OBJECT_ID('ops.policy_tickets') IS NULL
CREATE TABLE ops.policy_tickets (
    ticket_id       UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_tkt_id DEFAULT NEWSEQUENTIALID(),
    ticket_no       NVARCHAR(20)     NOT NULL,
    raised_by       UNIQUEIDENTIFIER NOT NULL,
    application_id  UNIQUEIDENTIFIER NULL,
    issue           NVARCHAR(1000)   NOT NULL,
    policy_ref      NVARCHAR(120)    NULL,
    priority        VARCHAR(20)      NOT NULL DEFAULT 'medium',
    status          VARCHAR(20)      NOT NULL DEFAULT 'open',
    resolution      NVARCHAR(1000)   NULL,
    resolved_by     UNIQUEIDENTIFIER NULL,
    resolved_at     DATETIME2(3)     NULL,
    created_at      DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    updated_at      DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    -- [PM] A ticket is a queue with a wait; these make it measurable like any
    -- other work item rather than an untracked side channel.
    first_response_at DATETIME2(3)   NULL,
    resolution_sec  BIGINT           NULL,

    CONSTRAINT PK_policy_tickets PRIMARY KEY CLUSTERED (ticket_id),
    CONSTRAINT UQ_policy_ticket_no UNIQUE (ticket_no),
    CONSTRAINT CK_tkt_priority CHECK (priority IN ('low','medium','high','urgent')),
    CONSTRAINT CK_tkt_status   CHECK (status IN ('open','in_progress','resolved','closed')),
    CONSTRAINT FK_tkt_user     FOREIGN KEY (raised_by)      REFERENCES ops.users(user_id),
    CONSTRAINT FK_tkt_resolver FOREIGN KEY (resolved_by)    REFERENCES ops.users(user_id),
    CONSTRAINT FK_tkt_app      FOREIGN KEY (application_id) REFERENCES core.loan_applications(application_id)
);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_policy_tickets_status')
    CREATE INDEX IX_policy_tickets_status ON ops.policy_tickets(status, created_at DESC);
GO

/* ---------------------------------------------------------------------------
   4. ops.generated_reports — credit memos, risk/fraud reports, exceptions
--------------------------------------------------------------------------- */
IF OBJECT_ID('ops.generated_reports') IS NULL
CREATE TABLE ops.generated_reports (
    report_id       UNIQUEIDENTIFIER NOT NULL CONSTRAINT DF_rpt_id DEFAULT NEWSEQUENTIALID(),
    report_no       NVARCHAR(20)     NOT NULL,
    application_id  UNIQUEIDENTIFIER NOT NULL,
    report_type     VARCHAR(40)      NOT NULL,
    status          VARCHAR(30)      NOT NULL DEFAULT 'draft',
    generated_by    UNIQUEIDENTIFIER NULL,
    generated_at    DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    sent_at         DATETIME2(3)     NULL,
    file_path       NVARCHAR(500)    NULL,
    content_json    NVARCHAR(MAX)    NULL,
    -- [PM] Report generation is an activity with a duration and a re-run count.
    generation_ms   INT              NULL,
    regenerated_count SMALLINT       NOT NULL DEFAULT 0,

    CONSTRAINT PK_generated_reports PRIMARY KEY CLUSTERED (report_id),
    CONSTRAINT UQ_report_no UNIQUE (report_no),
    CONSTRAINT CK_rpt_type CHECK (report_type IN
        ('credit_memo','risk_report','fraud_report','policy_exception')),
    CONSTRAINT CK_rpt_status CHECK (status IN
        ('draft','generated','reviewed','sent_to_manager')),
    CONSTRAINT CK_rpt_json CHECK (content_json IS NULL OR ISJSON(content_json) = 1),
    CONSTRAINT FK_rpt_app  FOREIGN KEY (application_id) REFERENCES core.loan_applications(application_id),
    CONSTRAINT FK_rpt_user FOREIGN KEY (generated_by)   REFERENCES ops.users(user_id)
);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_reports_app')
    CREATE INDEX IX_reports_app ON ops.generated_reports(application_id);
GO

PRINT 'FILE 6 complete — application_no, next-action columns, policy_tickets, generated_reports.';
GO
