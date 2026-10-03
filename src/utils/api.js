// Client for the lending API (api/app/lending.py). Everything the borrower and
// underwriting-officer screens display comes through here — none of it is
// hardcoded in the frontend any more.
//
// Same convention as utils/ocr.js and utils/compliance.js: one base URL, thin
// wrappers, errors surfaced rather than swallowed so a screen can show what
// actually went wrong instead of an empty table that looks like "no data".
import { useCallback, useEffect, useRef, useState } from "react";

const API = import.meta.env?.VITE_OCR_API_URL || "http://localhost:8000";
const READ_TIMEOUT_MS = 30000;

async function get(path, params) {
  const qs = params
    ? "?" + new URLSearchParams(
        Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "")
      ).toString()
    : "";
  let resp;
  try {
    // Reads are quick; one that takes 30 s means the API or its database is stuck. Give up and
    // say so (the screen then offers Retry) rather than showing a spinner forever.
    resp = await fetch(`${API}/lending${path}${qs}`, { signal: AbortSignal.timeout(READ_TIMEOUT_MS) });
  } catch (e) {
    if (e?.name === "TimeoutError")
      throw new Error("The FinSight API did not answer within 30 seconds — its database may be stuck. " +
                      "Close FinSight and start it again from the desktop shortcut.");
    // fetch() only rejects on a network-level failure, which here almost always
    // means the backend isn't running. Say that, rather than "Failed to fetch".
    throw new Error(`Cannot reach the FinSight API at ${API}. Start FinSight from the desktop shortcut.`);
  }
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const body = await resp.json();
      if (body?.detail) detail = body.detail;
    } catch { /* keep the status line */ }
    throw new Error(detail);
  }
  return resp.json();
}

// Writes (decisions, uploads, read receipts). Same error convention as reads: the
// API's own "detail" message is what the screen shows.
async function send(path, body, { form = false, timeoutMs = 120000 } = {}) {
  let resp;
  try {
    resp = await fetch(`${API}/lending${path}`, {
      method: "POST",
      headers: form ? undefined : { "Content-Type": "application/json" },
      body: form ? body : JSON.stringify(body || {}),
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (e) {
    if (e?.name === "TimeoutError") throw new Error("The FinSight API did not answer in time. Try again.");
    throw new Error(`Cannot reach the FinSight API at ${API}. Start FinSight from the desktop shortcut.`);
  }
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const b = await resp.json();
      if (b?.detail) detail = typeof b.detail === "string" ? b.detail : JSON.stringify(b.detail);
    } catch { /* keep the status line */ }
    throw new Error(detail);
  }
  return resp.json();
}

export const api = {
  health:         ()             => get("/health"),
  session:        (role)         => get("/session", { role }),
  products:       ()             => get("/products"),
  applications:   (p)            => get("/applications", p),
  application:    (no)           => get(`/applications/${encodeURIComponent(no)}`),
  notifications:  (user_id)      => get("/notifications", { user_id }),
  fraudCases:     (officer_id)   => get("/fraud/cases", { officer_id }),
  policyTickets:  (officer_id)   => get("/policy-tickets", { officer_id }),
  reports:        (officer_id)   => get("/reports", { officer_id }),
  officerStats:   (officer_id)   => get("/officer/stats", { officer_id }),
  borrowerStats:  (customer_id, submitted_by) => get("/borrower/stats", { customer_id, submitted_by }),
  overview:       ()             => get("/overview"),
  users:          ()             => get("/users"),
  audit:          (limit)        => get("/audit", { limit }),

  // decision workflow (api/app/decisions.py)
  actions:        (no, actor_id) => get(`/applications/${encodeURIComponent(no)}/actions`, { actor_id }),
  act:            (no, action, body) =>
                    send(`/applications/${encodeURIComponent(no)}/actions/${action}`, body),
  uploadDocument: (no, actor_id, doc_type, file) => {
    const f = new FormData();
    f.append("actor_id", actor_id); f.append("doc_type", doc_type); f.append("file", file);
    return send(`/applications/${encodeURIComponent(no)}/documents`, f, { form: true });
  },
  documentUrl:    (document_id, actor_id, variant = "auto") =>
                    `${API}/lending/documents/${encodeURIComponent(document_id)}/file?` +
                    new URLSearchParams({ actor_id, variant }).toString(),
  markRead:       (id, user_id) => send(`/notifications/${encodeURIComponent(id)}/read`, { user_id }),
  markAllRead:    (user_id)     => send("/notifications/read-all", { user_id }),
  // credit memo as a document: format "pdf" or "docx"
  downloadMemo:   (no, actor_id, format) =>
                    downloadFile(`${API}/lending/applications/${encodeURIComponent(no)}/memo?` +
                                 new URLSearchParams({ actor_id, format }).toString(),
                                 `CreditMemo_${no}.${format}`),
};

// Fetch a file the API generates and hand it to the browser as a download. Fetching first
// (instead of following a link) keeps the page in place and lets an API error be shown.
async function downloadFile(url, fallbackName) {
  let resp;
  try {
    resp = await fetch(url, { signal: AbortSignal.timeout(READ_TIMEOUT_MS) });
  } catch {
    throw new Error(`Cannot reach the FinSight API at ${API}. Start FinSight from the desktop shortcut.`);
  }
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try { const b = await resp.json(); if (b?.detail) detail = b.detail; } catch { /* keep the status line */ }
    throw new Error(detail);
  }
  const name = /filename="([^"]+)"/.exec(resp.headers.get("content-disposition") || "")?.[1] || fallbackName;
  const href = URL.createObjectURL(await resp.blob());
  const a = Object.assign(document.createElement("a"), { href, download: name });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(href), 10000);
  return name;
}

/**
 * Load data from the API inside a component.
 *
 *   const { data, loading, error, reload } = useApi(() => api.applications({...}), [dep]);
 *
 * `fn` is re-run whenever `deps` change. A result that arrives after the
 * component unmounted (or after a newer request was fired) is discarded, so a
 * slow response can never overwrite a fresh one.
 */
export function useApi(fn, deps = [], { skip = false } = {}) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(!skip);
  const [error, setError] = useState(null);
  const runId = useRef(0);

  const run = useCallback(() => {
    if (skip) { setLoading(false); return; }
    const id = ++runId.current;
    setLoading(true);
    setError(null);
    Promise.resolve()
      .then(fn)
      .then((d) => { if (id === runId.current) { setData(d); setLoading(false); } })
      .catch((e) => { if (id === runId.current) { setError(e.message || String(e)); setLoading(false); } });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [skip, ...deps]);

  useEffect(() => { run(); return () => { runId.current++; }; }, [run]);

  return { data, loading, error, reload: run };
}

// ── formatting helpers ──────────────────────────────────────────────────────
// Shared so the officer queue and the borrower's list render the same value
// the same way.

export function formatINR(n) {
  if (n === null || n === undefined || n === "") return "—";
  return "₹" + Number(n).toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

/** A timestamp from the API as a Date. SQL Server's DATETIME2 values are UTC
 *  (SYSUTCDATETIME) but serialise without a zone, and `new Date("2026-09-27T00:00:12")`
 *  reads that as LOCAL time — every time on screen was 5½ hours early in India.
 *  A date-time with no zone is therefore taken as UTC; plain dates stay dates. */
export function parseTs(iso) {
  if (!iso) return null;
  const s = String(iso);
  return new Date(/T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(s) ? s + "Z" : s);
}

/** "Today, 10:32 AM" / "Yesterday" / "17 Jun 2026" */
export function formatWhen(iso) {
  if (!iso) return "—";
  const d = parseTs(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const today = new Date();
  const sameDay = (a, b) =>
    a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  const yest = new Date(today); yest.setDate(today.getDate() - 1);
  if (sameDay(d, today))
    return "Today, " + d.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" });
  if (sameDay(d, yest)) return "Yesterday";
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

export function formatDate(iso) {
  if (!iso) return "—";
  const d = parseTs(iso);
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

export function relativeTime(minutes) {
  if (minutes === null || minutes === undefined) return "";
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const h = Math.floor(minutes / 60);
  if (h < 24) return `${h} hr ago`;
  const d = Math.floor(h / 24);
  return d === 1 ? "1 day ago" : `${d} days ago`;
}

/** SLA countdown from minutes remaining. Negative = breached. */
export function formatSla(minutesLeft) {
  if (minutesLeft === null || minutesLeft === undefined) return { text: "—", overdue: false };
  if (minutesLeft < 0) return { text: "OVERDUE", overdue: true };
  if (minutesLeft < 60) return { text: `${minutesLeft}m left`, overdue: false };
  const h = Math.floor(minutesLeft / 60);
  if (h < 24) return { text: `${h}h left`, overdue: false };
  return { text: `${Math.floor(h / 24)}d left`, overdue: false };
}

/** Fraud severity code from the database → the label FraudBadge expects. */
export const FRAUD_LABEL = {
  none: "None", low: "Low", medium: "Medium", high: "High", critical: "High", unassessed: "Not assessed",
};
