#!/usr/bin/env python3
"""
Hybrid retrieval engine for the COE capability corpus.

Combines pgvector cosine similarity, Postgres full-text search (BM25-style),
and Reciprocal Rank Fusion to find the most relevant capability records for
a given opportunity description. Optionally reranks with bge-reranker-base.

Usage:
    from retrieval import retrieve
    results = retrieve("data governance and analytics for VA")

    # Or run standalone to test:
    python retrieval.py "data governance and analytics for VA"
"""

import sys
import time
import requests
from dataclasses import dataclass, field

from sqlalchemy import text

from coe.database import get_session
from coe.models import TechnicalCompetency, PastPerformance, ServiceArea

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
OLLAMA_URL = "http://localhost:11434/api/embeddings"
EMBED_MODEL = "nomic-embed-text"
RRF_K = 60                  # RRF constant — higher = less top-rank dominance
VECTOR_TOP_K = 30           # How many results to pull from vector search
BM25_TOP_K = 30             # How many results to pull from text search
RERANK_TOP_K = 20           # How many RRF results to feed to the reranker
FINAL_TOP_K = 10            # How many results to return after reranking

# Lazy-loaded reranker (heavy import, only load if needed)
_reranker = None


def _get_reranker():
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder
        print("  Loading reranker model (first time only)...")
        _reranker = CrossEncoder("BAAI/bge-reranker-base")
    return _reranker


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class RetrievedRecord:
    """One retrieved capability record with its scores."""
    record_type: str          # "competency", "past_performance", "service_area"
    record_id: int
    name: str
    description: str | None = None
    vector_rank: int | None = None
    vector_score: float | None = None
    bm25_rank: int | None = None
    bm25_score: float | None = None
    rrf_score: float = 0.0
    rerank_score: float | None = None
    final_rank: int | None = None
    # Extra context for scoring prompt
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Stage 1a: Vector search via pgvector
# ---------------------------------------------------------------------------
def _get_query_embedding(query_text: str) -> list[float]:
    """Embed the opportunity text using nomic-embed-text with search_query prefix."""
    resp = requests.post(OLLAMA_URL, json={
        "model": EMBED_MODEL,
        "prompt": f"search_query: {query_text}",
    })
    resp.raise_for_status()
    return resp.json()["embedding"]


def _vector_search(session, query_embedding: list[float], top_k: int) -> list[RetrievedRecord]:
    """Find closest competencies and past performances by cosine similarity."""
    results = []

    comp_sql = text("""
        SELECT id, name, description,
                1 - (embedding <=> cast(:embedding AS vector)) AS similarity
        FROM technical_competencies
        WHERE embedding IS NOT NULL
        ORDER BY embedding <=> cast(:embedding AS vector)
        LIMIT :limit
    """)
    rows = session.execute(comp_sql, {
        "embedding": str(query_embedding),
        "limit": top_k,
    }).fetchall()

    for rank, row in enumerate(rows, 1):
        results.append(RetrievedRecord(
            record_type="competency",
            record_id=row.id,
            name=row.name,
            description=row.description,
            vector_rank=rank,
            vector_score=float(row.similarity),
        ))

    # Search past performances
    pp_sql = text("""
        SELECT id, project_name, agency, description,
               1 - (embedding <=> cast(:embedding AS vector)) AS similarity
        FROM past_performances
        WHERE embedding IS NOT NULL
        ORDER BY embedding <=> cast(:embedding AS vector)
        LIMIT :limit
    """)
    rows = session.execute(pp_sql, {
        "embedding": str(query_embedding),
        "limit": top_k,
    }).fetchall()

    for rank, row in enumerate(rows, 1):
        results.append(RetrievedRecord(
            record_type="past_performance",
            record_id=row.id,
            name=row.project_name,
            description=row.description,
            vector_rank=rank,
            vector_score=float(row.similarity),
            metadata={"agency": row.agency},
        ))

    return results


# ---------------------------------------------------------------------------
# Stage 1b: BM25-style full-text search via Postgres
# ---------------------------------------------------------------------------
def _bm25_search(session, query_text: str, top_k: int) -> list[RetrievedRecord]:
    """Keyword search using Postgres full-text search with ts_rank."""
    results = []

    # Build a tsquery from the raw text — plainto_tsquery handles spaces/punctuation
    comp_sql = text("""
        SELECT id, name, description,
               ts_rank(to_tsvector('english', coalesce(name, '') || ' ' || coalesce(description, '')),
                       plainto_tsquery('english', :query)) AS rank
        FROM technical_competencies
        WHERE to_tsvector('english', coalesce(name, '') || ' ' || coalesce(description, ''))
              @@ plainto_tsquery('english', :query)
        ORDER BY rank DESC
        LIMIT :limit
    """)
    rows = session.execute(comp_sql, {"query": query_text, "limit": top_k}).fetchall()

    for rank, row in enumerate(rows, 1):
        results.append(RetrievedRecord(
            record_type="competency",
            record_id=row.id,
            name=row.name,
            description=row.description,
            bm25_rank=rank,
            bm25_score=float(row.rank),
        ))

    # Past performances
    pp_sql = text("""
        SELECT id, project_name, agency, description,
               ts_rank(to_tsvector('english', coalesce(project_name, '') || ' ' ||
                       coalesce(agency, '') || ' ' || coalesce(description, '')),
                       plainto_tsquery('english', :query)) AS rank
        FROM past_performances
        WHERE to_tsvector('english', coalesce(project_name, '') || ' ' ||
              coalesce(agency, '') || ' ' || coalesce(description, ''))
              @@ plainto_tsquery('english', :query)
        ORDER BY rank DESC
        LIMIT :limit
    """)
    rows = session.execute(pp_sql, {"query": query_text, "limit": top_k}).fetchall()

    for rank, row in enumerate(rows, 1):
        results.append(RetrievedRecord(
            record_type="past_performance",
            record_id=row.id,
            name=row.project_name,
            description=row.description,
            bm25_rank=rank,
            bm25_score=float(row.rank),
            metadata={"agency": row.agency},
        ))

    return results


# ---------------------------------------------------------------------------
# Stage 1c: Service area lookup (keyword match, no embeddings)
# ---------------------------------------------------------------------------
def _service_area_search(session, query_text: str) -> list[RetrievedRecord]:
    """Simple ILIKE search for service areas — they don't have embeddings."""
    results = []
    # Split query into key terms and search for any match
    terms = [t.strip() for t in query_text.lower().split() if len(t.strip()) > 3]

    if not terms:
        return results

    # Build OR conditions for ILIKE
    conditions = " OR ".join([f"lower(name) LIKE :term{i}" for i in range(len(terms))])
    params = {f"term{i}": f"%{t}%" for i, t in enumerate(terms)}
    params["limit"] = 10

    sql = text(f"""
        SELECT id, name, description
        FROM service_areas
        WHERE {conditions}
        ORDER BY name
        LIMIT :limit
    """)
    rows = session.execute(sql, params).fetchall()

    for row in rows:
        results.append(RetrievedRecord(
            record_type="service_area",
            record_id=row.id,
            name=row.name,
            description=row.description,
        ))

    return results


# ---------------------------------------------------------------------------
# Stage 2: Reciprocal Rank Fusion
# ---------------------------------------------------------------------------
def _reciprocal_rank_fusion(
    vector_results: list[RetrievedRecord],
    bm25_results: list[RetrievedRecord],
    k: int = RRF_K,
) -> list[RetrievedRecord]:
    """
    Merge vector and BM25 results using RRF.
    score = sum(1 / (k + rank)) across both lists.
    """
    # Build a combined map keyed by (record_type, record_id)
    merged: dict[tuple, RetrievedRecord] = {}

    for rec in vector_results:
        key = (rec.record_type, rec.record_id)
        if key not in merged:
            merged[key] = rec
        else:
            merged[key].vector_rank = rec.vector_rank
            merged[key].vector_score = rec.vector_score

    for rec in bm25_results:
        key = (rec.record_type, rec.record_id)
        if key not in merged:
            merged[key] = rec
        else:
            merged[key].bm25_rank = rec.bm25_rank
            merged[key].bm25_score = rec.bm25_score

    # Calculate RRF scores
    for rec in merged.values():
        score = 0.0
        if rec.vector_rank is not None:
            score += 1.0 / (k + rec.vector_rank)
        if rec.bm25_rank is not None:
            score += 1.0 / (k + rec.bm25_rank)
        rec.rrf_score = score

    # Sort by RRF score descending
    ranked = sorted(merged.values(), key=lambda r: r.rrf_score, reverse=True)
    return ranked


# ---------------------------------------------------------------------------
# Stage 3: Cross-encoder reranking
# ---------------------------------------------------------------------------
def _rerank(query_text: str, candidates: list[RetrievedRecord], top_k: int) -> list[RetrievedRecord]:
    """Rerank candidates using bge-reranker-base cross-encoder."""
    if not candidates:
        return candidates

    reranker = _get_reranker()

    # Build (query, document) pairs
    pairs = []
    for rec in candidates:
        doc = rec.name
        if rec.description:
            doc += f" — {rec.description}"
        pairs.append((query_text, doc))

    scores = reranker.predict(pairs)

    for rec, score in zip(candidates, scores):
        rec.rerank_score = float(score)

    # Sort by rerank score descending
    reranked = sorted(candidates, key=lambda r: r.rerank_score, reverse=True)

    # Assign final ranks
    for i, rec in enumerate(reranked[:top_k], 1):
        rec.final_rank = i

    return reranked[:top_k]


# ---------------------------------------------------------------------------
# Main retrieve function
# ---------------------------------------------------------------------------
def retrieve(
    query_text: str,
    top_k: int = FINAL_TOP_K,
    use_reranker: bool = True,
    verbose: bool = False,
) -> list[RetrievedRecord]:
    """
    Retrieve the most relevant capability records for an opportunity.

    Args:
        query_text: The opportunity description to match against.
        top_k: Number of final results to return.
        use_reranker: Whether to run the cross-encoder reranker (slower but better).
        verbose: Print intermediate results for debugging.

    Returns:
        List of RetrievedRecord objects, ranked by relevance.
    """
    session = get_session()

    try:
        # Stage 1a: Vector search
        t0 = time.time()
        query_embedding = _get_query_embedding(query_text)
        vector_results = _vector_search(session, query_embedding, VECTOR_TOP_K)
        t_vector = time.time() - t0

        # Stage 1b: BM25 search
        t0 = time.time()
        bm25_results = _bm25_search(session, query_text, BM25_TOP_K)
        t_bm25 = time.time() - t0

        # Stage 1c: Service areas
        sa_results = _service_area_search(session, query_text)

        if verbose:
            print(f"  Vector: {len(vector_results)} results ({t_vector:.2f}s)")
            print(f"  BM25:   {len(bm25_results)} results ({t_bm25:.2f}s)")
            print(f"  Service areas: {len(sa_results)} matched")

        # Stage 2: RRF fusion
        fused = _reciprocal_rank_fusion(vector_results, bm25_results)

        if verbose:
            print(f"  RRF:    {len(fused)} merged results")
            for r in fused[:5]:
                print(f"          {r.name} (rrf={r.rrf_score:.4f}, "
                      f"vec={r.vector_rank}, bm25={r.bm25_rank})")

        # Stage 3: Rerank top candidates
        candidates = fused[:RERANK_TOP_K]
        if use_reranker and candidates:
            t0 = time.time()
            final = _rerank(query_text, candidates, top_k)
            t_rerank = time.time() - t0
            if verbose:
                print(f"  Rerank: {len(final)} results ({t_rerank:.2f}s)")
        else:
            final = candidates[:top_k]
            for i, rec in enumerate(final, 1):
                rec.final_rank = i

        # Append service areas (not ranked, just context)
        final.extend(sa_results)

        return final

    finally:
        session.close()


# ---------------------------------------------------------------------------
# CLI test mode
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python retrieval.py \"<opportunity description>\"")
        sys.exit(1)

    query = " ".join(sys.argv[1:])
    print(f"Query: {query}\n")

    results = retrieve(query, verbose=True)

    print(f"\n{'='*70}")
    print(f"Top results:")
    print(f"{'='*70}")
    for r in results:
        label = f"[{r.record_type}]".ljust(20)
        rerank = f"rerank={r.rerank_score:.3f}" if r.rerank_score is not None else ""
        rrf = f"rrf={r.rrf_score:.4f}" if r.rrf_score else ""
        rank = f"#{r.final_rank}" if r.final_rank else ""
        print(f"  {rank:>3} {label} {r.name}")
        if rerank or rrf:
            print(f"       {rrf}  {rerank}")