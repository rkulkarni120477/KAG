"""Reproducible, clearly illustrative fixtures; never official state standards."""
from datetime import datetime, timezone
import os

from .db import db
from .security import hash_password

SKILLS = [
    ("rational", "Rational numbers", "Build fluency with fractions, decimals, percentages, and signed arithmetic.", []),
    ("ratios", "Ratios & proportional reasoning", "Compare quantities, calculate unit rates, and recognize proportional relationships.", ["rational"]),
    ("expressions", "Expressions & variables", "Translate situations into expressions; apply the distributive property and combine like terms.", ["rational"]),
    ("equations", "Equations & inequalities", "Solve one- and two-step equations and inequalities using equivalent transformations.", ["expressions", "rational"]),
    ("coordinates", "Coordinates & linear relationships", "Connect ordered pairs, tables, graphs, rates of change, and introductory linear relationships.", ["ratios", "equations"]),
]


def seed():
    now = datetime.now(timezone.utc).isoformat()
    with db() as connection:
        if connection.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            return
        connection.execute("INSERT INTO institutions VALUES (?,?,?)", ("northstar", "Northstar Demonstration District", "DEMO"))
        connection.execute("INSERT INTO institutions VALUES (?,?,?)", ("other", "Isolation Test District", "DEMO"))
        password = os.getenv("KAG_DEMO_PASSWORD", "demo-pass-2026")
        for uid,name,role in [("student", "Alex Morgan", "student"),("teacher", "Jordan Lee", "teacher"),("advisor", "Taylor Brooks", "advisor"),("admin", "Casey Rivera", "admin")]:
            connection.execute("INSERT INTO users VALUES (?,?,?,?,?,?)", (uid,"northstar",name,f"{uid}@northstar.demo",role,hash_password(password)))
        # Second tenant exists only for authorization tests; no public demo account.
        connection.execute("INSERT INTO users VALUES (?,?,?,?,?,?)", ("outsider","other","Isolation fixture","isolation@other.demo","teacher",hash_password(os.urandom(32).hex())))
        connection.executemany("INSERT INTO students VALUES (?,?,?,?,?)", [("alex","northstar","Alex Morgan",7,"student"),("jamie","northstar","Jamie Chen",7,None),("outside-student","other","Private fixture",7,None)])
        connection.executemany("INSERT INTO access_grants VALUES (?,?)", [("student","alex"),("teacher","alex"),("teacher","jamie"),("advisor","alex"),("outsider","outside-student")])
        connection.execute("INSERT INTO courses VALUES (?,?,?,?)", ("algebra-1","northstar","Algebra I readiness","Illustrative course readiness map. Placement requires an educator's review."))
        connection.execute("INSERT INTO academic_policies VALUES (?,?,?,?,?)", ("demo-mastery","northstar","Demonstration mastery rubric","A synthetic score of 80 or above is considered demonstrated mastery in this demo. Missing evidence is unknown, not failure. This is not an institutional policy.","demo-v1"))
        for position,(sid,name,description,prereqs) in enumerate(SKILLS):
            connection.execute("INSERT INTO skills VALUES (?,?,?,?,?)", (sid,"northstar",name,description,position))
        for position,(sid,name,description,prereqs) in enumerate(SKILLS):
            for requires in prereqs:
                connection.execute("INSERT INTO prerequisites VALUES (?,?,?)", ("northstar",sid,requires))
            standard_id = f"DEMO-READINESS-{position+1:02}"
            connection.execute("INSERT INTO curriculum_standards VALUES (?,?,?,?,?,?,?,?,?)", (standard_id,"northstar",name,"DEMO",7,description,"demo://curriculum/readiness","demo-v1",1))
            content = f"{name}. {description} Recommended preparation for Algebra I. "
            content += "Prerequisite skills: " + (", ".join(prereqs) if prereqs else "foundational arithmetic") + ". "
            content += "Illustrative curriculum guidance prepared for the Northstar synthetic demonstration; it is not an official state standard or placement requirement."
            connection.execute("INSERT INTO resources VALUES (?,?,?,?,?,?,?,?,?,?)", (f"resource-{sid}","northstar",f"{name}: guided practice",content,sid,"Lesson","demo://resources/"+sid,"demo-v1","curriculum",now))
            connection.execute("INSERT INTO assessments VALUES (?,?,?,?)", (f"assessment-{sid}","northstar",f"{name} diagnostic",sid))
            scores = [92,None,64,None,None]
            jamie_scores = [88,86,91,83,None]
            for student_id,values in [("alex",scores),("jamie",jamie_scores)]:
                connection.execute("INSERT INTO student_progress VALUES (?,?,?,?)", (student_id,sid,values[position],now if values[position] is not None else None))
        connection.execute("INSERT INTO resources VALUES (?,?,?,?,?,?,?,?,?,?)", ("course-outline","northstar","Algebra I preparation guide","Recommended sequence: rational numbers; ratios and proportional reasoning; expressions and variables; equations and inequalities; coordinates and linear relationships. Review supporting skills before dependent skills. This synthetic guide provides advisory preparation and does not determine official enrollment eligibility.",None,"Course guide","demo://courses/algebra-1","demo-v1","curriculum",now))
        connection.execute("INSERT INTO resources VALUES (?,?,?,?,?,?,?,?,?,?)", ("isolation-secret","other","Private tenant document","This document must never be returned to Northstar users.",None,"Policy","demo://other/private","demo-v1","curriculum",now))
        connection.execute("INSERT INTO index_manifests VALUES (?,?,?)", ("northstar",1,now))
        connection.execute("INSERT INTO index_manifests VALUES (?,?,?)", ("other",1,now))
