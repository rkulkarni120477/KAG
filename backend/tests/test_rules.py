"""Prerequisite rule fixtures: cycles, mastery thresholds, missing evidence and effective ordering."""
import os
import tempfile
from uuid import uuid4

os.environ.setdefault("KAG_DATA_DIR", tempfile.mkdtemp(prefix="kag-rules-test-"))
os.environ["KAG_LOAD_DOTENV"] = "false"

import pytest

from app.db import db, initialize
from app.reasoning import learning_path
from app.security import hash_password


@pytest.fixture(autouse=True)
def schema():
    initialize()


def make_lab(skills, prerequisites, progress):
    """Create an isolated tenant with one teacher, one student and the given curriculum."""
    tenant = f"lab-rules-{uuid4().hex[:8]}"
    teacher = {"id": f"{tenant}-teacher", "tenant_id": tenant, "role": "teacher", "name": "Lab", "email": f"{tenant}@lab.demo"}
    student_id = f"{tenant}-student"
    with db() as c:
        c.execute("INSERT INTO institutions VALUES (?,?,?)", (tenant, "Rules lab", "DEMO"))
        c.execute("INSERT INTO users VALUES (?,?,?,?,?,?)", (teacher["id"], tenant, "Lab", teacher["email"], "teacher", hash_password("x")))
        c.execute("INSERT INTO students VALUES (?,?,?,?,?)", (student_id, tenant, "Lab student", 7, None))
        c.execute("INSERT INTO access_grants VALUES (?,?)", (teacher["id"], student_id))
        for position, (name, _) in enumerate(skills):
            c.execute("INSERT INTO skills VALUES (?,?,?,?,?)", (f"{tenant}-{name}", tenant, name, f"{name} lab skill", position))
        for skill, requires in prerequisites:
            c.execute("INSERT INTO prerequisites VALUES (?,?,?)", (tenant, f"{tenant}-{skill}", f"{tenant}-{requires}"))
        for name, score in progress.items():
            c.execute("INSERT INTO student_progress VALUES (?,?,?,?)", (student_id, f"{tenant}-{name}", score, "2026-01-01" if score is not None else None))
    return teacher, student_id, tenant


def statuses(path):
    return {s["name"]: s["status"] for s in path["steps"]}


def test_prerequisite_cycle_is_rejected_for_educator_review():
    teacher, student_id, _ = make_lab(
        skills=[("a", None), ("b", None)],
        prerequisites=[("a", "b"), ("b", "a")],
        progress={},
    )
    with pytest.raises(ValueError, match="cycle"):
        learning_path(teacher, student_id)


def test_mastery_threshold_is_inclusive_at_80():
    teacher, student_id, _ = make_lab(
        skills=[("at-threshold", None), ("below-threshold", None)],
        prerequisites=[],
        progress={"at-threshold": 80, "below-threshold": 79},
    )
    path = learning_path(teacher, student_id)
    assert statuses(path) == {"at-threshold": "satisfied", "below-threshold": "not_satisfied"}
    assert path["mastered"] == 1


def test_missing_evidence_is_unknown_not_failure():
    teacher, student_id, _ = make_lab(
        skills=[("assessed", None), ("never-assessed", None)],
        prerequisites=[],
        progress={"assessed": 50},
    )
    path = learning_path(teacher, student_id)
    assert statuses(path)["never-assessed"] == "unknown"
    assert statuses(path)["assessed"] == "not_satisfied"


def test_steps_follow_prerequisites_in_order():
    teacher, student_id, tenant = make_lab(
        skills=[("base", None), ("middle", None), ("top", None)],
        prerequisites=[("middle", "base"), ("top", "middle")],
        progress={},
    )
    path = learning_path(teacher, student_id)
    position = {s["id"]: i for i, s in enumerate(path["steps"])}
    assert position[f"{tenant}-base"] < position[f"{tenant}-middle"] < position[f"{tenant}-top"]
    assert path["steps"][position[f"{tenant}-top"]]["prerequisites"] == [f"{tenant}-middle"]


def test_student_outside_the_callers_tenant_is_not_evaluated():
    teacher, student_id, _ = make_lab(skills=[("solo", None)], prerequisites=[], progress={"solo": 90})
    intruder = {"id": "outsider", "tenant_id": "other", "role": "teacher", "name": "x", "email": "x@other.demo"}
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as caught:
        learning_path(intruder, student_id)
    assert caught.value.status_code == 404
