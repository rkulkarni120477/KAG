"""Request identity and student-record authorization."""
from fastapi import Depends, HTTPException, Request

from . import auth
from .policy import allow_student_access, purpose_for
from .source_client import source


def current_user(request: Request) -> dict:
    token = request.cookies.get("kag_session", "")
    if not token and request.headers.get("authorization", "").lower().startswith("bearer "):
        token = request.headers["authorization"][7:].strip()
    try:
        claims = auth.verify(token)
    except auth.TokenError:
        raise HTTPException(401, "Please sign in to continue")
    # Second check: the token's identity must exist in the source service with the same role.
    identity = source().user_identity(claims["tenant_id"], claims["kag_user_id"])
    if not identity or identity["role"] != claims["role"]:
        raise HTTPException(401, "Please sign in to continue")
    return {k: identity[k] for k in ("id", "tenant_id", "name", "email", "role")}


def admin(user=Depends(current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "Administrator permission required")
    return user


def student_access(user: dict, student_id: str, purpose: str | None = None):
    """Return the student record if the caller may act on it for `purpose`; otherwise 404.

    Missing, other-tenant, ungranted and wrong-purpose cases all return the same 404 so IDs cannot
    be enumerated.
    """
    purpose = purpose or purpose_for(user["role"])
    student = source().student(user["tenant_id"], student_id)
    if not student:
        raise HTTPException(404, "Student not found in your authorized scope")
    granted = source().is_granted(user["tenant_id"], user["id"], student_id)
    if purpose is None or not allow_student_access(user, student, granted, purpose):
        raise HTTPException(404, "Student not found in your authorized scope")
    return student
