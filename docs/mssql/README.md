# FinSight AI — SQL Server database + process-mining layer

The PostgreSQL design in [`../finsight-database-schema.sql`](../finsight-database-schema.sql)
translated to T-SQL for SQL Server / SSMS, **plus** the schema changes needed to make the
system minable end-to-end.

---

## 1. How to run it

Open SSMS, connect to your instance, and execute these in order — each in its own query
window, top to bottom:

| # | File | What it creates |
|---|------|-----------------|
| 1 | [`01-database-and-reference.sql`](01-database-and-reference.sql) | `FinSight` database, 8 schemas, 20 reference tables (seeded) |
| 2 | [`02-core-tables.sql`](02-core-tables.sql) | The 31 operational tables |
| 3 | [`03-process-mining.sql`](03-process-mining.sql) | Event log, OCEL objects, variants, SLA, calendar, conformance |
| 4 | [`04-triggers-and-procedures.sql`](04-triggers-and-procedures.sql) | Audit triggers, event projection, `pm.usp_log_event`, refresh jobs |
| 5 | [`05-views-process-mining.sql`](05-views-process-mining.sql) | 15 analysis/export views |
| 6 | [`06-ui-support-tables.sql`](06-ui-support-tables.sql) | "Next action" per status, policy tickets, generated reports |
| 7 | [`07-seed-demo-data.sql`](07-seed-demo-data.sql) | Optional demo applications (not used on the live database) |
| 8 | [`08-agentic-layer.sql`](08-agentic-layer.sql) | `agent` schema (runs, steps, tool calls), fraud assessments, identity keys |
| 9 | [`09-decision-workflow.sql`](09-decision-workflow.sql) | Remarks / reason / wait reason on every status change; next-action labels for the officer → Head → borrower workflow |
| 10 | [`10-aadhaar-last4.sql`](10-aadhaar-last4.sql) | `customers.aadhaar_last4` — only the masked Aadhaar (last 4 digits) is kept |
| 11 | [`11-consent-backfill.sql`](11-consent-backfill.sql) | `form_data.consent` for web applications submitted before the wizard's five declarations were saved (new submissions record them automatically); the policy check's consent rule reads it |

Every table and column is catalogued in **[`db.md`](db.md)** — generated from the built
database, so it matches exactly what the scripts produce.

```bash
sqlcmd -S localhost -E -f 65001 -b -i 01-database-and-reference.sql -i 02-core-tables.sql -i 03-process-mining.sql -i 04-triggers-and-procedures.sql -i 05-views-process-mining.sql
```

**Requires SQL Server 2017 or newer** (`STRING_AGG` in the variant refresh). Everything else
works on 2016+. Nothing here needs Enterprise Edition — columnstore and partitioning have been
in every edition since 2016 SP1.

Each script sets `QUOTED_IDENTIFIER ON` explicitly. SSMS defaults it on, `sqlcmd` doesn't, and
filtered indexes fail without it.

### Verified

All five scripts were executed against SQL Server 2019 Express (LocalDB), followed by an
end-to-end test — insert a case, drive it through six status changes, log AI and human events,
run the refresh procedures, read every view. What that produced:

| | |
|---|---|
| Tables | **72** (`ref` 21, `pm` 20, `core` 9, `ops` 6, `comp` 5, `fraud` 5, `audit` 3, `risk` 3) |
| Views / procedures / triggers | 15 / 8 / 37 |
| Foreign keys / indexes | 147 / 144 |
| Seeded activities / normative edges | 65 / 55 |

Confirmed working end-to-end: automatic status→event projection, chronological sequence
assignment across both feeds, backward-transition detection flagged as rework, terminal status
auto-closing the case and computing duration, idempotency (the same key logged twice stored
once), handover detection across an intervening customer step, the OCEL links, working-hours vs
wall-clock (a 30-hour case measured as 7.4 **working** hours), and the audit trail.

The test database was dropped afterwards; nothing was left on the instance.

### What changed in translation

| PostgreSQL | SQL Server | Note |
|---|---|---|
| 14 `ENUM` types | 14 reference tables in `ref` | Enums can't carry metadata. `ref.application_status.sort_order` **is** the normative process order and `is_terminal` defines case completion — conformance checking needs both. |
| `UUID` / `gen_random_uuid()` | `UNIQUEIDENTIFIER` / `NEWSEQUENTIALID()` | Sequential GUIDs avoid index fragmentation on high-insert tables. |
| `TIMESTAMPTZ` | `DATETIME2(3)`, UTC | Timezone handled by `pm.calendar` + `AT TIME ZONE`. Mixed timezones silently corrupt duration analysis. |
| `JSONB` | `NVARCHAR(MAX)` + `CHECK (ISJSON(x)=1)` | Query with `JSON_VALUE` / `OPENJSON`. |
| `VECTOR(384)` (pgvector) | `VARBINARY(MAX)` | SQL Server 2025 has a native `VECTOR` type; the live compliance module uses BM25 anyway. |
| Partial indexes | Filtered indexes | Direct equivalent. |
| `PARTITION BY RANGE` | Partition function + scheme | `PF_AuditMonthly` / `PS_AuditMonthly`. |
| `current_setting('session_vars.actor_id')` | `SESSION_CONTEXT(N'actor_id')` | Set via `ops.usp_set_session_context` once per request. |
| One generic audit trigger function | Generated per-table triggers | T-SQL triggers are table-bound; file 04 generates them from a list so they can't drift. |
| `TEXT[]` | JSON array in `NVARCHAR(MAX)` | Only used by `notification_templates.variables`. |

Two behavioural notes worth knowing:

- **`condition` → `condition_expr`** on the two compliance tables. Not reserved in T-SQL, but
  it collides with enough tooling to be worth renaming while nothing depends on it yet.
- **`updated_at` maintenance is folded into the audit UPDATE trigger.** In PostgreSQL these
  were separate triggers; in SQL Server a separate one would re-fire the audit trigger
  (nested triggers are on by default) and log every edit twice.

---

## 2. Yes — columns are missing. Here's the analysis.

The existing schema records **state**. Process mining needs **events**. Those are different
data models, and the gap is not small.

An event log is a table where each row says *"in case X, activity Y happened at time T,
performed by resource R"*. Every algorithm — discovery, conformance, bottleneck analysis,
variant analysis — consumes that and nothing else. The current schema can't produce it:
`loan_applications.status = 'underReview'` tells you where a case **is**, never how it got
there, how long each hop took, or who did the work.

### 2.1 The structural gap: no event log at all

`application_status_history` is the closest thing, and `business_events` is close too, but
both fall short in the same three ways:

1. **No ordering key.** Two status changes can land in the same millisecond (an automated
   cascade emits several at once). Without an explicit sequence, the directly-follows graph —
   the backbone of every process map — is non-deterministic and the *discovered model changes
   between refreshes*.
2. **No duration.** One timestamp per row means you can measure the gap *between* activities
   but never how long an activity itself took. You cannot separate **queue time** from **work
   time**, which is the entire point of a bottleneck analysis.
3. **No resource dimension beyond a bare user id.** No role, team, or branch snapshot, so
   organisational mining (handover-of-work, social network, workload) is impossible.

Added: **`pm.event_log`** (file 03) — ~60 columns covering identity/ordering, activity +
XES lifecycle, five timestamp forms, the resource dimension with point-in-time snapshots,
the automation/AI dimension, rework and exception flags, SLA, cost, and a JSON payload.

### 2.2 Column additions to existing tables

| Table | Added | Why it's needed |
|---|---|---|
| **loan_applications** | `case_started_at`, `case_ended_at`, `case_duration_sec`, `case_business_sec`, `is_case_closed` | `applied_at`/`completed_at` are nullable and business-defined. Throughput time needs unambiguous anchors. |
| | `variant_id`, `event_count`, `touch_count`, `rework_count`, `handover_count`, `deviation_count`, `automation_ratio`, `is_straight_through` | Per-case aggregates. Recomputing these from the event log on every dashboard load is what makes naive PM setups unusably slow. |
| | `sla_due_at`, `sla_breached`, `priority`, `queue_entered_at`, `current_owner_id`, `current_owner_since`, `current_wait_reason`, `last_activity_at` | Queue/SLA analysis and "stuck case" detection. |
| | `channel_code`, `process_version`, `environment_code`, `reopened_count` | Segmentation. `process_version` matters when you change the workflow: cases started under the old design must be compared against the model they actually ran under, or they all look non-conformant. |
| **application_status_history** | `event_seq` | Deterministic ordering — see (1) above. |
| | `duration_in_previous_sec`, `business_duration_sec` | Time-in-state. Computing it with `LAG()` over millions of rows is the single most expensive query in a PM workload; written once at insert by trigger instead. |
| | `is_automated`, `component_code`, `wait_reason_code` | Was anyone actually working during that wait, or was it a queue? |
| | `reason_code`, `is_rework`, `is_backward_transition`, `sla_due_at`, `sla_met` | `change_reason` is unqueryable prose. `reason_code` is groupable: *"28% of returns were reason = blurry_doc"*. |
| **uploaded_documents** | `queued_at`, `processing_started_at`, `processing_completed_at`, `queue_wait_ms`, `processing_ms` | Only `uploaded_at`/`updated_at` exist today, so *"is OCR the bottleneck, or the queue in front of OCR?"* is unanswerable. |
| | `preprocess_ms`, `extract_ms`, `tampering_ms`, `face_ms`, `mask_ms`, `storage_ms` | **The backend already measures every one of these** — the `timings` dict returned by `/extract`, `/document/integrity`, `/face/verify`, `/document/mask` — and then throws them away. Persisting them turns existing instrumentation into a mineable sub-process for free. |
| | `attempt_no`, `resubmission_of_document_id`, `rejection_reason_code`, `rejected_by/at` | Document resubmission is the #1 rework loop in retail lending. Without a link to the document being replaced, a resubmission looks like a brand-new upload and the loop is invisible. |
| | `ocr_confidence`, `needs_review`, `tampering_risk_tier`, `provider_used`, `was_preprocessed`, `page_count` | Outcome slicing without joining three tables. |
| **document_extraction_results** | `provider_name`, `prompt_version`, `retry_count`, `fell_back_to`, `preprocess_applied`, `quality_gate_result`, `started_at`, `completed_at` | The tampering CNN alone has ten versions (v001–v010, v009 live). *"Did the process get faster after v009?"* needs the version on the event. Retries and provider fallbacks are themselves process steps. |
| **field_verification_log** | `is_automated`, `component_code`, `started_at`, `duration_ms`, `machine_value`, `was_overridden`, `overridden_by/at`, `override_reason`, `attempt_no` | **Human-overrides-machine is the highest-value metric in an AI-assisted process** and needs three things that don't exist yet: the machine's answer, the human's answer, and when the human stepped in. |
| **loan_offers** | `sent_at`, `first_viewed_at`, `decided_at`, `customer_decision_sec`, `generated_by_type`, `revision_no`, `revision_of_offer_id` | The offer stage hides two very different delays: how long the *bank* took, and how long the *customer* sat on it. Only the first is a bottleneck you can fix. |
| **disbursements** | `initiated_at`, `settled_at`, `settlement_lag_sec`, `attempt_no`, `failure_reason_code` | External rail latency, and a failure loop that is currently invisible. |
| **fraud_cases** | `first_signal_at`, `detected_at`, `assigned_at`, `triage_started_at`, `triage_completed_at`, `time_to_detect_sec`, `time_to_assign_sec`, `time_to_resolve_sec`, `reopened_count`, `escalation_count`, `sla_*`, `is_false_positive` | MTTD/MTTR — the two numbers a fraud team is judged on — can't be derived from `created_at`/`resolved_at`, because `created_at` is when the *system noticed*, not when the risk appeared. |
| **fraud_signals** | `component_code`, `document_id`, `stage_no`, `compute_ms`, `model_version`, `contributed_to_verdict` | Makes the 10-signal, 2-stage tampering cascade minable as a sub-process: which signal fires first, which is slowest, which never contributes to a confirmed case. |
| **compliance_rule_candidates** | `extracted_at`, `queued_at`, `review_started_at`, `queue_wait_sec`, `review_duration_sec`, `extraction_method`, `extraction_ms` | Queue time is the whole story in maker-checker: drafts sit unreviewed for days and nothing currently records it. |
| **compliance_check_results** | `check_started_at`, `duration_ms`, `rule_version`, `run_id`, `is_automated`, `was_overridden`, `overridden_by`, `triggered_rework` | `rule_version` pins which rule version produced the result — without it, replaying history gives different answers. `run_id` groups all rules evaluated in one pass. |
| **risk_assessments** | `assessment_no`, `previous_score`, `trigger_reason`, `started_at`, `duration_ms`, `was_overridden`, `overridden_score/by`, `feature_snapshot` | Re-assessment is a rework loop; score drift *within* a single case is currently unobservable. |
| **users** | `team_name`, `manager_user_id`, `resource_type_code`, `is_bot`, `calendar_code`, `fte_cost_per_hour`, `hired_at` | Organisational mining needs the org structure, not just a user id. `calendar_code` enables working-hours durations; `fte_cost_per_hour` enables cost mining. |
| **user_sessions** | `channel_code`, `device_type`, `cases_touched`, `events_logged` | Enables task-level mining: how often does an officer context-switch between cases? (Usually a large hidden cost.) |
| **audit_log** | `application_id`, `txn_id`, `changed_columns`, `source_system` | Two real defects. `audit_log` has `entity_id` but **no case id**, so audit rows can't be correlated to a loan without a per-table join. And one user action fires N row-level triggers — without a transaction grouping key, those N rows look like N separate events and inflate every activity count. |
| **notifications** | `application_id`, `responded_at`, `response_latency_sec`, `action_taken`, `reminder_no` | Notifications are process *interventions*. Linking them to a case measures whether a nudge actually moves a stalled case. |
| **application_form_responses** | `was_autofilled`, `autofill_source_document_id`, `was_corrected`, `edit_count`, `first_filled_at`, `time_to_fill_ms` | FinSight autofills from extracted documents. Whether the borrower **accepted or corrected** the autofill is a direct, free measure of OCR quality — and `time_to_fill_ms` exposes the fields causing abandonment. |
| **customers** | `first_application_at`, `application_count` | Repeat borrowers follow measurably shorter paths (documents already on file). First-vs-nth application is one of the strongest variant predictors in lending. |
| **everything** | `environment_code` | The app runs in *"DEMO MODE · ALL DATA IS PLACEHOLDER"*. Demo cases must never contaminate a real process model, so every case and event carries an environment tag and every mining view filters on it. |

### 2.3 New tables in the `pm` schema

| Table | Purpose |
|---|---|
| `pm.event_log` | The event log. Everything else is derived from it. |
| `pm.activity` | Activity dictionary, seeded with **65 real FinSight activities** taken from `api/app/main.py`, the 4-step wizard, and the compliance maker-checker flow. Free-text activity names are the most common way a PM project fails — "Doc Verified" / "document verified" / "Verify Document" become three nodes for one step. |
| `pm.process_definition`, `pm.process_edge` | The **normative model** as an allowed directly-follows edge set — seeded with a 30-step happy path plus 21 tolerated loops (document rework, head-returns, offer renegotiation, disbursement retry). Conformance = comparing the log's edges against these. It's data, not code: edit it as the real workflow settles. |
| `pm.object_type`, `pm.object_instance`, `pm.event_object_link`, `pm.object_relation` | **Object-centric (OCEL 2.0) layer.** Classic PM forces each event into exactly one case, which fits badly here: "Document Uploaded" belongs to the application *and* the document; "Face Verification" spans several documents at once. Flattening either duplicates events (inflating counts) or loses the relationship — the classic convergence/divergence problem. |
| `pm.case_variant`, `pm.case_metrics` | Distinct end-to-end paths and per-case aggregates. Typically 80% of cases follow ~5 variants and the rest explode into hundreds; finding that tail is usually the first real insight. |
| `pm.sla_definition` | SLA per activity/status/product, with `exclude_customer_wait` so staff aren't penalised for customer delay. |
| `pm.calendar`, `pm.business_calendar`, `pm.calendar_holiday` | Working-time calendars. A case submitted Friday 18:05 and picked up Monday 09:35 is 63 wall-clock hours and ~5 working minutes. Without a calendar every weekend becomes a fake bottleneck and the real ones vanish into the noise. |
| `pm.conformance_result`, `pm.deviation` | Per-case fitness and the specific deviations. |
| `pm.dfg_snapshot` | Materialised directly-follows graph. Recomputing the DFG on every dashboard render is the usual reason PM dashboards feel slow. |
| `pm.export_run` | Bookkeeping + watermark for incremental exports to PM4Py / Celonis / Disco / ProM. |
| `pm.status_activity_map`, `pm.number` | Wiring and helper. |

### 2.4 Index design

Two choices matter more than the rest:

- **Clustered index on `(process_code, case_id, occurred_at, event_seq, event_id)`.** Process
  mining almost never reads one event — it reads *whole cases in order*. This makes each trace
  one sequential range scan instead of a random-IO gather.
- **A nonclustered columnstore index** on the analytical columns. PM queries are overwhelmingly
  aggregations across millions of events; columnstore typically gives 5–20× on exactly that
  shape while the rowstore clustered index keeps trace reads fast.

---

## 3. Four processes, not one

This database contains four genuinely distinct processes, all worth mining:

| Process | Case id | Path |
|---|---|---|
| `loan_application` | `application_id` | draft → documents → verification → underwriting → offer → disbursed |
| `document` | `document_id` | upload → preprocess → extract → validate → verify (or reject → resubmit) |
| `fraud_case` | `case_id` | signal → open → assign → triage → escalate? → resolve |
| `rule_authoring` | `candidate_id` | ingest → index → extract → review → approve/reject |

`rule_authoring` is the easiest first win: it's a small, purely human maker-checker queue that
already exists in the code, so it produces a readable process map from very little data.

---

## 4. What the backend must emit

The DB layer is complete; nothing writes to it yet. Wiring, in order of value:

1. **`ops.usp_set_session_context`** at the start of every FastAPI request (actor, session, IP,
   correlation id). Everything else depends on it.
2. **`pm.usp_log_event`** after each meaningful step. `/extract` alone should emit
   `DOC_PREPROCESS`, `DOC_EXTRACT`, `DOC_GUARDRAILS` — it already computes the durations for all
   three in its `timings` dict.
3. **Persist the timings** onto `uploaded_documents` instead of discarding them.
4. **`pm.usp_link_event_object`** for the OCEL links (event → application, document, offer…).
5. **Nightly `pm.usp_refresh_all`** as a SQL Agent job.

Status changes and business events need no wiring — triggers project them into `pm.event_log`
automatically.

Two things that need product decisions, not code: officer/head **approve, reject and reassign**
actions must be persisted (today they're inert UI), and the **override** paths need capturing —
without them the AI-vs-human comparison, which is the most interesting question this system can
answer, stays empty.

---

## 5. Reading the results

Start with these five views ([`05-views-process-mining.sql`](05-views-process-mining.sql)):

| View | Question it answers |
|---|---|
| `pm.vw_directly_follows` | What does the process actually look like? |
| `pm.vw_bottlenecks` | Where is the time going? (Ranked by **total** time lost, not average — a step averaging 4h across 5 cases matters far less than one averaging 20min across 5,000.) |
| `pm.vw_variant_summary` | How many paths cover 80% of cases? |
| `pm.vw_automation_impact` | Do the AI components save time, or generate review work? |
| `pm.vw_stuck_cases` | Which cases are sitting longer than their status allows? |

`pm.vw_event_log_flat` is the export view — its column-to-XES mapping is documented at the top
of the file. Save the grid as CSV and open it directly in PM4Py or Disco.
