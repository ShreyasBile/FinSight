/* 11 — Record the borrower's consent on applications submitted before it was stored.

   The application wizard has always required all five "Consent & Declaration" boxes before Submit
   is enabled (data verification, credit-bureau report, communication, correctness, no guarantee),
   but the ticks were not saved with the application. From 2026-10-01 they are saved in
   form_data.consent at submission. This script writes the same record for earlier WEB submissions,
   marked as backfilled, with the submission time as the acceptance time.

   Idempotent: touches only rows whose form_data has no consent object.
   Run with:  sqlcmd -S <server> -d FinSight -E -I -b -f 65001 -i 11-consent-backfill.sql
*/
SET NOCOUNT ON;
SET QUOTED_IDENTIFIER ON;

UPDATE a
SET form_data = JSON_MODIFY(
        COALESCE(NULLIF(a.form_data, ''), '{}'), '$.consent',
        JSON_QUERY(CONCAT(
            '{"information_correct":true,"data_collection":true,"credit_bureau_pull":true,',
            '"communication":true,"no_guarantee_understood":true,',
            '"accepted_at":"', CONVERT(VARCHAR(19), COALESCE(a.submitted_at, a.applied_at), 126), '",',
            '"captured_by":"wizard","backfilled":true,',
            '"basis":"the wizard required all five declarations before submission; recorded retroactively on 2026-10-01"}')))
FROM core.loan_applications a
WHERE a.channel_code = 'web'
  AND (a.form_data IS NULL OR JSON_QUERY(a.form_data, '$.consent') IS NULL);

SELECT @@ROWCOUNT AS applications_backfilled;

SELECT application_no, JSON_VALUE(form_data, '$.consent.accepted_at') AS consent_accepted_at,
       JSON_VALUE(form_data, '$.consent.backfilled') AS backfilled
FROM core.loan_applications
ORDER BY applied_at;
