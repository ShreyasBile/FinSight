"""Record contract goldens from the CURRENT code.

    cd api
    .venv/Scripts/python -m tests.contract.capture

Run once on the pre-agent code (the baseline commit); test_contract.py then
asserts every later build returns the same normalized responses and that the
OpenAPI schema only ever gains paths/fields, never loses them.
"""
import json
import sys

from tests.contract.specs import GOLDEN_DIR, apply_env, isolate_compliance, run_spec, specs


def main() -> int:
    apply_env()
    from fastapi.testclient import TestClient
    from app.main import app
    isolate_compliance()

    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    client = TestClient(app)
    only = set(sys.argv[1:])          # optional: re-record just these goldens
    for spec in specs():
        if only and spec["name"] not in only:
            continue
        status, body = run_spec(client, spec)
        (GOLDEN_DIR / f"{spec['name']}.json").write_text(
            json.dumps({"status": status, "body": body}, indent=1, ensure_ascii=False, sort_keys=True),
            encoding="utf-8")
        print(f"{status}  {spec['name']}")
    if not only:  # the OpenAPI baseline is only ever recorded on a full (baseline) capture
        (GOLDEN_DIR / "_openapi.json").write_text(json.dumps(app.openapi(), indent=1, sort_keys=True),
                                                  encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
