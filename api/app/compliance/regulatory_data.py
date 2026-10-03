"""
regulatory_data.py
------------------
Seed content for the RBI Compliance Checker.

Two things live here, both grounded in the *current* RBI regulatory
framework (verified mid-2026):

  SEED_DOCUMENTS  - the knowledge base: paraphrased provisions from the
                    actual regulations, each with a citation. These are
                    chunked and indexed for the RAG query endpoint.

  SEED_RULES      - the machine-checkable compliance rules applied to a loan
                    application. Every rule cites the specific regulation it
                    enforces, so a FAIL is always traceable to a source.

Regulations referenced (all current as of 2026):
  * RBI (Digital Lending) Directions, 2025  (effective 08-May-2025)
  * Key Facts Statement (KFS) for Loans & Advances (effective 01-Oct-2024)
  * Fair Lending Practice – Penal Charges in Loan Accounts (effective 01-Apr-2024)
  * RBI (Regulatory Framework for Microfinance Loans) Directions, 2022
  * Master Direction – Know Your Customer (KYC) Direction, 2016 (amended to 2025)
  * Master Direction – Credit Information Reporting, 2025 (fortnightly, from 01-Jan-2025)

NOTE: Provision text is paraphrased summary for compliance-tooling purposes,
not a substitute for the official RBI circulars.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# KNOWLEDGE BASE
# --------------------------------------------------------------------------
# Each document has one or more sections; sections are chunked at ingestion.

SEED_DOCUMENTS = [
    {
        "title": "RBI (Digital Lending) Directions, 2025",
        "category": "Digital Lending",
        "source_type": "RBI",
        "effective_date": "2025-05-08",
        "sections": [
            {
                "section_title": "Disbursement and repayment flow",
                "text": (
                    "All loan disbursals and repayments must be executed only "
                    "between the bank account of the borrower and the Regulated "
                    "Entity (RE). No pass-through or pool account of a Lending "
                    "Service Provider (LSP) or any third party may be used in "
                    "the flow of funds. This ensures money moves directly "
                    "between the borrower and the lender."
                ),
            },
            {
                "section_title": "Fees payable to Lending Service Providers",
                "text": (
                    "Any fees or charges payable to Lending Service Providers "
                    "in the credit intermediation process must be paid directly "
                    "by the Regulated Entity and not charged by the LSP to the "
                    "borrower. The borrower pays only what is disclosed in the "
                    "Key Facts Statement."
                ),
            },
            {
                "section_title": "Cooling-off / look-up period",
                "text": (
                    "Borrowers are permitted to exit a digital loan during a "
                    "cooling-off (look-up) period by repaying the principal and "
                    "proportionate Annual Percentage Rate without any penalty. "
                    "The cooling-off period must be at least one day, regardless "
                    "of loan tenor. The RE may retain a disclosed one-time "
                    "processing fee."
                ),
            },
            {
                "section_title": "Borrower consent and data minimisation",
                "text": (
                    "Data collected by Digital Lending Apps must be need-based, "
                    "with prior explicit borrower consent and a clear audit "
                    "trail. Accessing mobile phone resources such as contact "
                    "lists, call logs, files and media is not permitted. "
                    "Biometric data may not be stored except as allowed by law. "
                    "Borrowers must be able to give, deny or revoke consent."
                ),
            },
            {
                "section_title": "Default Loss Guarantee (DLG)",
                "text": (
                    "A Regulated Entity may enter a Default Loss Guarantee "
                    "arrangement only with an LSP or another RE acting as an "
                    "LSP that is incorporated under the Companies Act, 2013. "
                    "Total DLG cover on any outstanding loan portfolio is capped "
                    "at five percent of the amount of that loan portfolio."
                ),
            },
            {
                "section_title": "Multi-lender platform disclosure",
                "text": (
                    "Where an LSP or Digital Lending App partners with multiple "
                    "lenders, it must disclose upfront the names of all Regulated "
                    "Entities with which it has arrangements, and present "
                    "available loan offers to the borrower in a consistent, "
                    "unbiased manner without steering."
                ),
            },
        ],
    },
    {
        "title": "Key Facts Statement (KFS) for Loans & Advances",
        "category": "Fair Practices",
        "source_type": "RBI",
        "effective_date": "2024-10-01",
        "sections": [
            {
                "section_title": "KFS before contract execution",
                "text": (
                    "Regulated Entities must provide a Key Facts Statement to "
                    "the borrower before execution of the loan contract, in a "
                    "standardised format and in a language understood by the "
                    "borrower. The KFS applies to all retail and MSME term loans "
                    "sanctioned on or after 1 October 2024."
                ),
            },
            {
                "section_title": "Annual Percentage Rate (APR) disclosure",
                "text": (
                    "The KFS must contain the all-inclusive Annual Percentage "
                    "Rate along with a computation sheet and an amortisation "
                    "schedule over the loan tenor. Charges recovered on behalf "
                    "of third parties, such as insurance and legal charges, must "
                    "also be included in the APR and disclosed separately."
                ),
            },
            {
                "section_title": "No charges outside the KFS",
                "text": (
                    "Any fees or charges not mentioned in the Key Facts "
                    "Statement cannot be levied on the borrower at any stage "
                    "during the loan tenure without the borrower's explicit "
                    "consent. The KFS must also carry a unique proposal number "
                    "and a stated validity period."
                ),
            },
        ],
    },
    {
        "title": "Fair Lending Practice – Penal Charges in Loan Accounts",
        "category": "Fair Practices",
        "source_type": "RBI",
        "effective_date": "2024-04-01",
        "sections": [
            {
                "section_title": "Penal charges, not penal interest",
                "text": (
                    "Penalty for non-compliance with material terms of a loan "
                    "must be levied as 'penal charges' and not as 'penal "
                    "interest' added to the rate of interest. There shall be no "
                    "capitalisation of penal charges — no further interest is to "
                    "be computed on penal charges."
                ),
            },
            {
                "section_title": "Reasonableness and disclosure",
                "text": (
                    "Penal charges must be reasonable and proportionate to the "
                    "non-compliance, must not be used as a revenue enhancement "
                    "tool, and their quantum and reason must be disclosed to the "
                    "borrower in the loan agreement and the Key Facts Statement."
                ),
            },
        ],
    },
    {
        "title": "RBI (Regulatory Framework for Microfinance Loans) Directions, 2022",
        "category": "Credit Risk",
        "source_type": "RBI",
        "effective_date": "2022-04-01",
        "sections": [
            {
                "section_title": "Repayment capacity — 50% of household income",
                "text": (
                    "For microfinance loans, the total monthly loan repayment "
                    "obligations of a household — including principal and "
                    "interest on all outstanding loans plus the loan under "
                    "consideration — must not exceed 50 percent of the monthly "
                    "household income. This is a hard limit on indebtedness."
                ),
            },
            {
                "section_title": "Collateral-free and no prepayment penalty",
                "text": (
                    "Microfinance loans are collateral-free and must not be "
                    "linked to a lien on the borrower's deposit account. No "
                    "prepayment penalty may be charged, and pricing must be "
                    "non-usurious with a board-approved ceiling."
                ),
            },
        ],
    },
    {
        "title": "Master Direction – Know Your Customer (KYC) Direction, 2016",
        "category": "KYC",
        "source_type": "RBI",
        "effective_date": "2016-02-25",
        "sections": [
            {
                "section_title": "Customer Due Diligence and OVDs",
                "text": (
                    "Regulated Entities must carry out Customer Due Diligence "
                    "using an Officially Valid Document. The six OVDs are the "
                    "passport, driving licence, proof of possession of Aadhaar, "
                    "Voter's Identity Card, NREGA job card, and the letter "
                    "issued by the National Population Register. PAN or Form 60 "
                    "must also be obtained."
                ),
            },
            {
                "section_title": "Video-based Customer Identification (V-CIP)",
                "text": (
                    "V-CIP is a consent-based, real-time audio-visual "
                    "interaction conducted by a trained official of the RE to "
                    "establish customer identity for remote onboarding. The "
                    "session must be recorded, geo-tagged and subjected to "
                    "liveness and anti-spoofing checks."
                ),
            },
            {
                "section_title": "Periodic updation and record keeping",
                "text": (
                    "KYC records must be updated periodically — at least once "
                    "every two years for high-risk, eight years for medium-risk "
                    "and ten years for low-risk customers — and uploaded to the "
                    "Central KYC Records Registry. Records are retained for at "
                    "least five years after the relationship ends."
                ),
            },
        ],
    },
    {
        "title": "Master Direction – Credit Information Reporting, 2025",
        "category": "Credit Risk",
        "source_type": "RBI",
        "effective_date": "2025-01-01",
        "sections": [
            {
                "section_title": "Fortnightly credit information reporting",
                "text": (
                    "Credit Institutions must report credit information to all "
                    "Credit Information Companies on a fortnightly basis — as on "
                    "the 15th and the last day of each month — with submission "
                    "within seven calendar days of the reporting fortnight. This "
                    "replaced the earlier monthly cycle from 1 January 2025."
                ),
            },
            {
                "section_title": "Borrower consent for credit bureau pull",
                "text": (
                    "A lender must obtain the borrower's consent before "
                    "accessing their credit information report from a Credit "
                    "Information Company, and must inform the borrower when "
                    "adverse credit information is reported."
                ),
            },
        ],
    },
]


# --------------------------------------------------------------------------
# COMPLIANCE RULES
# --------------------------------------------------------------------------
# rule_type: "mandatory" (regulatory, blocks auto-approval on fail)
#            "internal_policy" (underwriting, -> WARNING on fail)
#            "advisory" (informational, -> WARNING on fail)

# Rules about the LOAN TERMS (Key Facts Statement, cooling-off, fund flow, penal charges) can only be
# judged once an offer exists. During credit assessment there are no terms yet, so they are NOT
# APPLICABLE rather than "missing information" — otherwise no application could ever pass the check.
# policy_context.build_context sets application.stage from the application's status.
FROM_OFFER_STAGE = {"field": "application.stage", "op": "in", "value": ["offer", "sanction", "disbursal"]}
OFFER_STAGE_RULES = ("DL_KFS_001", "DL_KFS_002", "DL_COOL_001", "DL_DISB_001", "PENAL_001")

SEED_RULES = [
    # ---- KYC ----
    {
        "rule_id": "KYC_001",
        "rule_name": "KYC completed with a verified Officially Valid Document",
        "category": "KYC",
        "rule_type": "mandatory",
        "regulation": "Master Direction – KYC Direction, 2016",
        "regulation_ref": "CDD / OVD verification",
        "remediation": "Complete Customer Due Diligence and verify at least one OVD before disbursal.",
        "required_fields": ["customer.kyc_status", "customer.ovd_verified"],
        "condition": {
            "all": [
                {"field": "customer.kyc_status", "op": "eq", "value": "complete"},
                {"field": "customer.ovd_verified", "op": "eq", "value": True},
            ]
        },
    },
    {
        "rule_id": "KYC_002",
        "rule_name": "PAN (or Form 60) available",
        "category": "KYC",
        "rule_type": "mandatory",
        "regulation": "Master Direction – KYC Direction, 2016",
        "regulation_ref": "Mandatory PAN/Form 60",
        "remediation": "Obtain PAN or a valid Form 60 declaration from the borrower.",
        "required_fields": ["customer.pan_available"],
        "condition": {"field": "customer.pan_available", "op": "eq", "value": True},
    },
    # ---- Digital Lending ----
    {
        "rule_id": "DL_KFS_001",
        "rule_name": "Key Facts Statement provided with APR before contract",
        "category": "Digital Lending",
        "rule_type": "mandatory",
        "regulation": "KFS for Loans & Advances (2024) / Digital Lending Directions, 2025",
        "regulation_ref": "KFS before contract execution + APR disclosure",
        "remediation": "Generate and share the KFS (including APR and amortisation schedule) before executing the loan contract.",
        "applies_when": FROM_OFFER_STAGE,
        "required_fields": ["loan_terms.kfs_provided", "loan_terms.apr_disclosed"],
        "condition": {
            "all": [
                {"field": "loan_terms.kfs_provided", "op": "eq", "value": True},
                {"field": "loan_terms.apr_disclosed", "op": "eq", "value": True},
            ]
        },
    },
    {
        "rule_id": "DL_KFS_002",
        "rule_name": "No fees levied outside the Key Facts Statement",
        "category": "Digital Lending",
        "rule_type": "mandatory",
        "regulation": "KFS for Loans & Advances (2024)",
        "regulation_ref": "No charges outside the KFS",
        "remediation": "Ensure every fee charged to the borrower is itemised in the KFS.",
        "applies_when": FROM_OFFER_STAGE,
        "required_fields": ["loan_terms.all_fees_in_kfs"],
        "condition": {"field": "loan_terms.all_fees_in_kfs", "op": "eq", "value": True},
    },
    {
        "rule_id": "DL_COOL_001",
        "rule_name": "Cooling-off period of at least 1 day offered",
        "category": "Digital Lending",
        "rule_type": "mandatory",
        "regulation": "RBI (Digital Lending) Directions, 2025",
        "regulation_ref": "Cooling-off / look-up period (>= 1 day)",
        "remediation": "Offer a cooling-off period of at least one day during which the borrower can exit without penalty.",
        "applies_when": FROM_OFFER_STAGE,
        "required_fields": ["loan_terms.cooling_off_period_days"],
        "condition": {"field": "loan_terms.cooling_off_period_days", "op": "gte", "value": 1},
    },
    {
        "rule_id": "DL_DISB_001",
        "rule_name": "Disbursal directly to borrower's bank account (no LSP pass-through)",
        "category": "Digital Lending",
        "rule_type": "mandatory",
        "regulation": "RBI (Digital Lending) Directions, 2025",
        "regulation_ref": "Disbursement & repayment flow",
        "remediation": "Route disbursal and repayment only between the borrower's account and the RE; remove any LSP pool account from the fund flow.",
        "applies_when": FROM_OFFER_STAGE,
        "required_fields": ["loan_terms.disbursal_to_borrower_account"],
        "condition": {"field": "loan_terms.disbursal_to_borrower_account", "op": "eq", "value": True},
    },
    {
        "rule_id": "DL_CONSENT_001",
        "rule_name": "Explicit borrower consent for data collection and credit-bureau pull",
        "category": "Digital Lending",
        "rule_type": "mandatory",
        "regulation": "RBI (Digital Lending) Directions, 2025 / Credit Information Reporting, 2025",
        "regulation_ref": "Consent and data minimisation",
        "remediation": "Capture explicit, revocable borrower consent for data collection and for pulling the credit report.",
        "required_fields": [
            "customer.consent.data_collection",
            "customer.consent.credit_bureau_pull",
        ],
        "condition": {
            "all": [
                {"field": "customer.consent.data_collection", "op": "eq", "value": True},
                {"field": "customer.consent.credit_bureau_pull", "op": "eq", "value": True},
            ]
        },
    },
    # ---- Penal charges ----
    {
        "rule_id": "PENAL_001",
        "rule_name": "Penalty levied as penal charges, not penal interest",
        "category": "Fair Practices",
        "rule_type": "mandatory",
        "regulation": "Fair Lending Practice – Penal Charges in Loan Accounts (2024)",
        "regulation_ref": "Penal charges, not penal interest",
        "remediation": "Convert any penal-interest component to a fixed penal charge with no capitalisation.",
        "applies_when": FROM_OFFER_STAGE,
        "required_fields": ["loan_terms.penal_charge_is_interest"],
        "condition": {"field": "loan_terms.penal_charge_is_interest", "op": "eq", "value": False},
    },
    # ---- Microfinance (conditional) ----
    {
        "rule_id": "MFI_FOIR_001",
        "rule_name": "Household repayment obligations within 50% of household income",
        "category": "Credit Risk",
        "rule_type": "mandatory",
        "regulation": "RBI (Regulatory Framework for Microfinance Loans) Directions, 2022",
        "regulation_ref": "Repayment capacity — 50% of household income",
        "remediation": "Reduce the loan amount/EMI so that total household repayment obligations stay within 50% of monthly household income.",
        "applies_when": {"field": "loan_type", "op": "eq", "value": "microfinance_loan"},
        "required_fields": [
            "customer.existing_emi",
            "customer.proposed_emi",
            "customer.monthly_household_income",
        ],
        "condition": {
            "expr": "(customer.existing_emi + customer.proposed_emi) / customer.monthly_household_income",
            "op": "lte",
            "value": 0.5,
        },
    },
    # ---- Internal underwriting policy ----
    {
        "rule_id": "POL_FOIR_001",
        "rule_name": "FOIR within 50% of monthly income (underwriting policy)",
        "category": "Credit Risk",
        "rule_type": "internal_policy",
        "regulation": "Internal NBFC underwriting policy",
        "regulation_ref": "Fixed Obligation to Income Ratio",
        "remediation": "Fixed obligations exceed half of income; consider a lower loan amount or longer tenor.",
        "required_fields": [
            "customer.existing_emi",
            "customer.proposed_emi",
            "customer.monthly_income",
        ],
        "condition": {
            "expr": "(customer.existing_emi + customer.proposed_emi) / customer.monthly_income",
            "op": "lte",
            "value": 0.5,
        },
    },
    {
        "rule_id": "POL_CREDIT_001",
        "rule_name": "Credit score at or above 650 (underwriting policy)",
        "category": "Credit Risk",
        "rule_type": "internal_policy",
        "regulation": "Internal NBFC underwriting policy",
        "regulation_ref": "Minimum credit score",
        "remediation": "Sub-650 credit score; route to manual underwriting.",
        "required_fields": ["customer.credit_score"],
        "condition": {"field": "customer.credit_score", "op": "gte", "value": 650},
    },
    # ---- Integration with FinSight fraud/tamper/face modules ----
    {
        "rule_id": "RISK_TAMPER_001",
        "rule_name": "Document tamper score below rejection threshold",
        "category": "Fraud Risk",
        "rule_type": "mandatory",
        "regulation": "Internal fraud-control policy (FinSight tamper module)",
        "regulation_ref": "Tamper score < 50",
        "remediation": "High tamper score — hold for forensic manual review before any approval.",
        "required_fields": ["risk.tamper_score"],
        "condition": {"field": "risk.tamper_score", "op": "lt", "value": 50},
    },
    {
        "rule_id": "RISK_FRAUD_001",
        "rule_name": "Overall fraud score within acceptable range",
        "category": "Fraud Risk",
        "rule_type": "internal_policy",
        "regulation": "Internal fraud-control policy (FinSight fraud module)",
        "regulation_ref": "Fraud score < 50",
        "remediation": "Elevated fraud score; corroborate with additional checks.",
        "required_fields": ["risk.fraud_score"],
        "condition": {"field": "risk.fraud_score", "op": "lt", "value": 50},
    },
    {
        "rule_id": "FACE_001",
        "rule_name": "Face match between selfie and ID verified",
        "category": "Fraud Risk",
        "rule_type": "mandatory",
        "regulation": "Internal fraud-control policy (FinSight face module) / KYC identity assurance",
        # 0.363 = the validated SFace cosine threshold the face module uses (the
        # earlier 0.62 was a different model's scale and failed every genuine match)
        "regulation_ref": "Face match score >= 0.363 (SFace cosine)",
        "remediation": "Face match below threshold — re-verify identity via V-CIP before approval.",
        "applies_when": {"field": "risk.face_match_score", "op": "exists"},
        "required_fields": ["risk.face_match_score"],
        "condition": {"field": "risk.face_match_score", "op": "gte", "value": 0.363},
    },
]
