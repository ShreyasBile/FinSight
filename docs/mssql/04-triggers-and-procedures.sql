/* ============================================================================
   FinSight AI  ·  SQL Server (SSMS) Schema
   FILE 4 of 5 — Triggers, helper functions, and the event-logging API
   ----------------------------------------------------------------------------
   Contents
     1. Session context (the T-SQL equivalent of PostgreSQL's current_setting)
     2. Helper: numbers table + working-time function
     3. Audit triggers (generated, so 11 tables x 3 operations stay in sync)
     4. core.loan_applications — one consolidated trigger (audit + updated_at +
        status history + case metrics), see the note on trigger recursion
     5. Automatic projection into pm.event_log
     6. pm.usp_log_event — the single entry point the FastAPI backend calls
     7. Refresh procedures: case metrics, variants, DFG, conformance

   Run AFTER 03-process-mining.sql.
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
   1. SESSION CONTEXT
   ---------------------------------------------------------------------------
   PostgreSQL used  SET LOCAL session_vars.actor_id = '…'  read back through
   current_setting(). The T-SQL equivalent is sp_set_session_context /
   SESSION_CONTEXT(), which is connection-scoped. The API must call
   ops.usp_set_session_context once per request, before any DML.
########################################################################### */

CREATE OR ALTER PROCEDURE ops.usp_set_session_context
    @actor_id       UNIQUEIDENTIFIER = NULL,
    @session_id     UNIQUEIDENTIFIER = NULL,
    @ip_address     NVARCHAR(45)     = NULL,
    @source_system  NVARCHAR(40)     = NULL,
    @correlation_id UNIQUEIDENTIFIER = NULL
AS
BEGIN
    SET NOCOUNT ON;
    EXEC sp_set_session_context @key = N'actor_id',       @value = @actor_id;
    EXEC sp_set_session_context @key = N'session_id',     @value = @session_id;
    EXEC sp_set_session_context @key = N'ip_address',     @value = @ip_address;
    EXEC sp_set_session_context @key = N'source_system',  @value = @source_system;
    EXEC sp_set_session_context @key = N'correlation_id', @value = @correlation_id;
END;
GO


/* ###########################################################################
   2. HELPERS
########################################################################### */

-- 2.1 · A small numbers table. Needed by the working-time function; a
-- recursive CTE cannot be used inside an inline table-valued function because
-- OPTION (MAXRECURSION) is not permitted there.
CREATE TABLE pm.number (n INT NOT NULL CONSTRAINT PK_number PRIMARY KEY CLUSTERED);
GO
INSERT INTO pm.number (n)
SELECT TOP (10000) ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) - 1
FROM sys.all_columns a CROSS JOIN sys.all_columns b;
GO

-- 2.2 · Working-time between two UTC timestamps.
-- WHY: a case submitted Friday 18:05 and picked up Monday 09:35 is 63 wall-
-- clock hours and about 5 working minutes. Feed wall-clock durations into a
-- bottleneck analysis and every weekend becomes a fake bottleneck, drowning
-- out the real ones.
CREATE OR ALTER FUNCTION pm.fn_business_seconds
(
    @start_utc  DATETIME2(3),
    @end_utc    DATETIME2(3),
    @calendar   VARCHAR(40),
    @timezone   NVARCHAR(60)      -- e.g. N'India Standard Time'
)
RETURNS TABLE
AS
RETURN
(
    WITH bounds AS (
        SELECT
            s = CAST(@start_utc AT TIME ZONE 'UTC' AT TIME ZONE @timezone AS DATETIME2(3)),
            e = CAST(@end_utc   AT TIME ZONE 'UTC' AT TIME ZONE @timezone AS DATETIME2(3))
    ),
    days AS (
        SELECT d = DATEADD(DAY, n.n, CAST(b.s AS DATE)), b.s, b.e
        FROM   bounds b
        JOIN   pm.number n ON n.n <= DATEDIFF(DAY, CAST(b.s AS DATE), CAST(b.e AS DATE))
        WHERE  b.e >= b.s
    ),
    windows AS (
        SELECT
            ws = DATEADD(SECOND, DATEDIFF(SECOND, 0, bc.start_time), CAST(d.d AS DATETIME2(3))),
            we = DATEADD(SECOND, DATEDIFF(SECOND, 0, bc.end_time),   CAST(d.d AS DATETIME2(3))),
            d.s, d.e
        FROM days d
        -- Normalise the weekday so the result never depends on @@DATEFIRST
        JOIN pm.business_calendar bc
              ON bc.calendar_code = @calendar
             AND bc.day_of_week   = ((DATEPART(WEEKDAY, d.d) + @@DATEFIRST - 1) % 7) + 1
        WHERE NOT EXISTS (SELECT 1 FROM pm.calendar_holiday h
                          WHERE h.calendar_code = @calendar AND h.holiday_date = d.d)
    )
    SELECT business_seconds = ISNULL(SUM(
        CASE WHEN seg_end > seg_start THEN DATEDIFF(SECOND, seg_start, seg_end) ELSE 0 END), 0)
    FROM (
        SELECT seg_start = CASE WHEN s > ws THEN s ELSE ws END,
               seg_end   = CASE WHEN e < we THEN e ELSE we END
        FROM windows
    ) x
);
GO

-- 2.3 · Status -> activity mapping, used when projecting status history into
-- the event log. Keeping it as data (not a CASE expression buried in a
-- trigger) means adding a status never requires editing procedural code.
CREATE TABLE pm.status_activity_map (
    status_code     VARCHAR(40) NOT NULL,
    activity_code   VARCHAR(60) NOT NULL,

    CONSTRAINT PK_status_activity_map PRIMARY KEY CLUSTERED (status_code),
    CONSTRAINT FK_sam_status   FOREIGN KEY (status_code)   REFERENCES ref.application_status(status_code),
    CONSTRAINT FK_sam_activity FOREIGN KEY (activity_code) REFERENCES pm.activity(activity_code)
);
GO
INSERT INTO pm.status_activity_map (status_code, activity_code) VALUES
    ('draft',                  'APP_CREATE'),
    ('submitted',              'APP_SUBMIT'),
    ('documentsProcessing',    'DOC_EXTRACT'),
    ('documentsVerified',      'DOC_VERIFY'),
    ('goldValuationScheduled', 'OFFICER_REVIEW'),
    ('goldValued',             'OFFICER_REVIEW'),
    ('fieldVerification',      'OFFICER_REVIEW'),
    ('propertyValuation',      'OFFICER_REVIEW'),
    ('legalVerification',      'OFFICER_REVIEW'),
    ('incomeVerification',     'CROSS_VERIFY'),
    ('propertyDocReview',      'OFFICER_REVIEW'),
    ('legalCheck',             'OFFICER_REVIEW'),
    ('technicalValuation',     'OFFICER_REVIEW'),
    ('bankGstAnalysis',        'CROSS_VERIFY'),
    ('vehicleInspection',      'OFFICER_REVIEW'),
    ('underwritingReview',     'OFFICER_REVIEW'),
    ('underReview',            'OFFICER_REVIEW'),
    ('offerGenerated',         'OFFER_GENERATE'),
    ('accepted',               'OFFER_ACCEPT'),
    ('sanctioned',             'APP_SANCTION'),
    ('disbursed',              'DISBURSE_SETTLE'),
    ('rejected',               'APP_REJECT'),
    ('cancelled',              'APP_CANCEL'),
    ('expired',                'APP_EXPIRE');
GO


/* ###########################################################################
   3. AUDIT TRIGGERS (generated)
   ---------------------------------------------------------------------------
   T-SQL has no equivalent of PostgreSQL's single generic trigger function —
   every trigger is bound to one table. Rather than hand-write 33 near-identical
   triggers (and let them drift), they are generated from a list. Re-run this
   block after adding a table to the list.
########################################################################### */

DECLARE @audited TABLE (
    schema_name  SYSNAME,
    table_name   SYSNAME,
    pk_column    SYSNAME,
    app_column   SYSNAME NULL,   -- column holding the case id, if any
    has_updated  BIT
);

INSERT INTO @audited VALUES
    ('core',  'customers',                 'customer_id',   NULL,             1),
    ('core',  'loan_offers',               'offer_id',      'application_id', 0),
    ('core',  'disbursements',             'disbursement_id','application_id',0),
    ('fraud', 'fraud_cases',               'case_id',       'application_id', 1),
    ('fraud', 'fraud_rings',               'ring_id',       NULL,             1),
    ('comp',  'compliance_documents',      'document_id',   NULL,             1),
    ('comp',  'compliance_rules',          'rule_id',       NULL,             1),
    ('comp',  'compliance_check_results',  'check_id',      'application_id', 0),
    ('ops',   'users',                     'user_id',       NULL,             1),
    ('risk',  'risk_assessments',          'assessment_id', 'application_id', 0),
    ('risk',  'pricing_models',            'model_id',      NULL,             1);

DECLARE @s SYSNAME, @t SYSNAME, @pk SYSNAME, @app SYSNAME, @upd BIT, @sql NVARCHAR(MAX);
DECLARE @appExprIns NVARCHAR(200), @appExprDel NVARCHAR(200);

DECLARE cur CURSOR LOCAL FAST_FORWARD FOR
    SELECT schema_name, table_name, pk_column, app_column, has_updated FROM @audited;
OPEN cur;
FETCH NEXT FROM cur INTO @s, @t, @pk, @app, @upd;

WHILE @@FETCH_STATUS = 0
BEGIN
    SET @appExprIns = ISNULL('i.' + QUOTENAME(@app), 'NULL');
    SET @appExprDel = ISNULL('d.' + QUOTENAME(@app), 'NULL');

    ---------------------------------------------------------------- INSERT --
    SET @sql = N'
CREATE OR ALTER TRIGGER ' + QUOTENAME(@s) + '.' + QUOTENAME('trg_' + @t + '_audit_insert')
+ ' ON ' + QUOTENAME(@s) + '.' + QUOTENAME(@t) + ' AFTER INSERT AS
BEGIN
    SET NOCOUNT ON;
    IF NOT EXISTS (SELECT 1 FROM inserted) RETURN;
    INSERT INTO audit.audit_log
        (table_name, operation, entity_id, new_data, changed_by, session_id,
         ip_address, application_id, txn_id, source_system)
    SELECT ''' + @s + '.' + @t + ''', ''INSERT'', i.' + QUOTENAME(@pk) + ',
           (SELECT x.* FROM inserted x WHERE x.' + QUOTENAME(@pk) + ' = i.' + QUOTENAME(@pk) + '
            FOR JSON PATH, WITHOUT_ARRAY_WRAPPER),
           CONVERT(UNIQUEIDENTIFIER, SESSION_CONTEXT(N''actor_id'')),
           CONVERT(UNIQUEIDENTIFIER, SESSION_CONTEXT(N''session_id'')),
           CONVERT(NVARCHAR(45),     SESSION_CONTEXT(N''ip_address'')),
           ' + @appExprIns + ',
           CURRENT_TRANSACTION_ID(),
           CONVERT(NVARCHAR(40),     SESSION_CONTEXT(N''source_system''))
    FROM inserted i;
END;';
    EXEC sp_executesql @sql;

    ---------------------------------------------------------------- UPDATE --
    -- The updated_at maintenance is folded INTO the audit trigger on purpose.
    -- If it lived in its own AFTER UPDATE trigger, that trigger's own UPDATE
    -- would re-fire the audit trigger (nested triggers are ON by default) and
    -- every edit would be logged twice. One trigger + direct recursion being
    -- disabled by default = exactly one audit row per statement.
    SET @sql = N'
CREATE OR ALTER TRIGGER ' + QUOTENAME(@s) + '.' + QUOTENAME('trg_' + @t + '_audit_update')
+ ' ON ' + QUOTENAME(@s) + '.' + QUOTENAME(@t) + ' AFTER UPDATE AS
BEGIN
    SET NOCOUNT ON;
    IF NOT EXISTS (SELECT 1 FROM inserted) RETURN;
    INSERT INTO audit.audit_log
        (table_name, operation, entity_id, old_data, new_data, changed_by,
         session_id, ip_address, application_id, txn_id, source_system)
    SELECT ''' + @s + '.' + @t + ''', ''UPDATE'', i.' + QUOTENAME(@pk) + ',
           (SELECT x.* FROM deleted  x WHERE x.' + QUOTENAME(@pk) + ' = i.' + QUOTENAME(@pk) + '
            FOR JSON PATH, WITHOUT_ARRAY_WRAPPER),
           (SELECT x.* FROM inserted x WHERE x.' + QUOTENAME(@pk) + ' = i.' + QUOTENAME(@pk) + '
            FOR JSON PATH, WITHOUT_ARRAY_WRAPPER),
           CONVERT(UNIQUEIDENTIFIER, SESSION_CONTEXT(N''actor_id'')),
           CONVERT(UNIQUEIDENTIFIER, SESSION_CONTEXT(N''session_id'')),
           CONVERT(NVARCHAR(45),     SESSION_CONTEXT(N''ip_address'')),
           ' + @appExprIns + ',
           CURRENT_TRANSACTION_ID(),
           CONVERT(NVARCHAR(40),     SESSION_CONTEXT(N''source_system''))
    FROM inserted i;'
+ CASE WHEN @upd = 1 THEN N'
    UPDATE t SET updated_at = SYSUTCDATETIME()
    FROM ' + QUOTENAME(@s) + '.' + QUOTENAME(@t) + ' t
    JOIN inserted i ON i.' + QUOTENAME(@pk) + ' = t.' + QUOTENAME(@pk) + ';'
       ELSE N'' END
+ N'
END;';
    EXEC sp_executesql @sql;

    ---------------------------------------------------------------- DELETE --
    SET @sql = N'
CREATE OR ALTER TRIGGER ' + QUOTENAME(@s) + '.' + QUOTENAME('trg_' + @t + '_audit_delete')
+ ' ON ' + QUOTENAME(@s) + '.' + QUOTENAME(@t) + ' AFTER DELETE AS
BEGIN
    SET NOCOUNT ON;
    IF NOT EXISTS (SELECT 1 FROM deleted) RETURN;
    INSERT INTO audit.audit_log
        (table_name, operation, entity_id, old_data, changed_by, session_id,
         ip_address, application_id, txn_id, source_system)
    SELECT ''' + @s + '.' + @t + ''', ''DELETE'', d.' + QUOTENAME(@pk) + ',
           (SELECT x.* FROM deleted x WHERE x.' + QUOTENAME(@pk) + ' = d.' + QUOTENAME(@pk) + '
            FOR JSON PATH, WITHOUT_ARRAY_WRAPPER),
           CONVERT(UNIQUEIDENTIFIER, SESSION_CONTEXT(N''actor_id'')),
           CONVERT(UNIQUEIDENTIFIER, SESSION_CONTEXT(N''session_id'')),
           CONVERT(NVARCHAR(45),     SESSION_CONTEXT(N''ip_address'')),
           ' + @appExprDel + ',
           CURRENT_TRANSACTION_ID(),
           CONVERT(NVARCHAR(40),     SESSION_CONTEXT(N''source_system''))
    FROM deleted d;
END;';
    EXEC sp_executesql @sql;

    FETCH NEXT FROM cur INTO @s, @t, @pk, @app, @upd;
END;
CLOSE cur; DEALLOCATE cur;
GO

PRINT '  audit triggers generated for 11 tables.';
GO


/* ###########################################################################
   4. core.loan_applications — one consolidated AFTER UPDATE trigger
   ---------------------------------------------------------------------------
   Everything this table needs on UPDATE lives in a single trigger: audit row,
   updated_at, status-history row, and the case-level process fields. One
   trigger means SQL Server's default "no direct recursion" rule covers the
   self-UPDATE at the end, so nothing double-fires.
########################################################################### */

CREATE OR ALTER TRIGGER core.trg_loan_applications_insert
ON core.loan_applications AFTER INSERT
AS
BEGIN
    SET NOCOUNT ON;
    IF NOT EXISTS (SELECT 1 FROM inserted) RETURN;

    INSERT INTO audit.audit_log
        (table_name, operation, entity_id, new_data, changed_by, session_id,
         ip_address, application_id, txn_id, source_system)
    SELECT 'core.loan_applications', 'INSERT', i.application_id,
           (SELECT x.* FROM inserted x WHERE x.application_id = i.application_id
            FOR JSON PATH, WITHOUT_ARRAY_WRAPPER),
           CONVERT(UNIQUEIDENTIFIER, SESSION_CONTEXT(N'actor_id')),
           CONVERT(UNIQUEIDENTIFIER, SESSION_CONTEXT(N'session_id')),
           CONVERT(NVARCHAR(45),     SESSION_CONTEXT(N'ip_address')),
           i.application_id, CURRENT_TRANSACTION_ID(),
           CONVERT(NVARCHAR(40),     SESSION_CONTEXT(N'source_system'))
    FROM inserted i;

    -- Case start: the anchor every throughput calculation depends on.
    UPDATE a
    SET    case_started_at   = ISNULL(a.case_started_at, ISNULL(a.applied_at, a.created_at)),
           last_activity_at  = ISNULL(a.last_activity_at, a.created_at)
    FROM   core.loan_applications a
    JOIN   inserted i ON i.application_id = a.application_id;

    -- Opening event of the trace.
    INSERT INTO core.application_status_history
        (application_id, from_status, to_status, changed_by, change_reason,
         created_at, event_seq, is_automated, reason_code)
    SELECT i.application_id, NULL, i.status_code,
           CONVERT(UNIQUEIDENTIFIER, SESSION_CONTEXT(N'actor_id')),
           N'Application created', ISNULL(i.created_at, SYSUTCDATETIME()), 1, 0, N'created'
    FROM inserted i;
END;
GO

CREATE OR ALTER TRIGGER core.trg_loan_applications_update
ON core.loan_applications AFTER UPDATE
AS
BEGIN
    SET NOCOUNT ON;
    IF NOT EXISTS (SELECT 1 FROM inserted) RETURN;

    DECLARE @now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @actor UNIQUEIDENTIFIER = CONVERT(UNIQUEIDENTIFIER, SESSION_CONTEXT(N'actor_id'));

    ---- 4a. audit -----------------------------------------------------------
    INSERT INTO audit.audit_log
        (table_name, operation, entity_id, old_data, new_data, changed_by,
         session_id, ip_address, application_id, txn_id, source_system)
    SELECT 'core.loan_applications', 'UPDATE', i.application_id,
           (SELECT x.* FROM deleted  x WHERE x.application_id = i.application_id
            FOR JSON PATH, WITHOUT_ARRAY_WRAPPER),
           (SELECT x.* FROM inserted x WHERE x.application_id = i.application_id
            FOR JSON PATH, WITHOUT_ARRAY_WRAPPER),
           @actor,
           CONVERT(UNIQUEIDENTIFIER, SESSION_CONTEXT(N'session_id')),
           CONVERT(NVARCHAR(45),     SESSION_CONTEXT(N'ip_address')),
           i.application_id, CURRENT_TRANSACTION_ID(),
           CONVERT(NVARCHAR(40),     SESSION_CONTEXT(N'source_system'))
    FROM inserted i;

    ---- 4b. status history, only when the status actually moved -------------
    IF UPDATE(status_code)
    BEGIN
        INSERT INTO core.application_status_history
            (application_id, from_status, to_status, changed_by, created_at,
             event_seq, duration_in_previous_sec, is_backward_transition,
             is_rework, is_automated, correlation_id)
        SELECT
            i.application_id,
            d.status_code,
            i.status_code,
            @actor,
            @now,
            -- next ordinal for this case
            (SELECT ISNULL(MAX(h.event_seq), 0) + 1
             FROM core.application_status_history h
             WHERE h.application_id = i.application_id),
            -- time spent in the status we are leaving
            DATEDIFF(SECOND,
                ISNULL((SELECT MAX(h.created_at)
                        FROM core.application_status_history h
                        WHERE h.application_id = i.application_id), i.created_at),
                @now),
            -- moving to an EARLIER stage is the signature of rework
            CASE WHEN sNew.sort_order < sOld.sort_order THEN 1 ELSE 0 END,
            CASE WHEN EXISTS (SELECT 1 FROM core.application_status_history h
                              WHERE h.application_id = i.application_id
                                AND h.to_status = i.status_code)
                 THEN 1 ELSE 0 END,
            CASE WHEN @actor IS NULL THEN 1 ELSE 0 END,
            CONVERT(UNIQUEIDENTIFIER, SESSION_CONTEXT(N'correlation_id'))
        FROM inserted i
        JOIN deleted  d   ON d.application_id = i.application_id
        JOIN ref.application_status sOld ON sOld.status_code = d.status_code
        JOIN ref.application_status sNew ON sNew.status_code = i.status_code
        WHERE i.status_code <> d.status_code;
    END;

    ---- 4c. case-level process fields ---------------------------------------
    UPDATE a
    SET    updated_at       = @now,
           last_activity_at = @now,
           case_started_at  = ISNULL(a.case_started_at, ISNULL(a.applied_at, a.created_at)),
           case_ended_at    = CASE WHEN s.is_terminal = 1 AND a.case_ended_at IS NULL
                                   THEN @now ELSE a.case_ended_at END,
           is_case_closed   = s.is_terminal,
           case_duration_sec= CASE WHEN s.is_terminal = 1
                                   THEN DATEDIFF(SECOND,
                                        ISNULL(a.case_started_at, a.created_at),
                                        ISNULL(a.case_ended_at, @now))
                                   ELSE a.case_duration_sec END,
           reopened_count   = CASE WHEN s.is_terminal = 0 AND a.is_case_closed = 1
                                   THEN a.reopened_count + 1 ELSE a.reopened_count END
    FROM   core.loan_applications a
    JOIN   inserted i ON i.application_id = a.application_id
    JOIN   ref.application_status s ON s.status_code = a.status_code;
END;
GO


/* ###########################################################################
   5. AUTOMATIC PROJECTION INTO pm.event_log
   ---------------------------------------------------------------------------
   Two feeds write events without the application having to remember to:
     - every status change  (core.application_status_history)
     - every business event (audit.business_events)
   Anything richer (per-document AI stages, overrides, SLA data) is logged
   explicitly through pm.usp_log_event.
########################################################################### */

CREATE OR ALTER TRIGGER core.trg_status_history_to_eventlog
ON core.application_status_history AFTER INSERT
AS
BEGIN
    SET NOCOUNT ON;
    IF NOT EXISTS (SELECT 1 FROM inserted) RETURN;

    INSERT INTO pm.event_log
        (process_code, process_definition_id, case_id, event_seq,
         activity_id, activity_name, lifecycle_transition,
         occurred_at, timestamp_source, since_previous_ms,
         resource_id, resource_name, resource_type_code,
         resource_role_snapshot, resource_branch_snapshot,
         is_automated, component_code, wait_reason_code,
         from_status, to_status, is_rework, correlation_id,
         source_system, environment_code, attributes)
    SELECT
        'loan_application',
        (SELECT TOP 1 process_definition_id FROM pm.process_definition
         WHERE process_code = 'loan_application' AND is_active = 1 ORDER BY process_definition_id DESC),
        i.application_id,
        -- The ordinal must be the next free one IN THE EVENT LOG, not the one
        -- from the status-history table. History counts only status changes,
        -- while the log also holds explicitly-logged activities — reusing the
        -- history ordinal makes the two feeds collide on the same numbers and
        -- silently scrambles the directly-follows graph.
        (SELECT ISNULL(MAX(e.event_seq), 0) + 1 FROM pm.event_log e
         WHERE e.process_code = 'loan_application' AND e.case_id = i.application_id)
        + ROW_NUMBER() OVER (PARTITION BY i.application_id ORDER BY i.created_at, i.history_id) - 1,
        act.activity_id,
        act.activity_name,
        'complete',
        i.created_at,
        'server',
        i.duration_in_previous_sec * 1000,
        i.changed_by,
        u.full_name,
        CASE WHEN i.changed_by IS NULL THEN 'system' ELSE ISNULL(u.resource_type_code, 'human') END,
        u.role_code,
        u.branch_code,
        i.is_automated,
        i.component_code,
        i.wait_reason_code,
        i.from_status,
        i.to_status,
        i.is_rework,
        i.correlation_id,
        N'status_history',
        ISNULL(a.environment_code, 'prod'),
        (SELECT i.reason_code AS reason_code, i.change_reason AS reason_text,
                i.is_backward_transition AS backward FOR JSON PATH, WITHOUT_ARRAY_WRAPPER)
    FROM inserted i
    JOIN pm.status_activity_map m ON m.status_code = i.to_status
    JOIN pm.activity act          ON act.activity_code = m.activity_code
    LEFT JOIN ops.users u         ON u.user_id = i.changed_by
    LEFT JOIN core.loan_applications a ON a.application_id = i.application_id;
END;
GO

CREATE OR ALTER TRIGGER audit.trg_business_events_to_eventlog
ON audit.business_events AFTER INSERT
AS
BEGIN
    SET NOCOUNT ON;
    IF NOT EXISTS (SELECT 1 FROM inserted WHERE application_id IS NOT NULL) RETURN;

    -- Only event types that map to a real activity are projected; the rest
    -- (user_login, report_generated…) stay as business events only, so the
    -- process map is not polluted with non-process noise.
    INSERT INTO pm.event_log
        (process_code, case_id, event_seq, activity_id, activity_name,
         lifecycle_transition, occurred_at, timestamp_source,
         resource_id, resource_type_code, is_automated,
         correlation_id, source_system, environment_code, attributes)
    SELECT
        'loan_application',
        i.application_id,
        (SELECT ISNULL(MAX(e.event_seq), 0) + 1 FROM pm.event_log e
         WHERE e.process_code = 'loan_application' AND e.case_id = i.application_id),
        act.activity_id, act.activity_name, 'complete',
        i.created_at, 'server',
        i.created_by,
        CASE WHEN i.created_by IS NULL THEN 'system' ELSE 'human' END,
        CASE WHEN i.created_by IS NULL THEN 1 ELSE 0 END,
        i.correlation_id,
        ISNULL(i.source, N'business_events'),
        ISNULL(a.environment_code, 'prod'),
        i.event_data
    FROM inserted i
    JOIN (VALUES
            ('application_created',  'APP_CREATE'),
            ('application_submitted','APP_SUBMIT'),
            ('document_uploaded',    'DOC_UPLOAD'),
            ('document_verified',    'DOC_VERIFY'),
            ('document_rejected',    'DOC_REJECT'),
            ('offer_generated',      'OFFER_GENERATE'),
            ('offer_accepted',       'OFFER_ACCEPT'),
            ('offer_rejected',       'OFFER_REJECT'),
            ('disbursed',            'DISBURSE_SETTLE'),
            ('fraud_detected',       'FRAUD_SIGNAL'),
            ('fraud_escalated',      'FRAUD_ESCALATE'),
            ('compliance_check',     'COMPLIANCE_CHECK')
         ) AS map(event_type, activity_code) ON map.event_type = i.event_type
    JOIN pm.activity act ON act.activity_code = map.activity_code
    LEFT JOIN core.loan_applications a ON a.application_id = i.application_id
    WHERE i.application_id IS NOT NULL;

    UPDATE b SET projected_to_pm = 1
    FROM audit.business_events b JOIN inserted i ON i.event_id = b.event_id;
END;
GO


/* ###########################################################################
   6. pm.usp_log_event — THE ENTRY POINT FOR THE APPLICATION
   ---------------------------------------------------------------------------
   The FastAPI backend should call this once per meaningful step, e.g. after
   /extract returns:  activity DOC_EXTRACT, component ocr_extract, the model
   name/version actually used, and the measured duration from its own `timings`
   dict. Everything derived (sequence number, gap since the previous event,
   handover detection, working-time) is computed here so callers cannot get it
   subtly wrong.
########################################################################### */

CREATE OR ALTER PROCEDURE pm.usp_log_event
    @process_code        VARCHAR(50),
    @case_id             UNIQUEIDENTIFIER,
    @activity_code       VARCHAR(60),
    @occurred_at         DATETIME2(3)     = NULL,
    @lifecycle           VARCHAR(20)      = 'complete',
    @case_key            NVARCHAR(60)     = NULL,
    @activity_instance_id UNIQUEIDENTIFIER = NULL,
    @resource_id         UNIQUEIDENTIFIER = NULL,
    @resource_type       VARCHAR(30)      = NULL,
    @queued_at           DATETIME2(3)     = NULL,
    @started_at          DATETIME2(3)     = NULL,
    @completed_at        DATETIME2(3)     = NULL,
    @duration_ms         BIGINT           = NULL,
    @component_code      VARCHAR(40)      = NULL,
    @model_name          NVARCHAR(80)     = NULL,
    @model_version       NVARCHAR(40)     = NULL,
    @ai_confidence       DECIMAL(5,4)     = NULL,
    @ai_outcome          NVARCHAR(40)     = NULL,
    @human_overrode_ai   BIT              = 0,
    @override_reason     NVARCHAR(300)    = NULL,
    @from_status         VARCHAR(40)      = NULL,
    @to_status           VARCHAR(40)      = NULL,
    @wait_reason_code    VARCHAR(40)      = NULL,
    @is_rework           BIT              = 0,
    @attempt_no          SMALLINT         = 1,
    @is_exception        BIT              = 0,
    @exception_type      NVARCHAR(60)     = NULL,
    @channel_code        VARCHAR(20)      = NULL,
    @source_system       NVARCHAR(40)     = NULL,
    @correlation_id      UNIQUEIDENTIFIER = NULL,
    @idempotency_key     NVARCHAR(120)    = NULL,
    @environment_code    VARCHAR(20)      = NULL,
    @attributes          NVARCHAR(MAX)    = NULL,
    @activity_cost       DECIMAL(12,4)    = NULL,
    @event_id            BIGINT           OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    SET @occurred_at      = ISNULL(@occurred_at, SYSUTCDATETIME());
    SET @correlation_id   = ISNULL(@correlation_id,  CONVERT(UNIQUEIDENTIFIER, SESSION_CONTEXT(N'correlation_id')));
    SET @source_system    = ISNULL(@source_system,   CONVERT(NVARCHAR(40),     SESSION_CONTEXT(N'source_system')));
    SET @environment_code = ISNULL(@environment_code, 'prod');

    -- Idempotency: a retried API call must not create a second event.
    IF @idempotency_key IS NOT NULL
    BEGIN
        SELECT @event_id = event_id FROM pm.event_log WHERE idempotency_key = @idempotency_key;
        IF @event_id IS NOT NULL RETURN 0;
    END;

    DECLARE @activity_id INT, @activity_name NVARCHAR(120), @default_rtype VARCHAR(30);
    SELECT @activity_id = activity_id, @activity_name = activity_name,
           @default_rtype = default_resource_type,
           @component_code = ISNULL(@component_code, component_code)
    FROM pm.activity WHERE activity_code = @activity_code;

    IF @activity_id IS NULL
    BEGIN
        RAISERROR('Unknown activity_code "%s" — add it to pm.activity first.', 16, 1, @activity_code);
        RETURN -1;
    END;

    SET @resource_type = ISNULL(@resource_type, @default_rtype);

    -- Fall back to the logged-in user ONLY for staff activities. A caller that
    -- says "this step was performed by the customer, or by a model" must not
    -- have the officer whose session happened to trigger the HTTP request
    -- attributed to it — that quietly inflates every human workload metric and
    -- makes automated steps look manual.
    IF @resource_id IS NULL AND @resource_type = 'human'
        SET @resource_id = CONVERT(UNIQUEIDENTIFIER, SESSION_CONTEXT(N'actor_id'));

    DECLARE @seq INT, @prev_time DATETIME2(3), @prev_res UNIQUEIDENTIFIER;

    -- Serialise sequence assignment per case. HOLDLOCK keeps two concurrent
    -- events in the same case from claiming the same ordinal.
    SELECT @seq       = ISNULL(MAX(event_seq), 0) + 1
    FROM   pm.event_log WITH (UPDLOCK, HOLDLOCK)
    WHERE  process_code = @process_code AND case_id = @case_id;

    -- "Previous" means chronologically previous, not last-inserted. Events can
    -- arrive out of order (a backdated import, or a module reporting a step it
    -- finished earlier), and ordering by insertion would produce negative gaps.
    SELECT TOP 1 @prev_time = occurred_at
    FROM   pm.event_log
    WHERE  process_code = @process_code AND case_id = @case_id
      AND  occurred_at <= @occurred_at
    ORDER  BY occurred_at DESC, event_seq DESC;

    -- Handover is measured between IDENTIFIED people. The last automated or
    -- customer step in between is not a participant, so skipping rows without
    -- a resource is what makes officer -> head register as one handover
    -- instead of disappearing behind whatever the system did last.
    SELECT TOP 1 @prev_res = resource_id
    FROM   pm.event_log
    WHERE  process_code = @process_code AND case_id = @case_id
      AND  occurred_at <= @occurred_at
      AND  resource_id IS NOT NULL
    ORDER  BY occurred_at DESC, event_seq DESC;

    IF @duration_ms IS NULL AND @started_at IS NOT NULL AND @completed_at IS NOT NULL
        SET @duration_ms = DATEDIFF_BIG(MILLISECOND, @started_at, @completed_at);

    DECLARE @is_automated BIT =
        (SELECT is_automated FROM ref.resource_type WHERE resource_type_code = @resource_type);

    INSERT INTO pm.event_log
        (process_code, process_definition_id, case_id, case_key, event_seq,
         activity_id, activity_name, lifecycle_transition, activity_instance_id,
         occurred_at, timestamp_source, queued_at, started_at, completed_at,
         duration_ms, waiting_ms, since_previous_ms,
         resource_id, resource_name, resource_type_code, resource_role_snapshot,
         resource_team_snapshot, resource_branch_snapshot,
         previous_resource_id, is_handover, session_id,
         is_automated, component_code, model_name, model_version,
         ai_confidence, ai_outcome, human_overrode_ai, override_reason,
         from_status, to_status, wait_reason_code,
         is_rework, attempt_no, is_exception, exception_type,
         activity_cost, channel_code, source_system, correlation_id,
         idempotency_key, environment_code, attributes)
    SELECT
        @process_code,
        (SELECT TOP 1 process_definition_id FROM pm.process_definition
         WHERE process_code = @process_code AND is_active = 1 ORDER BY process_definition_id DESC),
        @case_id, @case_key, @seq,
        @activity_id, @activity_name, @lifecycle, @activity_instance_id,
        @occurred_at, 'server', @queued_at, @started_at, @completed_at,
        @duration_ms,
        CASE WHEN @queued_at IS NOT NULL AND @started_at IS NOT NULL
             THEN DATEDIFF_BIG(MILLISECOND, @queued_at, @started_at) END,
        CASE WHEN @prev_time IS NOT NULL
             THEN DATEDIFF_BIG(MILLISECOND, @prev_time, @occurred_at) END,
        @resource_id, u.full_name, @resource_type, u.role_code,
        u.team_name, u.branch_code,
        @prev_res,
        CASE WHEN @prev_res IS NOT NULL AND @resource_id IS NOT NULL
                  AND @prev_res <> @resource_id THEN 1 ELSE 0 END,
        CONVERT(UNIQUEIDENTIFIER, SESSION_CONTEXT(N'session_id')),
        ISNULL(@is_automated, 0), @component_code, @model_name, @model_version,
        @ai_confidence, @ai_outcome, @human_overrode_ai, @override_reason,
        @from_status, @to_status, @wait_reason_code,
        @is_rework, @attempt_no, @is_exception, @exception_type,
        @activity_cost, @channel_code, @source_system, @correlation_id,
        @idempotency_key, @environment_code, @attributes
    FROM (SELECT 1 AS x) dummy
    LEFT JOIN ops.users u ON u.user_id = @resource_id;

    SET @event_id = SCOPE_IDENTITY();

    -- Keep the case row's process counters current so officer screens and
    -- "stuck case" queries never need to touch the event log.
    IF @process_code = 'loan_application'
        UPDATE core.loan_applications
        SET    last_activity_at = @occurred_at,
               event_count      = event_count + 1,
               touch_count      = touch_count + CASE WHEN ISNULL(@is_automated,0) = 0 THEN 1 ELSE 0 END,
               rework_count     = rework_count + CASE WHEN @is_rework = 1 THEN 1 ELSE 0 END,
               handover_count   = handover_count + CASE WHEN @prev_res IS NOT NULL
                                                        AND @resource_id IS NOT NULL
                                                        AND @prev_res <> @resource_id THEN 1 ELSE 0 END
        WHERE  application_id = @case_id;

    RETURN 0;
END;
GO

-- 6.1 · Link an event to an object (OCEL). Call once per related object.
CREATE OR ALTER PROCEDURE pm.usp_link_event_object
    @event_id       BIGINT,
    @object_type    VARCHAR(40),
    @natural_key    UNIQUEIDENTIFIER,
    @display_key    NVARCHAR(80) = NULL,
    @qualifier      NVARCHAR(40) = 'related'
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @object_id BIGINT;

    SELECT @object_id = object_id FROM pm.object_instance
    WHERE object_type_code = @object_type AND natural_key = @natural_key;

    IF @object_id IS NULL
    BEGIN
        INSERT INTO pm.object_instance (object_type_code, natural_key, display_key)
        VALUES (@object_type, @natural_key, @display_key);
        SET @object_id = SCOPE_IDENTITY();
    END;

    IF NOT EXISTS (SELECT 1 FROM pm.event_object_link
                   WHERE event_id = @event_id AND object_id = @object_id AND qualifier = @qualifier)
        INSERT INTO pm.event_object_link (event_id, object_id, qualifier)
        VALUES (@event_id, @object_id, @qualifier);
END;
GO


/* ###########################################################################
   7. REFRESH PROCEDURES  (run nightly, or after a bulk import)
########################################################################### */

-- 7.1 · Variants ------------------------------------------------------------
-- Builds the distinct activity sequences and links each case to its variant.
CREATE OR ALTER PROCEDURE pm.usp_refresh_variants
    @process_code VARCHAR(50) = 'loan_application'
AS
BEGIN
    SET NOCOUNT ON;

    ;WITH traces AS (
        SELECT e.case_id,
               seq = STRING_AGG(CONVERT(NVARCHAR(MAX), a.activity_code), '>')
                     WITHIN GROUP (ORDER BY e.occurred_at, e.event_seq),
               activity_count = COUNT(*)
        FROM   pm.event_log e
        JOIN   pm.activity  a ON a.activity_id = e.activity_id
        WHERE  e.process_code = @process_code
          AND  e.is_valid_for_mining = 1
          AND  e.lifecycle_transition = 'complete'
        GROUP BY e.case_id
    )
    MERGE pm.case_variant AS tgt
    USING (
        SELECT process_code = @process_code,
               variant_hash = HASHBYTES('SHA2_256', seq),
               activity_sequence = seq,
               activity_count = MAX(activity_count),
               case_count = COUNT(*)
        FROM traces
        GROUP BY seq
    ) AS src
      ON tgt.process_code = src.process_code AND tgt.variant_hash = src.variant_hash
    WHEN MATCHED THEN
        UPDATE SET case_count = src.case_count, last_seen_at = SYSUTCDATETIME()
    WHEN NOT MATCHED THEN
        INSERT (process_code, variant_hash, activity_sequence, activity_count,
                case_count, first_seen_at, last_seen_at)
        VALUES (src.process_code, src.variant_hash, src.activity_sequence,
                src.activity_count, src.case_count, SYSUTCDATETIME(), SYSUTCDATETIME());

    -- Attach each loan case to its variant.
    IF @process_code = 'loan_application'
    BEGIN
        ;WITH traces AS (
            SELECT e.case_id,
                   h = HASHBYTES('SHA2_256',
                        STRING_AGG(CONVERT(NVARCHAR(MAX), a.activity_code), '>')
                        WITHIN GROUP (ORDER BY e.occurred_at, e.event_seq))
            FROM   pm.event_log e
            JOIN   pm.activity  a ON a.activity_id = e.activity_id
            WHERE  e.process_code = @process_code
              AND  e.is_valid_for_mining = 1
              AND  e.lifecycle_transition = 'complete'
            GROUP BY e.case_id
        )
        UPDATE app
        SET    variant_id = v.variant_id
        FROM   core.loan_applications app
        JOIN   traces t ON t.case_id = app.application_id
        JOIN   pm.case_variant v ON v.variant_hash = t.h AND v.process_code = @process_code;

        -- Per-variant outcome statistics: the answer to "which paths actually
        -- convert, and which ones burn a week and end in a rejection?"
        UPDATE v
        SET    avg_duration_sec = s.avg_dur,
               min_duration_sec = s.min_dur,
               max_duration_sec = s.max_dur,
               approval_rate    = s.approval_rate,
               rework_rate      = s.rework_rate
        FROM   pm.case_variant v
        JOIN (
            SELECT app.variant_id,
                   avg_dur = AVG(CAST(app.case_duration_sec AS BIGINT)),
                   min_dur = MIN(app.case_duration_sec),
                   max_dur = MAX(app.case_duration_sec),
                   approval_rate = CAST(SUM(CASE WHEN app.status_code IN ('disbursed','sanctioned','accepted')
                                                 THEN 1.0 ELSE 0 END) / NULLIF(COUNT(*),0) AS DECIMAL(5,4)),
                   rework_rate   = CAST(SUM(CASE WHEN app.rework_count > 0 THEN 1.0 ELSE 0 END)
                                        / NULLIF(COUNT(*),0) AS DECIMAL(5,4))
            FROM core.loan_applications app
            WHERE app.variant_id IS NOT NULL
            GROUP BY app.variant_id
        ) s ON s.variant_id = v.variant_id;
    END;
END;
GO

-- 7.2 · Case metrics --------------------------------------------------------
CREATE OR ALTER PROCEDURE pm.usp_refresh_case_metrics
    @process_code VARCHAR(50) = NULL
AS
BEGIN
    SET NOCOUNT ON;

    MERGE pm.case_metrics AS tgt
    USING (
        SELECT e.process_code, e.case_id,
               started_at      = MIN(e.occurred_at),
               ended_at        = MAX(e.occurred_at),
               duration_sec    = DATEDIFF_BIG(SECOND, MIN(e.occurred_at), MAX(e.occurred_at)),
               event_count     = COUNT(*),
               activity_count  = COUNT(DISTINCT e.activity_id),
               touch_count     = SUM(CASE WHEN e.is_automated = 0 THEN 1 ELSE 0 END),
               rework_count    = SUM(CASE WHEN e.is_rework   = 1 THEN 1 ELSE 0 END),
               handover_count  = SUM(CASE WHEN e.is_handover = 1 THEN 1 ELSE 0 END),
               deviation_count = SUM(CASE WHEN e.is_deviation = 1 THEN 1 ELSE 0 END),
               automation_ratio= CAST(SUM(CASE WHEN e.is_automated = 1 THEN 1.0 ELSE 0 END)
                                      / NULLIF(COUNT(*),0) AS DECIMAL(5,4)),
               total_cost      = SUM(e.activity_cost),
               sla_breach_count= SUM(CASE WHEN e.sla_met = 0 THEN 1 ELSE 0 END)
        FROM   pm.event_log e
        WHERE  e.is_valid_for_mining = 1
          AND (@process_code IS NULL OR e.process_code = @process_code)
        GROUP BY e.process_code, e.case_id
    ) AS src
      ON tgt.process_code = src.process_code AND tgt.case_id = src.case_id
    WHEN MATCHED THEN UPDATE SET
        started_at = src.started_at, ended_at = src.ended_at,
        duration_sec = src.duration_sec, event_count = src.event_count,
        activity_count = src.activity_count, touch_count = src.touch_count,
        rework_count = src.rework_count, handover_count = src.handover_count,
        deviation_count = src.deviation_count, automation_ratio = src.automation_ratio,
        total_cost = src.total_cost, sla_breach_count = src.sla_breach_count,
        refreshed_at = SYSUTCDATETIME()
    WHEN NOT MATCHED THEN
        INSERT (process_code, case_id, started_at, ended_at, duration_sec,
                event_count, activity_count, touch_count, rework_count,
                handover_count, deviation_count, automation_ratio, total_cost,
                sla_breach_count)
        VALUES (src.process_code, src.case_id, src.started_at, src.ended_at,
                src.duration_sec, src.event_count, src.activity_count,
                src.touch_count, src.rework_count, src.handover_count,
                src.deviation_count, src.automation_ratio, src.total_cost,
                src.sla_breach_count);

    -- Mirror onto the loan case row (no join needed on operational screens).
    UPDATE app
    SET    event_count        = m.event_count,
           activity_count     = m.activity_count,
           touch_count        = m.touch_count,
           rework_count       = m.rework_count,
           handover_count     = m.handover_count,
           deviation_count    = m.deviation_count,
           automation_ratio   = m.automation_ratio,
           is_straight_through= CASE WHEN m.touch_count = 0 AND m.rework_count = 0 THEN 1 ELSE 0 END
    FROM   core.loan_applications app
    JOIN   pm.case_metrics m
           ON m.case_id = app.application_id AND m.process_code = 'loan_application';
END;
GO

-- 7.3 · Directly-follows graph snapshot -------------------------------------
CREATE OR ALTER PROCEDURE pm.usp_refresh_dfg
    @process_code VARCHAR(50) = 'loan_application',
    @filter_json  NVARCHAR(MAX) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @hash BINARY(32) = HASHBYTES('SHA2_256', ISNULL(@filter_json, N'{}'));

    DELETE FROM pm.dfg_snapshot WHERE process_code = @process_code AND filter_hash = @hash;

    ;WITH ordered AS (
        SELECT e.case_id, e.activity_id, e.occurred_at, e.event_seq,
               next_activity_id = LEAD(e.activity_id) OVER (PARTITION BY e.case_id ORDER BY e.occurred_at, e.event_seq),
               next_time        = LEAD(e.occurred_at) OVER (PARTITION BY e.case_id ORDER BY e.occurred_at, e.event_seq)
        FROM   pm.event_log e
        WHERE  e.process_code = @process_code
          AND  e.is_valid_for_mining = 1
          AND  e.lifecycle_transition = 'complete'
    )
    INSERT INTO pm.dfg_snapshot
        (process_code, filter_hash, filter_json, from_activity_id, to_activity_id,
         transition_count, case_count, avg_wait_sec, median_wait_sec, p90_wait_sec,
         max_wait_sec, is_deviation_edge)
    SELECT @process_code, @hash, @filter_json,
           o.activity_id, o.next_activity_id,
           COUNT(*), COUNT(DISTINCT o.case_id),
           AVG(DATEDIFF_BIG(SECOND, o.occurred_at, o.next_time)),
           MAX(o.median_wait), MAX(o.p90_wait),
           MAX(DATEDIFF_BIG(SECOND, o.occurred_at, o.next_time)),
           CASE WHEN EXISTS (
                SELECT 1 FROM pm.process_edge pe
                JOIN pm.process_definition pd ON pd.process_definition_id = pe.process_definition_id
                WHERE pd.process_code = @process_code
                  AND pe.from_activity_id = o.activity_id
                  AND pe.to_activity_id   = o.next_activity_id
                  AND pe.is_allowed = 1)
                THEN 0 ELSE 1 END
    FROM (
        SELECT *,
               median_wait = PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY DATEDIFF_BIG(SECOND, occurred_at, next_time))
                             OVER (PARTITION BY activity_id, next_activity_id),
               p90_wait    = PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY DATEDIFF_BIG(SECOND, occurred_at, next_time))
                             OVER (PARTITION BY activity_id, next_activity_id)
        FROM ordered
        WHERE next_activity_id IS NOT NULL
    ) o
    GROUP BY o.activity_id, o.next_activity_id;
END;
GO

-- 7.4 · Conformance ---------------------------------------------------------
-- A deliberately simple, explainable check: every directly-follows pair in the
-- log is compared against the allowed edge set. It is not alignment-based
-- conformance (that belongs in PM4Py/ProM), but it runs in-database, needs no
-- extra tooling, and catches the deviations people actually act on.
CREATE OR ALTER PROCEDURE pm.usp_run_conformance
    @process_code VARCHAR(50) = 'loan_application'
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @pdid BIGINT = (SELECT TOP 1 process_definition_id FROM pm.process_definition
                            WHERE process_code = @process_code AND is_active = 1
                            ORDER BY process_definition_id DESC);
    IF @pdid IS NULL RETURN;

    -- Mark deviating transitions on the events themselves.
    ;WITH ordered AS (
        SELECT e.event_id, e.case_id, e.activity_id,
               next_activity_id = LEAD(e.activity_id) OVER (PARTITION BY e.case_id ORDER BY e.occurred_at, e.event_seq)
        FROM   pm.event_log e
        WHERE  e.process_code = @process_code AND e.is_valid_for_mining = 1
    )
    UPDATE e
    SET    is_deviation = 1, deviation_type = N'unexpected_transition'
    FROM   pm.event_log e
    JOIN   ordered o ON o.event_id = e.event_id
    WHERE  o.next_activity_id IS NOT NULL
      AND  NOT EXISTS (SELECT 1 FROM pm.process_edge pe
                       WHERE pe.process_definition_id = @pdid
                         AND pe.from_activity_id = o.activity_id
                         AND pe.to_activity_id   = o.next_activity_id
                         AND pe.is_allowed = 1);

    MERGE pm.conformance_result AS tgt
    USING (
        SELECT case_id,
               deviation_count = SUM(CASE WHEN is_deviation = 1 THEN 1 ELSE 0 END),
               total           = COUNT(*)
        FROM   pm.event_log
        WHERE  process_code = @process_code AND is_valid_for_mining = 1
        GROUP BY case_id
    ) src
      ON tgt.process_definition_id = @pdid AND tgt.case_id = src.case_id
    WHEN MATCHED THEN UPDATE SET
        deviation_count = src.deviation_count,
        fitness = CAST(1.0 - (src.deviation_count * 1.0 / NULLIF(src.total,0)) AS DECIMAL(5,4)),
        is_conformant = CASE WHEN src.deviation_count = 0 THEN 1 ELSE 0 END,
        computed_at = SYSUTCDATETIME()
    WHEN NOT MATCHED THEN
        INSERT (process_definition_id, case_id, deviation_count, fitness, is_conformant)
        VALUES (@pdid, src.case_id, src.deviation_count,
                CAST(1.0 - (src.deviation_count * 1.0 / NULLIF(src.total,0)) AS DECIMAL(5,4)),
                CASE WHEN src.deviation_count = 0 THEN 1 ELSE 0 END);

    UPDATE app
    SET    deviation_count = c.deviation_count
    FROM   core.loan_applications app
    JOIN   pm.conformance_result c ON c.case_id = app.application_id
    WHERE  c.process_definition_id = @pdid;
END;
GO

-- 7.5 · One call to refresh everything (nightly SQL Agent job).
CREATE OR ALTER PROCEDURE pm.usp_refresh_all
AS
BEGIN
    SET NOCOUNT ON;
    EXEC pm.usp_refresh_case_metrics;
    EXEC pm.usp_refresh_variants   @process_code = 'loan_application';
    EXEC pm.usp_refresh_variants   @process_code = 'fraud_case';
    EXEC pm.usp_refresh_variants   @process_code = 'rule_authoring';
    EXEC pm.usp_run_conformance    @process_code = 'loan_application';
    EXEC pm.usp_refresh_dfg        @process_code = 'loan_application';
END;
GO

PRINT 'FILE 4 complete — audit triggers, event-log projection, pm.usp_log_event, refresh procedures.';
GO
