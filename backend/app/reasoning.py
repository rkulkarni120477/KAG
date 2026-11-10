"""Deterministic prerequisite rules: ALL/ANY groups, effective dates, waivers and mastery thresholds.

Semantics (documented for educators):
- A skill's prerequisites are a list of requirement groups. Every group must be met. A group with
  logic ALL is met when every skill it names is met; logic ANY when at least one is met.
- A required skill is met when the student's assessed status is "satisfied" or an active waiver covers
  it. Missing evidence is "unknown", which is not a failure and does not satisfy a requirement.
- Rules are effective-dated. Only rules active at `as_of` decide readiness. Cycles are checked against
  every rule, including future or expired ones, so a date cannot hide a circular curriculum.
- Waivers are per student and skill, effective-dated, and can be revoked.
"""
from datetime import datetime, timezone

import networkx as nx

from .indexes import graph_index
from .security import student_access
from .source_client import source

RULE_VERSION = "demo-rules-v2"
DEFAULT_THRESHOLD = 80


def _parse(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _active(from_value: str | None, until_value: str | None, as_of: datetime) -> bool:
    start, end = _parse(from_value), _parse(until_value)
    return (start is None or start <= as_of) and (end is None or as_of < end)


def _graph(skills: list[dict], rules: list[dict]) -> nx.DiGraph:
    graph = nx.DiGraph()
    graph.add_nodes_from(s["id"] for s in skills)
    graph.add_edges_from((r["requires_id"], r["skill_id"]) for r in rules)
    if not nx.is_directed_acyclic_graph(graph):
        raise ValueError("Curriculum prerequisite cycle; educator review required")
    return graph


def _groups(skill_id: str, rules: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for rule in rules:
        if rule["skill_id"] != skill_id:
            continue
        group = grouped.setdefault(rule["group_id"], {"group_id": rule["group_id"], "logic": rule["logic"], "requires": []})
        if group["logic"] != rule["logic"]:
            raise ValueError(f"Inconsistent logic inside requirement group {rule['group_id']}")
        group["requires"].append(rule["requires_id"])
    return sorted(grouped.values(), key=lambda g: g["group_id"])


def learning_path(user: dict, student_id: str | None = None, as_of: datetime | None = None) -> dict:
    as_of = as_of or datetime.now(timezone.utc)
    student = student_access(user, student_id) if student_id else None
    snap = source().snapshot(user["tenant_id"], include_web=False)
    skills = snap["skills"]
    all_rules = snap["rules"]
    _graph(skills, all_rules)  # cycle check over every rule, active or not
    rules = [r for r in all_rules if _active(r["effective_from"], r["effective_until"], as_of)]
    threshold = snap["threshold"] or DEFAULT_THRESHOLD

    progress = {}
    waived = set()
    if student:
        progress = {r["skill_id"]: r for r in source().progress(user["tenant_id"], student_id)}
        waived = {w["skill_id"] for w in source().waivers(user["tenant_id"], student_id)
                  if w["revoked_at"] is None and _active(w["effective_from"], w["effective_until"], as_of)}

    statuses = {}
    evidence = {}
    for skill in skills:
        record = progress.get(skill["id"], {})
        assessed = _parse(record.get("assessed_at"))
        score = record.get("score") if assessed is None or assessed <= as_of else None
        if score is None:
            statuses[skill["id"]] = "unknown"
        else:
            statuses[skill["id"]] = "satisfied" if score >= threshold else "not_satisfied"
        evidence[skill["id"]] = {"score": score, "assessed_at": record.get("assessed_at") if score is not None else None,
                                 "assessment": record.get("assessment_name")}

    def requirement_met(skill_id: str) -> bool:
        return statuses.get(skill_id) == "satisfied" or skill_id in waived

    graph = _graph(skills, all_rules)
    order = list(nx.lexicographical_topological_sort(graph, key=lambda x: next(s["position"] for s in skills if s["id"] == x)))
    by_id = {s["id"]: s for s in skills}
    steps = []
    for sid in order:
        skill = by_id[sid]
        groups = _groups(sid, rules)
        group_results = []
        blocked_by = []
        for group in groups:
            requirement_states = [{"id": req, "met": requirement_met(req)} for req in group["requires"]]
            met = all(r["met"] for r in requirement_states) if group["logic"] == "ALL" else any(r["met"] for r in requirement_states)
            group_results.append({**group, "met": met, "requires": requirement_states})
            if not met:
                blocked_by.extend(r["id"] for r in requirement_states if not r["met"])
        ready = not blocked_by
        status = statuses[sid]
        if sid in waived:
            reason = "A current waiver covers this skill's evidence requirement; educator review is recorded."
        elif status == "satisfied":
            reason = "Review briefly; the synthetic assessment meets the demo threshold."
        elif status == "not_satisfied":
            reason = "Practice this skill; the synthetic assessment is below the demo threshold."
        else:
            reason = "Start with a diagnostic; no mastery evidence is available at this date."
        if not ready and status != "satisfied":
            reason += " Prerequisites are not yet met: " + ", ".join(sorted(set(blocked_by))) + "."
        steps.append({
            **skill,
            "score": evidence[sid]["score"],
            "status": status,
            "assessed_at": evidence[sid]["assessed_at"],
            "assessment": evidence[sid]["assessment"],
            "waived": sid in waived,
            "ready": ready,
            "blocked_by": sorted(set(blocked_by)),
            "prerequisites": sorted({r for g in groups for r in g["requires"]}),
            "requirement_groups": group_results,
            "resources": [r for r in snap["resources"] if r["skill_id"] == sid],
            "reason": reason,
        })
    mastered = sum(s["status"] == "satisfied" for s in steps)
    return {
        "student": student, "steps": steps, "mastered": mastered, "total": len(steps),
        "rule_version": RULE_VERSION, "threshold": threshold, "as_of": as_of.isoformat(),
        "advisory_only": True,
        "notice": "Synthetic data. The threshold is illustrative, not an official academic requirement.",
    }


def knowledge_graph(user: dict) -> dict:
    snap = source().snapshot(user["tenant_id"], include_web=True)
    skills = snap["skills"]
    resources = [r for r in snap["resources"]]
    resource_ids = {r["id"] for r in resources}
    skill_ids = {s["id"] for s in skills}
    nodes = [{"id": s["id"], "label": s["name"], "type": "skill", "description": s["description"]} for s in skills]
    nodes += [{"id": c["id"], "label": c["name"], "type": "course", "description": c["description"]} for c in snap["courses"]]
    nodes += [{"id": s["id"], "label": s["id"], "type": "standard", "description": s["description"]} for s in snap["standards"]]
    nodes += [{"id": t["id"], "label": t["label"], "type": "topic", "description": f"Topic from local sources saved {t['created_at'][:10]}."} for t in snap["topics"]]
    nodes += [{"id": r["id"], "label": r["title"], "type": "resource", "description": r["text"][:400], "kind": r["kind"], "source_uri": r["source_uri"]} for r in resources]
    edges = [{"source": r["skill_id"], "target": r["requires_id"], "type": "SKILL_REQUIRES_SKILL", "logic": r["logic"], "group": r["group_id"]} for r in snap["rules"]]
    # Links come from the graph index (Neo4j when configured), limited to links SQLite has published.
    published = {tuple(p) for p in snap["support"]}
    indexed = graph_index().support_pairs(user["tenant_id"], snap["generation"])
    support = sorted(p for p in indexed if p in published and p[0] in resource_ids and p[1] in skill_ids)
    edges += [{"source": r, "target": s, "type": "RESOURCE_SUPPORTS_SKILL"} for r, s in support]
    edges += [{"source": l["resource_id"], "target": l["topic_id"], "type": "RESOURCE_COVERS_TOPIC"} for l in snap["topic_links"] if l["resource_id"] in resource_ids]
    if snap["courses"]:
        edges += [{"source": snap["courses"][0]["id"], "target": s["id"], "type": "COURSE_HAS_PREREQUISITE"} for s in skills]
        edges += [{"source": snap["courses"][0]["id"], "target": s["id"], "type": "COURSE_ALIGNS_TO_STANDARD"} for s in snap["standards"]]
    return {"nodes": nodes, "edges": edges, "engine": "Source service + NetworkX; Neo4j when configured",
            "notice": "Illustrative curriculum graph. No student records are exposed in this graph."}
