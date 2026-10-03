/* ===========================================================================
   FinSight · 10 · Aadhaar: keep the last 4 digits only
   ---------------------------------------------------------------------------
   Run after 01-09. Idempotent, safe to re-run:
       sqlcmd -S "(localdb)\MSSQLLocalDB" -E -I -f 65001 -i 10-aadhaar-last4.sql

   UIDAI's rules let a lender that is not an authorised Aadhaar user keep and
   show only a MASKED Aadhaar number (the last four digits). core.customers.aadhaar
   (VARCHAR(12), unique) was therefore never filled, and every screen showed
   "Aadhaar —". The last four digits go in their own column instead: a masked
   value cannot live in the unique column, because two different people can
   share the same last four digits.

   The full number is not kept anywhere in these tables: the API masks it in
   core.uploaded_documents.extracted_data before writing (sql_persist.py).
=========================================================================== */
USE FinSight;
GO

IF COL_LENGTH('core.customers', 'aadhaar_last4') IS NULL
    ALTER TABLE core.customers ADD aadhaar_last4 CHAR(4) NULL
        CONSTRAINT CK_customers_aadhaar_last4
            CHECK (aadhaar_last4 IS NULL OR aadhaar_last4 LIKE '[0-9][0-9][0-9][0-9]');
GO
