"""Offline reproducible hybrid search. Hash vectors are explicitly not a trained model."""
from collections import Counter
import hashlib
import math
import re

from .db import db


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
    with db() as connection:
        docs = [dict(r) for r in connection.execute("SELECT * FROM resources WHERE tenant_id=? AND auth_scope='curriculum' ORDER BY id", (tenant_id,))]
    if not docs:
        return []
    query = set(tokens(question))
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
