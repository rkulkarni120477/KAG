import hashlib
import hmac
import os
import secrets
import time

from fastapi import Depends, HTTPException, Request

from .db import db
from .policy import allow_student_access


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    if "$" not in stored:
        return False
    salt, _ = stored.split("$", 1)
    return hmac.compare_digest(hash_password(password, salt), stored)


def new_session(user_id: str) -> str:
    token = secrets.token_urlsafe(40)
    with db() as connection:
        connection.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
        connection.execute("INSERT INTO sessions VALUES (?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), user_id, time.time() + 28800))
    return token


def current_user(request: Request) -> dict:
    token = request.cookies.get("kag_session", "")
    with db() as connection:
        row = connection.execute("SELECT u.id,u.tenant_id,u.name,u.email,u.role FROM users u JOIN sessions s ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?", (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
    if not row:
        raise HTTPException(401, "Please sign in to continue")
    return dict(row)


def admin(user=Depends(current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "Administrator permission required")
    return user


def student_access(user: dict, student_id: str):
    with db() as connection:
        student = connection.execute("SELECT * FROM students WHERE id=? AND tenant_id=?", (student_id, user["tenant_id"])).fetchone()
        grant = connection.execute("SELECT 1 FROM access_grants WHERE user_id=? AND student_id=?", (user["id"], student_id)).fetchone() if student else None
    # Same 404 for "missing", "other tenant" and "no grant" so IDs cannot be enumerated.
    if not student or not allow_student_access(user, dict(student), grant is not None):
        raise HTTPException(404, "Student not found in your authorized scope")
    return dict(student)


def enforce_local_demo():
    if os.getenv("KAG_DEMO_MODE", "true").lower() != "true":
        raise RuntimeError("This release is a synthetic-data pilot. Production identity/governance must be implemented before disabling demo mode.")
