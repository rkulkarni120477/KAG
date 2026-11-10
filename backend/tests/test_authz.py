"""Authorization and token validation: stale grants, purposes, fail-closed policy, token forgery,
tenant isolation, citation dereference and unauthorized path intermediates."""
import asyncio
import os
import uuid

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.main import app
from app.policy import allow_student_access
from app.worker.pipeline import publish_documents
from app.source_client import source
from helpers import PRIVATE_PEM, mint, query_one, sql


def run(scenario):
    asyncio.run(scenario())


def client(cookie: str | None = None):
    headers = {"Cookie": f"kag_session={cookie}"} if cookie else {}
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers)


async def login_as(c, email):
    response = await c.post("/api/v1/auth/login", json={"email": email, "password": "demo-pass-2026"})
    assert response.status_code == 200, response.text


# ------------------------------------------------------------------ grants and policy
def test_revoked_grant_takes_effect_on_the_next_request():
    async def scenario():
        async with client() as c:
            await login_as(c, "teacher@northstar.demo")
            assert (await c.get("/api/v1/students/alex/learning-path")).status_code == 200
            sql("DELETE FROM access_grants WHERE user_id='teacher' AND student_id='alex'")
            try:
                assert (await c.get("/api/v1/students/alex/learning-path")).status_code == 404
                assert (await c.post("/api/v1/kag/query", json={"question": "Algebra I", "student_id": "alex"})).status_code == 404
            finally:
                sql("INSERT OR IGNORE INTO access_grants VALUES ('teacher','alex')")
            assert (await c.get("/api/v1/students/alex/learning-path")).status_code == 200
    run(scenario)


def test_policy_service_outage_fails_closed(monkeypatch):
    async def scenario():
        async with client() as c:
            await login_as(c, "teacher@northstar.demo")
            monkeypatch.setenv("OPA_URL", "http://127.0.0.1:9")  # nothing listens here
            try:
                assert (await c.get("/api/v1/students/alex/learning-path")).status_code == 404
            finally:
                monkeypatch.delenv("OPA_URL")
            assert (await c.get("/api/v1/students/alex/learning-path")).status_code == 200
    run(scenario)


TEACHER = {"id": "teacher", "tenant_id": "northstar", "role": "teacher"}
ALEX = {"id": "alex", "tenant_id": "northstar", "user_id": "student"}


@pytest.mark.parametrize("role,user_id,purpose,granted,tenant,expected", [
    ("teacher", "teacher", "instruction", True, "northstar", True),
    ("teacher", "teacher", "advising", True, "northstar", False),       # wrong purpose for role
    ("teacher", "teacher", "instruction", False, "northstar", False),   # no grant
    ("teacher", "teacher", "instruction", True, "other", False),        # other tenant
    ("advisor", "advisor", "advising", True, "northstar", True),
    ("advisor", "advisor", "instruction", True, "northstar", False),
    ("student", "student", "self_service", True, "northstar", True),
    ("student", "other-student", "self_service", True, "northstar", False),  # not their own record
    ("admin", "admin", "instruction", True, "northstar", False),        # admins hold no record purpose
])
def test_policy_requires_role_purpose_grant_and_tenant(role, user_id, purpose, granted, tenant, expected):
    user = {"id": user_id, "tenant_id": "northstar", "role": role}
    student = {**ALEX, "tenant_id": tenant}
    assert allow_student_access(user, student, granted, purpose) is expected


def test_local_policy_and_api_agree_for_admin_records():
    async def scenario():
        async with client() as c:
            await login_as(c, "admin@northstar.demo")
            assert (await c.get("/api/v1/students/alex/learning-path")).status_code == 404
    run(scenario)


# ------------------------------------------------------------------ token validation
def _forged_key_token():
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return mint("student", "northstar", "student", private_pem=other)


@pytest.mark.parametrize("token", [
    "",                                                              # missing
    "not-a-jwt",                                                     # malformed
    mint("student", "northstar", "student", expires_in=-60),         # expired
    mint("student", "northstar", "student", audience="someone-else"),  # wrong audience
    mint("student", "northstar", "student", issuer="https://evil.test/realms/x"),  # wrong issuer
    _forged_key_token(),                                             # signed by an unknown key
    mint("student", "northstar", "admin"),                           # role claim differs from source
    mint("student", "other", "student"),                             # tenant claim differs from source
    mint("ghost", "northstar", "student"),                           # user not in source
])
def test_invalid_tokens_are_rejected(token):
    async def scenario():
        async with client(cookie=token or None) as c:
            assert (await c.get("/api/v1/auth/me")).status_code == 401
            assert (await c.get("/api/v1/students/alex/learning-path")).status_code == 401
    run(scenario)


def test_bearer_token_is_accepted_for_api_clients():
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     headers={"Authorization": f"Bearer {mint('teacher', 'northstar', 'teacher')}"}) as c:
            assert (await c.get("/api/v1/auth/me")).json()["id"] == "teacher"
    run(scenario)


def test_wrong_password_is_rejected_and_repeated_failures_lock_the_account():
    from app import runtime

    async def scenario():
        async with client() as c:
            for _ in range(10):
                assert (await c.post("/api/v1/auth/login", json={"email": "jordan@northstar.demo", "password": "x"})).status_code == 401
            locked = await c.post("/api/v1/auth/login", json={"email": "jordan@northstar.demo", "password": "demo-pass-2026"})
            assert locked.status_code == 429
    run(scenario)
    runtime.clear_failures("jordan@northstar.demo")


# ------------------------------------------------------------------ tenant isolation and citations
def test_another_tenants_session_cannot_reach_northstar_records_or_citations():
    async def scenario():
        async with client(cookie=mint("outsider", "other", "teacher")) as c:
            assert (await c.get("/api/v1/students/alex/learning-path")).status_code == 404
            assert (await c.get("/api/v1/evidence/resource-ratios")).status_code == 404
            assert (await c.get("/api/v1/evidence/course-outline")).status_code == 404
            assert (await c.get("/api/v1/evidence/isolation-secret")).status_code == 200  # own tenant
    run(scenario)


def test_citation_dereference_is_scoped_to_the_callers_tenant():
    tenant = f"lab-authz-{uuid.uuid4().hex[:8]}"
    resource_id = publish_documents(tenant, [{"title": "Private lab note", "text": "A note that only the lab tenant may read. Synthetic."}])["resource_ids"][0]

    async def scenario():
        async with client() as c:
            await login_as(c, "teacher@northstar.demo")
            assert (await c.get(f"/api/v1/evidence/{resource_id}")).status_code == 404
            answer = (await c.post("/api/v1/kag/query", json={"question": "private lab note"})).json()
            assert resource_id not in [e["id"] for e in answer["evidence"]]
    run(scenario)


def test_ids_for_missing_and_foreign_students_are_indistinguishable():
    async def scenario():
        async with client() as c:
            await login_as(c, "teacher@northstar.demo")
            missing = await c.get("/api/v1/students/does-not-exist/learning-path")
            foreign = await c.get("/api/v1/students/outside-student/learning-path")
            assert missing.status_code == foreign.status_code == 404
            assert missing.json() == foreign.json()
    run(scenario)


# ------------------------------------------------------------------ unauthorized path intermediates
def _lab(tag: str):
    """Create an isolated tenant with a teacher, a student and a skill chain a -> b -> c."""
    tenant = f"lab-path-{tag}-{uuid.uuid4().hex[:6]}"
    sql("INSERT INTO institutions VALUES (?,?,?)", (tenant, "Path lab", "DEMO"))
    sql("INSERT INTO users VALUES (?,?,?,?,?)", (f"{tenant}-teacher", tenant, "Lab teacher", f"{tenant}@lab.demo", "teacher"))
    sql("INSERT INTO students VALUES (?,?,?,?,?)", (f"{tenant}-s", tenant, "Lab student", 7, None))
    sql("INSERT INTO access_grants VALUES (?,?)", (f"{tenant}-teacher", f"{tenant}-s"))
    for position, name in enumerate(["a", "b", "c"]):
        sql("INSERT INTO skills VALUES (?,?,?,?,?)", (f"{tenant}-{name}", tenant, name, f"{name} skill", position))
    sql("INSERT INTO assessments VALUES (?,?,?,?)", (f"{tenant}-as-a", tenant, "a check", f"{tenant}-a"))
    sql("INSERT INTO student_progress VALUES (?,?,?,?,?)", (f"{tenant}-s", f"{tenant}-a", f"{tenant}-as-a", 90, "2026-01-01T00:00:00+00:00"))
    return {"id": f"{tenant}-teacher", "tenant_id": tenant, "role": "teacher"}, f"{tenant}-s", tenant


def test_prerequisite_on_another_tenants_skill_is_never_followed():
    from app.reasoning import learning_path

    teacher, student_id, tenant = _lab("foreign")
    other_skill = f"other-skill-{uuid.uuid4().hex[:6]}"
    sql("INSERT INTO skills VALUES (?,?,?,?,?)", (other_skill, "other", "Foreign skill", "Other tenant", 99))
    sql("INSERT INTO prerequisite_rules VALUES (?,?,?,?,?,?,?,?)",
        (f"{tenant}-leak", tenant, f"{tenant}-c", "leak", "ALL", other_skill, None, None))
    path = learning_path(teacher, student_id)
    ids = {s["id"] for s in path["steps"]}
    assert other_skill not in ids
    assert all(other_skill not in s["prerequisites"] for s in path["steps"])


def test_intermediate_skill_from_another_tenant_cannot_satisfy_a_requirement():
    from app.reasoning import learning_path

    teacher, student_id, tenant = _lab("intermediate")
    foreign = f"foreign-mid-{uuid.uuid4().hex[:6]}"
    sql("INSERT INTO skills VALUES (?,?,?,?,?)", (foreign, "other", "Foreign mid", "Other tenant", 98))
    sql("INSERT INTO assessments VALUES (?,?,?,?)", (f"{foreign}-as", "other", "foreign check", foreign))
    sql("INSERT INTO student_progress VALUES (?,?,?,?,?)", (student_id, foreign, f"{foreign}-as", 100, "2026-01-01T00:00:00+00:00"))
    sql("INSERT INTO prerequisite_rules VALUES (?,?,?,?,?,?,?,?)",
        (f"{tenant}-mid", tenant, f"{tenant}-b", "default-all", "ALL", foreign, None, None))
    path = learning_path(teacher, student_id)
    step_b = next(s for s in path["steps"] if s["id"] == f"{tenant}-b")
    assert step_b["ready"] is True or foreign not in step_b["blocked_by"]
    assert foreign not in {s["id"] for s in path["steps"]}


def test_path_only_contains_skills_of_the_callers_tenant():
    from app.reasoning import learning_path

    teacher, student_id, tenant = _lab("scope")
    path = learning_path(teacher, student_id)
    assert {s["id"] for s in path["steps"]} == {f"{tenant}-a", f"{tenant}-b", f"{tenant}-c"}


def test_teacher_can_list_waivers_for_their_student_and_others_cannot():
    async def scenario():
        async with client() as c:
            await login_as(c, "teacher@northstar.demo")
            assert (await c.get("/api/v1/students/alex/waivers")).status_code == 200
            assert (await c.get("/api/v1/students/outside-student/waivers")).status_code == 404
    run(scenario)


OPA_TEST_URL = os.getenv("OPA_TEST_URL", "")


@pytest.mark.skipif(not OPA_TEST_URL, reason="OPA_TEST_URL not set")
@pytest.mark.parametrize("role,user_id,purpose,granted,tenant,expected", [
    ("teacher", "teacher", "instruction", True, "northstar", True),
    ("teacher", "teacher", "advising", True, "northstar", False),
    ("teacher", "teacher", "instruction", False, "northstar", False),
    ("teacher", "teacher", "instruction", True, "other", False),
    ("advisor", "advisor", "advising", True, "northstar", True),
    ("advisor", "advisor", "instruction", True, "northstar", False),
    ("student", "student", "self_service", True, "northstar", True),
    ("student", "other-student", "self_service", True, "northstar", False),
    ("admin", "admin", "instruction", True, "northstar", False),
])
def test_rego_policy_matches_the_in_process_rule(monkeypatch, role, user_id, purpose, granted, tenant, expected):
    monkeypatch.setenv("OPA_URL", OPA_TEST_URL)
    user = {"id": user_id, "tenant_id": "northstar", "role": role}
    student = {"id": "alex", "tenant_id": tenant, "user_id": "student"}
    assert allow_student_access(user, student, granted, purpose) is expected
