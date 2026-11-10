import networkx as nx

from .db import db
from .security import student_access


def learning_path(user: dict, student_id: str | None = None) -> dict:
    student = student_access(user, student_id) if student_id else None
    tenant = user["tenant_id"]
    with db() as connection:
        skills = [dict(r) for r in connection.execute("SELECT * FROM skills WHERE tenant_id=? ORDER BY position", (tenant,))]
        relationships = [dict(r) for r in connection.execute("SELECT * FROM prerequisites WHERE tenant_id=?", (tenant,))]
        progress = {r["skill_id"]: dict(r) for r in connection.execute("SELECT * FROM student_progress WHERE student_id=?", (student_id,))} if student else {}
        resources = [dict(r) for r in connection.execute("SELECT id,title,skill_id FROM resources WHERE tenant_id=?", (tenant,))]
    graph = nx.DiGraph()
    graph.add_nodes_from(s["id"] for s in skills)
    graph.add_edges_from((r["requires_id"],r["skill_id"]) for r in relationships)
    if not nx.is_directed_acyclic_graph(graph):
        raise ValueError("Curriculum prerequisite cycle; educator review required")
    positions = {sid:i for i,sid in enumerate(nx.lexicographical_topological_sort(graph, key=lambda x: next(s["position"] for s in skills if s["id"] == x)))}
    steps = []
    for skill in sorted(skills, key=lambda s: positions[s["id"]]):
        record = progress.get(skill["id"], {})
        score = record.get("score")
        status = "satisfied" if score is not None and score >= 80 else "not_satisfied" if score is not None else "unknown"
        requirements = list(graph.predecessors(skill["id"]))
        steps.append({**skill,"score":score,"status":status,"assessed_at":record.get("assessed_at"),"prerequisites":requirements,"resources":[r for r in resources if r["skill_id"] == skill["id"]],"reason": "Review briefly; the synthetic assessment meets the demo threshold." if status == "satisfied" else "Practice this skill; the synthetic assessment is below the demo threshold." if status == "not_satisfied" else "Start with a diagnostic; no mastery evidence is available."})
    mastered = sum(s["status"] == "satisfied" for s in steps)
    return {"student":student,"steps":steps,"mastered":mastered,"total":len(steps),"rule_version":"demo-mastery-v1","threshold":80,"advisory_only":True,"notice":"Synthetic data. The 80% threshold is illustrative, not an official academic requirement."}


def knowledge_graph(user: dict) -> dict:
    tenant = user["tenant_id"]
    with db() as connection:
        skills = [dict(r) for r in connection.execute("SELECT * FROM skills WHERE tenant_id=? ORDER BY position", (tenant,))]
        prerequisites = [dict(r) for r in connection.execute("SELECT * FROM prerequisites WHERE tenant_id=?", (tenant,))]
        standards = [dict(r) for r in connection.execute("SELECT * FROM curriculum_standards WHERE tenant_id=?", (tenant,))]
        courses = [dict(r) for r in connection.execute("SELECT * FROM courses WHERE tenant_id=?", (tenant,))]
    nodes = [{"id":s["id"],"label":s["name"],"type":"skill","description":s["description"]} for s in skills]
    nodes += [{"id":c["id"],"label":c["name"],"type":"course","description":c["description"]} for c in courses]
    nodes += [{"id":s["id"],"label":s["id"],"type":"standard","description":s["description"]} for s in standards]
    edges = [{"source":p["skill_id"],"target":p["requires_id"],"type":"SKILL_REQUIRES_SKILL"} for p in prerequisites]
    if courses:
        edges += [{"source":courses[0]["id"],"target":s["id"],"type":"COURSE_HAS_PREREQUISITE"} for s in skills]
        edges += [{"source":courses[0]["id"],"target":s["id"],"type":"COURSE_ALIGNS_TO_STANDARD"} for s in standards]
    return {"nodes":nodes,"edges":edges,"engine":"SQLite + NetworkX","notice":"Illustrative curriculum graph. No student records are exposed in this graph."}
