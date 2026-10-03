/* ============================================================================
   FinSight AI  ·  SQL Server (SSMS) Schema
   FILE 7 of 7 — Demo business data
   ----------------------------------------------------------------------------
   Everything the borrower and underwriting-officer screens used to hardcode in
   the src/data JavaScript modules, inserted as real rows. After running this
   the frontend reads all of it over the API; nothing is baked into the
   JavaScript any more.
   (Note: no "slash-star" sequences in these comments — T-SQL nests block
   comments, so a path like src/data/<star>.js would silently open a new one.)

   Rahul Verma is the demo borrower (RolePicker "Borrower" binds to him).
   Priya Sharma is the demo underwriting officer.
   Their applications live in the same table as everyone else's — one database,
   two views of it, which is the whole point.

   RE-RUNNABLE: deletes its own rows first, so you can run it repeatedly.

   Run AFTER 06-ui-support-tables.sql.
============================================================================ */

USE FinSight;
GO

SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
SET NOCOUNT ON;
GO

/* ###########################################################################
   0. CLEAN OUT ANY PREVIOUS SEED  (child rows first)
########################################################################### */
DELETE FROM pm.event_object_link;
DELETE FROM pm.deviation;
DELETE FROM pm.conformance_result;
DELETE FROM pm.dfg_snapshot;
DELETE FROM pm.case_metrics;
DELETE FROM pm.event_log;
DELETE FROM pm.object_instance;
UPDATE core.loan_applications SET variant_id = NULL;
DELETE FROM pm.case_variant;

DELETE FROM ops.generated_reports;
DELETE FROM ops.policy_tickets;
DELETE FROM ops.notifications;
DELETE FROM fraud.fraud_signals;
DELETE FROM fraud.fraud_escalations;
DELETE FROM fraud.fraud_ring_members;
DELETE FROM fraud.fraud_cases;
DELETE FROM comp.compliance_check_results;
DELETE FROM risk.risk_assessments;
DELETE FROM core.disbursements;
DELETE FROM core.loan_offers;
DELETE FROM core.field_verification_log;
DELETE FROM core.document_extraction_results;
DELETE FROM core.uploaded_documents;
DELETE FROM core.application_form_responses;
DELETE FROM core.application_status_history;
DELETE FROM core.loan_applications;
DELETE FROM core.customers;
DELETE FROM ops.loan_product_document_requirements;
DELETE FROM ops.loan_products;
DELETE FROM ops.user_sessions;
DELETE FROM ops.users;
DELETE FROM audit.audit_log_details;
DELETE FROM audit.business_events;
DELETE FROM audit.audit_log;
GO

DECLARE @now DATETIME2(3) = SYSUTCDATETIME();

/* ###########################################################################
   1. STAFF  (ops.users)
########################################################################### */
INSERT INTO ops.users (email, password_hash, full_name, role_code, branch_code,
                       team_name, calendar_code, fte_cost_per_hour, hired_at, resource_type_code)
VALUES
 ('priya.sharma@finsight.in', 'seed-not-a-real-hash', N'Priya Sharma', 'officer','JAI', N'Underwriting A','IN_BRANCH', 520, '2022-03-14','human'),
 ('meena.rawat@finsight.in',  'seed-not-a-real-hash', N'Meena Rawat',  'officer','JAI', N'Field Ops',     'IN_BRANCH', 410, '2021-07-01','human'),
 ('amit.kumar@finsight.in',   'seed-not-a-real-hash', N'Amit Kumar',   'officer','IND', N'Underwriting B','IN_BRANCH', 460, '2023-01-09','human'),
 ('ravi.tiwari@finsight.in',  'seed-not-a-real-hash', N'Ravi Tiwari',  'officer','LKO', N'Underwriting B','IN_BRANCH', 455, '2022-11-21','human'),
 ('anil.verma@finsight.in',   'seed-not-a-real-hash', N'Anil Verma',   'head',   'JAI', N'Credit Head',   'IN_BRANCH', 950, '2019-05-02','human'),
 ('rahul.verma@example.com',  'seed-not-a-real-hash', N'Rahul Verma',  'user',   NULL,  NULL,             'IN_24X7',  NULL, NULL,       'customer');

-- Officers report to the head.
UPDATE u SET manager_user_id = h.user_id
FROM ops.users u CROSS JOIN (SELECT user_id FROM ops.users WHERE email='anil.verma@finsight.in') h
WHERE u.role_code = 'officer';
GO

/* ###########################################################################
   2. LOAN PRODUCTS  (ops.loan_products)
   The catalogue and its limits now live here. Icons, colours and the dynamic
   form schema stay in src/data/loanProducts.js — those are presentation, not
   data, and cannot be meaningfully stored as rows.
########################################################################### */
INSERT INTO ops.loan_products (product_code, display_name, description, min_amount, max_amount,
                               min_tenure_months, max_tenure_months, base_interest_rate,
                               processing_fee_pct, target_tat_hours)
VALUES
 ('gold',              N'Gold Loan',            N'Loan against gold ornaments',        20000,   2500000, 6,  36, 11.500, 1.000,  24),
 ('home_loan',         N'Home Loan',            N'Purchase or construction of a home',500000, 50000000,60, 360,  8.750, 0.500, 336),
 ('lap',               N'Loan Against Property',N'Loan against residential/commercial property',500000,30000000,60,240,10.250,1.000,336),
 ('tractor',           N'Tractor Loan',         N'Agricultural equipment finance',    100000,  2500000,12,  84, 12.500, 1.500, 120),
 ('microfinance',      N'Microfinance',         N'Small-ticket group lending',         10000,   150000, 6,  24, 19.000, 1.000,  48),
 ('msme',              N'MSME Loan',            N'Working capital and expansion for small business',100000,10000000,12,84,14.000,1.500,168),
 ('working_capital',   N'Working Capital',      N'Short-term business liquidity',     200000, 15000000,12,  36, 13.500, 1.250, 120),
 ('commercial_vehicle',N'Commercial Vehicle',   N'Trucks, buses and light commercial vehicles',200000,7500000,12,84,12.000,1.500,120),
 ('used_vehicle',      N'Used Vehicle Loan',    N'Pre-owned car finance',             100000,  3000000,12,  72, 14.500, 2.000,  72),
 ('two_wheeler',       N'Two Wheeler Loan',     N'Scooter and motorcycle finance',      30000,   300000, 6,  48, 15.500, 2.000,  48),
 ('personal_loan',     N'Personal Loan',        N'Unsecured personal finance',          50000,  2000000,12,  60, 16.000, 2.000,  48),
 ('education_loan',    N'Education Loan',       N'Tuition and living costs',           100000,  5000000,12, 120, 10.500, 0.500, 168);

-- Document requirements per product (drives the wizard's checklist).
INSERT INTO ops.loan_product_document_requirements (product_id, document_type, is_mandatory, display_order)
SELECT p.product_id, d.doc, d.mandatory, d.ord
FROM ops.loan_products p
JOIN (VALUES
 ('gold','aadhaar',1,1),('gold','pan',1,2),('gold','photograph',1,3),
 ('home_loan','aadhaar',1,1),('home_loan','pan',1,2),('home_loan','salary_slip',1,3),
 ('home_loan','bank_statement',1,4),('home_loan','itr',1,5),('home_loan','property_document',1,6),
 ('lap','aadhaar',1,1),('lap','pan',1,2),('lap','bank_statement',1,3),('lap','property_document',1,4),
 ('tractor','aadhaar',1,1),('tractor','pan',1,2),('tractor','bank_statement',1,3),
 ('microfinance','aadhaar',1,1),('microfinance','photograph',1,2),
 ('msme','aadhaar',1,1),('msme','pan',1,2),('msme','gst_certificate',1,3),
 ('msme','bank_statement',1,4),('msme','business_proof',1,5),
 ('working_capital','pan',1,1),('working_capital','gst_certificate',1,2),('working_capital','bank_statement',1,3),
 ('commercial_vehicle','aadhaar',1,1),('commercial_vehicle','pan',1,2),
 ('commercial_vehicle','bank_statement',1,3),('commercial_vehicle','vehicle_rc',0,4),
 ('used_vehicle','aadhaar',1,1),('used_vehicle','pan',1,2),('used_vehicle','vehicle_rc',1,3),
 ('two_wheeler','aadhaar',1,1),('two_wheeler','pan',1,2),('two_wheeler','salary_slip',1,3),
 ('personal_loan','aadhaar',1,1),('personal_loan','pan',1,2),('personal_loan','salary_slip',1,3),
 ('personal_loan','bank_statement',1,4),
 ('education_loan','aadhaar',1,1),('education_loan','pan',1,2),('education_loan','bank_statement',1,3)
) AS d(prod, doc, mandatory, ord) ON d.prod = p.product_code;
GO

/* ###########################################################################
   3. CUSTOMERS
########################################################################### */
INSERT INTO core.customers (first_name, last_name, date_of_birth, mobile, email, pan,
                            city, state, pincode, occupation, annual_income, credit_score)
VALUES
 (N'Rahul',   N'Verma',        '1993-02-18','9811100001','rahul.verma@example.com','AAAPV1111A',N'Jaipur',   N'Rajasthan',    '302001',N'Salaried',        780000, 748),
 (N'Rajesh',  N'Kumar',        '1985-06-12','9811100002',NULL,'AAAPK2222B',N'Jaipur',   N'Rajasthan',    '302012',N'Transport Operator',1450000,712),
 (N'Sunita',  N'Devi',         '1990-11-03','9811100003',NULL,'AAAPD3333C',N'Jaipur',   N'Rajasthan',    '302018',N'Farmer',           620000, 758),
 (N'Vikram',  N'Verma',        '1978-01-25','9811100004',NULL,'AAAPV4444D',N'Indore',   N'Madhya Pradesh','452001',N'Trader',          2850000, 640),
 (N'Arun',    N'Mehta',        '1987-09-14','9811100005',NULL,'AAAPM5555E',N'Jaipur',   N'Rajasthan',    '302020',N'Salaried',        1980000, 769),
 (N'Lakshmi', N'Bai',          '1982-04-08','9811100006',NULL,'AAAPB6666F',N'Patna',    N'Bihar',        '800001',N'Self-employed',    180000, 690),
 (N'Dhruv',   N'Constructions','1975-03-30','9811100007',NULL,'AAACD7777G',N'Jaipur',   N'Rajasthan',    '302015',N'Builder',         6400000, 705),
 (N'Kavita',  N'Singh',        '1991-12-19','9811100008',NULL,'AAAPS8888H',N'Lucknow',  N'Uttar Pradesh','226001',N'Homemaker',        340000, 781),
 (N'Ratan',   N'Auto Parts',   '1980-08-05','9811100009',NULL,'AAACR9999I',N'Jaipur',   N'Rajasthan',    '302003',N'Business Owner',  3200000, 598),
 (N'Meena',   N'Kumari',       '1994-05-22','9811100010',NULL,'AAAPK1010J',N'Indore',   N'Madhya Pradesh','452010',N'Salaried',        420000, 736),
 (N'Prakash', N'Transports',   '1979-10-11','9811100011',NULL,'AAACP1111K',N'Jaipur',   N'Rajasthan',    '302006',N'Transport Operator',2400000,668),
 (N'Suresh',  N'Fabricators',  '1976-07-17','9811100012',NULL,'AAACF1212L',N'Lucknow',  N'Uttar Pradesh','226010',N'Manufacturer',    1850000, 602),
 (N'Gita',    N'Devi',         '1988-02-02','9811100013',NULL,'AAAPD1313M',N'Patna',    N'Bihar',        '800020',N'Self-employed',    150000, 724),
 (N'Anita',   N'Garments',     '1983-06-28','9811100014',NULL,'AAACG1414N',N'Jaipur',   N'Rajasthan',    '302017',N'Business Owner',  1520000, 688),
 (N'Vijay',   N'Dairy Farm',   '1981-09-09','9811100015',NULL,'AAACV1515O',N'Indore',   N'Madhya Pradesh','452016',N'Farmer',           850000, 741),
 (N'Sunil',   N'Electronics',  '1974-12-01','9811100016',NULL,'AAACE1616P',N'Lucknow',  N'Uttar Pradesh','226016',N'Retailer',        4600000, 571),
 (N'Deepak',  N'And Co',       '1986-03-16','9811100017',NULL,'AAACD1717Q',N'Jaipur',   N'Rajasthan',    '302021',N'Trader',          2200000, 659),
 (N'Rekha',   N'Jewellers',    '1984-11-24','9811100018',NULL,'AAACJ1818R',N'Indore',   N'Madhya Pradesh','452005',N'Jeweller',        1900000, 792),
 (N'Omni',    N'Logistics',    '1977-05-06','9811100019',NULL,'AAACL1919S',N'Jaipur',   N'Rajasthan',    '302033',N'Logistics',       3300000, 655),
 (N'Sakshi',  N'Enterprises',  '1989-08-13','9811100020',NULL,'AAACS2020T',N'Lucknow',  N'Uttar Pradesh','226022',N'Business Owner',  1250000, 703),
 (N'Bharat',  N'Agri',         '1980-01-19','9811100021',NULL,'AAACA2121U',N'Indore',   N'Madhya Pradesh','452020',N'Farmer',           980000, 728),
 (N'Dinesh',  N'Kumar',        '1992-07-07','9811100022',NULL,'AAAPK2222V',N'Jaipur',   N'Rajasthan',    '302004',N'Salaried',         660000, 664),
 (N'Sharma',  N'Traders',      '1979-04-04','9811100023',NULL,'AAACT2323W',N'Indore',   N'Madhya Pradesh','452011',N'Trader',          2750000, 631),
 (N'Pradeep', N'Nair',         '1990-10-30','9811100024',NULL,'AAAPN2424X',N'Patna',    N'Bihar',        '800013',N'Salaried',         890000, 717);
GO

/* ###########################################################################
   4. APPLICATIONS
   Triggers are disabled for this insert so the audit/status-history triggers
   don't stamp every row with "now" — the seed writes its own backdated
   journey in section 5, which is what makes the event log realistic.
########################################################################### */
ALTER TABLE core.loan_applications DISABLE TRIGGER ALL;
GO

DECLARE @now DATETIME2(3) = SYSUTCDATETIME();

;WITH seed AS (
    SELECT * FROM (VALUES
    -- app_no,          customer mobile, product,             status,               amount,  tenure, rate,   officer email,               branch, days_ago, risk, fraud
    ('APP-2026-1001','9811100001','two_wheeler',       'underReview',        120000,  36, 15.500,'priya.sharma@finsight.in','JAI', 12, 32,'none'),
    ('APP-2026-1002','9811100001','personal_loan',     'offerGenerated',     350000,  48, 16.000,'priya.sharma@finsight.in','JAI', 20, 38,'none'),
    ('APP-2026-1003','9811100001','gold',              'draft',              200000,  24, 11.500,NULL,                       'JAI',  4, 20,'none'),
    ('APP-2026-1004','9811100001','home_loan',         'documentsProcessing',2500000,240,  8.750,'ravi.tiwari@finsight.in', 'JAI', 34, 44,'low'),
    ('APP-2026-1005','9811100001','msme',              'disbursed',          500000,  60, 14.000,'meena.rawat@finsight.in', 'JAI', 61, 29,'none'),
    ('APP-2026-1006','9811100001','used_vehicle',      'rejected',           800000,  60, 14.500,'amit.kumar@finsight.in',  'JAI', 75, 66,'low'),

    ('APP-2024-0891','9811100002','commercial_vehicle','underwritingReview',1800000,  60, 12.000,'priya.sharma@finsight.in','JAI',  1, 72,'low'),
    ('APP-2024-0890','9811100003','tractor',           'accepted',           850000,  60, 12.500,'meena.rawat@finsight.in', 'JAI',  1, 41,'low'),
    ('APP-2024-0889','9811100004','msme',              'rejected',          3500000,  84, 14.000,'priya.sharma@finsight.in','IND',  2, 81,'medium'),
    ('APP-2024-0888','9811100005','home_loan',         'documentsProcessing',5500000,240,  8.750,'ravi.tiwari@finsight.in', 'JAI',  2, 56,'low'),
    ('APP-2024-0887','9811100006','microfinance',      'accepted',            50000,  24, 19.000,'meena.rawat@finsight.in', 'PAT', 10, 28,'none'),
    ('APP-2024-0886','9811100007','lap',               'underwritingReview',12000000,240, 10.250,'amit.kumar@finsight.in',  'JAI', 11, 63,'low'),
    ('APP-2024-0885','9811100008','gold',              'accepted',           240000,  24, 11.500,'ravi.tiwari@finsight.in', 'LKO', 11, 18,'none'),
    ('APP-2024-0884','9811100009','working_capital',   'underReview',       2800000,  36, 13.500,'priya.sharma@finsight.in','JAI', 12, 75,'high'),
    ('APP-2024-0883','9811100010','two_wheeler',       'accepted',           120000,  36, 15.500,'amit.kumar@finsight.in',  'IND', 12, 32,'none'),
    ('APP-2024-0882','9811100011','commercial_vehicle','underReview',       2400000,  60, 12.000,'priya.sharma@finsight.in','JAI', 13, 68,'low'),
    ('APP-2024-0881','9811100012','msme',              'rejected',          1800000,  84, 14.000,'ravi.tiwari@finsight.in', 'LKO', 13, 79,'medium'),
    ('APP-2024-0880','9811100013','microfinance',      'accepted',            75000,  24, 19.000,'meena.rawat@finsight.in', 'PAT', 14, 22,'none'),
    ('APP-2024-0878','9811100014','msme',              'underReview',       1500000,  60, 14.000,'priya.sharma@finsight.in','JAI', 14, 55,'low'),
    ('APP-2024-0875','9811100015','tractor',           'underwritingReview', 650000,  60, 12.500,'priya.sharma@finsight.in','IND', 15, 35,'none'),
    ('APP-2024-0872','9811100016','lap',               'underReview',       4500000,180, 10.250,'priya.sharma@finsight.in','LKO', 16, 88,'medium'),
    ('APP-2024-0870','9811100017','working_capital',   'documentsProcessing',2200000, 36, 13.500,'priya.sharma@finsight.in','JAI', 17, 60,'low'),
    ('APP-2024-0868','9811100018','gold',              'accepted',           380000,  24, 11.500,'priya.sharma@finsight.in','IND', 18, 22,'none'),
    ('APP-2024-0865','9811100019','commercial_vehicle','underReview',       3200000,  72, 12.000,'priya.sharma@finsight.in','JAI', 19, 78,'low'),
    ('APP-2024-0861','9811100020','msme',              'documentsProcessing',1200000, 60, 14.000,'priya.sharma@finsight.in','LKO', 20, 45,'none'),
    ('APP-2024-0858','9811100021','tractor',           'underReview',        950000,  60, 12.500,'priya.sharma@finsight.in','IND', 21, 38,'low'),
    ('APP-2024-0876','9811100022','personal_loan',     'underReview',        450000,  48, 16.000,'priya.sharma@finsight.in','JAI', 23, 71,'medium'),
    ('APP-2024-0862','9811100023','working_capital',   'underReview',       1900000,  36, 13.500,'amit.kumar@finsight.in',  'IND', 26, 65,'medium'),
    ('APP-2024-0855','9811100024','personal_loan',     'accepted',           900000,  48, 16.000,'meena.rawat@finsight.in', 'PAT', 30, 44,'low')
    ) AS t(app_no, mobile, product, status, amount, tenure, rate, officer_email, branch, days_ago, risk, fraud)
)
INSERT INTO core.loan_applications
    (application_no, customer_id, loan_product, status_code, loan_amount, tenure_months,
     interest_rate, assigned_officer_id, assigned_head_id, branch_code,
     applied_at, submitted_at, completed_at, created_at, updated_at,
     case_started_at, case_ended_at, is_case_closed, case_duration_sec,
     sla_due_at, priority, current_owner_id, current_owner_since, last_activity_at,
     channel_code, environment_code, process_version)
SELECT
    s.app_no, c.customer_id, s.product, s.status, s.amount, s.tenure, s.rate,
    o.user_id, h.user_id, s.branch,
    DATEADD(DAY, -s.days_ago, @now),
    CASE WHEN s.status <> 'draft' THEN DATEADD(DAY, -s.days_ago, DATEADD(HOUR, 2, @now)) END,
    CASE WHEN st.is_terminal = 1 THEN DATEADD(DAY, -CAST(s.days_ago/3 AS INT), @now) END,
    DATEADD(DAY, -s.days_ago, @now), @now,
    DATEADD(DAY, -s.days_ago, @now),
    CASE WHEN st.is_terminal = 1 THEN DATEADD(DAY, -CAST(s.days_ago/3 AS INT), @now) END,
    st.is_terminal,
    CASE WHEN st.is_terminal = 1
         THEN DATEDIFF(SECOND, DATEADD(DAY, -s.days_ago, @now),
                               DATEADD(DAY, -CAST(s.days_ago/3 AS INT), @now)) END,
    -- SLA due date, spread deliberately across the queue: the target for the
    -- current status, minus two hours for every day the case has been open.
    -- Fresh cases have time left, older ones are breached, and the officer
    -- queue therefore has a realistic gradient to sort by instead of being
    -- uniformly red.
    CASE WHEN st.is_terminal = 1 THEN NULL
         ELSE DATEADD(HOUR, ISNULL(st.target_hours, 24) - ((s.days_ago % 9) * 6), @now) END,
    CASE WHEN s.risk >= 70 THEN 1 WHEN s.risk >= 50 THEN 2 ELSE 3 END,
    o.user_id,
    DATEADD(DAY, -CAST(s.days_ago/2 AS INT), @now),
    DATEADD(DAY, -CAST(s.days_ago/3 AS INT), @now),
    'web', 'prod', '1.0'
FROM seed s
JOIN core.customers c            ON c.mobile = s.mobile
JOIN ref.application_status st   ON st.status_code = s.status
LEFT JOIN ops.users o            ON o.email = s.officer_email
CROSS JOIN (SELECT user_id FROM ops.users WHERE email = 'anil.verma@finsight.in') h;
GO

ALTER TABLE core.loan_applications ENABLE TRIGGER ALL;
GO

/* ###########################################################################
   5. STATUS JOURNEYS  → these project into pm.event_log automatically
   Each application gets the sequence of statuses that led to where it is,
   spread across its real lifetime. This is what gives process mining actual
   traces to discover rather than a single event per case.
########################################################################### */
DECLARE @now DATETIME2(3) = SYSUTCDATETIME();

;WITH journey AS (
    SELECT * FROM (VALUES
      ('draft',1,'draft'),

      ('documentsProcessing',1,'draft'),('documentsProcessing',2,'submitted'),
      ('documentsProcessing',3,'documentsProcessing'),

      ('underReview',1,'draft'),('underReview',2,'submitted'),
      ('underReview',3,'documentsProcessing'),('underReview',4,'documentsVerified'),
      ('underReview',5,'underReview'),

      ('underwritingReview',1,'draft'),('underwritingReview',2,'submitted'),
      ('underwritingReview',3,'documentsProcessing'),('underwritingReview',4,'documentsVerified'),
      ('underwritingReview',5,'underReview'),('underwritingReview',6,'underwritingReview'),

      ('offerGenerated',1,'draft'),('offerGenerated',2,'submitted'),
      ('offerGenerated',3,'documentsProcessing'),('offerGenerated',4,'documentsVerified'),
      ('offerGenerated',5,'underwritingReview'),('offerGenerated',6,'offerGenerated'),

      ('accepted',1,'draft'),('accepted',2,'submitted'),
      ('accepted',3,'documentsProcessing'),('accepted',4,'documentsVerified'),
      ('accepted',5,'underwritingReview'),('accepted',6,'offerGenerated'),('accepted',7,'accepted'),

      ('disbursed',1,'draft'),('disbursed',2,'submitted'),
      ('disbursed',3,'documentsProcessing'),('disbursed',4,'documentsVerified'),
      ('disbursed',5,'underwritingReview'),('disbursed',6,'offerGenerated'),
      ('disbursed',7,'accepted'),('disbursed',8,'sanctioned'),('disbursed',9,'disbursed'),

      -- The rejected path includes a real rework loop: documents come back for
      -- a second pass before the decline. Without a loop like this the
      -- discovered model is a straight line and rework analysis has nothing.
      ('rejected',1,'draft'),('rejected',2,'submitted'),
      ('rejected',3,'documentsProcessing'),('rejected',4,'underReview'),
      ('rejected',5,'documentsProcessing'),('rejected',6,'underReview'),('rejected',7,'rejected')
    ) AS j(target, step, status)
)
INSERT INTO core.application_status_history
    (application_id, from_status, to_status, changed_by, change_reason, created_at,
     event_seq, duration_in_previous_sec, is_automated, reason_code, is_backward_transition)
SELECT
    a.application_id,
    LAG(j.status) OVER (PARTITION BY a.application_id ORDER BY j.step),
    j.status,
    -- Early steps are the borrower / the system; from documentsVerified on it
    -- is the assigned officer doing the work.
    CASE WHEN j.step >= 4 THEN a.assigned_officer_id END,
    N'Seeded journey',
    DATEADD(SECOND,
            CAST(j.step * (DATEDIFF(SECOND, a.case_started_at,
                 ISNULL(a.case_ended_at, @now)) * 1.0
                 / NULLIF(MAX(j.step) OVER (PARTITION BY a.application_id), 0)) AS BIGINT),
            a.case_started_at),
    j.step,
    NULL,
    CASE WHEN j.step <= 3 THEN 1 ELSE 0 END,
    N'seed',
    0
FROM core.loan_applications a
JOIN journey j ON j.target = a.status_code
ORDER BY a.application_id, j.step;
GO

-- Time-in-state and the backward-transition flag, computed from what landed.
;WITH h AS (
    SELECT history_id, application_id, created_at, to_status, from_status,
           prev_at = LAG(created_at) OVER (PARTITION BY application_id ORDER BY event_seq)
    FROM core.application_status_history
)
UPDATE sh
SET duration_in_previous_sec = DATEDIFF(SECOND, h.prev_at, h.created_at),
    is_backward_transition   = CASE WHEN sn.sort_order < so.sort_order THEN 1 ELSE 0 END,
    is_rework                = CASE WHEN sn.sort_order < so.sort_order THEN 1 ELSE 0 END
FROM core.application_status_history sh
JOIN h ON h.history_id = sh.history_id
LEFT JOIN ref.application_status so ON so.status_code = h.from_status
LEFT JOIN ref.application_status sn ON sn.status_code = h.to_status
WHERE h.prev_at IS NOT NULL;
GO

/* ###########################################################################
   6. RISK ASSESSMENTS
########################################################################### */
DECLARE @now DATETIME2(3) = SYSUTCDATETIME();

;WITH risk_seed AS (
    SELECT * FROM (VALUES
     ('APP-2026-1001',32),('APP-2026-1002',38),('APP-2026-1003',20),('APP-2026-1004',44),
     ('APP-2026-1005',29),('APP-2026-1006',66),
     ('APP-2024-0891',72),('APP-2024-0890',41),('APP-2024-0889',81),('APP-2024-0888',56),
     ('APP-2024-0887',28),('APP-2024-0886',63),('APP-2024-0885',18),('APP-2024-0884',75),
     ('APP-2024-0883',32),('APP-2024-0882',68),('APP-2024-0881',79),('APP-2024-0880',22),
     ('APP-2024-0878',55),('APP-2024-0875',35),('APP-2024-0872',88),('APP-2024-0870',60),
     ('APP-2024-0868',22),('APP-2024-0865',78),('APP-2024-0861',45),('APP-2024-0858',38),
     ('APP-2024-0876',71),('APP-2024-0862',65),('APP-2024-0855',44)
    ) AS r(app_no, score)
)
INSERT INTO risk.risk_assessments
    (application_id, overall_risk_level, risk_score, model_version, assessed_at,
     started_at, duration_ms, is_automated, assessment_no, trigger_reason, assessment_data)
SELECT a.application_id,
       CASE WHEN r.score >= 80 THEN 'critical' WHEN r.score >= 65 THEN 'high'
            WHEN r.score >= 45 THEN 'medium' ELSE 'low' END,
       r.score / 100.0,
       N'risk-heuristic-v1',
       DATEADD(HOUR, 3, a.case_started_at),
       DATEADD(HOUR, 3, a.case_started_at),
       850, 1, 1, N'initial',
       (SELECT r.score AS score_0_100,
               a.loan_amount AS exposure,
               c.credit_score AS bureau_score
        FOR JSON PATH, WITHOUT_ARRAY_WRAPPER)
FROM core.loan_applications a
JOIN risk_seed r ON r.app_no = a.application_no
JOIN core.customers c ON c.customer_id = a.customer_id;
GO

/* ###########################################################################
   7. FRAUD CASES + SIGNALS
########################################################################### */
DECLARE @now DATETIME2(3) = SYSUTCDATETIME();

;WITH fraud_seed AS (
    SELECT * FROM (VALUES
     ('APP-2024-0884','high',    'escalated',    N'Cash Cycling',      N'Round-number credits of Rs 8L across 3 days - cash cycling pattern', 0.820,'bank_statement_analysis'),
     ('APP-2024-0876','medium',  'investigating',N'Tampered Document', N'Font inconsistency in salary slip - June 2026',                       0.710,'tampering_cascade'),
     ('APP-2024-0881','medium',  'investigating',N'Income Mismatch',   N'Salary slip Rs 65K vs bank credit average Rs 18K (72% variance)',     0.680,'cross_document_check'),
     ('APP-2024-0862','medium',  'open',         N'GST Inflation',     N'GSTR-3B turnover 3.2x higher than bank credits',                      0.650,'gst_reconciliation'),
     ('APP-2024-0855','low',     'dismissed',    N'Sudden Deposit',    N'Rs 4.5L deposit 12 days before application',                          0.440,'bank_statement_analysis'),
     ('APP-2024-0889','medium',  'open',         N'Document Mismatch', N'Business address on GST certificate differs from bank statement',     0.610,'cross_document_check'),
     ('APP-2024-0872','medium',  'investigating',N'Valuation Inflation',N'Declared property value 41% above area benchmark',                   0.660,'valuation_check'),
     ('APP-2026-1004','low',     'open',         N'Address Mismatch',  N'Aadhaar address differs from bank statement address',                 0.380,'cross_document_check'),
     ('APP-2026-1006','low',     'dismissed',    N'Thin File',         N'No credit history in the last 24 months',                             0.350,'bureau_check'),
     ('APP-2024-0891','low',     'open',         N'Multiple Applications',N'Third commercial-vehicle application in 90 days',                  0.420,'velocity_check'),
     ('APP-2024-0888','low',     'open',         N'Employer Unverified',N'Employer PAN not found in the verified-employer registry',           0.400,'employer_check'),
     ('APP-2024-0886','low',     'open',         N'Related Party',     N'Director shares a mobile number with an existing borrower',           0.450,'graph_check'),
     ('APP-2024-0882','low',     'open',         N'Route Overlap',     N'Vehicle route overlaps an account already in arrears',                0.410,'graph_check'),
     ('APP-2024-0870','low',     'open',         N'Late Filing',       N'GST returns filed late in 4 of the last 6 periods',                   0.390,'gst_reconciliation'),
     ('APP-2024-0865','low',     'open',         N'Fleet Concentration',N'Six vehicles financed against one permit holder',                    0.430,'graph_check'),
     ('APP-2024-0858','low',     'open',         N'Land Record Gap',   N'Land record ownership date later than the declared purchase',         0.370,'cross_document_check')
    ) AS f(app_no, severity, status, category, description, confidence, method)
)
INSERT INTO fraud.fraud_cases
    (application_id, customer_id, severity, status, fraud_category, description,
     detection_method, ai_confidence, flagged_by, assigned_to, created_at, updated_at,
     first_signal_at, detected_at, assigned_at, resolved_at, environment_code)
SELECT a.application_id, a.customer_id, f.severity, f.status, f.category, f.description,
       f.method, f.confidence, NULL, a.assigned_officer_id,
       DATEADD(HOUR, 6, a.case_started_at), SYSUTCDATETIME(),
       DATEADD(HOUR, 5, a.case_started_at),
       DATEADD(HOUR, 6, a.case_started_at),
       DATEADD(HOUR, 8, a.case_started_at),
       CASE WHEN f.status IN ('confirmed','dismissed')
            THEN DATEADD(HOUR, 30, a.case_started_at) END,
       'prod'
FROM core.loan_applications a
JOIN fraud_seed f ON f.app_no = a.application_no;

UPDATE fraud.fraud_cases
SET time_to_detect_sec  = DATEDIFF(SECOND, first_signal_at, detected_at),
    time_to_assign_sec  = DATEDIFF(SECOND, detected_at, assigned_at),
    time_to_resolve_sec = CASE WHEN resolved_at IS NOT NULL
                               THEN DATEDIFF(SECOND, detected_at, resolved_at) END,
    sla_due_at = DATEADD(HOUR, (SELECT target_resolution_hours FROM ref.fraud_severity s
                                WHERE s.severity_code = fraud.fraud_cases.severity), detected_at);

UPDATE fraud.fraud_cases
SET sla_breached = CASE WHEN ISNULL(resolved_at, SYSUTCDATETIME()) > sla_due_at THEN 1 ELSE 0 END;
GO

-- One or two supporting signals per case, attributed to the module that found it.
INSERT INTO fraud.fraud_signals
    (case_id, signal_name, signal_value, signal_threshold, signal_score, signal_category,
     component_code, stage_no, compute_ms, model_version, contributed_to_verdict, detected_at)
SELECT c.case_id, s.name, s.val, s.thr, s.score, s.cat, s.comp, s.stage, s.ms, s.ver, 1,
       c.detected_at
FROM fraud.fraud_cases c
CROSS APPLY (VALUES
    (N'fused_risk_score',      c.ai_confidence * 100, 60.0, c.ai_confidence, N'fusion',
     'fraud_fusion', 2, 1900, N'fusion-v1'),
    (N'document_integrity',    CASE WHEN c.severity IN ('high','critical') THEN 0.74 ELSE 0.21 END,
     0.30, CASE WHEN c.severity IN ('high','critical') THEN 0.74 ELSE 0.21 END, N'forensic',
     'tampering', 2, 44200, N'v009')
) AS s(name, val, thr, score, cat, comp, stage, ms, ver);
GO

/* ###########################################################################
   8. OFFERS + DISBURSEMENTS  (for applications that got that far)
########################################################################### */
INSERT INTO core.loan_offers
    (application_id, offered_amount, interest_rate, tenure_months, processing_fee,
     emi_amount, offer_valid_until, status, created_at, sent_at, first_viewed_at,
     decided_at, accepted_at, generated_by_type, generated_by)
SELECT a.application_id, a.loan_amount, a.interest_rate, a.tenure_months,
       ROUND(a.loan_amount * p.processing_fee_pct / 100.0, 2),
       ROUND(a.loan_amount * (a.interest_rate/1200.0)
             * POWER(1 + a.interest_rate/1200.0, a.tenure_months)
             / NULLIF(POWER(1 + a.interest_rate/1200.0, a.tenure_months) - 1, 0), 2),
       DATEADD(DAY, 15, ISNULL(a.case_ended_at, SYSUTCDATETIME())),
       CASE WHEN a.status_code IN ('accepted','sanctioned','disbursed') THEN 'accepted'
            WHEN a.status_code = 'rejected' THEN 'rejected' ELSE 'pending' END,
       DATEADD(HOUR, -30, ISNULL(a.case_ended_at, SYSUTCDATETIME())),
       DATEADD(HOUR, -29, ISNULL(a.case_ended_at, SYSUTCDATETIME())),
       DATEADD(HOUR, -20, ISNULL(a.case_ended_at, SYSUTCDATETIME())),
       CASE WHEN a.status_code IN ('accepted','sanctioned','disbursed')
            THEN DATEADD(HOUR, -6, ISNULL(a.case_ended_at, SYSUTCDATETIME())) END,
       CASE WHEN a.status_code IN ('accepted','sanctioned','disbursed')
            THEN DATEADD(HOUR, -6, ISNULL(a.case_ended_at, SYSUTCDATETIME())) END,
       'system', a.assigned_officer_id
FROM core.loan_applications a
JOIN ops.loan_products p ON p.product_code = a.loan_product
WHERE a.status_code IN ('offerGenerated','accepted','sanctioned','disbursed');

UPDATE core.loan_offers
SET customer_decision_sec = DATEDIFF(SECOND, sent_at, decided_at)
WHERE decided_at IS NOT NULL;

INSERT INTO core.disbursements
    (offer_id, application_id, disbursed_amount, disbursed_at, disbursement_method,
     reference_number, initiated_at, settled_at)
SELECT o.offer_id, a.application_id, o.offered_amount,
       ISNULL(a.case_ended_at, SYSUTCDATETIME()), N'NEFT',
       N'NEFT' + RIGHT(a.application_no, 6),
       DATEADD(HOUR, -2, ISNULL(a.case_ended_at, SYSUTCDATETIME())),
       ISNULL(a.case_ended_at, SYSUTCDATETIME())
FROM core.loan_applications a
JOIN core.loan_offers o ON o.application_id = a.application_id
WHERE a.status_code = 'disbursed';

UPDATE core.disbursements SET settlement_lag_sec = DATEDIFF(SECOND, initiated_at, settled_at);
GO

/* ###########################################################################
   9. NOTIFICATIONS  (officer inbox + borrower inbox)
########################################################################### */
DECLARE @now DATETIME2(3) = SYSUTCDATETIME();

INSERT INTO ops.notifications (user_id, application_id, title, body, channel, priority,
                               is_read, reference_type, sent_at, read_at)
SELECT u.user_id, a.application_id, n.title, n.body, 'in_app', n.priority, n.is_read,
       N'application', DATEADD(MINUTE, -n.mins_ago, @now),
       CASE WHEN n.is_read = 1 THEN DATEADD(MINUTE, -n.mins_ago + 10, @now) END
FROM (VALUES
 ('priya.sharma@finsight.in','APP-2024-0891',N'Fraud score updated to HIGH',      N'Income mismatch detected on APP-2024-0891 — review required.','urgent',0,   2),
 ('priya.sharma@finsight.in','APP-2024-0888',N'Property documents received',       N'Field officer uploaded property documents for APP-2024-0888.','medium',0,  18),
 ('priya.sharma@finsight.in',NULL,           N'Policy update published',           N'RBI circular NBFC/2026/41 has been embedded in the policy engine.','medium',0, 60),
 ('priya.sharma@finsight.in','APP-2024-0886',N'Legal opinion received',            N'Title clear on APP-2024-0886 — cleared for disbursement.','low',1,      120),
 ('priya.sharma@finsight.in','APP-2024-0884',N'SLA breach on your queue',          N'APP-2024-0884 has passed its review SLA.','urgent',0,                   180),
 ('priya.sharma@finsight.in','APP-2024-0872',N'High risk case assigned',           N'APP-2024-0872 scored 88 — assigned to you for underwriting.','high',1,   400),
 ('rahul.verma@example.com','APP-2026-1001', N'Application moved to Under Review', N'Your Two Wheeler loan application is now under review.','medium',0,      120),
 ('rahul.verma@example.com','APP-2026-1004', N'Document verification completed',   N'Documents for your Home Loan have been verified.','low',1,             1440),
 ('rahul.verma@example.com','APP-2026-1002', N'Your loan offer is ready',          N'An offer of Rs 3,50,000 is waiting for your acceptance.','high',0,      2880),
 ('rahul.verma@example.com','APP-2026-1005', N'Loan disbursed',                    N'Rs 5,00,000 has been credited to your account.','medium',1,            8000)
) AS n(email, app_no, title, body, priority, is_read, mins_ago)
JOIN ops.users u ON u.email = n.email
LEFT JOIN core.loan_applications a ON a.application_no = n.app_no;
GO

/* ###########################################################################
   10. POLICY TICKETS
########################################################################### */
INSERT INTO ops.policy_tickets (ticket_no, raised_by, application_id, issue, policy_ref,
                                priority, status, created_at, resolved_at, resolution)
SELECT t.no, u.user_id, a.application_id, t.issue, t.ref, t.priority, t.status,
       DATEADD(DAY, -t.days_ago, SYSUTCDATETIME()),
       CASE WHEN t.status = 'resolved'
            THEN DATEADD(DAY, -t.days_ago + 2, SYSUTCDATETIME()) END,
       CASE WHEN t.status = 'resolved' THEN t.resolution END
FROM (VALUES
 ('TKT-041','priya.sharma@finsight.in','APP-2024-0885',N'Gold Loan LTV limit unclear for 18K vs 22K gold — Policy 3.4 contradicts 5.2',N'Gold Policy 3.4 / 5.2','high','open',0,NULL),
 ('TKT-040','ravi.tiwari@finsight.in', 'APP-2024-0881',N'MSME GST turnover threshold for Micro vs Small classification is ambiguous',N'MSME Policy 2.1','medium','open',1,NULL),
 ('TKT-039','meena.rawat@finsight.in', 'APP-2024-0883',N'Two-wheeler income proof waiver criteria for existing customers unclear',N'Retail Policy 7.3','low','resolved',3,N'Waiver applies only where the prior loan closed without any DPD.'),
 ('TKT-038','amit.kumar@finsight.in',  'APP-2024-0858',N'Tractor loan land record requirement for leased vs owned agricultural land',N'Agri Policy 4.2','medium','resolved',5,N'Registered lease deed of 5+ years is acceptable in place of ownership.')
) AS t(no, email, app_no, issue, ref, priority, status, days_ago, resolution)
JOIN ops.users u ON u.email = t.email
LEFT JOIN core.loan_applications a ON a.application_no = t.app_no;

UPDATE ops.policy_tickets
SET resolution_sec = DATEDIFF(SECOND, created_at, resolved_at)
WHERE resolved_at IS NOT NULL;
GO

/* ###########################################################################
   11. GENERATED REPORTS
########################################################################### */
INSERT INTO ops.generated_reports (report_no, application_id, report_type, status,
                                   generated_by, generated_at, sent_at, generation_ms)
SELECT r.no, a.application_id, r.rtype, r.status, u.user_id,
       DATEADD(DAY, -r.days_ago, SYSUTCDATETIME()),
       CASE WHEN r.status = 'sent_to_manager'
            THEN DATEADD(DAY, -r.days_ago + 1, SYSUTCDATETIME()) END,
       r.ms
FROM (VALUES
 ('RPT-001','APP-2024-0891','credit_memo',     'generated',      3, 4200),
 ('RPT-002','APP-2024-0889','credit_memo',     'generated',      6, 3900),
 ('RPT-003','APP-2024-0884','fraud_report',    'draft',          8, 6100),
 ('RPT-004','APP-2024-0882','credit_memo',     'sent_to_manager',10, 4400),
 ('RPT-005','APP-2024-0878','risk_report',     'generated',     12, 3100),
 ('RPT-006','APP-2024-0875','credit_memo',     'draft',         14, 4000),
 ('RPT-007','APP-2024-0872','policy_exception','draft',         16, 2600),
 ('RPT-008','APP-2024-0868','risk_report',     'sent_to_manager',18, 3300)
) AS r(no, app_no, rtype, status, days_ago, ms)
JOIN core.loan_applications a ON a.application_no = r.app_no
CROSS JOIN (SELECT user_id FROM ops.users WHERE email = 'priya.sharma@finsight.in') u;
GO

/* ###########################################################################
   12. CUSTOMER ROLL-UPS + PROCESS-MINING AGGREGATES
########################################################################### */
UPDATE c
SET application_count    = x.cnt,
    first_application_at = x.first_at
FROM core.customers c
JOIN (SELECT customer_id, cnt = COUNT(*), first_at = MIN(case_started_at)
      FROM core.loan_applications GROUP BY customer_id) x ON x.customer_id = c.customer_id;
GO

EXEC pm.usp_refresh_all;
GO

/* ###########################################################################
   13. WHAT LANDED
########################################################################### */
SELECT metric = 'users',           n = (SELECT COUNT(*) FROM ops.users)
UNION ALL SELECT 'customers',       (SELECT COUNT(*) FROM core.customers)
UNION ALL SELECT 'loan_products',   (SELECT COUNT(*) FROM ops.loan_products)
UNION ALL SELECT 'applications',    (SELECT COUNT(*) FROM core.loan_applications)
UNION ALL SELECT 'status_history',  (SELECT COUNT(*) FROM core.application_status_history)
UNION ALL SELECT 'event_log',       (SELECT COUNT(*) FROM pm.event_log)
UNION ALL SELECT 'variants',        (SELECT COUNT(*) FROM pm.case_variant)
UNION ALL SELECT 'risk_assessments',(SELECT COUNT(*) FROM risk.risk_assessments)
UNION ALL SELECT 'fraud_cases',     (SELECT COUNT(*) FROM fraud.fraud_cases)
UNION ALL SELECT 'fraud_signals',   (SELECT COUNT(*) FROM fraud.fraud_signals)
UNION ALL SELECT 'offers',          (SELECT COUNT(*) FROM core.loan_offers)
UNION ALL SELECT 'disbursements',   (SELECT COUNT(*) FROM core.disbursements)
UNION ALL SELECT 'notifications',   (SELECT COUNT(*) FROM ops.notifications)
UNION ALL SELECT 'policy_tickets',  (SELECT COUNT(*) FROM ops.policy_tickets)
UNION ALL SELECT 'reports',         (SELECT COUNT(*) FROM ops.generated_reports);
GO

PRINT 'FILE 7 complete — demo business data seeded.';
GO
