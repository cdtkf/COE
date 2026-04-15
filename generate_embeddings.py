#!/usr/bin/env python3
"""
Generate embeddings for technical competencies and past performances.

Uses Ollama's nomic-embed-text model to produce 768-dim vectors, then stores
them in the embedding column for pgvector similarity search.

Usage:
    python generate_embeddings.py

Requires:
    - Ollama running locally (ollama serve)
    - nomic-embed-text model pulled (ollama pull nomic-embed-text)
    - Docker Postgres up (docker compose up -d)
"""

import requests
import time

from coe.database import get_session
from coe.models import TechnicalCompetency, PastPerformance

OLLAMA_URL = "http://localhost:11434/api/embeddings"
MODEL = "nomic-embed-text"


def get_embedding(text: str) -> list[float]:
    """Call Ollama to get a 768-dim embedding for the given text."""
    resp = requests.post(OLLAMA_URL, json={
        "model": MODEL,
        "prompt": text,
    })
    resp.raise_for_status()
    return resp.json()["embedding"]


def embed_competencies(session):
    """Embed all technical competencies that don't have embeddings yet."""
    comps = session.query(TechnicalCompetency).filter(
        TechnicalCompetency.embedding.is_(None)
    ).all()

    print(f"  Competencies to embed: {len(comps)}")

    for i, comp in enumerate(comps, 1):
        # Build text: name + description if available
        text = f"search_document: {comp.name}"
        if comp.description:
            text += f" — {comp.description}"

        comp.embedding = get_embedding(text)
        print(f"    [{i}/{len(comps)}] {comp.name}")

    return len(comps)


def embed_past_performances(session):
    """Embed all past performances that don't have embeddings yet."""
    pps = session.query(PastPerformance).filter(
        PastPerformance.embedding.is_(None)
    ).all()

    print(f"  Past performances to embed: {len(pps)}")

    for i, pp in enumerate(pps, 1):
        text = f"search_document: {pp.project_name}"
        if pp.agency:
            text += f" | {pp.agency}"
        if pp.description:
            text += f" — {pp.description}"

        pp.embedding = get_embedding(text)
        print(f"    [{i}/{len(pps)}] {pp.project_name}")

    return len(pps)


def main():
    print("Generating embeddings with nomic-embed-text...\n")
    start = time.time()

    session = get_session()

    try:
        comp_count = embed_competencies(session)
        pp_count = embed_past_performances(session)

        session.commit()

        elapsed = time.time() - start
        print(f"\nDone! Embedded {comp_count} competencies + {pp_count} past performances")
        print(f"Time: {elapsed:.1f}s")

    except requests.ConnectionError:
        print("\nError: Can't connect to Ollama. Is it running? (ollama serve)")
        raise
    except Exception as e:
        session.rollback()
        print(f"\nError: {e}")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()