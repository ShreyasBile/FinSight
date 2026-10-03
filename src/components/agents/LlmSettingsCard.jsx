import { useEffect, useMemo, useState } from "react";
import { Icon, Icons } from "../../icons/Icons.jsx";
import { llmSettings, agentsApi, KIND_LABEL } from "../../utils/agents.js";

// Head-only "AI Models" settings: which LLM every LLM-using agent runs on.
// Changes apply to the very next agent run — no restart. Local Ollama keeps
// every document on this machine; a hosted provider needs an explicit
// acknowledgment of what leaves the machine.

const PROVIDER_LABEL = { ollama: "Local (Ollama)", anthropic: "Anthropic Claude", mock: "Mock (dev only)" };
const CAP_BADGE = { vision: "badge-blue", tools: "badge-purple", thinking: "badge-amber", json: "badge-gray" };
const enc = (c) => (c && c.provider ? `${c.provider}|${c.model || ""}` : "");
const dec = (v) => {
  if (!v) return null;
  const [provider, model] = v.split("|");
  return { provider, model };
};

function Caps({ caps }) {
  return (
    <span style={{ display: "inline-flex", gap: 4, flexWrap: "wrap" }}>
      {(caps || []).filter((c) => c !== "completion").map((c) => (
        <span key={c} className={`badge ${CAP_BADGE[c] || "badge-gray"}`} style={{ fontSize: 10 }}>{c}</span>
      ))}
    </span>
  );
}

export default function LlmSettingsCard({ role = "head", actorId }) {
  const [cfg, setCfg] = useState(null);
  const [ollama, setOllama] = useState({ models: [], reachable: true });
  const [anthropic, setAnthropic] = useState({ models: [], configured: false });
  const [agents, setAgents] = useState(null);
  const [draft, setDraft] = useState(null);
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [tests, setTests] = useState({});
  const [error, setError] = useState(null);

  const load = async (refresh = false) => {
    setError(null);
    try {
      const c = await llmSettings.get();
      setCfg(c);
      setDraft({
        default: { provider: c.default.provider, model: c.default.model },
        lanes: { vision: c.lanes?.vision || null, text: c.lanes?.text || null },
        agents: Object.fromEntries(Object.entries(c.agents || {}).map(([k, v]) => [k, v])),
      });
      setAck(false);
      llmSettings.models("ollama", refresh).then(setOllama).catch(() => setOllama({ models: [], reachable: false }));
      if (c.anthropic?.configured) llmSettings.models("anthropic").then(setAnthropic).catch(() => {});
      agentsApi.list().then(setAgents).catch(() => setAgents([]));
    } catch (e) {
      setError(e.message);
    }
  };
  useEffect(() => { load(); }, []);

  const options = useMemo(() => {
    const out = [];
    (ollama.models || []).forEach((m) => out.push({ value: `ollama|${m.name}`, provider: "ollama", model: m.name, caps: m.capabilities, meta: `${m.params || ""} ${m.quant || ""}`.trim(), loaded: m.loaded }));
    if (cfg?.anthropic?.configured && !cfg?.hosted_forbidden)
      (anthropic.models || []).forEach((m) => out.push({ value: `anthropic|${m.name}`, provider: "anthropic", model: m.name, caps: ["vision", "tools"], meta: m.display || "" }));
    out.push({ value: "mock|mock", provider: "mock", model: "mock", caps: [], meta: "canned answers, dev only" });
    return out;
  }, [ollama, anthropic, cfg]);

  if (error) return <div className="card" style={{ color: "var(--red)" }}>Could not load AI model settings: {error}</div>;
  if (!cfg || !draft) return <div className="card" style={{ color: "var(--text3)" }}>Loading AI model settings…</div>;

  const optionFor = (v) => options.find((o) => o.value === v);

  // effective choice per agent in the DRAFT (mirrors server resolution)
  const effective = (cfg.effective || []).map((e) => {
    const lane = draft.lanes[e.lane];
    const own = draft.agents[e.agent_id];
    const pick = own?.provider ? own : lane?.provider ? lane : draft.default;
    const source = own?.provider ? "agent" : lane?.provider ? "lane" : "default";
    return { ...e, provider: pick.provider, model: pick.model, source };
  });
  const hostedNow = effective.filter((e) => e.provider === "anthropic");
  const needsAck = hostedNow.length > 0 && !cfg.hosted_ack;
  const dirty = JSON.stringify(draft) !== JSON.stringify({
    default: { provider: cfg.default.provider, model: cfg.default.model },
    lanes: { vision: cfg.lanes?.vision || null, text: cfg.lanes?.text || null },
    agents: cfg.agents || {},
  });

  const setDefault = (v) => setDraft({ ...draft, default: dec(v) || draft.default });
  const setLane = (lane, v) => setDraft({ ...draft, lanes: { ...draft.lanes, [lane]: dec(v) } });
  const setAgent = (id, v) => {
    const agentsNext = { ...draft.agents };
    const c = dec(v);
    if (c) agentsNext[id] = c; else delete agentsNext[id];
    setDraft({ ...draft, agents: agentsNext });
  };

  const save = async () => {
    setBusy(true); setMsg(null);
    try {
      const agentsBody = {};
      const ids = new Set([...Object.keys(cfg.agents || {}), ...Object.keys(draft.agents)]);
      ids.forEach((id) => { agentsBody[id] = draft.agents[id] || null; });
      const body = {
        expected_revision: cfg.revision,
        default: draft.default,
        lanes: { vision: draft.lanes.vision, text: draft.lanes.text },
        agents: agentsBody,
        acknowledge_hosted: ack,
      };
      await llmSettings.update(body, role, actorId);
      setMsg({ ok: true, text: "Saved — the next agent run uses the new model." });
      await load();
    } catch (e) {
      setMsg({ ok: false, text: e.message });
    } finally {
      setBusy(false);
    }
  };

  const runTest = async (o, vision) => {
    const key = `${o.value}|${vision ? "v" : "t"}`;
    setTests({ ...tests, [key]: { running: true } });
    try {
      const r = await llmSettings.test(o.provider, o.model, vision, role);
      setTests((t) => ({ ...t, [key]: r }));
    } catch (e) {
      setTests((t) => ({ ...t, [key]: { ok: false, error: e.message } }));
    }
  };

  const defaultOpt = optionFor(enc(draft.default));
  const testKey = defaultOpt ? `${defaultOpt.value}|v` : null;
  const testRes = testKey ? tests[testKey] : null;

  const Select = ({ value, onChange, allowInherit, inheritLabel }) => (
    <select className="form-input" value={value} onChange={(e) => onChange(e.target.value)} style={{ padding: "6px 10px", fontSize: 12 }}>
      {allowInherit && <option value="">{inheritLabel || "Use default"}</option>}
      {["ollama", "anthropic", "mock"].map((p) => {
        const group = options.filter((o) => o.provider === p);
        if (!group.length) return null;
        return (
          <optgroup key={p} label={PROVIDER_LABEL[p]}>
            {group.map((o) => <option key={o.value} value={o.value}>{o.model}{o.meta ? ` — ${o.meta}` : ""}</option>)}
          </optgroup>
        );
      })}
      {value && !optionFor(value) && <option value={value}>{value.replace("|", " · ")} (not available)</option>}
    </select>
  );

  return (
    <div>
      <div className="card" style={{ marginBottom: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
          <div>
            <div className="section-head">Agent language model</div>
            <div style={{ fontSize: 12, color: "var(--text3)", maxWidth: 620, lineHeight: 1.6 }}>
              The model every LLM-using agent runs on. Deterministic agents (validation, masking, face match,
              tampering signals, fraud fusion, policy rules) never use it and are unaffected. Changes apply to the next run.
            </div>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn btn-ghost btn-sm" onClick={() => load(true)}><Icon d={Icons.refresh} size={12} /> Refresh models</button>
            <button className="btn btn-primary btn-sm" disabled={!dirty || busy || (needsAck && !ack)} onClick={save}>
              <Icon d={Icons.check} size={12} /> {busy ? "Saving…" : "Save model settings"}
            </button>
          </div>
        </div>

        <div className="form-row" style={{ marginTop: 16, alignItems: "flex-end" }}>
          <div className="form-group" style={{ flex: 2 }}>
            <label className="form-label">Default model (all agents)</label>
            <Select value={enc(draft.default)} onChange={setDefault} />
            {defaultOpt && <div style={{ marginTop: 6 }}><Caps caps={defaultOpt.caps} /></div>}
          </div>
          <div className="form-group" style={{ flex: 1 }}>
            <label className="form-label">Connection</label>
            <div style={{ display: "flex", gap: 6 }}>
              <button className="btn btn-secondary btn-sm" disabled={!defaultOpt || testRes?.running} onClick={() => runTest(defaultOpt, true)}>
                <Icon d={Icons.zap} size={12} /> {testRes?.running ? "Testing…" : "Test (reads an image)"}
              </button>
            </div>
            {testRes && !testRes.running && (
              <div style={{ fontSize: 11, marginTop: 6, color: testRes.ok ? "var(--green)" : "var(--red)" }}>
                {testRes.ok ? `OK · ${testRes.latency_ms} ms · read "${(testRes.reply || "").slice(0, 40)}"` : testRes.error}
              </div>
            )}
          </div>
        </div>

        <div className="form-row">
          <div className="form-group">
            <label className="form-label">Image-reading agents (vision lane)</label>
            <Select value={enc(draft.lanes.vision)} onChange={(v) => setLane("vision", v)} allowInherit inheritLabel="Same as default" />
          </div>
          <div className="form-group">
            <label className="form-label">Writing agents (text lane)</label>
            <Select value={enc(draft.lanes.text)} onChange={(v) => setLane("text", v)} allowInherit inheritLabel="Same as default" />
          </div>
        </div>

        {!ollama.reachable && (
          <div style={{ fontSize: 12, color: "var(--amber)", marginTop: 4 }}>
            Ollama is not reachable at {cfg.ollama?.host}. Start Ollama to list and use local models.
          </div>
        )}
        {!cfg.anthropic?.configured && (
          <div style={{ fontSize: 12, color: "var(--text3)", marginTop: 4 }}>
            Anthropic Claude: not configured on the server (set ANTHROPIC_API_KEY in api/.env). The key is never entered or shown here.
          </div>
        )}
        {cfg.anthropic?.configured && <div style={{ fontSize: 12, color: "var(--text3)", marginTop: 4 }}>Anthropic Claude: configured on server · recommended model {cfg.anthropic.default_model}</div>}

        {hostedNow.length > 0 && (
          <div style={{ marginTop: 14, padding: 14, borderRadius: 8, background: "var(--red-tint)", border: "1px solid var(--red)" }}>
            <div style={{ fontWeight: 600, color: "var(--red)", fontSize: 13, marginBottom: 6 }}>
              <Icon d={Icons.alert} size={13} /> Applicant data will leave this machine
            </div>
            <div style={{ fontSize: 12, color: "var(--text2)", lineHeight: 1.6 }}>{cfg.privacy?.notice}</div>
            <div style={{ fontSize: 12, color: "var(--text2)", marginTop: 6 }}>
              Sent to Anthropic: {hostedNow.map((e) => e.label + (e.data_class === "pii_image" ? " (document images)" : "")).join(", ")}.
            </div>
            {needsAck && (
              <label style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 10, fontSize: 12, color: "var(--text1)", cursor: "pointer" }}>
                <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
                I understand and approve sending this data to Anthropic.
              </label>
            )}
            {cfg.hosted_ack && <div style={{ fontSize: 11, color: "var(--text3)", marginTop: 6 }}>Acknowledged by {cfg.hosted_ack.by} at {cfg.hosted_ack.at}</div>}
          </div>
        )}
        {msg && <div style={{ marginTop: 12, fontSize: 12, color: msg.ok ? "var(--green)" : "var(--red)" }}>{msg.text}</div>}
        {cfg.updated_at && <div style={{ marginTop: 8, fontSize: 11, color: "var(--text3)", fontFamily: "var(--font-mono)" }}>revision {cfg.revision} · last changed {cfg.updated_at} by {cfg.updated_by}</div>}
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="section-head">Per-agent model</div>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Agent</th><th>What the LLM does</th><th>Reads</th><th>Model in use</th><th>Override</th></tr></thead>
            <tbody>
              {effective.map((e) => (
                <tr key={e.agent_id}>
                  <td className="bold" style={{ whiteSpace: "nowrap" }}>{e.label}</td>
                  <td style={{ fontSize: 11, color: "var(--text2)", maxWidth: 260 }}>{e.purpose}</td>
                  <td><span className={`badge ${e.data_class === "pii_image" ? "badge-amber" : e.data_class === "pii_text" ? "badge-blue" : "badge-gray"}`} style={{ fontSize: 10 }}>{e.data_class === "pii_image" ? "document image" : e.data_class === "pii_text" ? "applicant data" : "public text"}</span></td>
                  <td style={{ fontSize: 11, fontFamily: "var(--font-mono)" }}>
                    {e.provider === "anthropic" && <span className="badge badge-red" style={{ fontSize: 9, marginRight: 4 }}>hosted</span>}
                    {e.model} <span style={{ color: "var(--text3)" }}>({e.source})</span>
                  </td>
                  <td style={{ minWidth: 200 }}>
                    <Select value={enc(draft.agents[e.agent_id])} onChange={(v) => setAgent(e.agent_id, v)} allowInherit inheritLabel={e.lane === "vision" ? "Inherit (vision lane)" : "Inherit (text lane)"} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div style={{ fontSize: 11, color: "var(--text3)", marginTop: 8, lineHeight: 1.6 }}>
          Tip: two different local models cannot both stay loaded on an 8 GB GPU — mixing them makes Ollama swap models between calls (several seconds each).
        </div>
      </div>

      <div className="card">
        <div className="section-head">Agent inventory</div>
        {agents === null && <div style={{ fontSize: 12, color: "var(--text3)" }}>Loading agents…</div>}
        {agents && agents.length === 0 && <div style={{ fontSize: 12, color: "var(--text3)" }}>Agent registry not available.</div>}
        {agents && agents.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>#</th><th>Agent</th><th>Crew</th><th>Kind</th><th>Wraps</th><th>Runs</th><th>p50</th><th>Status</th></tr></thead>
              <tbody>
                {agents.map((a) => (
                  <tr key={a.id}>
                    <td style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>{a.number}</td>
                    <td className="bold">{a.name}</td>
                    <td style={{ fontSize: 11 }}>{a.crew}</td>
                    <td><span className={`badge ${a.kind === "D" ? "badge-gray" : a.kind === "H" ? "badge-blue" : "badge-purple"}`} style={{ fontSize: 10 }}>{KIND_LABEL[a.kind]}</span></td>
                    <td style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text3)" }}>{a.wraps}</td>
                    <td style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>{a.stats?.runs ?? 0}</td>
                    <td style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>{a.stats?.p50_ms != null ? `${a.stats.p50_ms} ms` : "—"}</td>
                    <td><span className={`badge ${a.enabled ? "badge-green" : "badge-gray"}`}>{a.enabled ? "Active" : "Disabled"}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
