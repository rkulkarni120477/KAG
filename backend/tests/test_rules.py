"""Prerequisite rule fixtures: cycles, ALL/ANY groups, effective dates, waivers, thresholds and missing evidence."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.reasoning import learning_path
from helpers import sql


def _iso(days: int) -> str:
    return (datetime(2026, 6, 1, tzinfo=timezone.utc) + timedelta(days=days)).isoformat()


AS_OF = datetime(2026, 6, 1, tzinfo=timezone.utc)


def make_lab(skills, rules=(), progress=None, waivers=(), threshold=80):
    """skills: names. rules: (skill, group, logic, requires, from_day|None, until_day|None). progress: name -> (score, day)."""
    tenant = f"lab-rules-{uuid.uuid4().hex[:8]}"
    teacher = {"id": f"{tenant}-teacher", "tenant_id": tenant, "role": "teacher"}
    student = f"{tenant}-student"
    sql("INSERT INTO institutions VALUES (?,?,?)", (tenant, "Rules lab", "DEMO"))
    sql("INSERT INTO users VALUES (?,?,?,?,?)", (teacher["id"], tenant, "Lab teacher", f"{tenant}@lab.demo", "teacher"))
    sql("INSERT INTO students VALUES (?,?,?,?,?)", (student, tenant, "Lab student", 7, None))
    sql("INSERT INTO access_grants VALUES (?,?)", (teacher["id"], student))
    sql("INSERT INTO academic_policies VALUES (?,?,?,?,?,?)", (f"{tenant}-policy", tenant, "Rubric", "Lab rubric.", "lab-v1", threshold))
    for position, name in enumerate(skills):
        sql("INSERT INTO skills VALUES (?,?,?,?,?)", (f"{tenant}-{name}", tenant, name, f"{name} lab skill", position))
        sql("INSERT INTO assessments VALUES (?,?,?,?)", (f"{tenant}-as-{name}", tenant, f"{name} check", f"{tenant}-{name}"))
    for index, (skill, group, logic, requires, start, end) in enumerate(rules):
        sql("INSERT INTO prerequisite_rules VALUES (?,?,?,?,?,?,?,?)",
            (f"{tenant}-rule-{index}", tenant, f"{tenant}-{skill}", group, logic, f"{tenant}-{requires}",
             _iso(start) if start is not None else None, _iso(end) if end is not None else None))
    for name, (score, day) in (progress or {}).items():
        sql("INSERT INTO student_progress VALUES (?,?,?,?,?)",
            (student, f"{tenant}-{name}", f"{tenant}-as-{name}", score, _iso(day) if day is not None else None))
    for skill, start, end, revoked in waivers:
        sql("INSERT INTO waivers VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"{tenant}-waiver-{uuid.uuid4().hex[:6]}", tenant, student, f"{tenant}-{skill}", "Documented exception",
             teacher["id"], _iso(start or 0), _iso(end) if end is not None else None,
             _iso(revoked) if revoked is not None else None, _iso(0)))
    return teacher, student, tenant


def statuses(path):
    return {s["name"]: s["status"] for s in path["steps"]}


def steps_by_name(path):
    return {s["name"]: s for s in path["steps"]}


# ------------------------------------------------------------------ cycles
def test_prerequisite_cycle_is_rejected_for_educator_review():
    teacher, student, _ = make_lab(["a", "b"], rules=[("a", "g", "ALL", "b", None, None), ("b", "g", "ALL", "a", None, None)])
    with pytest.raises(ValueError, match="cycle"):
        learning_path(teacher, student, AS_OF)


def test_a_future_dated_rule_cannot_hide_a_cycle():
    teacher, student, _ = make_lab(["a", "b"], rules=[
        ("a", "g", "ALL", "b", None, None),
        ("b", "g", "ALL", "a", 400, None),  # not active until later, but still part of the curriculum
    ])
    with pytest.raises(ValueError, match="cycle"):
        learning_path(teacher, student, AS_OF)


# ------------------------------------------------------------------ AND / OR groups
def test_all_group_requires_every_skill():
    teacher, student, tenant = make_lab(
        ["base", "left", "right", "top"],
        rules=[("top", "g", "ALL", "left", None, None), ("top", "g", "ALL", "right", None, None)],
        progress={"base": (90, 0), "left": (90, 0), "right": (40, 0)},
    )
    top = steps_by_name(learning_path(teacher, student, AS_OF))["top"]
    assert top["ready"] is False
    assert top["blocked_by"] == [f"{tenant}-right"]
    assert top["requirement_groups"][0]["logic"] == "ALL" and top["requirement_groups"][0]["met"] is False


def test_any_group_is_met_by_one_skill():
    teacher, student, tenant = make_lab(
        ["rational", "ratios", "target"],
        rules=[("target", "either", "ANY", "rational", None, None), ("target", "either", "ANY", "ratios", None, None)],
        progress={"rational": (85, 0), "ratios": (20, 0)},
    )
    target = steps_by_name(learning_path(teacher, student, AS_OF))["target"]
    assert target["ready"] is True
    assert target["blocked_by"] == []
    assert target["requirement_groups"][0]["logic"] == "ANY"


def test_any_group_is_not_met_when_none_of_its_skills_are():
    teacher, student, tenant = make_lab(
        ["x", "y", "target"],
        rules=[("target", "either", "ANY", "x", None, None), ("target", "either", "ANY", "y", None, None)],
        progress={"x": (10, 0), "y": (20, 0)},
    )
    target = steps_by_name(learning_path(teacher, student, AS_OF))["target"]
    assert target["ready"] is False
    assert set(target["blocked_by"]) == {f"{tenant}-x", f"{tenant}-y"}


def test_every_group_must_be_met_independently():
    teacher, student, _ = make_lab(
        ["p", "q", "r", "target"],
        rules=[
            ("target", "group-one", "ANY", "p", None, None), ("target", "group-one", "ANY", "q", None, None),
            ("target", "group-two", "ALL", "r", None, None),
        ],
        progress={"p": (90, 0), "r": (10, 0)},
    )
    target = steps_by_name(learning_path(teacher, student, AS_OF))["target"]
    assert target["ready"] is False  # group one met, group two not
    assert [g["met"] for g in target["requirement_groups"]] == [True, False]


def test_a_group_cannot_mix_logic_types():
    teacher, student, _ = make_lab(
        ["a", "b", "c"],
        rules=[("c", "g", "ALL", "a", None, None), ("c", "g", "ANY", "b", None, None)],
    )
    with pytest.raises(ValueError, match="Inconsistent logic"):
        learning_path(teacher, student, AS_OF)


# ------------------------------------------------------------------ effective dates
def test_rule_before_its_effective_date_does_not_block():
    teacher, student, _ = make_lab(["base", "target"], rules=[("target", "g", "ALL", "base", 30, None)],
                                   progress={"base": (10, 0)})
    assert steps_by_name(learning_path(teacher, student, AS_OF))["target"]["ready"] is True
    assert steps_by_name(learning_path(teacher, student, AS_OF + timedelta(days=31)))["target"]["ready"] is False


def test_expired_rule_no_longer_applies():
    teacher, student, _ = make_lab(["base", "target"], rules=[("target", "g", "ALL", "base", None, 10)],
                                   progress={"base": (10, 0)})
    assert steps_by_name(learning_path(teacher, student, AS_OF))["target"]["ready"] is False
    assert steps_by_name(learning_path(teacher, student, AS_OF + timedelta(days=11)))["target"]["ready"] is True


def test_evidence_recorded_after_the_as_of_date_is_not_used():
    teacher, student, _ = make_lab(["solo"], progress={"solo": (95, 10)})
    early = steps_by_name(learning_path(teacher, student, AS_OF))["solo"]
    late = steps_by_name(learning_path(teacher, student, AS_OF + timedelta(days=11)))["solo"]
    assert early["status"] == "unknown"
    assert late["status"] == "satisfied"


# ------------------------------------------------------------------ waivers
def test_active_waiver_satisfies_a_requirement_for_dependents():
    teacher, student, _ = make_lab(["base", "target"], rules=[("target", "g", "ALL", "base", None, None)],
                                   progress={"base": (10, 0)}, waivers=[("base", None, None, None)])
    path = learning_path(teacher, student, AS_OF)
    base, target = steps_by_name(path)["base"], steps_by_name(path)["target"]
    assert base["waived"] is True and base["status"] == "not_satisfied"
    assert target["ready"] is True


def test_waiver_is_effective_dated_and_revocable():
    teacher, student, _ = make_lab(["base", "target"], rules=[("target", "g", "ALL", "base", None, None)],
                                   progress={"base": (10, 0)},
                                   waivers=[("base", 20, None, None), ("base", 0, None, 5)])
    assert steps_by_name(learning_path(teacher, student, AS_OF))["target"]["ready"] is False      # future waiver
    assert steps_by_name(learning_path(teacher, student, AS_OF + timedelta(days=6)))["target"]["ready"] is False  # revoked
    assert steps_by_name(learning_path(teacher, student, AS_OF + timedelta(days=21)))["target"]["ready"] is True   # active


def test_waiver_on_another_tenants_skill_has_no_effect():
    teacher, student, tenant = make_lab(["base", "target"], rules=[("target", "g", "ALL", "base", None, None)],
                                        progress={"base": (10, 0)})
    foreign = f"foreign-{uuid.uuid4().hex[:6]}"
    sql("INSERT INTO skills VALUES (?,?,?,?,?)", (foreign, "other", "Foreign", "Other tenant", 50))
    sql("INSERT INTO waivers VALUES (?,?,?,?,?,?,?,?,?,?)",
        (f"foreign-waiver-{uuid.uuid4().hex[:6]}", tenant, student, foreign, "x", "x", _iso(0), None, None, _iso(0)))
    path = learning_path(teacher, student, AS_OF)
    assert foreign not in {s["id"] for s in path["steps"]}
    assert steps_by_name(path)["target"]["ready"] is False


# ------------------------------------------------------------------ thresholds and missing evidence
def test_mastery_threshold_is_inclusive_at_the_policy_value():
    teacher, student, _ = make_lab(["at", "below"], progress={"at": (80, 0), "below": (79, 0)})
    path = learning_path(teacher, student, AS_OF)
    assert statuses(path) == {"at": "satisfied", "below": "not_satisfied"}
    assert path["mastered"] == 1
    assert path["threshold"] == 80


def test_threshold_comes_from_the_academic_policy():
    teacher, student, _ = make_lab(["solo"], progress={"solo": (70, 0)}, threshold=65)
    path = learning_path(teacher, student, AS_OF)
    assert path["threshold"] == 65
    assert path["steps"][0]["status"] == "satisfied"


def test_missing_evidence_is_unknown_not_failure():
    teacher, student, _ = make_lab(["assessed", "never"], progress={"assessed": (50, 0)})
    path = learning_path(teacher, student, AS_OF)
    assert statuses(path)["never"] == "unknown"
    assert statuses(path)["assessed"] == "not_satisfied"


def test_unknown_prerequisite_does_not_satisfy_an_all_group():
    teacher, student, _ = make_lab(["base", "target"], rules=[("target", "g", "ALL", "base", None, None)])
    assert steps_by_name(learning_path(teacher, student, AS_OF))["target"]["ready"] is False


def test_steps_follow_prerequisites_in_order():
    teacher, student, tenant = make_lab(["base", "middle", "top"], rules=[
        ("middle", "g", "ALL", "base", None, None), ("top", "g", "ALL", "middle", None, None)])
    path = learning_path(teacher, student, AS_OF)
    position = {s["id"]: i for i, s in enumerate(path["steps"])}
    assert position[f"{tenant}-base"] < position[f"{tenant}-middle"] < position[f"{tenant}-top"]
    assert path["steps"][position[f"{tenant}-top"]]["prerequisites"] == [f"{tenant}-middle"]


def test_student_outside_the_callers_tenant_is_not_evaluated():
    teacher, student, _ = make_lab(["solo"], progress={"solo": (90, 0)})
    intruder = {"id": "outsider", "tenant_id": "other", "role": "teacher"}
    with pytest.raises(HTTPException) as caught:
        learning_path(intruder, student, AS_OF)
    assert caught.value.status_code == 404
