/* ===========================================================================
   FinSight · 09 · decision workflow (officer / head / borrower actions)
   ---------------------------------------------------------------------------
   Run after 01-08. Idempotent (CREATE OR ALTER / UPDATE), safe to re-run.
   Run it with:  sqlcmd -S "(localdb)\MSSQLLocalDB" -E -I -f 65001 -i 09-decision-workflow.sql

   The API (api/app/decisions.py) moves an application through its lifecycle:
     documentsProcessing → documentsVerified → underReview
       → (underwritingReview: referred to the Head) → offerGenerated
       → accepted → sanctioned → disbursed          (or rejected / cancelled)
   Each action is ONE UPDATE of core.loan_applications.status_code; the update
   trigger turns it into the status-history row and, from there, the pm event.

   What changes here:
   1. The update trigger now takes WHY the status moved from the session
      context, next to the actor it already took from there:
          change_reason  (free text: the officer's / head's remarks)
          reason_code    (the action: approve, refer_to_head, send_back…)
          wait_reason    (what the case waits for next, ref.wait_reason)
      Set them with sp_set_session_context before the UPDATE. They land on the
      status-history row at INSERT time, so the event-log projection (which
      copies reason_code/reason_text into pm.event_log.attributes) sees them —
      patching the history row afterwards would be too late for that.
   2. The "next action" labels for the statuses the workflow now really uses.
=========================================================================== */
USE FinSight;
GO

CREATE OR ALTER TRIGGER core.trg_loan_applications_update
ON core.loan_applications AFTER UPDATE
AS
BEGIN
    SET NOCOUNT ON;
    IF NOT EXISTS (SELECT 1 FROM inserted) RETURN;

    DECLARE @now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @actor UNIQUEIDENTIFIER = CONVERT(UNIQUEIDENTIFIER, SESSION_CONTEXT(N'actor_id'));
    DECLARE @reason NVARCHAR(1000) = CONVERT(NVARCHAR(1000), SESSION_CONTEXT(N'change_reason'));
    DECLARE @reason_code NVARCHAR(60) = CONVERT(NVARCHAR(60), SESSION_CONTEXT(N'reason_code'));
    DECLARE @wait VARCHAR(40) = CONVERT(VARCHAR(40), SESSION_CONTEXT(N'wait_reason'));
    IF @wait IS NOT NULL AND NOT EXISTS (SELECT 1 FROM ref.wait_reason WHERE wait_reason_code = @wait)
        SET @wait = NULL;   -- an unknown code must not fail the business update

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
            (application_id, from_status, to_status, changed_by, change_reason, reason_code,
             wait_reason_code, created_at, event_seq, duration_in_previous_sec,
             is_backward_transition, is_rework, is_automated, correlation_id)
        SELECT
            i.application_id,
            d.status_code,
            i.status_code,
            @actor,
            @reason,
            @reason_code,
            @wait,
            @now,
            (SELECT ISNULL(MAX(h.event_seq), 0) + 1
             FROM core.application_status_history h
             WHERE h.application_id = i.application_id),
            DATEDIFF(SECOND,
                ISNULL((SELECT MAX(h.created_at)
                        FROM core.application_status_history h
                        WHERE h.application_id = i.application_id), i.created_at),
                @now),
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

-- "What now?" for the statuses the decision workflow moves through.
UPDATE s SET officer_next_action = v.oa, borrower_next_action = v.ba
FROM ref.application_status s
JOIN (VALUES
    ('documentsProcessing', N'Verify Documents',          N'Documents being checked'),
    ('documentsVerified',   N'Start Underwriting Review', N'Wait for officer review'),
    ('underReview',         N'Approve / Refer / Reject',  N'Application under review'),
    ('underwritingReview',  N'Awaiting Head decision',    N'Awaiting final approval'),
    ('offerGenerated',      N'Await Customer',            N'Review & accept loan offer'),
    ('accepted',            N'Sanction',                  N'Wait for sanction'),
    ('sanctioned',          N'Disburse',                  N'Await disbursement'),
    ('disbursed',           NCHAR(8212),                  N'Loan disbursed')  -- NCHAR: sqlcmd without -f 65001 misreads a literal dash
) AS v(code, oa, ba) ON v.code = s.status_code;
GO
