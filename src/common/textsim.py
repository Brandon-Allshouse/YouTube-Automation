"""Our own text similarity primitives: tokenisation, TF-IDF, character n-grams, cosine.

Used by the comment miner (clustering questions) and the distinctness guard (near-duplicate
detection). Sparse vectors are plain dicts; corpora here are small (hundreds of documents), so
clarity beats a dependency (ADR-024).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence

STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "aren't",
        "as",
        "at",
        "be",
        "been",
        "but",
        "by",
        "can",
        "can't",
        "could",
        "did",
        "do",
        "does",
        "don't",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "i",
        "i'm",
        "if",
        "in",
        "into",
        "is",
        "isn't",
        "it",
        "it's",
        "its",
        "just",
        "like",
        "me",
        "more",
        "most",
        "my",
        "no",
        "not",
        "of",
        "on",
        "or",
        "our",
        "out",
        "so",
        "some",
        "than",
        "that",
        "that's",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "to",
        "too",
        "up",
        "us",
        "very",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "won't",
        "would",
        "you",
        "your",
        "yours",
    ]
)

Vec = dict[str, float]


def tokens(text: str, *, keep_stopwords: bool = False) -> list[str]:
    """Lower-case word tokens (letters/digits/apostrophes), stopwords removed by default."""
    words = re.findall(r"[a-z0-9]+(?:'[a-z]+)?", text.lower())
    return words if keep_stopwords else [w for w in words if w not in STOPWORDS and len(w) > 1]


def word_ngrams(text: str, n: int = 2) -> list[str]:
    """Word unigrams plus n-grams (stopwords kept inside n-grams to preserve phrasing)."""
    ws = tokens(text, keep_stopwords=True)
    grams = [w for w in ws if w not in STOPWORDS]
    for k in range(2, n + 1):
        grams += [" ".join(ws[i : i + k]) for i in range(len(ws) - k + 1)]
    return grams


def char_ngrams(text: str, n: int = 4) -> list[str]:
    """Character n-grams over normalised text (robust to small rewordings and typos)."""
    s = " " + re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", text.lower())).strip() + " "
    return [s[i : i + n] for i in range(max(len(s) - n + 1, 0))]


class TfIdf:
    """Fit IDF on a corpus of token lists; transform documents to L2-normalised sparse vectors."""

    def __init__(self, docs: Iterable[Sequence[str]]) -> None:
        df: Counter[str] = Counter()
        n = 0
        for d in docs:
            n += 1
            df.update(set(d))
        self.n_docs = n
        self.idf = {t: math.log((1 + n) / (1 + c)) + 1.0 for t, c in df.items()}

    def vector(self, doc: Sequence[str]) -> Vec:
        """TF-IDF vector (sublinear tf) normalised to unit length."""
        tf = Counter(doc)
        default_idf = math.log(1 + self.n_docs) + 1.0
        v = {t: (1 + math.log(c)) * self.idf.get(t, default_idf) for t, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / norm for t, x in v.items()}


def cosine(a: Vec, b: Vec) -> float:
    """Cosine similarity of two L2-normalised sparse vectors."""
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(t, 0.0) for t, x in a.items())


def similarity(a: str, b: str, corpus: Sequence[str] = ()) -> float:
    """Blend of word-level TF-IDF cosine (incl. bigrams) and character 4-gram cosine, in [0, 1]."""
    docs = [*corpus, a, b]
    wt = TfIdf(word_ngrams(d) for d in docs)
    ct = TfIdf(char_ngrams(d) for d in docs)
    w = cosine(wt.vector(word_ngrams(a)), wt.vector(word_ngrams(b)))
    c = cosine(ct.vector(char_ngrams(a)), ct.vector(char_ngrams(b)))
    return max(0.0, min(1.0, 0.6 * w + 0.4 * c))


def cluster(texts: Sequence[str], threshold: float = 0.35) -> list[list[int]]:
    """Greedy single-pass clustering by cosine to cluster centroids (stable, deterministic)."""
    tf = TfIdf(word_ngrams(t) for t in texts)
    vecs = [tf.vector(word_ngrams(t)) for t in texts]
    clusters: list[list[int]] = []
    centroids: list[Vec] = []
    for i, v in enumerate(vecs):
        if not v:
            continue
        best, best_s = -1, threshold
        for k, c in enumerate(centroids):
            s = cosine(v, c)
            if s >= best_s:
                best, best_s = k, s
        if best < 0:
            clusters.append([i])
            centroids.append(dict(v))
        else:
            clusters[best].append(i)
            members = clusters[best]
            acc: Counter[str] = Counter()
            for m in members:
                acc.update(vecs[m])
            norm = math.sqrt(sum(x * x for x in acc.values())) or 1.0
            centroids[best] = {t: x / norm for t, x in acc.items()}
    return clusters
