/* ============================================================================
   FinSight AI  ·  SQL Server (SSMS) Schema
   FILE 5 of 5 — Process-mining views
   ----------------------------------------------------------------------------
   These are the queries a process-mining project actually runs. Each one is
   deliberately a plain view so it can be opened in SSMS, pointed at from Power
   BI, or exported straight to PM4Py / Celonis / Disco / ProM.

   Run AFTER 04-triggers-and-procedures.sql.
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
   1. THE EXPORT VIEW  — start here
   ---------------------------------------------------------------------------
   A flat event log in the shape every process-mining tool expects. The XES
   standard names its columns case:concept:name / concept:name / time:timestamp
   / org:resource / lifecycle:transition; those names are illegal-ish in SQL, so
   the mapping is given per column below and applied at export time.

     case_id     -> case:concept:name
     activity    -> concept:name
     event_time  -> time:timestamp
     resource    -> org:resource
     lifecycle   -> lifecycle:transition
     instance_id -> concept:instance
########################################################################### */
CREATE OR ALTER VIEW pm.vw_event_log_flat
AS
SELECT
    e.event_id,
    e.process_code,
    e.case_id,
    e.case_key,
    e.event_seq,
    activity            = e.activity_name,
    activity_code       = a.activity_code,
    activity_category   = a.activity_category,
    lifecycle           = e.lifecycle_transition,
    instance_id         = e.activity_instance_id,
    event_time          = e.occurred_at,
    -- Named person if there is one, else the module that did the work, else
    -- the actor class ("customer", "external_api"). Never a bare "system" for
    -- a step a borrower performed.
    resource            = COALESCE(e.resource_name, e.component_code, e.resource_type_code),
    resource_id         = e.resource_id,
    resource_type       = e.resource_type_code,
    resource_role       = e.resource_role_snapshot,
    resource_branch     = e.resource_branch_snapshot,
    e.is_automated,
    e.component_code,
    e.model_name,
    e.model_version,
    e.ai_confidence,
    e.human_overrode_ai,
    duration_sec        = e.duration_ms / 1000.0,
    waiting_sec         = e.waiting_ms / 1000.0,
    since_previous_sec  = e.since_previous_ms / 1000.0,
    e.wait_reason_code,
    e.is_rework,
    e.attempt_no,
    e.is_handover,
    e.is_exception,
    e.is_deviation,
    e.sla_met,
    e.activity_cost,
    e.from_status,
    e.to_status,
    -- case-level attributes, denormalised so a single CSV is self-contained
    case_product        = app.loan_product,
    case_amount         = app.loan_amount,
    case_branch         = app.branch_code,
    case_status         = app.status_code,
    case_variant_id     = app.variant_id,
    case_channel        = app.channel_code,
    e.environment_code,
    e.attributes
FROM        pm.event_log e
JOIN        pm.activity  a   ON a.activity_id = e.activity_id
LEFT JOIN   core.loan_applications app
       ON   app.application_id = e.case_id AND e.process_code = 'loan_application'
WHERE       e.is_valid_for_mining = 1;
GO


/* ###########################################################################
   2. CASE OVERVIEW  — one row per case, for filtering and cohorting
########################################################################### */
CREATE OR ALTER VIEW pm.vw_case_overview
AS
SELECT
    case_id             = app.application_id,
    case_key            = CONVERT(NVARCHAR(60), app.application_id),
    customer_name       = c.first_name + N' ' + c.last_name,
    product             = p.display_name,
    product_code        = app.loan_product,
    app.loan_amount,
    app.branch_code,
    status              = s.display_name,
    stage_group         = s.stage_group,
    is_closed           = app.is_case_closed,
    started_at          = app.case_started_at,
    ended_at            = app.case_ended_at,
    duration_hours      = app.case_duration_sec / 3600.0,
    business_hours      = app.case_business_sec / 3600.0,
    -- A case still open is measured to NOW, otherwise every open case looks
    -- artificially fast and the average lead time is quietly optimistic.
    age_hours           = DATEDIFF_BIG(SECOND, app.case_started_at,
                                       ISNULL(app.case_ended_at, SYSUTCDATETIME())) / 3600.0,
    idle_hours          = DATEDIFF_BIG(SECOND, app.last_activity_at, SYSUTCDATETIME()) / 3600.0,
    app.event_count,
    app.activity_count,
    app.touch_count,
    app.rework_count,
    app.handover_count,
    app.reopened_count,
    app.deviation_count,
    app.automation_ratio,
    app.is_straight_through,
    app.sla_breached,
    app.variant_id,
    variant_case_count  = v.case_count,
    app.channel_code,
    officer             = uo.full_name,
    app.environment_code
FROM        core.loan_applications app
JOIN        core.customers c            ON c.customer_id = app.customer_id
JOIN        ref.loan_product_type p     ON p.product_code = app.loan_product
JOIN        ref.application_status s    ON s.status_code = app.status_code
LEFT JOIN   pm.case_variant v           ON v.variant_id = app.variant_id
LEFT JOIN   ops.users uo                ON uo.user_id = app.assigned_officer_id;
GO


/* ###########################################################################
   3. DIRECTLY-FOLLOWS GRAPH  — the process map itself
   ---------------------------------------------------------------------------
   Every process map you have ever seen is this: for each pair of consecutive
   activities in a case, how often does the transition happen and how long does
   it take. Node = activity, edge = row of this view.
########################################################################### */
CREATE OR ALTER VIEW pm.vw_directly_follows
AS
WITH ordered AS (
    SELECT  e.process_code, e.case_id, e.activity_id, e.occurred_at, e.event_seq,
            next_activity_id = LEAD(e.activity_id) OVER (PARTITION BY e.process_code, e.case_id ORDER BY e.occurred_at, e.event_seq),
            next_time        = LEAD(e.occurred_at) OVER (PARTITION BY e.process_code, e.case_id ORDER BY e.occurred_at, e.event_seq)
    FROM    pm.event_log e
    WHERE   e.is_valid_for_mining = 1
      AND   e.lifecycle_transition = 'complete'
)
SELECT
    o.process_code,
    from_activity   = af.activity_name,
    to_activity     = at2.activity_name,
    from_code       = af.activity_code,
    to_code         = at2.activity_code,
    transition_count= COUNT(*),
    case_count      = COUNT(DISTINCT o.case_id),
    avg_wait_hours  = AVG(DATEDIFF_BIG(SECOND, o.occurred_at, o.next_time)) / 3600.0,
    max_wait_hours  = MAX(DATEDIFF_BIG(SECOND, o.occurred_at, o.next_time)) / 3600.0,
    total_wait_hours= SUM(DATEDIFF_BIG(SECOND, o.occurred_at, o.next_time)) / 3600.0,
    -- Is this edge in the designed process, or did it emerge on its own?
    is_designed     = MAX(CASE WHEN pe.edge_id IS NOT NULL THEN 1 ELSE 0 END)
FROM        ordered o
JOIN        pm.activity af  ON af.activity_id  = o.activity_id
JOIN        pm.activity at2 ON at2.activity_id = o.next_activity_id
LEFT JOIN   pm.process_definition pd ON pd.process_code = o.process_code AND pd.is_active = 1
LEFT JOIN   pm.process_edge pe
       ON   pe.process_definition_id = pd.process_definition_id
      AND   pe.from_activity_id = o.activity_id
      AND   pe.to_activity_id   = o.next_activity_id
      AND   pe.is_allowed = 1
WHERE       o.next_activity_id IS NOT NULL
GROUP BY    o.process_code, af.activity_name, at2.activity_name, af.activity_code, at2.activity_code;
GO


/* ###########################################################################
   4. ACTIVITY PERFORMANCE  — where the time actually goes
########################################################################### */
CREATE OR ALTER VIEW pm.vw_activity_performance
AS
SELECT
    a.activity_code,
    a.activity_name,
    a.activity_category,
    e.process_code,
    execution_count     = COUNT(*),
    case_count          = COUNT(DISTINCT e.case_id),
    -- Executions per case above 1.0 is the cleanest rework signal there is.
    executions_per_case = CAST(COUNT(*) * 1.0 / NULLIF(COUNT(DISTINCT e.case_id), 0) AS DECIMAL(10,3)),
    avg_service_sec     = AVG(e.duration_ms) / 1000.0,
    max_service_sec     = MAX(e.duration_ms) / 1000.0,
    avg_queue_sec       = AVG(e.waiting_ms) / 1000.0,
    avg_gap_before_sec  = AVG(e.since_previous_ms) / 1000.0,
    expected_sec        = a.expected_duration_sec,
    -- >1 means this step routinely runs slower than its design target
    slowdown_factor     = CAST((AVG(e.duration_ms) / 1000.0)
                               / NULLIF(a.expected_duration_sec, 0) AS DECIMAL(10,2)),
    automated_pct       = CAST(SUM(CASE WHEN e.is_automated = 1 THEN 1.0 ELSE 0 END)
                               / NULLIF(COUNT(*),0) * 100 AS DECIMAL(5,2)),
    rework_pct          = CAST(SUM(CASE WHEN e.is_rework = 1 THEN 1.0 ELSE 0 END)
                               / NULLIF(COUNT(*),0) * 100 AS DECIMAL(5,2)),
    exception_pct       = CAST(SUM(CASE WHEN e.is_exception = 1 THEN 1.0 ELSE 0 END)
                               / NULLIF(COUNT(*),0) * 100 AS DECIMAL(5,2)),
    override_pct        = CAST(SUM(CASE WHEN e.human_overrode_ai = 1 THEN 1.0 ELSE 0 END)
                               / NULLIF(COUNT(*),0) * 100 AS DECIMAL(5,2)),
    total_cost          = SUM(e.activity_cost),
    a.is_value_adding
FROM    pm.event_log e
JOIN    pm.activity  a ON a.activity_id = e.activity_id
WHERE   e.is_valid_for_mining = 1
GROUP BY a.activity_code, a.activity_name, a.activity_category, e.process_code,
         a.expected_duration_sec, a.is_value_adding;
GO


/* ###########################################################################
   5. BOTTLENECKS  — ranked by TOTAL time lost, not average
   ---------------------------------------------------------------------------
   A step averaging 4 hours across 5 cases matters far less than one averaging
   20 minutes across 5,000. Ranking by total time consumed is what points at
   the fix worth making.
########################################################################### */
CREATE OR ALTER VIEW pm.vw_bottlenecks
AS
SELECT
    df.process_code,
    df.from_activity,
    df.to_activity,
    df.transition_count,
    df.avg_wait_hours,
    df.max_wait_hours,
    df.total_wait_hours,
    -- DECIMAL(9,2), not (5,2): on a small or heavily-skewed log a single edge's
    -- share can exceed 100% of the (net) total and overflow a 3-digit type.
    share_of_total_wait = CAST(df.total_wait_hours * 100.0
        / NULLIF(SUM(df.total_wait_hours) OVER (PARTITION BY df.process_code), 0) AS DECIMAL(9,2)),
    df.is_designed
FROM pm.vw_directly_follows df;
GO


/* ###########################################################################
   6. HANDOVER OF WORK  — the social network of the process
   ---------------------------------------------------------------------------
   Who passes work to whom, how often, and how long the baton sits in mid-air.
   Long handover waits between two specific people, or a resource that only
   ever receives and never passes on, are classic findings here.
########################################################################### */
CREATE OR ALTER VIEW pm.vw_handover_of_work
AS
WITH ordered AS (
    SELECT  e.process_code, e.case_id, e.resource_id, e.occurred_at, e.event_seq,
            e.resource_name, e.resource_role_snapshot, e.resource_branch_snapshot,
            next_resource_id   = LEAD(e.resource_id)   OVER (PARTITION BY e.process_code, e.case_id ORDER BY e.occurred_at, e.event_seq),
            next_resource_name = LEAD(e.resource_name) OVER (PARTITION BY e.process_code, e.case_id ORDER BY e.occurred_at, e.event_seq),
            next_role          = LEAD(e.resource_role_snapshot) OVER (PARTITION BY e.process_code, e.case_id ORDER BY e.occurred_at, e.event_seq),
            next_time          = LEAD(e.occurred_at)   OVER (PARTITION BY e.process_code, e.case_id ORDER BY e.occurred_at, e.event_seq)
    FROM    pm.event_log e
    WHERE   e.is_valid_for_mining = 1 AND e.resource_id IS NOT NULL
)
SELECT
    o.process_code,
    from_resource   = o.resource_name,
    to_resource     = o.next_resource_name,
    from_role       = o.resource_role_snapshot,
    to_role         = o.next_role,
    branch          = o.resource_branch_snapshot,
    handover_count  = COUNT(*),
    case_count      = COUNT(DISTINCT o.case_id),
    avg_wait_hours  = AVG(DATEDIFF_BIG(SECOND, o.occurred_at, o.next_time)) / 3600.0,
    max_wait_hours  = MAX(DATEDIFF_BIG(SECOND, o.occurred_at, o.next_time)) / 3600.0
FROM   ordered o
WHERE  o.next_resource_id IS NOT NULL
  AND  o.next_resource_id <> o.resource_id
GROUP BY o.process_code, o.resource_name, o.next_resource_name,
         o.resource_role_snapshot, o.next_role, o.resource_branch_snapshot;
GO


/* ###########################################################################
   7. REWORK  — activities that repeat inside one case
########################################################################### */
CREATE OR ALTER VIEW pm.vw_rework
AS
SELECT
    e.process_code,
    e.case_id,
    a.activity_code,
    a.activity_name,
    occurrences     = COUNT(*),
    first_at        = MIN(e.occurred_at),
    last_at         = MAX(e.occurred_at),
    rework_span_hours = DATEDIFF_BIG(SECOND, MIN(e.occurred_at), MAX(e.occurred_at)) / 3600.0,
    wasted_sec      = SUM(e.duration_ms) / 1000.0 - MIN(e.duration_ms) / 1000.0,
    wasted_cost     = SUM(e.activity_cost) - MIN(e.activity_cost)
FROM     pm.event_log e
JOIN     pm.activity  a ON a.activity_id = e.activity_id
WHERE    e.is_valid_for_mining = 1 AND e.lifecycle_transition = 'complete'
GROUP BY e.process_code, e.case_id, a.activity_code, a.activity_name
HAVING   COUNT(*) > 1;
GO


/* ###########################################################################
   8. VARIANT SUMMARY  — the 80/20 view
########################################################################### */
CREATE OR ALTER VIEW pm.vw_variant_summary
AS
SELECT
    v.variant_id,
    v.process_code,
    v.activity_sequence,
    v.activity_count,
    v.case_count,
    share_pct = CAST(v.case_count * 100.0
        / NULLIF(SUM(v.case_count) OVER (PARTITION BY v.process_code), 0) AS DECIMAL(5,2)),
    cumulative_pct = CAST(SUM(v.case_count) OVER (
            PARTITION BY v.process_code ORDER BY v.case_count DESC
            ROWS UNBOUNDED PRECEDING) * 100.0
        / NULLIF(SUM(v.case_count) OVER (PARTITION BY v.process_code), 0) AS DECIMAL(5,2)),
    avg_duration_hours = v.avg_duration_sec / 3600.0,
    max_duration_hours = v.max_duration_sec / 3600.0,
    v.approval_rate,
    v.rework_rate,
    v.is_happy_path,
    v.is_conformant,
    v.first_seen_at,
    v.last_seen_at
FROM pm.case_variant v;
GO


/* ###########################################################################
   9. AUTOMATION IMPACT  — the question this project exists to answer
   ---------------------------------------------------------------------------
   FinSight automates OCR, tampering detection, face matching and compliance.
   This view says, per component: how much work it did, how fast, how confident
   it was, and how often a human disagreed. A component with a high override
   rate is not saving anyone time — it is generating review work.
########################################################################### */
CREATE OR ALTER VIEW pm.vw_automation_impact
AS
SELECT
    c.component_code,
    c.display_name,
    c.module_path,
    executions      = COUNT(*),
    cases_touched   = COUNT(DISTINCT e.case_id),
    avg_duration_sec= AVG(e.duration_ms) / 1000.0,
    p_expected_sec  = c.typical_ms / 1000.0,
    total_hours     = SUM(e.duration_ms) / 3600000.0,
    avg_confidence  = AVG(e.ai_confidence),
    low_confidence_pct = CAST(SUM(CASE WHEN e.ai_confidence < 0.60 THEN 1.0 ELSE 0 END)
                              / NULLIF(COUNT(*),0) * 100 AS DECIMAL(5,2)),
    override_count  = SUM(CASE WHEN e.human_overrode_ai = 1 THEN 1 ELSE 0 END),
    override_pct    = CAST(SUM(CASE WHEN e.human_overrode_ai = 1 THEN 1.0 ELSE 0 END)
                           / NULLIF(COUNT(*),0) * 100 AS DECIMAL(5,2)),
    exception_pct   = CAST(SUM(CASE WHEN e.is_exception = 1 THEN 1.0 ELSE 0 END)
                           / NULLIF(COUNT(*),0) * 100 AS DECIMAL(5,2)),
    model_versions  = COUNT(DISTINCT e.model_version)
FROM     pm.event_log e
JOIN     ref.automation_component c ON c.component_code = e.component_code
WHERE    e.is_valid_for_mining = 1
GROUP BY c.component_code, c.display_name, c.module_path, c.typical_ms;
GO


/* ###########################################################################
   10. RESOURCE WORKLOAD  — capacity and specialisation
########################################################################### */
CREATE OR ALTER VIEW pm.vw_resource_workload
AS
SELECT
    u.user_id,
    u.full_name,
    u.role_code,
    u.team_name,
    u.branch_code,
    events_performed    = COUNT(*),
    cases_touched       = COUNT(DISTINCT e.case_id),
    distinct_activities = COUNT(DISTINCT e.activity_id),
    total_work_hours    = SUM(e.duration_ms) / 3600000.0,
    avg_work_sec        = AVG(e.duration_ms) / 1000.0,
    first_event         = MIN(e.occurred_at),
    last_event          = MAX(e.occurred_at),
    rework_pct          = CAST(SUM(CASE WHEN e.is_rework = 1 THEN 1.0 ELSE 0 END)
                               / NULLIF(COUNT(*),0) * 100 AS DECIMAL(5,2)),
    sla_breach_pct      = CAST(SUM(CASE WHEN e.sla_met = 0 THEN 1.0 ELSE 0 END)
                               / NULLIF(COUNT(*),0) * 100 AS DECIMAL(5,2)),
    override_count      = SUM(CASE WHEN e.human_overrode_ai = 1 THEN 1 ELSE 0 END),
    estimated_cost      = SUM(e.duration_ms) / 3600000.0 * ISNULL(u.fte_cost_per_hour, 0)
FROM     pm.event_log e
JOIN     ops.users u ON u.user_id = e.resource_id
WHERE    e.is_valid_for_mining = 1
GROUP BY u.user_id, u.full_name, u.role_code, u.team_name, u.branch_code, u.fte_cost_per_hour;
GO


/* ###########################################################################
   11. SLA COMPLIANCE  — per activity and per status
########################################################################### */
CREATE OR ALTER VIEW pm.vw_sla_compliance
AS
SELECT
    a.activity_code,
    a.activity_name,
    sla_name        = s.sla_name,
    target_hours    = s.target_sec / 3600.0,
    measured        = COUNT(*),
    met             = SUM(CASE WHEN e.sla_met = 1 THEN 1 ELSE 0 END),
    breached        = SUM(CASE WHEN e.sla_met = 0 THEN 1 ELSE 0 END),
    compliance_pct  = CAST(SUM(CASE WHEN e.sla_met = 1 THEN 1.0 ELSE 0 END)
                           / NULLIF(COUNT(*),0) * 100 AS DECIMAL(5,2)),
    avg_breach_hours= AVG(CASE WHEN e.sla_met = 0 THEN e.sla_breach_ms END) / 3600000.0,
    worst_breach_hours = MAX(e.sla_breach_ms) / 3600000.0
FROM        pm.event_log e
JOIN        pm.activity  a ON a.activity_id = e.activity_id
LEFT JOIN   pm.sla_definition s ON s.sla_id = e.sla_id
WHERE       e.is_valid_for_mining = 1 AND e.sla_met IS NOT NULL
GROUP BY    a.activity_code, a.activity_name, s.sla_name, s.target_sec;
GO


/* ###########################################################################
   12. DOCUMENT PIPELINE  — the AI sub-process, straight from the timings the
       backend already measures
########################################################################### */
CREATE OR ALTER VIEW pm.vw_document_pipeline
AS
SELECT
    d.document_id,
    d.application_id,
    doc_type        = dt.display_name,
    d.document_type,
    d.provider_used,
    d.was_preprocessed,
    d.attempt_no,
    is_resubmission = CASE WHEN d.resubmission_of_document_id IS NOT NULL THEN 1 ELSE 0 END,
    queue_wait_sec  = d.queue_wait_ms / 1000.0,
    preprocess_sec  = d.preprocess_ms / 1000.0,
    extract_sec     = d.extract_ms    / 1000.0,
    tampering_sec   = d.tampering_ms  / 1000.0,
    face_sec        = d.face_ms       / 1000.0,
    mask_sec        = d.mask_ms       / 1000.0,
    total_pipeline_sec = (ISNULL(d.preprocess_ms,0) + ISNULL(d.extract_ms,0)
                        + ISNULL(d.tampering_ms,0)  + ISNULL(d.face_ms,0)
                        + ISNULL(d.mask_ms,0)) / 1000.0,
    -- Wall-clock minus the sum of the stages = time the document spent doing
    -- nothing at all. That gap is usually where the real delay lives.
    idle_sec        = DATEDIFF_BIG(SECOND, d.uploaded_at,
                        ISNULL(d.processing_completed_at, d.updated_at))
                      - (ISNULL(d.preprocess_ms,0) + ISNULL(d.extract_ms,0)
                       + ISNULL(d.tampering_ms,0)  + ISNULL(d.face_ms,0)
                       + ISNULL(d.mask_ms,0)) / 1000.0,
    d.ocr_confidence,
    d.needs_review,
    d.tampering_risk_tier,
    d.ocr_status,
    d.verification_status,
    d.uploaded_at,
    d.processing_completed_at,
    expected_sec    = dt.typical_processing_ms / 1000.0
FROM  core.uploaded_documents d
JOIN  ref.document_type dt ON dt.document_type_code = d.document_type;
GO


/* ###########################################################################
   13. STUCK CASES  — the operational payoff of all of the above
########################################################################### */
CREATE OR ALTER VIEW pm.vw_stuck_cases
AS
SELECT
    app.application_id,
    customer        = c.first_name + N' ' + c.last_name,
    product         = app.loan_product,
    status          = s.display_name,
    stage_group     = s.stage_group,
    idle_hours      = DATEDIFF_BIG(SECOND, app.last_activity_at, SYSUTCDATETIME()) / 3600.0,
    target_hours    = s.target_hours,
    -- >1 means the case has been sitting longer than its status is designed to take
    overdue_factor  = CAST(DATEDIFF_BIG(SECOND, app.last_activity_at, SYSUTCDATETIME())
                           / NULLIF(s.target_hours * 3600.0, 0) AS DECIMAL(10,2)),
    is_waiting_state = s.is_waiting_state,
    wait_reason     = w.display_name,
    is_customer_side = w.is_customer_side,
    owner           = u.full_name,
    app.branch_code,
    app.rework_count,
    app.deviation_count,
    age_hours       = DATEDIFF_BIG(SECOND, app.case_started_at, SYSUTCDATETIME()) / 3600.0
FROM        core.loan_applications app
JOIN        core.customers c         ON c.customer_id = app.customer_id
JOIN        ref.application_status s ON s.status_code = app.status_code
LEFT JOIN   ref.wait_reason w        ON w.wait_reason_code = app.current_wait_reason
LEFT JOIN   ops.users u              ON u.user_id = app.current_owner_id
WHERE       app.is_case_closed = 0
  AND       app.environment_code = 'prod';
GO


/* ###########################################################################
   14. OCEL EXPORT VIEWS  (object-centric event log)
########################################################################### */
CREATE OR ALTER VIEW pm.vw_ocel_events
AS
SELECT
    ocel_id         = e.event_id,
    ocel_activity   = e.activity_name,
    ocel_timestamp  = e.occurred_at,
    ocel_resource   = e.resource_name,
    e.process_code,
    e.duration_ms,
    e.attributes
FROM pm.event_log e
WHERE e.is_valid_for_mining = 1;
GO

CREATE OR ALTER VIEW pm.vw_ocel_relations
AS
SELECT
    ocel_event_id   = l.event_id,
    ocel_object_id  = o.object_id,
    ocel_object_type= o.object_type_code,
    ocel_qualifier  = l.qualifier,
    object_key      = o.display_key
FROM        pm.event_object_link l
JOIN        pm.object_instance   o ON o.object_id = l.object_id;
GO


/* ###########################################################################
   15. SAMPLE QUERIES  (copy into a new SSMS window)
###########################################################################

-- The process map, biggest edges first
SELECT TOP 30 * FROM pm.vw_directly_follows
WHERE process_code = 'loan_application' ORDER BY transition_count DESC;

-- Where is the time going?
SELECT TOP 15 * FROM pm.vw_bottlenecks ORDER BY total_wait_hours DESC;

-- How many variants cover 80% of cases?
SELECT * FROM pm.vw_variant_summary
WHERE process_code = 'loan_application' AND cumulative_pct <= 80
ORDER BY case_count DESC;

-- Do the AI components actually save time, or create review work?
SELECT * FROM pm.vw_automation_impact ORDER BY override_pct DESC;

-- Which documents get resubmitted, and what did that cost?
SELECT document_type, COUNT(*) AS resubmissions, AVG(total_pipeline_sec) AS avg_sec
FROM pm.vw_document_pipeline WHERE is_resubmission = 1
GROUP BY document_type ORDER BY resubmissions DESC;

-- Cases sitting longer than their status allows
SELECT TOP 25 * FROM pm.vw_stuck_cases WHERE overdue_factor > 1 ORDER BY overdue_factor DESC;

-- Working hours vs wall-clock for one case
SELECT wall_clock_hours = DATEDIFF(SECOND, case_started_at, case_ended_at) / 3600.0,
       business_hours   = b.business_seconds / 3600.0
FROM   core.loan_applications a
CROSS APPLY pm.fn_business_seconds(a.case_started_at, a.case_ended_at,
                                   'IN_BRANCH', N'India Standard Time') b
WHERE  a.application_id = '<paste-a-guid-here>';

-- Export a flat event log for PM4Py / Disco (then save the grid as CSV)
SELECT case_id, activity, event_time, resource, lifecycle
FROM   pm.vw_event_log_flat
WHERE  process_code = 'loan_application'
ORDER  BY case_id, event_seq;

########################################################################### */

PRINT 'FILE 5 complete — 15 process-mining views created.';
GO
