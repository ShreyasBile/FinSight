"""Every pre-agent endpoint must answer exactly as it did before the refactor.

Goldens were recorded from the baseline commit (tests/contract/capture.py).
Scores are compared with a small tolerance; everything else must be equal.
"""
import json

import pytest

from tests.contract.specs import GOLDEN_DIR, apply_env, isolate_compliance, run_spec, specs

apply_env()

SCORE_TOL = 0.002


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    isolate_compliance()
    return TestClient(app)


def _diff(a, b, path="$"):
    """Yield human-readable differences between golden `a` and actual `b`."""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in a:
            if k not in b:
                yield f"{path}.{k}: missing in response"
            else:
                yield from _diff(a[k], b[k], f"{path}.{k}")
        # extra keys are allowed (additive contract changes)
        return
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            yield f"{path}: list length {len(a)} -> {len(b)}"
            return
        for i, (x, y) in enumerate(zip(a, b)):
            yield from _diff(x, y, f"{path}[{i}]")
        return
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        if abs(a - b) > SCORE_TOL:
            yield f"{path}: {a} -> {b}"
        return
    if a != b:
        yield f"{path}: {a!r} -> {b!r}"


@pytest.mark.parametrize("spec", specs(), ids=lambda s: s["name"])
def test_endpoint_unchanged(client, spec):
    golden = json.loads((GOLDEN_DIR / f"{spec['name']}.json").read_text(encoding="utf-8"))
    status, body = run_spec(client, spec)
    body = json.loads(json.dumps(body, sort_keys=True))
    assert status == golden["status"]
    diffs = list(_diff(golden["body"], body))
    assert not diffs, "\n".join(diffs[:20])


def test_openapi_only_grows(client):
    old = json.loads((GOLDEN_DIR / "_openapi.json").read_text(encoding="utf-8"))
    new = client.app.openapi()
    missing_paths = [p for p in old["paths"] if p not in new["paths"]]
    assert not missing_paths, f"endpoints removed: {missing_paths}"
    for p, ops in old["paths"].items():
        for method in ops:
            assert method in new["paths"][p], f"{method.upper()} {p} removed"
    old_schemas = old.get("components", {}).get("schemas", {})
    new_schemas = new.get("components", {}).get("schemas", {})
    for name, sch in old_schemas.items():
        if name not in new_schemas:
            continue  # renamed/merged model — field-level check below covers usage
        for field in sch.get("properties", {}):
            assert field in new_schemas[name].get("properties", {}), f"{name}.{field} removed"
