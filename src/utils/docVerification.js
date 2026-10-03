// Deterministic document verification — no model calls anywhere in this file.
// Everything here is regex + rule based so results are reproducible and free.
//
// Four jobs:
//   1. EXPECTED_FIELDS      — which fields each doc type should yield (mirrors api/app/doc_types.py)
//   2. validateField()      — per-field format checks (PAN/Aadhaar/IFSC/GSTIN/dates/amounts)
//   3. checkDocumentIdentity() — "is this actually the document we asked for?"
//   4. crossVerifyDocuments() + collectAutofillCandidates() — cross-doc consistency
//      with Indian-document realities: case differences, initials, honorifics,
//      surname-first ordering, and surname changes after marriage.

export const EXPECTED_FIELDS = {
  pan: ["pan_number", "full_name", "dob", "father_name"],
  aadhaar: ["aadhaar_number", "full_name", "dob", "gender", "address", "pincode"],
  salary_slip: ["employee_name", "employer_name", "month_year", "designation", "basic_pay", "hra", "gross_salary", "net_salary"],
  bank_statement: ["account_holder", "bank_name", "account_number", "ifsc", "statement_period", "opening_balance", "closing_balance", "total_credits", "total_debits", "average_balance"],
  itr: ["document_type", "name", "assessment_year", "gross_total_income", "total_tds"],
  vehicle_quotation: ["dealer_name", "vehicle_make", "vehicle_model", "on_road_price", "ex_showroom_price"],
  rc_book: ["registration_number", "owner_name", "chassis_number", "engine_number", "vehicle_make", "vehicle_model", "year_of_manufacture", "fuel_type"],
  insurance: ["policy_number", "insurer", "insured_name", "valid_from", "valid_until", "sum_insured"],
  property_doc: ["document_type", "property_address", "owner_name", "property_value", "area_sqft"],
  land_record: ["survey_number", "village", "taluk", "district", "land_owner", "area_acres", "soil_type"],
  gst_return: ["gstin", "legal_name", "trade_name", "return_period", "turnover", "tax_paid"],
  financial_statement: ["document_type", "entity_name", "period", "total_revenue", "net_profit", "total_assets"],
  form16: ["employee_name", "employer_name", "employer_tan", "assessment_year", "gross_salary", "total_tds"],
  trade_license: ["license_number", "business_name", "owner_name", "valid_until", "business_type"],
  cibil: ["name", "cibil_score", "total_accounts", "active_accounts", "settled_accounts", "defaults"],
  photo: ["has_face", "is_passport_size"],
};

// ── Format regexes (Indian document formats) ────────────────────────────────
const RE = {
  pan: /^[A-Z]{5}[0-9]{4}[A-Z]$/,
  ifsc: /^[A-Z]{4}0[A-Z0-9]{6}$/,
  gstin: /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$/,
  pincode: /^[1-9][0-9]{5}$/,
  tan: /^[A-Z]{4}[0-9]{5}[A-Z]$/,
  mobile: /^[6-9]\d{9}$/,
  // Standard state series (MH 12 AB 1234) or Bharat series (22 BH 1234 AB)
  vehicleReg: /^([A-Z]{2}[\s-]?\d{1,2}[\s-]?[A-Z]{1,3}[\s-]?\d{4}|\d{2}[\s-]?BH[\s-]?\d{4}[\s-]?[A-Z]{1,2})$/i,
  assessmentYear: /20\d{2}\s*[-–]\s*\d{2,4}/,
};

const isBlank = (v) =>
  v === null || v === undefined || (typeof v === "string" && v.trim() === "");

export function toNumber(v) {
  if (typeof v === "number") return v;
  if (typeof v === "string") {
    const cleaned = v.replace(/[₹,\s]/g, "");
    const n = parseFloat(cleaned);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

// Accepts DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY, YYYY-MM-DD → "YYYY-MM-DD" or null.
export function parseDateAny(s) {
  if (isBlank(s)) return null;
  const str = String(s).trim();
  let m = str.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (m) return `${m[1]}-${m[2]}-${m[3]}`;
  m = str.match(/^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})$/);
  if (m) {
    const [, d, mo, y] = m;
    const day = Number(d), mon = Number(mo);
    if (day >= 1 && day <= 31 && mon >= 1 && mon <= 12)
      return `${y}-${String(mon).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
  }
  return null;
}

function aadhaarLooksValid(v) {
  if (isBlank(v)) return false;
  const s = String(v).toUpperCase();
  const digits = s.replace(/\D/g, "");
  if (digits.length === 12) return true;               // full number
  return /X/.test(s) && /\d{4}\s*$/.test(s.trim());    // masked: XXXX-XXXX-1234
}

// Account numbers are digits, optionally masked (XXXXXX7890). Anything with
// other letters (e.g. a PAN misread into this field) is not an account number.
function accountNumberLooksValid(v) {
  if (isBlank(v)) return false;
  const rest = String(v).toUpperCase().replace(/[X\s-]/g, "");
  return /^\d{4,}$/.test(rest);
}

const DATE_KEYS = new Set(["dob", "valid_from", "valid_until"]);
const AMOUNT_KEYS = new Set([
  "basic_pay", "hra", "gross_salary", "net_salary", "opening_balance", "closing_balance",
  "total_credits", "total_debits", "average_balance", "gross_total_income", "total_tds",
  "on_road_price", "ex_showroom_price", "sum_insured", "property_value", "area_sqft",
  "area_acres", "turnover", "tax_paid", "total_revenue", "net_profit", "total_assets",
]);

// → "ok" | "warn" | "error" | "missing"
export function validateField(key, value) {
  if (isBlank(value)) return "missing";
  const s = String(value).trim();

  if (key === "pan_number") return RE.pan.test(s.toUpperCase()) ? "ok" : "error";
  if (key === "aadhaar_number") return aadhaarLooksValid(s) ? "ok" : "error";
  if (key === "account_number") return accountNumberLooksValid(s) ? "ok" : "error";
  if (key === "ifsc") return RE.ifsc.test(s.toUpperCase()) ? "ok" : "error";
  if (key === "gstin") return RE.gstin.test(s.toUpperCase()) ? "ok" : "error";
  if (key === "pincode") return RE.pincode.test(s) ? "ok" : "error";
  if (key === "employer_tan") return RE.tan.test(s.toUpperCase()) ? "ok" : "warn";
  if (key === "registration_number") return RE.vehicleReg.test(s) ? "ok" : "warn";
  if (key === "assessment_year") return RE.assessmentYear.test(s) ? "ok" : "warn";
  if (key === "cibil_score") {
    const n = toNumber(s);
    return n !== null && n >= 300 && n <= 900 ? "ok" : "error";
  }
  if (DATE_KEYS.has(key)) return parseDateAny(s) ? "ok" : "warn";
  if (AMOUNT_KEYS.has(key)) {
    const n = toNumber(s);
    if (n === null) return "warn";
    return n > 0 ? "ok" : "warn";
  }
  return "ok";
}

// ── "Is this the right document?" ───────────────────────────────────────────
// Each doc type has an identifying signal a different document can't fake:
// a wrong upload prompts the model for fields that simply aren't there, so the
// key field comes back empty or in the wrong shape.
const IDENTITY_RULES = {
  pan: (p) => validateField("pan_number", p.pan_number) === "ok",
  aadhaar: (p) => aadhaarLooksValid(p.aadhaar_number),
  salary_slip: (p) =>
    !isBlank(p.employee_name) &&
    [p.net_salary, p.gross_salary, p.basic_pay].some((v) => (toNumber(v) ?? 0) > 0),
  bank_statement: (p) =>
    accountNumberLooksValid(p.account_number) ||
    validateField("ifsc", p.ifsc) === "ok" ||
    [p.opening_balance, p.closing_balance, p.average_balance].some((v) => toNumber(v) !== null && toNumber(v) !== 0),
  itr: (p) =>
    validateField("assessment_year", p.assessment_year) === "ok" ||
    (toNumber(p.gross_total_income) ?? 0) > 0,
  vehicle_quotation: (p) =>
    (toNumber(p.on_road_price) ?? 0) > 0 ||
    (toNumber(p.ex_showroom_price) ?? 0) > 0 ||
    (!isBlank(p.dealer_name) && !isBlank(p.vehicle_make)),
  rc_book: (p) => !isBlank(p.registration_number) && !isBlank(p.chassis_number || p.engine_number || p.owner_name),
  insurance: (p) => !isBlank(p.policy_number),
  property_doc: (p) => !isBlank(p.owner_name) || !isBlank(p.property_address),
  land_record: (p) => !isBlank(p.survey_number) || !isBlank(p.village),
  gst_return: (p) => validateField("gstin", p.gstin) === "ok",
  financial_statement: (p) => !isBlank(p.entity_name) || (toNumber(p.total_revenue) ?? 0) > 0,
  form16: (p) =>
    validateField("employer_tan", p.employer_tan) === "ok" ||
    validateField("assessment_year", p.assessment_year) === "ok" ||
    (toNumber(p.gross_salary) ?? 0) > 0,
  trade_license: (p) => !isBlank(p.license_number) || !isBlank(p.business_name),
  cibil: (p) => validateField("cibil_score", p.cibil_score) === "ok",
  photo: (p) => p.has_face === true || p.has_face === "true",
};

export function checkDocumentIdentity(docId, parsed) {
  const rule = IDENTITY_RULES[docId];
  if (!rule) return { ok: true };            // no rule → can't judge, let it through
  if (!parsed || typeof parsed !== "object") return { ok: false };
  return { ok: !!rule(parsed) };
}

// ── Indian-aware name comparison ────────────────────────────────────────────
const HONORIFICS = new Set([
  "MR", "MRS", "MS", "MISS", "MASTER", "DR", "SHRI", "SRI", "SMT", "SH",
  "KUM", "KUMARI", "MOHD", "MD",
]);

export function nameTokens(s) {
  if (isBlank(s)) return [];
  return String(s)
    .toUpperCase()
    .replace(/[.,]/g, " ")
    .split(/\s+/)
    .filter((t) => t && !HONORIFICS.has(t));
}

// Greedy match: every token of the shorter name must match a token of the
// longer one — exactly, or as an initial ("R" ↔ "RAHUL"). Order-insensitive,
// because many Indian documents print surname first.
// → the positions in `longer` that the words of `shorter` account for (an
// initial stands for a word), or null when some word of `shorter` is missing.
function tokensSubsume(shorter, longer) {
  const pool = longer.map((p, i) => [i, p]);
  const used = [];
  for (const t of shorter) {
    const idx = pool.findIndex(
      ([, p]) => p === t || (t.length === 1 && p.startsWith(t)) || (p.length === 1 && t.startsWith(p))
    );
    if (idx === -1) return null;
    used.push(pool.splice(idx, 1)[0][0]);
  }
  return used;
}

// → { level: "match" | "partial" | "mismatch", note?: string }
export function compareNames(a, b) {
  const ta = nameTokens(a);
  const tb = nameTokens(b);
  if (!ta.length || !tb.length) return { level: "match" };  // nothing to compare

  const [shorter, longer] = ta.length <= tb.length ? [ta, tb] : [tb, ta];
  const used = tokensSubsume(shorter, longer);
  if (used) {
    // Every word of the shorter name is in the longer one. A dropped MIDDLE
    // name is the same person; a dropped FIRST name is not: "Suresh Sharma"
    // against "Rahul Suresh Sharma" is the father's document (given name +
    // father's name + surname is the Western-India convention). Found by the
    // real-data evaluation: 150 of 150 such pairs were judged the same person.
    if (shorter.length < longer.length && !used.includes(0)) {
      return {
        level: "partial",
        note: "Part of the name differs between documents — this is common after marriage or with initials. Our team will verify.",
      };
    }
    return { level: "match" };
  }

  // Shared given-name tokens but different remaining parts — common after
  // marriage (surname change) or when one doc uses the middle name. A shared
  // SURNAME alone (the last token of both names) does not count: "Suresh Gupta"
  // vs "Ramesh Gupta" are different people, not a partial match (found by the
  // agent evaluation, eval/agents/case_eval.py NAME_PAIRS).
  const full = (t) => t.length > 1;
  const lastA = ta[ta.length - 1], lastB = tb[tb.length - 1];
  const common = ta.filter((t) => full(t) && tb.includes(t) && !(t === lastA && t === lastB));
  if (common.length >= 1) {
    return {
      level: "partial",
      note: "Part of the name differs between documents — this is common after marriage or with initials. Our team will verify.",
    };
  }
  return { level: "mismatch" };
}

// ── Cross-document verification ─────────────────────────────────────────────
// Personal-identity fields: this name must be the applicant's own — no
// legitimate reason a PAN, salary slip, bank account or insurance policy
// would be in someone else's name, so a mismatch here is a hard error.
const STRICT_NAME_KEYS = ["full_name", "employee_name", "account_holder", "insured_name", "name"];
// Ownership/collateral fields: land or property is very commonly still in a
// parent's name, and a trade license may list a co-founder or an earlier
// signatory rather than the specific applicant. Real mismatches here are
// worth a human glance, not an automatic block.
const OWNERSHIP_NAME_KEYS = ["owner_name", "land_owner"];
const PERSON_NAME_KEYS = [...STRICT_NAME_KEYS, ...OWNERSHIP_NAME_KEYS];

// docsMap: { docId: { parsed, label } }  →  [{ level, title, detail }]
export function crossVerifyDocuments(docsMap) {
  const checks = [];

  const names = [];
  const dobs = [];
  const genders = [];
  Object.values(docsMap).forEach(({ parsed, label }) => {
    if (!parsed) return;
    for (const k of PERSON_NAME_KEYS) {
      if (!isBlank(parsed[k])) {
        names.push({ label, value: String(parsed[k]).trim(), ownership: OWNERSHIP_NAME_KEYS.includes(k) });
        break;
      }
    }
    if (!isBlank(parsed.dob)) dobs.push({ label, value: String(parsed.dob).trim() });
    if (!isBlank(parsed.gender)) genders.push({ label, value: String(parsed.gender).trim() });
  });

  if (names.length >= 2) {
    let worst = { level: "match" };
    let detail = "";
    let anyOwnership = false;
    for (let i = 1; i < names.length; i++) {
      const r = compareNames(names[0].value, names[i].value);
      const involvesOwnership = names[0].ownership || names[i].ownership;
      if (r.level === "mismatch" || r.level === "partial") {
        const rank = { match: 0, partial: 1, mismatch: 2 };
        if (rank[r.level] > rank[worst.level] || worst.level === "match") {
          worst = r;
          detail = `"${names[0].value}" (${names[0].label}) vs "${names[i].value}" (${names[i].label})`;
          anyOwnership = involvesOwnership;
        }
      }
    }
    if (worst.level === "match")
      checks.push({ level: "ok", title: "Name matches across documents", detail: "" });
    else if (worst.level === "partial")
      checks.push({ level: "warn", title: "Names are similar but not identical", detail: `${detail}. ${worst.note}` });
    else if (anyOwnership)
      checks.push({
        level: "warn",
        title: "A document lists a different name for ownership",
        detail: `${detail}. This is common when land or property is in a family member's name — our team will confirm the relationship.`,
      });
    else
      checks.push({ level: "error", title: "Names don't match between documents", detail: `${detail}. Please check you uploaded your own documents.` });
  }

  if (dobs.length >= 2) {
    const parsedDates = dobs.map((d) => ({ ...d, iso: parseDateAny(d.value) }));
    const known = parsedDates.filter((d) => d.iso);
    const uniq = [...new Set(known.map((d) => d.iso))];
    if (uniq.length <= 1)
      checks.push({ level: "ok", title: "Date of birth matches across documents", detail: "" });
    else
      checks.push({
        level: "error",
        title: "Date of birth differs between documents",
        detail: known.map((d) => `${d.value} (${d.label})`).join(" vs "),
      });
  }

  if (genders.length >= 2) {
    const uniq = [...new Set(genders.map((g) => g.value[0].toUpperCase()))];
    if (uniq.length > 1)
      checks.push({
        level: "error",
        title: "Gender differs between documents",
        detail: genders.map((g) => `${g.value} (${g.label})`).join(" vs "),
      });
  }

  return checks;
}

// ── Autofill from extracted documents ───────────────────────────────────────
const INDIAN_STATES = [
  "ANDHRA PRADESH", "ARUNACHAL PRADESH", "ASSAM", "BIHAR", "CHHATTISGARH", "GOA",
  "GUJARAT", "HARYANA", "HIMACHAL PRADESH", "JHARKHAND", "KARNATAKA", "KERALA",
  "MADHYA PRADESH", "MAHARASHTRA", "MANIPUR", "MEGHALAYA", "MIZORAM", "NAGALAND",
  "ODISHA", "PUNJAB", "RAJASTHAN", "SIKKIM", "TAMIL NADU", "TELANGANA", "TRIPURA",
  "UTTAR PRADESH", "UTTARAKHAND", "WEST BENGAL", "DELHI", "JAMMU AND KASHMIR",
  "LADAKH", "PUDUCHERRY", "CHANDIGARH",
];

function parseCityState(address) {
  if (isBlank(address)) return {};
  const parts = String(address).split(",").map((p) => p.replace(/[-–]?\s*\d{6}\s*$/, "").trim()).filter(Boolean);
  for (let i = parts.length - 1; i >= 0; i--) {
    const candidate = parts[i].toUpperCase();
    const state = INDIAN_STATES.find((s) => candidate === s || candidate.includes(s));
    if (state) {
      const title = (t) => t.toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
      return { state: title(state), city: i > 0 ? title(parts[i - 1]) : undefined };
    }
  }
  return {};
}

// Loan-specific form fields that map 1:1 onto extracted document keys.
// Keys are form-field ids (from loanProducts.js), values are parsed-doc keys
// in priority order — first non-blank wins per document.
const DIRECT_FIELD_SOURCES = {
  manufacturer: ["vehicle_make"],
  model: ["vehicle_model"],
  tractorModel: ["vehicle_model"],
  onRoadPrice: ["on_road_price"],
  quotationAmount: ["on_road_price", "ex_showroom_price"],
  vehicleCost: ["on_road_price", "ex_showroom_price"],
  vehicleValue: ["on_road_price", "ex_showroom_price"],
  dealerName: ["dealer_name"],
  dealerSeller: ["dealer_name"],       // car loan uses this field id, not dealerName
  village: ["village"],
  taluka: ["taluk"],
  district: ["district"],
  propertyLocation: ["property_address"],
  farmerName: PERSON_NAME_KEYS,
  landOwned: ["area_acres"],
  employerName: ["employer_name"],
  agreementValue: ["property_value"],
  businessName: ["business_name"],
};

// docsMap: { docId: { parsed, label } }
// → { fieldId: [{ value, source, key }] } for every field we can source.
export function collectAutofillCandidates(docsMap) {
  const out = { name: [], dob: [], pincode: [], city: [], state: [], monthlyIncome: [], employmentType: [], vehicleAge: [] };
  for (const fid of Object.keys(DIRECT_FIELD_SOURCES)) out[fid] = [];

  const push = (field, value, source, dedupeKey) => {
    if (isBlank(value)) return;
    const key = dedupeKey ?? String(value).trim().toUpperCase();
    if (out[field].some((c) => c.key === key)) return;
    out[field].push({ value: String(value).trim(), source, key });
  };

  // Deterministic priority: identity documents first, so a collateral document
  // (land record, property deed…) never outranks the applicant's own PAN/Aadhaar
  // as the "primary" name/DOB candidate. Anything not in this list ranks last,
  // in its original order.
  const order = ["pan", "aadhaar", "salary_slip", "bank_statement", "itr", "form16"];
  const rank = (id) => { const i = order.indexOf(id); return i === -1 ? Infinity : i; };
  const entries = Object.entries(docsMap).sort((a, b) => rank(a[0]) - rank(b[0]));

  for (const [docId, { parsed, label }] of entries) {
    if (!parsed) continue;
    for (const k of PERSON_NAME_KEYS) {
      if (!isBlank(parsed[k])) {
        push("name", parsed[k], label, nameTokens(parsed[k]).join(" "));
        break;
      }
    }
    const iso = parseDateAny(parsed.dob);
    if (iso) push("dob", iso, label, iso);
    if (validateField("pincode", parsed.pincode) === "ok") push("pincode", parsed.pincode, label);
    const { city, state } = parseCityState(parsed.address);
    if (city) push("city", city, label);
    if (state) push("state", state, label);

    // Income: prefer net salary, fall back to gross (some slips only show gross).
    const income = toNumber(parsed.net_salary) || toNumber(parsed.gross_salary);
    if (income && income > 0) push("monthlyIncome", String(income), label);

    // A salary slip strongly implies salaried employment.
    if (docId === "salary_slip") push("employmentType", "Salaried", label);

    // Used-vehicle age: computed from the RC book's manufacture year, not a
    // direct field on any document.
    if (docId === "rc_book" && !isBlank(parsed.year_of_manufacture)) {
      const year = parseInt(String(parsed.year_of_manufacture).match(/\d{4}/)?.[0], 10);
      if (year && year > 1980 && year <= new Date().getFullYear()) {
        const age = new Date().getFullYear() - year;
        push("vehicleAge", String(age), label, age);
      }
    }

    // Loan-specific 1:1 mappings (vehicle quotation, land record, property doc…).
    for (const [fid, keys] of Object.entries(DIRECT_FIELD_SOURCES)) {
      for (const k of keys) {
        const v = parsed[k];
        if (isBlank(v)) continue;
        if (AMOUNT_KEYS.has(k)) {
          const num = toNumber(v);
          if (!num || num <= 0) continue;   // 0/unreadable → try next source key
          push(fid, String(num), label);
        } else if (fid === "farmerName") {
          push(fid, v, label, nameTokens(v).join(" "));
        } else {
          push(fid, v, label);
        }
        break; // first usable source key wins for this doc
      }
    }
  }
  return out;
}
