"""LLM selection, gateway, privacy guard and queue."""
import json
import threading
import time

import pytest

from app.agents.context import current_step
from app.agents.llm import gateway, privacy, settings_store
from app.agents.governor import LLMGate, QueueTimeout


class _Step:
    def __init__(self):
        self.llm_calls, self.tool_calls = [], []


# ── resolution order ────────────────────────────────────────────────────────
def test_env_default_when_nothing_saved(fresh_settings):
    r = settings_store.resolve("extraction")
    assert r.provider == "mock" and r.source == "env"


def test_agent_override_beats_lane_beats_default(fresh_settings):
    doc = settings_store.load()
    doc["default"].update(provider="ollama", model="qwen2.5vl:7b")
    doc["lanes"]["text"] = {"provider": "ollama", "model": "qwen3-vl:4b"}
    doc["agents"]["credit_memo"] = {"provider": "mock", "model": "mock"}
    settings_store.save(doc, "t")
    assert settings_store.resolve("extraction").model == "qwen2.5vl:7b"       # vision lane unset -> default
    assert settings_store.resolve("regulatory_qa").model == "qwen3-vl:4b"     # text lane
    assert settings_store.resolve("regulatory_qa").source == "lane"
    r = settings_store.resolve("credit_memo")
    assert (r.provider, r.source) == ("mock", "agent")


def test_snapshot_is_frozen(fresh_settings):
    snap = settings_store.snapshot()
    doc = settings_store.load()
    doc["default"].update(provider="ollama", model="changed")
    settings_store.save(doc, "t")
    assert snap.resolve("extraction").provider == "mock"      # the run keeps its snapshot
    assert settings_store.resolve("extraction").model == "changed"


# ── Settings API validation ─────────────────────────────────────────────────
@pytest.fixture
def client(fresh_settings, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.agents.llm import ollama_admin
    monkeypatch.setattr(ollama_admin, "list_models", lambda force=False: [
        {"name": "qwen2.5vl:7b", "capabilities": ["completion", "vision"]},
        {"name": "qwen3-vl:4b", "capabilities": ["completion", "vision", "tools", "thinking"]},
        {"name": "llama-text", "capabilities": ["completion"]},
    ])
    monkeypatch.setattr(ollama_admin, "capabilities", lambda m: {
        "qwen2.5vl:7b": ["completion", "vision"], "llama-text": ["completion"]}.get(m, []))
    return TestClient(app)


HEAD = {"X-Actor-Role": "head", "X-Actor-Id": "head-1"}


def test_get_settings_never_returns_the_key(client):
    body = client.get("/settings/llm").json()
    assert "anthropic_api_key" not in json.dumps(body)
    assert {e["agent_id"] for e in body["effective"]} >= {"extraction", "authenticity", "credit_memo"}


def test_put_requires_head(client):
    r = client.put("/settings/llm", json={"expected_revision": 0}, headers={"X-Actor-Role": "officer"})
    assert r.status_code == 403


def test_put_rejects_stale_revision(client):
    r = client.put("/settings/llm", json={"expected_revision": 5}, headers=HEAD)
    assert r.status_code == 409


def test_put_switches_model_and_bumps_revision(client):
    r = client.put("/settings/llm", headers=HEAD, json={
        "expected_revision": 0, "default": {"provider": "ollama", "model": "qwen2.5vl:7b"}})
    assert r.status_code == 200, r.text
    assert r.json()["revision"] == 1
    assert settings_store.resolve("extraction").model == "qwen2.5vl:7b"


def test_put_rejects_uninstalled_model(client):
    r = client.put("/settings/llm", headers=HEAD, json={
        "expected_revision": 0, "default": {"provider": "ollama", "model": "not-pulled:1b"}})
    assert r.status_code == 422 and "not installed" in r.text


def test_put_rejects_text_model_for_image_agent(client):
    r = client.put("/settings/llm", headers=HEAD, json={
        "expected_revision": 0, "default": {"provider": "ollama", "model": "qwen2.5vl:7b"},
        "agents": {"extraction": {"provider": "ollama", "model": "llama-text"}}})
    assert r.status_code == 422 and "vision" in r.text


def test_hosted_provider_blocked_in_test_environment(client):
    r = client.put("/settings/llm", headers=HEAD, json={
        "expected_revision": 0, "agents": {"credit_memo": {"provider": "anthropic", "model": "claude-opus-5"}},
        "acknowledge_hosted": True})
    assert r.status_code == 422


def test_hosted_requires_acknowledgment(client, monkeypatch):
    from app.config import settings
    from app.agents.llm import anthropic_admin
    monkeypatch.setattr(settings, "finsight_forbid_hosted", False)
    monkeypatch.setattr(anthropic_admin, "configured", lambda: True)
    body = {"expected_revision": 0, "default": {"provider": "ollama", "model": "qwen2.5vl:7b"},
            "agents": {"credit_memo": {"provider": "anthropic", "model": "claude-opus-5"}}}
    assert client.put("/settings/llm", headers=HEAD, json=body).status_code == 422
    body["acknowledge_hosted"] = True
    r = client.put("/settings/llm", headers=HEAD, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["hosted_ack"]["agents"] == ["credit_memo"]
    # back to all-local clears the acknowledgment
    r = client.put("/settings/llm", headers=HEAD, json={"expected_revision": 1, "agents": {"credit_memo": None}})
    assert r.status_code == 200 and r.json()["hosted_ack"] is None


# ── privacy ─────────────────────────────────────────────────────────────────
def test_privacy_guard_fails_closed():
    with pytest.raises(privacy.PrivacyBlocked):
        privacy.check("credit_memo", "anthropic", hosted_acknowledged=True)  # forbidden in tests
    privacy.check("credit_memo", "ollama", hosted_acknowledged=False)       # local always fine


def test_pseudonymize_masks_ids_and_names():
    bundle = {"applicant_name": "Rahul Sharma", "pan": "ABCPS1234K", "aadhaar": "2345 6789 0123",
              "address": "12 MG Road", "notes": "Rahul Sharma's account 123456789012 is active",
              "net_salary": 75000}
    out = privacy.pseudonymize(bundle)
    s = json.dumps(out)
    assert "Rahul" not in s and "Sharma" not in s
    assert "ABCPS1234K" not in s and "XXXXXX234K" in s
    assert "2345 6789" not in s and "0123" in s
    assert "address" not in out and out["net_salary"] == 75000


# ── gateway ─────────────────────────────────────────────────────────────────
def test_mock_call_is_recorded_on_active_step(fresh_settings, mock_llm):
    mock_llm["regulatory_qa"] = lambda kw: {"answer": "ok"}
    step = _Step()
    tok = current_step.set(step)
    try:
        res = gateway.chat("regulatory_qa", user="hi")
    finally:
        current_step.reset(tok)
    assert res.parsed == {"answer": "ok"} and res.provider == "mock"
    assert step.llm_calls and step.llm_calls[0]["ok"] and step.llm_calls[0]["agent"] == "regulatory_qa"


def test_ollama_payload_unchanged(fresh_settings, monkeypatch):
    import httpx
    doc = settings_store.load()
    doc["default"].update(provider="ollama", model="qwen3-vl:4b")
    settings_store.save(doc, "t")
    from app.agents.llm import ollama_admin
    monkeypatch.setattr(ollama_admin, "capabilities", lambda m: ["completion", "vision", "thinking"])
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": '{"verdict": "genuine"}'},
                                         "prompt_eval_count": 10, "eval_count": 5})
    transport = httpx.MockTransport(handler)
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=transport, **kw))
    res = gateway.chat("authenticity", system="sys", user="u", images=[b"\xff\xd8\xff" + b"0" * 20])
    assert res.parsed == {"verdict": "genuine"}
    assert seen["format"] == "json" and seen["options"]["temperature"] == 0.0
    assert seen["think"] is False                         # thinking model: reasoning off
    assert seen["messages"][0]["role"] == "system" and seen["messages"][1]["images"]


def test_hosted_call_refused_by_gateway(fresh_settings):
    doc = settings_store.load()
    doc["agents"]["credit_memo"] = {"provider": "anthropic", "model": "claude-opus-5"}
    doc["hosted_ack"] = {"by": "x"}
    settings_store.save(doc, "t")
    with pytest.raises(privacy.PrivacyBlocked):
        gateway.chat("credit_memo", user="bundle")


# ── queue ───────────────────────────────────────────────────────────────────
def test_gate_serves_higher_priority_first():
    gate = LLMGate()
    order = []
    gate.acquire("ollama", 0, 5)            # occupy the only slot

    def waiter(name, prio):
        with gate.slot("ollama", prio, 5):
            order.append(name)
    t_low = threading.Thread(target=waiter, args=("memo", 4)); t_low.start()
    time.sleep(0.1)
    t_high = threading.Thread(target=waiter, args=("extraction", 0)); t_high.start()
    time.sleep(0.1)
    gate.release("ollama")
    t_low.join(); t_high.join()
    assert order == ["extraction", "memo"]


def test_gate_queue_timeout():
    gate = LLMGate()
    gate.acquire("ollama", 0, 5)
    with pytest.raises(QueueTimeout):
        gate.acquire("ollama", 1, 0.3)
    gate.release("ollama")
    assert gate.acquire("ollama", 1, 1) >= 0


# ── guardrails still enforced behind the gateway ────────────────────────────
def _auth_run(monkeypatch, mock_llm, reply):
    from PIL import Image
    from app.config import settings
    from app.tampering.signals import authenticity
    monkeypatch.setattr(settings, "tampering_authenticity_enabled", True)
    monkeypatch.setattr(authenticity, "_page_tokens", lambda im: [{"text": "Net Salary 75,000.00"}])
    mock_llm["authenticity"] = lambda kw: reply
    return authenticity.run(Image.new("RGB", (200, 100), "white"), "salary_slip",
                            {"net_salary": 75000})


def test_authenticity_ungrounded_number_dropped(fresh_settings, mock_llm, monkeypatch):
    r = _auth_run(monkeypatch, mock_llm, {"verdict": "likely_fabricated",
                                          "reasons": ["Net salary shows 9,99,999 which is not printed"]})
    # the only reason cites a number that is not on the page -> flag voided
    assert not (r.available and r.score >= 0.5)


def test_authenticity_arithmetic_claims_dropped(fresh_settings, mock_llm, monkeypatch):
    r = _auth_run(monkeypatch, mock_llm, {"verdict": "suspicious",
                                          "reasons": ["gross and deductions do not add up"]})
    assert not (r.available and r.score >= 0.5)


def test_authenticity_grounded_reason_kept_and_capped_in_fusion(fresh_settings, mock_llm, monkeypatch):
    r = _auth_run(monkeypatch, mock_llm, {"verdict": "likely_fabricated",
                                          "reasons": ["Employer name 'ABC Company' is a placeholder"]})
    assert r.available and r.score == 0.85
    from app.tampering.fusion import fuse
    from app.tampering.signals.base import SignalResult
    fused = fuse([r, SignalResult("business_rules", True, 0.0, ["All 2 applicable rules passed"])])
    assert fused["risk_tier"] == "MEDIUM"          # a lone authenticity flag never reaches HIGH
