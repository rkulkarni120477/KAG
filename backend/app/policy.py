"""Student-record authorization decisions.

A decision needs three things: a current grant, the same tenant, and a purpose the caller's role may
use. Purposes: teachers may use "instruction", advisors "advising", and students "self_service" on
their own record only. Administrators hold no student-record purpose, so they are denied by design.

OPA evaluates deployment/policies/education.rego when OPA_URL is set; otherwise the same rules run
in-process. Both fail closed.
"""
import logging
import os

import httpx

log = logging.getLogger("kag.policy")

ROLE_PURPOSES = {"teacher": "instruction", "advisor": "advising", "student": "self_service"}


def purpose_for(role: str) -> str | None:
    return ROLE_PURPOSES.get(role)


def _local_allow(data: dict) -> bool:
    user, student = data["user"], data["student"]
    if data["grant"] is not True or user["tenant_id"] != student["tenant_id"]:
        return False
    role, purpose = user["role"], data["purpose"]
    if role == "teacher":
        return purpose == "instruction"
    if role == "advisor":
        return purpose == "advising"
    if role == "student":
        return purpose == "self_service" and student.get("user_id") == user["id"]
    return False


def allow_student_access(user: dict, student: dict, granted: bool, purpose: str) -> bool:
    data = {
        "user": {"id": user["id"], "tenant_id": user["tenant_id"], "role": user["role"]},
        "student": {"id": student["id"], "tenant_id": student["tenant_id"], "user_id": student.get("user_id")},
        "grant": bool(granted),
        "purpose": purpose,
    }
    opa_url = os.getenv("OPA_URL", "").rstrip("/")
    if not opa_url:
        return _local_allow(data)
    try:
        response = httpx.post(f"{opa_url}/v1/data/education/allow", json={"input": data}, timeout=2.0)
        response.raise_for_status()
        decision = response.json().get("result")
    except Exception:
        log.warning("OPA decision unavailable; denying access")
        return False
    return decision is True
