"""Student-record authorization decisions.

Policy is evaluated by OPA when OPA_URL is set (deployment/policies/education.rego), otherwise by
the same rule in-process. Both paths fail closed: any error or unexpected answer denies access.
Inputs carry the current grant state read from SQLite on every call, so a revoked grant takes
effect on the next request.
"""
import logging
import os

import httpx

log = logging.getLogger("kag.policy")


def _local_allow(payload: dict) -> bool:
    data = payload["input"]
    return data["grant"] is True and data["user"]["tenant_id"] == data["student"]["tenant_id"]


def allow_student_access(user: dict, student: dict, granted: bool) -> bool:
    payload = {
        "input": {
            "user": {"id": user["id"], "tenant_id": user["tenant_id"], "role": user["role"]},
            "student": {"id": student["id"], "tenant_id": student["tenant_id"]},
            "grant": bool(granted),
        }
    }
    opa_url = os.getenv("OPA_URL", "").rstrip("/")
    if not opa_url:
        return _local_allow(payload)
    try:
        response = httpx.post(f"{opa_url}/v1/data/education/allow", json=payload, timeout=2.0)
        response.raise_for_status()
        decision = response.json().get("result")
    except Exception:
        log.exception("OPA decision failed; denying access")
        return False
    return decision is True
