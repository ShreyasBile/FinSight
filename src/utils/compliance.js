// API helpers for the integrated RBI Compliance module (/compliance/* on the
// same backend as OCR). Same base-URL convention as utils/ocr.js.
const API = import.meta.env?.VITE_OCR_API_URL || "http://localhost:8000";

async function req(path, opts = {}) {
  const res = await fetch(`${API}/compliance${path}`, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let detail;
    try { detail = (await res.json()).detail; } catch { /* non-JSON error body */ }
    throw new Error(detail || `Request failed (${res.status})`);
  }
  return res.json();
}

export const getStats = () => req("/stats");
export const listDocuments = () => req("/kb/documents");
export const ingestDocument = (doc) => req("/kb/documents", { method: "POST", body: JSON.stringify(doc) });
export const deleteDocument = (id) => req(`/kb/documents/${id}`, { method: "DELETE" });
export const extractRules = (docId, preferLlm = true) =>
  req(`/kb/documents/${docId}/extract`, { method: "POST", body: JSON.stringify({ prefer_llm: preferLlm }) });

export const listRules = () => req("/rules");
export const createRule = (rule) => req("/rules", { method: "POST", body: JSON.stringify(rule) });
export const deactivateRule = (ruleId) => req(`/rules/${ruleId}`, { method: "DELETE" });

export const listCandidates = (status = "pending") => req(`/rules/candidates?status=${status}`);
export const approveCandidate = (id, body) =>
  req(`/rules/candidates/${id}/approve`, { method: "POST", body: JSON.stringify(body) });
export const rejectCandidate = (id, body) =>
  req(`/rules/candidates/${id}/reject`, { method: "POST", body: JSON.stringify(body) });

export const getAudit = (limit = 100) => req(`/audit?limit=${limit}`);
// synthesize: the Regulatory Q&A agent writes a cited answer from the
// retrieved passages; it is kept only if every figure is in those passages
// (otherwise the verbatim top passage comes back, as before).
export const ragQuery = (query, topK = 5, synthesize = false) =>
  req("/rag/query", { method: "POST", body: JSON.stringify({ query, top_k: topK, synthesize }) });
export const checkApplication = (application) =>
  req("/policy/check", { method: "POST", body: JSON.stringify(application) });
