"""Hybrid retrieval: BM25 over published resources fused with a chunk-level vector ranking.

Lexical matching is always required, so vector collisions alone cannot establish relevance. Online
fallback results are published synchronously through the worker pipeline before they are cited, so every
citation in an answer resolves to a published, authorized record.
"""
import math
from collections import Counter

from . import web_search
from .domain.documents import content_resource_id, validate
from .indexes import vector_index
from .source_client import source
from .embeddings import tokens
from .worker.pipeline import publish_documents

# Question scaffolding ("which topics should I study before ...") carries no subject meaning.
# Counting these words would let unrelated documents satisfy the lexical match.
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
            title, text = validate(row["title"], row["text"])
        except ValueError:
            continue
        documents.append({
            "title": title, "text": text, "skill_id": None, "kind": "Web result",
            "source_uri": row["source_uri"], "source_version": row["retrieved_at"][:10],
            "created_at": row["retrieved_at"],
        })
    if not documents:
        return []
    label = keywords.strip().title()[:60] or "Web search"
    published = publish_documents(tenant_id, documents, topic_label=label)
    by_id = {content_resource_id(tenant_id, d["title"], d["text"]): d for d in documents}
    return [{"id": rid, "title": by_id[rid]["title"], "text": by_id[rid]["text"], "skill_id": None,
             "kind": "Web result", "source_uri": by_id[rid]["source_uri"], "source_version": by_id[rid]["source_version"],
             "created_at": by_id[rid]["created_at"], "relevance": 0.0}
            for rid in published["resource_ids"] if rid in by_id][:limit]


def _search_local(tenant_id: str, question: str, limit: int = 5, exclude_web: bool = False):
    snap = source().snapshot(tenant_id, include_web=not exclude_web)
    docs = snap["resources"]
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
    vector = vector_index().rank(tenant_id, snap["generation"], question, docs)
    scores = Counter()
    for ranking in [lexical, vector]:
        for rank, (sid, score) in enumerate(ranking):
            if score > 0:
                scores[sid] += 1 / (60 + rank + 1)
    lookup = {d["id"]: d for d in docs}
    lexical_matches = {sid for sid, score in lexical if score > 0}
    return [{**lookup[sid], "relevance": round(score, 5)} for sid, score in scores.most_common() if sid in lexical_matches][:limit]
