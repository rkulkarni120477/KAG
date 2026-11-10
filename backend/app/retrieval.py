"""Hybrid retrieval: BM25 over published SQLite rows fused with a vector ranking.

The vector ranking comes from the configured index adapter (SQLite hash vectors offline, or
Qdrant). Lexical matching is always required, so hash-vector collisions alone cannot establish
relevance. Web fallback results are published through the same ingestion path as curriculum.
"""
import math
from collections import Counter

from . import web_search
from .db import db
from .indexes import vector_index
from .ingestion import publish, validate
from .text import tokens

# Question scaffolding ("which topics should I study before ...") carries no subject meaning.
# Counting these words lets unrelated documents satisfy the lexical match and suppress the
# online fallback.
STOPWORDS = frozenset("""a an and are as at be before by can could do does for from get help how i in is it know learn
learning me my need of on or prepare ready should study studying that the this to topics topic very want what when which
who why will with you your""".split())


def retrieve(tenant_id: str, question: str, limit: int = 5):
    # Curriculum first. Cached web pages are excluded: they were fetched for other questions and
    # can match incidental words (e.g. "history" in a Geography article).
    results = _search_local(tenant_id, question, limit, exclude_web=True)
    if results or not web_search.enabled():
        return results
    keywords = " ".join(t for t in tokens(question) if t not in STOPWORDS)
    fetched = web_search.search(keywords, limit)
    documents = []
    for row in fetched:
        try:
            validate(row["title"], row["text"])
        except ValueError:
            continue
        documents.append({
            "title": row["title"], "text": row["text"], "skill_id": None, "kind": "Web result",
            "source_uri": row["source_uri"], "source_version": row["retrieved_at"][:10],
            "created_at": row["retrieved_at"],
        })
    if not documents:
        return []
    label = keywords.strip().title()[:60] or "Web search"
    published = publish(tenant_id, documents, topic_label=label)
    # Answer from the rows just published for this question; they are now cached locally.
    with db() as connection:
        rows = [dict(r) for r in connection.execute(
            f"SELECT * FROM resources WHERE tenant_id=? AND id IN ({','.join('?' * len(published['resource_ids']))})",
            (tenant_id, *published["resource_ids"]))]
    return [{**r, "skill_id": None, "relevance": 0.0} for r in rows][:limit]


def _search_local(tenant_id: str, question: str, limit: int = 5, exclude_web: bool = False):
    with db() as connection:
        generation_row = connection.execute("SELECT generation FROM index_manifests WHERE tenant_id=?", (tenant_id,)).fetchone()
        generation = generation_row["generation"] if generation_row else 0
        docs = [dict(r) for r in connection.execute(
            "SELECT * FROM resources WHERE tenant_id=? AND auth_scope='curriculum' AND (? = 0 OR kind != 'Web result') ORDER BY id",
            (tenant_id, int(exclude_web)))]
    if not docs:
        return []
    query = {t for t in tokens(question) if t not in STOPWORDS}
    counts = [Counter(tokens(d["title"] + " " + d["text"])) for d in docs]
    avg_length = sum(sum(c.values()) for c in counts) / len(counts)
    lexical = []
    for doc, count in zip(docs, counts):
        bm25 = 0.0
        for token in query:
            frequency = sum(token in other for other in counts)
            inverse = math.log(1 + (len(docs) - frequency + .5) / (frequency + .5))
            tf = count.get(token, 0)
            bm25 += inverse * tf * 2.2 / (tf + 1.2 * (.25 + .75 * sum(count.values()) / avg_length))
        lexical.append((doc["id"], bm25))
    lexical.sort(key=lambda x: x[1], reverse=True)
    vector = vector_index().rank(tenant_id, generation, question, docs)
    scores = Counter()
    for ranking in [lexical, vector]:
        for rank, (sid, score) in enumerate(ranking):
            if score > 0:
                scores[sid] += 1 / (60 + rank + 1)
    lookup = {d["id"]: d for d in docs}
    lexical_matches = {sid for sid, score in lexical if score > 0}
    return [{**lookup[sid], "relevance": round(score, 5)} for sid, score in scores.most_common() if sid in lexical_matches][:limit]
