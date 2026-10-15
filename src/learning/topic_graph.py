"""Topic graph: connect topics by textual similarity and suggest neighbours of proven winners.

Nodes are video topics (our titles + narration fingerprints, plus research topic candidates);
edges link pairs whose similarity (``src.common.textsim``) is between ``LOW`` and ``HIGH``:
related enough to share an audience, different enough not to be duplicates (the distinctness
guard would block > ``HIGH``). Suggestions = unexplored neighbours of the best-performing videos.

``python -m src.learning.topic_graph suggest --candidates data/research/comments-....json``
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

from src.common.textsim import TfIdf, char_ngrams, cosine, word_ngrams

LOW, HIGH = 0.12, 0.55


@dataclass
class TopicGraph:
    """Undirected weighted graph over topic strings."""

    nodes: list[str]
    edges: dict[int, list[tuple[int, float]]] = field(default_factory=dict)

    @classmethod
    def build(cls, topics: list[str]) -> TopicGraph:
        """Build edges for all pairs within the related-but-distinct band."""
        # same blend as the distinctness guard: words for meaning, char 4-grams for word forms
        wt = TfIdf(word_ngrams(t) for t in topics)
        ct = TfIdf(char_ngrams(t) for t in topics)
        wv = [wt.vector(word_ngrams(t)) for t in topics]
        cv = [ct.vector(char_ngrams(t)) for t in topics]
        g = cls(list(topics))
        for i in range(len(topics)):
            for j in range(i + 1, len(topics)):
                s = 0.6 * cosine(wv[i], wv[j]) + 0.4 * cosine(cv[i], cv[j])
                if LOW <= s <= HIGH:
                    g.edges.setdefault(i, []).append((j, s))
                    g.edges.setdefault(j, []).append((i, s))
        return g

    def suggest(self, winners: list[int], explored: set[int], k: int = 5) -> list[tuple[str, float]]:
        """Unexplored neighbours of winners, ranked by summed edge weight."""
        score: dict[int, float] = {}
        for w in winners:
            for n, s in self.edges.get(w, []):
                if n not in explored and n not in winners:
                    score[n] = score.get(n, 0.0) + s
        return [(self.nodes[n], round(v, 3)) for n, v in sorted(score.items(), key=lambda kv: -kv[1])[:k]]


def main(argv: list[str] | None = None) -> int:
    """CLI: suggest topics adjacent to given winners from a JSON list of topic strings."""
    p = argparse.ArgumentParser(description="Topic graph suggestions")
    p.add_argument("--topics", type=Path, required=True, help="JSON list of topic strings")
    p.add_argument("--winners", default="", help="comma-separated indices of winning topics")
    a = p.parse_args(argv)
    topics = json.loads(a.topics.read_text(encoding="utf-8"))
    g = TopicGraph.build([str(t) for t in topics])
    winners = [int(x) for x in a.winners.split(",") if x.strip().isdigit()]
    print(json.dumps(g.suggest(winners, set()), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
