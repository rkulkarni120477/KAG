"""Render editable SVG and matching 4K PNG diagrams using Pillow only.

Run from the repository root: python tools/render_diagrams.py
Vector shapes and text are authored directly, without raster image generation.
"""

from html import escape
from pathlib import Path
import math

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "diagrams"
NAVY = "#153453"
BLUE = "#326998"
INK = "#253e54"
MUTED = "#587085"
BORDER = "#c6d5e2"
LIGHT = "#f3f7fb"
TEAL = "#287d87"
GOLD = "#a76613"
SCALE = 2


class Canvas:
    def __init__(self, name, title, subtitle):
        self.name = name
        self.im = Image.new("RGB", (3840, 2160), "white")
        self.draw = ImageDraw.Draw(self.im)
        self.svg = [
            '<svg xmlns="http://www.w3.org/2000/svg" width="3840" height="2160" viewBox="0 0 1920 1080">',
            f'<title>{escape(title)}</title>',
            f'<desc>{escape(subtitle)}</desc>',
            '<rect width="1920" height="1080" fill="white"/>',
        ]
        self.text(50, 35, title, 33, NAVY, bold=True)
        self.text(50, 78, subtitle, 19, MUTED)

    def font(self, size, bold=False):
        file = "arialbd.ttf" if bold else "arial.ttf"
        return ImageFont.truetype(f"C:/Windows/Fonts/{file}", size * SCALE)

    def text(self, x, y, content, size=20, color=INK, bold=False, anchor="start"):
        font = self.font(size, bold)
        width = self.draw.textlength(content, font=font) / SCALE
        actual_x = x - (width / 2 if anchor == "middle" else width if anchor == "end" else 0)
        if actual_x < 0 or actual_x + width > 1920 or y + size > 1080:
            raise ValueError(f"Text outside canvas: {content}")
        self.draw.text((actual_x * SCALE, y * SCALE), content, font=font, fill=color, anchor="lt")
        weight = ' font-weight="700"' if bold else ""
        self.svg.append(f'<text x="{x}" y="{y}" font-family="Arial, sans-serif" font-size="{size}" fill="{color}" text-anchor="{anchor}" dominant-baseline="text-before-edge"{weight}>{escape(content)}</text>')

    def rect(self, x, y, w, h, fill="white", stroke=BORDER, radius=9):
        self.draw.rounded_rectangle((x*2, y*2, (x+w)*2, (y+h)*2), radius=radius*2, fill=fill, outline=stroke, width=2)
        self.svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{stroke}"/>')

    def panel(self, x, y, w, h, title, fill=LIGHT):
        self.rect(x, y, w, h, fill)
        self.text(x+18, y+12, title, 20, NAVY, True)

    def box(self, x, y, w, h, title, lines=(), fill="white", stroke=BORDER, size=21):
        self.rect(x, y, w, h, fill, stroke)
        self.text(x+w/2, y+14, title, size, NAVY, True, "middle")
        for index, line in enumerate(lines):
            self.text(x+w/2, y+46+index*25, line, 18, MUTED, anchor="middle")

    def line(self, points, color=BLUE, width=2, dashed=False, arrow=True):
        pts = [(int(x*2), int(y*2)) for x, y in points]
        if dashed:
            for a, b in zip(pts, pts[1:]):
                length = math.dist(a, b)
                for start in range(0, int(length), 22):
                    t1, t2 = start / length, min(start+13, length) / length
                    self.draw.line((a[0]+(b[0]-a[0])*t1, a[1]+(b[1]-a[1])*t1,
                                    a[0]+(b[0]-a[0])*t2, a[1]+(b[1]-a[1])*t2), fill=color, width=width*2)
        else:
            self.draw.line(pts, fill=color, width=width*2)
        dash = ' stroke-dasharray="7 5"' if dashed else ""
        path = " ".join(f"{x},{y}" for x,y in points)
        self.svg.append(f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="{width}"{dash}/>' )
        if arrow:
            x,y = points[-1]
            a,b = points[-2]
            angle = math.atan2(y-b, x-a)
            tri = [(x,y), (x-10*math.cos(angle-.45), y-10*math.sin(angle-.45)),
                   (x-10*math.cos(angle+.45), y-10*math.sin(angle+.45))]
            self.draw.polygon([(int(x*2),int(y*2)) for x,y in tri], fill=color)
            self.svg.append(f'<polygon points="{" ".join(f"{a},{b}" for a,b in tri)}" fill="{color}"/>')

    def label(self, x, y, text, color=MUTED, size=16):
        width = self.draw.textlength(text, font=self.font(size)) / 2
        self.rect(x-width/2-5, y-2, width+10, size+7, "white", "white", 2)
        self.text(x, y, text, size, color, anchor="middle")

    def footer(self, note):
        self.text(50, 1024, note, 17, MUTED)
        self.text(1870, 1051, "EDUCATIONAL KAG  |  ARCHITECTURE BASELINE: 10 NOV 2026", 15, MUTED, anchor="end")

    def save(self):
        OUT.mkdir(exist_ok=True)
        (OUT / f"{self.name}.svg").write_text("\n".join(self.svg+["</svg>"]), encoding="utf-8")
        self.im.save(OUT / f"{self.name}.png", dpi=(200,200))


def logical():
    c = Canvas("logical-architecture", "Educational KAG | Logical architecture", "Six layers • authoritative sources • validated reasoning • evidence-linked explanations")
    c.panel(50,125,1420,120,"1  USER APPLICATIONS")
    for x,title in [(80,"Student portal"),(425,"Faculty portal"),(770,"Advisor portal"),(1115,"Admin dashboard")]:
        c.box(x,170,325,55,title,size=20)
    c.line([(760,245),(760,280)])
    c.label(1060,251,"Authenticated HTTPS / validated SSE")
    c.panel(50,280,1420,125,"2  API AND ORCHESTRATION")
    c.box(80,325,1360,60,"FastAPI • Pydantic • SQLAlchemy adapters • LangGraph / LangChain",size=22)
    c.line([(760,405),(760,430),(235,430)],arrow=False)
    c.line([(760,430),(1270,430)],arrow=False)
    c.label(760,415,"Scoped retrieval / rules / generation calls")
    for x in [235,580,925,1270]:
        c.line([(x,430),(x,490)])
    c.panel(50,455,1420,165,"3  KAG INTELLIGENCE")
    # Draw calls after the panel so they remain visible in the boundary gap.
    for x in [235,580,925,1270]:
        c.line([(x,480),(x,500)])
    c.box(80,500,310,98,"Vector retrieval",["Qdrant • Sentence Transformers","Dense + lexical sparse search"])
    c.box(425,500,310,98,"Knowledge graph",["Neo4j Community","Standards • skills • prerequisites"])
    c.box(770,500,310,98,"Deterministic reasoning",["Python • NetworkX","Approved rules + evidence"])
    c.box(1115,500,325,98,"Amazon Bedrock",["Managed AWS LLM","Explain validated evidence"],"#fff5e7",GOLD)
    c.line([(925,620),(925,685)])
    c.label(1105,641,"Current authorized facts")
    c.panel(50,660,1420,130,"4  ENTERPRISE KNOWLEDGE SOURCES")
    c.box(80,705,570,65,"Approved documents",["Curriculum • resources • policies"],size=21)
    c.box(770,705,670,65,"SQLite source-service",["Authoritative records • grants • outbox • manifest"],size=21)
    c.line([(925,660),(925,705)])
    c.panel(50,845,1420,140,"5  INGESTION AND SYNCHRONIZATION")
    titles=[("Extract",["Validate / normalize"]),("Resolve",["IDs / approved edges"]),("Build",["Graph + embeddings"]),("Publish",["Reconcile / manifest"])]
    for i,(title,lines) in enumerate(titles):
        c.box(80+345*i,893,310,72,title,lines,size=21)
        if i<3:
            c.line([(390+345*i,930),(425+345*i,930)],TEAL,dashed=True)
    c.line([(365,790),(365,820),(235,820),(235,893)],TEAL,dashed=True)
    c.line([(1100,790),(1100,820),(235,820)],TEAL,dashed=True,arrow=False)
    c.label(760,801,"Committed outbox + approved documents → Celery / Redis",TEAL)
    # Dedicated left routing gutter: background writes never cross the read path.
    c.line([(925,965),(925,1000),(25,1000),(25,640),(235,640),(235,598)],TEAL,dashed=True)
    c.line([(235,640),(580,640),(580,598)],TEAL,dashed=True)
    c.label(300,623,"Staged index writes",TEAL,size=15)
    c.panel(1510,125,360,860,"6  CROSS-CUTTING CONTROLS")
    for y,title,lines in [
        (180,"Identity + policy",["Keycloak • OPA","Tenant / student / purpose"]),
        (345,"Security + governance",["IAM • Secrets Manager","TLS • encryption • deletion","FERPA / COPPA review"]),
        (535,"Observability",["OpenTelemetry • Prometheus","Grafana • CloudWatch","Redacted audit + cost"]),
        (725,"Delivery + recovery",["Docker Compose / ECS","OpenTofu • signed CI/CD","Backups • restore drills"]),
    ]:
        c.box(1530,y,320,140,title,lines,size=21)
    c.footer("Solid: synchronous calls    Dashed teal: background ingestion    Vector / graph indexes are derived; Bedrock is managed AWS.")
    c.save()


def aws():
    c = Canvas("aws-deployment", "Educational KAG | AWS pilot deployment", "Stateless services span two zones; the SQLite source and Community graph remain single-instance failure domains.")
    c.box(60,135,270,95,"Users",["Student • faculty • advisor","Browser / HTTPS"])
    c.box(420,135,390,95,"Public edge",["Route53 • ACM • ALB","Optional WAF • TLS routing"])
    c.line([(330,182),(420,182)])
    c.label(375,151,"HTTPS")
    c.panel(60,290,1350,650,"VPC  |  PRIVATE APPLICATION AND DATA SUBNETS")
    c.panel(90,350,620,255,"ECS FARGATE  |  AVAILABILITY ZONES A + B","#eaf3fc")
    c.box(115,400,270,85,"Next.js",["Role-based portals"])
    c.box(415,400,270,85,"FastAPI / LangGraph",["OPA • scoped retrieval"])
    c.line([(385,440),(415,440)])
    c.label(400,489,"HTTPS / delegated identity",size=15)
    c.box(115,525,570,60,"Celery workers + one scheduler",size=20)
    c.line([(615,230),(615,263),(250,263),(250,400)])
    c.label(420,248,"HTTPS target services")
    c.panel(760,350,620,385,"PRIVATE EC2  |  RETAINED ENCRYPTED EBS")
    c.box(790,405,560,85,"Single source host",["FastAPI source-service + local SQLite","No shared database filesystem"])
    c.box(790,535,260,82,"Neo4j Community",["Derived graph"])
    c.box(1090,535,260,82,"Qdrant",["Derived vector index"])
    c.box(790,645,560,65,"Redis broker • durable outbox replay",size=20)
    c.line([(685,440),(790,440)])
    c.label(738,405,"TLS / scope",size=15)
    c.line([(685,470),(740,470),(740,575),(790,575)])
    c.label(740,500,"Index read",size=14)
    c.line([(740,510),(740,520),(1220,520),(1220,535)])
    c.label(1070,502,"Scoped vector read",size=14)
    c.line([(685,585),(720,585),(720,630),(1220,630),(1220,617)],TEAL,dashed=True)
    c.line([(930,630),(930,617)],TEAL,dashed=True)
    c.label(1080,623,"Background writes",TEAL,size=14)
    c.panel(90,795,620,115,"IDENTITY AND INTERNAL AUTHORIZATION")
    c.text(110,841,"Keycloak + separate PostgreSQL identity DB",21)
    c.text(110,873,"Production identity state; OPA policy bundles",18,MUTED)
    c.panel(760,795,620,115,"NETWORK AND WORKLOAD CONTROLS")
    c.text(780,841,"Security groups • IAM roles • TLS / mTLS",21)
    c.text(780,873,"No public index, queue, policy or source ports",18,MUTED)
    c.panel(1460,290,410,650,"MANAGED AWS SERVICES")
    c.box(1480,350,370,115,"Amazon Bedrock",["Approved model / region","Workload IAM • minimized data"],"#fff5e7",GOLD)
    c.box(1480,510,370,110,"S3 + KMS",["Versioned documents / backups","Encrypted and retention-controlled"])
    c.box(1480,665,370,110,"Secrets + operations",["Secrets Manager • CloudWatch","OpenTelemetry / Prometheus"])
    c.box(1480,820,370,90,"Private endpoints",["Validate regional availability"])
    # Reserved upper corridor avoids crossing the stateful-host call.
    c.line([(550,400),(550,320),(1430,320),(1430,405),(1480,405)],GOLD)
    c.label(1080,304,"Bedrock Runtime API / IAM",GOLD)
    c.line([(1380,565),(1480,565)],TEAL,dashed=True)
    c.label(1430,535,"Backups",TEAL,size=15)
    c.footer("OpenTofu + CI/CD: separate environments, digest-pinned images, reviewed plans. Production scale: PostgreSQL + tested index HA.")
    c.save()


def sequence():
    c = Canvas("query-sequence", "Educational KAG | Query execution sequence", "A validated answer is streamed only after grounding checks; identity and evidence authorization are enforced before release.")
    actors=[(160,"Next.js"),(470,"FastAPI / LangGraph"),(780,"Policy + source"),(1090,"Qdrant / Neo4j"),(1400,"Python rules"),(1710,"Amazon Bedrock")]
    for x,title in actors:
        c.box(x-135,135,270,60,title,size=20,fill="#fff5e7" if x==1710 else LIGHT)
        c.line([(x,195),(x,980)],BORDER,1,dashed=True,arrow=False)
    rows=[
        (245,160,470,"1  Question / authenticated HTTPS"),
        (310,470,780,"2  Verify identity + authorized scope"),
        (375,470,780,"3  Classify / pin manifest"),
        (440,470,1090,"4–5  Authorized vector + graph retrieval"),
        (505,470,1400,"6  Current mastery + deterministic rules"),
        (655,470,1710,"8  Minimized evidence / generation"),
        (720,1710,470,"Buffered draft (untrusted until validated)"),
        (850,470,780,"Recheck current grants / tombstones"),
        (915,470,160,"10  Validated SSE answer + citations"),
    ]
    for y,a,b,label in rows:
        c.line([(a,y),(b,y)],GOLD if 1710 in [a,b] else BLUE)
        c.label((a+b)/2,y-27,label,size=18)
    c.rect(390,554,1180,55,LIGHT)
    c.text(980,570,"7  Consolidate • deduplicate • verify provenance and source versions",20,NAVY,True,"middle")
    c.rect(390,760,1180,55,LIGHT)
    c.text(980,776,"9  Validate citation support • policy • unsupported claims; repair or fall back",20,NAVY,True,"middle")
    c.rect(390,949,1180,45,LIGHT)
    c.text(980,961,"11  Redacted metrics / audit; optional user feedback through a separate request",19,NAVY,False,"middle")
    c.footer("Solid arrows: calls / response transfer. Steps 4–5 may run in parallel. Retrieval results feed reasoning before generation.")
    c.save()


def graph():
    c=Canvas("knowledge-graph", "Educational KAG | Knowledge graph model", "Core relationship paths; all nodes and edges include tenant, generation, effective dates and provenance.")
    # Each row reads left-to-right; the footer gives supporting entity types.
    c.box(60,180,300,90,"Institution",["Ownership / jurisdiction"])
    c.box(700,180,300,90,"Course",["Catalog + version"])
    c.box(1330,180,410,90,"Academic Standard",["Imported authority identifier"])
    c.line([(360,225),(700,225)])
    c.label(530,196,"INSTITUTION_OFFERS_COURSE",size=17)
    c.line([(1000,225),(1330,225)])
    c.label(1165,196,"COURSE_ALIGNS_TO_STANDARD",size=16)
    c.box(60,440,300,90,"Student",["Optional pseudonymous node"])
    c.box(700,440,300,90,"Skill",["Approved canonical concept"])
    c.box(1330,440,410,90,"Lesson",["Approved course material"])
    c.line([(360,485),(700,485)])
    c.label(530,455,"STUDENT_DEMONSTRATED_MASTERY",size=15)
    c.line([(1330,485),(1000,485)])
    c.label(1165,500,"LESSON_TEACHES_SKILL",size=17)
    # Completion uses a clear left-hand corridor between the first two rows.
    c.line([(210,440),(210,330),(850,330),(850,270)])
    c.label(510,312,"STUDENT_COMPLETED_COURSE",size=17)
    c.line([(900,440),(900,400),(1070,400),(1070,460),(1000,460)])
    c.label(1110,374,"SKILL_REQUIRES_SKILL",size=16)
    c.box(60,700,300,90,"Assessment",["Rubric / evidence date"])
    c.line([(360,745),(430,745),(430,620),(755,620),(755,530)])
    c.label(530,600,"ASSESSMENT_MEASURES_SKILL",size=16)
    c.box(700,700,300,90,"Competency",["Learning targets"])
    c.box(1330,700,410,90,"Learning Resource",["Approved / licensed / accessible"])
    c.line([(1330,745),(1000,745)])
    c.label(1165,714,"RESOURCE_SUPPORTS_COMPETENCY",size=15)
    c.line([(900,530),(900,700)])
    c.label(1020,659,"CONTRIBUTES_TO",size=16)
    c.box(1330,315,410,80,"Prerequisite",["AND / OR • threshold • policy"])
    c.line([(950,270),(950,298),(1535,298),(1535,315)])
    c.label(1255,280,"COURSE_HAS_PREREQUISITE",size=16)
    c.panel(60,855,1680,125,"SUPPORTING ENTITIES AND MODEL RULES")
    c.text(85,901,"Teacher • State • Grade • Learning Outcome • Academic Policy",23,NAVY,True)
    c.text(85,940,"Prerequisite groups reference skills / courses. Full directed relations and constraints are specified in docs/architecture.md.",19,MUTED)
    c.footer("Student mastery is source-verified at query time. Draft extracted relations are quarantined until steward approval.")
    c.save()


if __name__ == "__main__":
    for render in [logical,aws,sequence,graph]:
        render()
    print("Rendered four SVGs and four 3840 x 2160 PNGs to diagrams/")
