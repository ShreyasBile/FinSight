// Client for the agent layer: LLM selection (/settings/llm) and agent runs,
// traces and case evidence (/agents/*). Same conventions as api.js — one base
// URL, errors surfaced with the server's `detail` message.
const API = import.meta.env?.VITE_OCR_API_URL || "http://localhost:8000";

async function call(method, path, { body, role, actorId, params } = {}) {
  const qs = params
    ? "?" + new URLSearchParams(
        Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "")
      ).toString()
    : "";
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  // Advisory only — FinSight has no login yet; the API checks this header for
  // head-only changes (documented as not real security).
  if (role) headers["X-Actor-Role"] = role;
  if (actorId) headers["X-Actor-Id"] = actorId;
  let resp;
  try {
    resp = await fetch(`${API}${path}${qs}`, {
      method, headers, body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error(`Cannot reach the FinSight API at ${API}. Start FinSight from the desktop shortcut.`);
  }
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const b = await resp.json();
      if (b?.detail) detail = typeof b.detail === "string" ? b.detail : JSON.stringify(b.detail);
    } catch { /* keep status line */ }
    const err = new Error(detail);
    err.status = resp.status;
    throw err;
  }
  return resp.json();
}

export const llmSettings = {
  get:    ()                         => call("GET", "/settings/llm"),
  update: (body, role, actorId)      => call("PUT", "/settings/llm", { body, role, actorId }),
  models: (provider, refresh)        => call("GET", "/settings/llm/models", { params: { provider, refresh } }),
  test:   (provider, model, vision, role) =>
    call("POST", "/settings/llm/test", { body: { provider, model, vision }, role }),
};

export const agentsApi = {
  list:         ()                   => call("GET", "/agents"),
  agent:        (id)                 => call("GET", `/agents/${encodeURIComponent(id)}`),
  health:       ()                   => call("GET", "/agents/health"),
  stats:        ()                   => call("GET", "/agents/stats"),
  runs:         (params)             => call("GET", "/agents/runs", { params }),
  run:          (runId)              => call("GET", `/agents/runs/${encodeURIComponent(runId)}`),
  caseFile:     (caseId)             => call("GET", `/agents/cases/${encodeURIComponent(caseId)}`),
  caseByApp:    (appNo)              => call("GET", `/agents/cases/by-application/${encodeURIComponent(appNo)}`),
  assess:       (caseId, body)       => call("POST", `/agents/cases/${encodeURIComponent(caseId)}/assess`, { body: body || {} }),
  underwrite:   (caseId, body)       => call("POST", `/agents/cases/${encodeURIComponent(caseId)}/underwrite`, { body: body || {} }),
  underwriting: (caseId)             => call("GET", `/agents/cases/${encodeURIComponent(caseId)}/underwriting`),
  fraud:        (caseId)             => call("GET", `/agents/cases/${encodeURIComponent(caseId)}/fraud`),
  memo:         (caseId)             => call("GET", `/agents/cases/${encodeURIComponent(caseId)}/memo`),
  // who: { role, actorId } of the person asking - the run trace (and a credit memo) is attributed to them
  appAssessment:(appNo, body, who)   => call("POST", `/agents/applications/${encodeURIComponent(appNo)}/assess`,
                                             { body: body || {}, role: who?.role, actorId: who?.actorId }),
  appUnderwrite:(appNo, body, who)   => call("POST", `/agents/applications/${encodeURIComponent(appNo)}/underwrite`,
                                             { body: body || {}, role: who?.role, actorId: who?.actorId }),
  appResults:   (appNo)              => call("GET", `/agents/applications/${encodeURIComponent(appNo)}`),
  policyCheck:  (body)               => call("POST", "/agents/policy/check", { body }),
  processInsights: (params)          => call("GET", "/agents/process/insights", { params }),
  triage:       (params)             => call("GET", "/agents/fraud/triage", { params }),
  runAgent:     (id, inputs)         => call("POST", `/agents/${encodeURIComponent(id)}/run`, { body: { inputs } }),
};

// The wizard's case id: minted once per application draft and sent as
// X-Case-Id on every document call, so every agent step for that application
// lands in one case file (and one process-mining case).
export function newCaseId() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

export const KIND_LABEL = { D: "Deterministic", H: "Hybrid", L: "LLM" };
export const OUTCOME_BADGE = {
  pass: "badge-green", info: "badge-blue", flag: "badge-amber",
  uncertain: "badge-gray", fail: "badge-red",
};
// never "Rejected": every agent output is advisory evidence for a human
export const OUTCOME_LABEL = {
  pass: "Clear", info: "Info", flag: "Needs a look", uncertain: "Uncertain", fail: "Concern",
};
