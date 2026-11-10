"""Offline reproducible hybrid search. Hash vectors are explicitly not a trained model."""
from collections import Counter
import hashlib
import hashlib
import math
import re

from . import web_search
from .db import db


# Question scaffolding ("which topics should I study before ...") carries no subject meaning.
# Counting these words lets unrelated documents (e.g. Algebra text containing "before" or
# "study") satisfy the lexical match and suppress the online fallback.
STOPWORDS = frozenset("""a an and are as at be before by can could do does for from get help how i in is it know learn
learning me my need of on or prepare ready should study studying that the this to topics topic very want what when which
who why will with you your""".split())


def tokens(text):
    return re.findall(r"[a-z0-9]+", text.lower())


def embedding(text):
    result = [0.0] * 256
    words = tokens(text)
    for token in words:
        digest = hashlib.sha256(token.encode()).digest()
        result[int.from_bytes(digest[:2], "big") % 256] += 1 if digest[2] % 2 else -1
    norm = math.sqrt(sum(x*x for x in result)) or 1
    return [x/norm for x in result]


def retrieve(tenant_id: str, question: str, limit: int = 5):
    # Curriculum first. Cached web pages are excluded here: they were fetched for other
    # questions and can match incidental words (e.g. "history" in a Geography article).
    results = _search_local(tenant_id, question, limit, exclude_web=True)
    if results or not web_search.enabled():
        return results
    # Online fallback: cache web results locally, then search the local store again.
    # Send only content words to the search API; full question sentences match poorly.
    keywords = " ".join(t for t in tokens(question) if t not in STOPWORDS)
    fetched = web_search.search(keywords, limit)
    if not fetched:
        return []
    with db() as connection:
        for row in fetched:
            connection.execute(
                "INSERT OR REPLACE INTO resources VALUES (?,?,?,?,?,?,?,?,?,?)",
                (row["id"], tenant_id, row["title"], row["text"], None, "Web result", row["source_uri"], row["retrieved_at"][:10], "curriculum", row["retrieved_at"]),
            )
        label = keywords.strip().title()[:60]
        topic_id = "topic-" + hashlib.sha256(f"{tenant_id}|{label.lower()}".encode()).hexdigest()[:16]
        connection.execute("INSERT OR IGNORE INTO topics VALUES (?,?,?,?)", (topic_id, tenant_id, label, fetched[0]["retrieved_at"]))
        connection.executemany("INSERT OR IGNORE INTO topic_resources VALUES (?,?)", [(topic_id, row["id"]) for row in fetched])
        connection.execute("UPDATE index_manifests SET generation=generation+1, updated_at=? WHERE tenant_id=?", (fetched[0]["retrieved_at"], tenant_id))
    # Answer from the results fetched for this exact question (they are cached above).
    return [{"id": r["id"], "title": r["title"], "text": r["text"], "skill_id": None, "kind": "Web result",
             "source_uri": r["source_uri"], "source_version": r["retrieved_at"][:10], "auth_scope": "curriculum",
             "created_at": r["retrieved_at"], "relevance": 0.0} for r in fetched]


def _search_local(tenant_id: str, question: str, limit: int = 5, exclude_web: bool = False):
    with db() as connection:
        docs = [dict(r) for r in connection.execute("SELECT * FROM resources WHERE tenant_id=? AND auth_scope='curriculum' AND (? = 0 OR kind != 'Web result') ORDER BY id", (tenant_id, int(exclude_web)))]
    if not docs:
        return []
    # Stopwords must not count as evidence: "for" or "should" would otherwise match
    # almost any document and suppress the online fallback.
    query = {t for t in tokens(question) if t not in STOPWORDS}
    counts = [Counter(tokens(d["title"] + " " + d["text"])) for d in docs]
    avg_length = sum(sum(c.values()) for c in counts) / len(counts)
    lexical = []
    vector = []
    query_vector = embedding(question)
    for doc,count in zip(docs,counts):
        bm25 = 0.0
        for token in query:
            frequency = sum(token in other for other in counts)
            inverse = math.log(1 + (len(docs)-frequency+.5)/(frequency+.5))
            tf = count.get(token,0)
            bm25 += inverse * tf * 2.2 / (tf + 1.2 * (.25 + .75 * sum(count.values()) / avg_length))
        lexical.append((doc["id"],bm25))
        vector.append((doc["id"],sum(a*b for a,b in zip(query_vector,embedding(doc["title"] + " " + doc["text"])))))
    lexical.sort(key=lambda x:x[1],reverse=True)
    vector.sort(key=lambda x:x[1],reverse=True)
    scores = Counter()
    for ranking in [lexical,vector]:
        for rank,(sid,score) in enumerate(ranking):
            if score > 0:
                scores[sid] += 1/(60+rank+1)
    lookup = {d["id"]:d for d in docs}
    # Require a lexical match; hash collisions alone cannot establish relevance.
    lexical_matches = {sid for sid,score in lexical if score > 0}
    return [{**lookup[sid],"relevance":round(score,5)} for sid,score in scores.most_common() if sid in lexical_matches][:limit]
