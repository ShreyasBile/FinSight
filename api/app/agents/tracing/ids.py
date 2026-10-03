"""Case-id conventions for process-mining events.

  document-level activities (DOC_*)   process 'document', case = uuid5(case, doc_type, sha)
  application-level activities        process 'loan_application', case = the case GUID
  case-less agent work                process 'agent_ops', case = the run GUID

Wizard applications get their case GUID minted in the browser at wizard start
(X-Case-Id). Requests that carry no case id (older clients, direct API use)
get a stable synthetic case derived from the file hash, so retried calls still
land in the same case.
"""
from __future__ import annotations

import hashlib
import uuid

NS_FINSIGHT = uuid.UUID("6f1c2b8e-3d4a-5c6b-9e8f-0a1b2c3d4e5f")


def normalize_case_id(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return str(uuid.UUID(str(raw).strip()))
    except (ValueError, AttributeError):
        return str(uuid.uuid5(NS_FINSIGHT, "key:" + str(raw).strip()))


def synthetic_case(file_bytes: bytes | None) -> str:
    h = hashlib.sha256(file_bytes or b"").hexdigest()
    return str(uuid.uuid5(NS_FINSIGHT, "doc:" + h))


def document_case(case_id: str, doc_type: str, sha: str | None) -> str:
    return str(uuid.uuid5(NS_FINSIGHT, f"{case_id}:{doc_type}:{(sha or '')[:16]}"))


def idempotency_key(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:40]
