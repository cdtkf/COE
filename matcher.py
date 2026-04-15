#!/usr/bin/env python3
"""
matcher.py — Daily opportunity scoring engine.

Reads unscored opportunities from SQLite, scores each against ReefPoint's
capability profile using a local Ollama LLM, and writes scores back to the DB.

Usage:
    python matcher.py                    # Score all unscored opportunities
    python matcher.py --limit 10         # Score up to 10 opportunities
    python matcher.py --rescore          # Re-score all opportunities
    python matcher.py --model gemma3     # Use a specific Ollama model
    python matcher.py --dry-run          # Show what would be scored, don't call LLM
"""

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
import yaml

from db import Database
from prompts import SCORING_SYSTEM, SCORING_USER, SCORING_USER_V2
from retrieval import retrieve, RetrievedRecord

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.yaml"

OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "llama3.1"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def load_capability_profile(config: dict) -> dict:
    """Load the composite capability profile from JSON."""
    matching_cfg = config.get("matching", {})
    profiles_path = matching_cfg.get("profiles_path", "profiles/capability_profiles.json")
    if not Path(profiles_path).is_absolute():
        profiles_path = SCRIPT_DIR / profiles_path

    if not Path(profiles_path).exists():
        log.error(f"Capability profiles not found at {profiles_path}")
        log.error("Run extract_profiles.py first to generate profiles.")
        sys.exit(1)

    with open(profiles_path) as f:
        data = json.load(f)

    composite = data.get("composite_profile", {})
    log.info(f"Loaded capability profile: {composite.get('total_proposals_analyzed', 0)} proposals, "
             f"{len(composite.get('service_areas', {}))} service areas, "
             f"{len(composite.get('domains', {}))} domains")
    return composite


def build_profile_summary(profile: dict) -> str:
    """Build a concise text summary of the capability profile for the scoring prompt.

    We keep it compact to stay within reasonable token limits for local models.
    """
    parts = [f"Company: {profile.get('company', 'ReefPoint Group')}"]
    parts.append(f"Set-Aside: {', '.join(profile.get('set_aside_qualifications', []))}")

    # Top service areas (by frequency)
    sa = profile.get("service_areas", {})
    top_sa = list(sa.keys())[:15]
    if top_sa:
        parts.append(f"Service Areas: {', '.join(top_sa)}")

    # Top domains
    domains = profile.get("domains", {})
    top_domains = list(domains.keys())[:10]
    if top_domains:
        parts.append(f"Domains: {', '.join(top_domains)}")

    # NAICS codes
    naics = profile.get("naics_codes", {})
    top_naics = list(naics.keys())[:10]
    if top_naics:
        parts.append(f"NAICS Codes: {', '.join(top_naics)}")

    # Top technical competencies
    tc = profile.get("technical_competencies", {})
    top_tc = list(tc.keys())[:20]
    if top_tc:
        parts.append(f"Technical Competencies: {', '.join(top_tc)}")

    # Past performance (brief)
    pp = profile.get("past_performance", [])
    if pp:
        pp_strs = []
        for p in pp[:8]:
            pp_strs.append(f"{p.get('agency', '?')}: {p.get('contract_name', '?')}")
        parts.append(f"Past Performance: {'; '.join(pp_strs)}")

    # Contract vehicles
    cv = profile.get("contract_vehicles", {})
    if cv:
        parts.append(f"Contract Vehicles: {', '.join(list(cv.keys())[:5])}")

    return "\n".join(parts)


def extract_description_from_raw(raw_json_str: str) -> str:
    """Extract a useful description from the raw API JSON."""
    try:
        raw = json.loads(raw_json_str) if isinstance(raw_json_str, str) else raw_json_str
    except (json.JSONDecodeError, TypeError):
        return ""

    # Try several fields where description might live
    desc_parts = []

    # Direct description field
    desc = raw.get("description", "")
    if desc:
        desc_parts.append(desc)

    # Try award info description
    award_data = raw.get("award") or {}
    for award in award_data.get("lineItems", []):
        if award.get("description"):
            desc_parts.append(award["description"])

    # Some opportunities have description in additionalInfo
    additional = raw.get("additionalInfoText", "") or raw.get("additionalReporting", "")
    if additional:
        desc_parts.append(str(additional))

    full_desc = "\n".join(desc_parts)

    # Truncate to keep prompt sizes manageable
    if len(full_desc) > 3000:
        full_desc = full_desc[:3000] + "..."

    return full_desc


def score_opportunity_via_ollama(opp: dict, model: str) -> dict | None:
    """Score a single opportunity using retrieval-augmented context."""

    # Build opportunity description for retrieval + prompt
    raw_json = opp.get("raw_json", "{}")
    description = extract_description_from_raw(raw_json)
    title = opp.get("title", "N/A")

    # Build a query combining title + description for retrieval
    retrieval_query = f"{title} {description}"[:2000]

    # Run retrieval to find relevant capability records
    try:
        retrieved = retrieve(retrieval_query, use_reranker=True)
    except Exception as e:
        log.warning(f"Retrieval failed: {e}")
        retrieved = []

    # Format retrieved records into prompt context
    retrieved_context = build_retrieved_context(retrieved)

    # Build the scoring prompt using V2 template
    prompt = SCORING_USER_V2.format(
        retrieved_context=retrieved_context,
        opp_title=title,
        opp_sol_number=opp.get("solicitation_number", "N/A"),
        opp_notice_type=opp.get("base_type", opp.get("notice_type", "N/A")),
        opp_naics=opp.get("naics_code", "N/A"),
        opp_set_aside=opp.get("set_aside_type", "Unrestricted") or "Unrestricted",
        opp_office=f"{opp.get('office', 'N/A')} ({opp.get('office_code', '')})",
        opp_agency=opp.get("department", "N/A"),
        opp_description=description or "No description available",
        opp_deadline=opp.get("response_deadline", "N/A"),
    )

    full_prompt = f"{SCORING_SYSTEM}\n\n{prompt}"

    try:
        r = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": model,
                "prompt": full_prompt,
                "stream": False,
                "options": {
                    "temperature": 0.0,
                    "num_predict": 2048,
                },
            },
            timeout=300,
        )
        r.raise_for_status()
    except requests.ConnectionError:
        log.error("Cannot connect to Ollama. Is it running?")
        return None
    except requests.Timeout:
        log.warning("Ollama request timed out.")
        return None
    except requests.HTTPError as e:
        log.error(f"Ollama HTTP error: {e}")
        return None

    result = r.json()
    response_text = result.get("response", "").strip()

    if not response_text:
        return None

    # Parse JSON from response
    if "```" in response_text:
        match = re.search(r'```(?:json)?\s*\n?(.*?)```', response_text, re.DOTALL)
        if match:
            response_text = match.group(1).strip()

    json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
    if json_match:
        response_text = json_match.group(0)

    try:
        score_data = json.loads(response_text)
    except json.JSONDecodeError:
        log.warning(f"Failed to parse score JSON for: {title[:50]}")
        return None

    # Validate and clamp scores to 0-100
    def clamp(val, lo=0, hi=100):
        try:
            return max(lo, min(hi, int(val)))
        except (ValueError, TypeError):
            return 0

    capability_score = clamp(score_data.get("capability_score", 0))
    domain_score = clamp(score_data.get("domain_score", 0))
    naics_score = clamp(score_data.get("naics_score", 0))
    set_aside_fit = clamp(score_data.get("set_aside_fit", 0))

    computed_overall = int(
        (capability_score * 0.50) +
        (naics_score * 0.25) +
        (domain_score * 0.15) +
        (set_aside_fit * 0.10)
    )

    if capability_score <= 20:
        computed_overall = min(computed_overall, 35)

    return {
        "opportunity_id": opp["id"],
        "overall_score": computed_overall,
        "domain_score": domain_score,
        "capability_score": capability_score,
        "naics_score": naics_score,
        "set_aside_fit": set_aside_fit,
        "work_summary": score_data.get("work_summary", ""),
        "rationale": score_data.get("rationale", ""),
        "matched_capabilities_used": score_data.get("matched_capabilities_used", []),
        "key_alignment_factors": score_data.get("key_alignment_factors", []),
        "risk_factors": score_data.get("risk_factors", []),
        "model_used": model,
        "scored_at": datetime.now().isoformat(),
        "raw_response": result.get("response", ""),
        "retrieved_records": retrieved,  # pass through for audit trail in Commit 4
    }

def build_retrieved_context(records: list) -> str:
    """Format retrieval results into a structured prompt section.
    
    Groups records by type (competencies, past performance, service areas)
    and includes relevance scores so the LLM can gauge match confidence.
    """
    competencies = [r for r in records if r.record_type == "competency"]
    past_perfs = [r for r in records if r.record_type == "past_performance"]
    service_areas = [r for r in records if r.record_type == "service_area"]

    parts = []

    if competencies:
        parts.append("MATCHED TECHNICAL COMPETENCIES:")
        for r in competencies:
            # Prefer rerank score (most accurate), fall back to rrf
            if r.rerank_score is not None:
                score_info = f"(relevance: {r.rerank_score:.2f})"
            elif r.rrf_score:
                score_info = f"(rrf: {r.rrf_score:.4f})"
            else:
                score_info = ""
            parts.append(f"  - {r.name} {score_info}")
            if r.description:
                parts.append(f"    {r.description[:300]}")

    if past_perfs:
        parts.append("\nMATCHED PAST PERFORMANCE:")
        for r in past_perfs:
            agency = r.metadata.get("agency", "")
            if r.rerank_score is not None:
                score_info = f"(relevance: {r.rerank_score:.2f})"
            elif r.rrf_score:
                score_info = f"(rrf: {r.rrf_score:.4f})"
            else:
                score_info = ""
            agency_str = f" [{agency}]" if agency else ""
            parts.append(f"  - {r.name}{agency_str} {score_info}")
            if r.description:
                parts.append(f"    {r.description[:300]}")

    if service_areas:
        parts.append("\nMATCHED SERVICE AREAS:")
        parts.append(f"  {', '.join(r.name for r in service_areas)}")

    if not parts:
        return "No matching capabilities found in the company's corpus."

    return "\n".join(parts)

def save_scoring_run(score: dict, opp: dict) -> None:
    """Save a scoring run and its retrieved records to Postgres for audit trail."""
    from coe.database import get_session
    from coe.models import ScoringRun, ScoringRetrievedRecord

    pg_session = get_session()
    try:
        run = ScoringRun(
            opportunity_notice_id=opp.get("solicitation_number", opp.get("notice_id", str(opp["id"]))),
            opportunity_title=opp.get("title", "")[:1000],
            overall_score=score["overall_score"],
            capability_score=score["capability_score"],
            naics_score=score["naics_score"],
            domain_score=score["domain_score"],
            set_aside_score=score["set_aside_fit"],
            work_summary=score.get("work_summary"),
            rationale=score.get("rationale"),
            risk_factors=json.dumps(score.get("risk_factors", [])),
            model_used=score["model_used"],
            scoring_duration_ms=score.get("scoring_duration_ms"),
        )

        # Link each retrieved record to this scoring run
        for rec in score.get("retrieved_records", []):
            # Determine how this record was retrieved
            if rec.rerank_score is not None:
                method = "rerank"
                sim_score = rec.rerank_score
            elif rec.vector_score is not None:
                method = "vector"
                sim_score = rec.vector_score
            elif rec.bm25_score is not None:
                method = "bm25"
                sim_score = rec.bm25_score
            else:
                method = "keyword"
                sim_score = None

            run.retrieved_records.append(ScoringRetrievedRecord(
                record_type=rec.record_type,
                record_id=rec.record_id,
                record_name=rec.name[:500],
                retrieval_method=method,
                similarity_score=sim_score,
                rank=rec.final_rank,
            ))

        pg_session.add(run)
        pg_session.commit()
        log.info(f"  Saved scoring run to Postgres ({len(run.retrieved_records)} retrieved records)")
    except Exception as e:
        pg_session.rollback()
        log.warning(f"  Failed to save scoring run to Postgres: {e}")
    finally:
        pg_session.close()


def main():
    parser = argparse.ArgumentParser(description="Score SAM.gov opportunities against capability profile")
    parser.add_argument("--limit", type=int, default=50,
                        help="Max opportunities to score per run (default: 50)")
    parser.add_argument("--rescore", action="store_true",
                        help="Re-score all opportunities (clears existing scores)")
    parser.add_argument("--model", default=None,
                        help=f"Ollama model to use (default: from config or {DEFAULT_MODEL})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be scored without calling LLM")
    args = parser.parse_args()

    config = load_config()
    matching_cfg = config.get("matching", {})
    model = args.model or matching_cfg.get("ollama_model", DEFAULT_MODEL)

    # Check Ollama
    if not args.dry_run:
        try:
            r = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
            if r.status_code != 200:
                raise ConnectionError()
        except Exception:
            log.error("Ollama is not running! Start with: ollama serve")
            sys.exit(1)

    # Load database (retrieval handles capability context per-opportunity now)
    db_path = matching_cfg.get("database", config.get("settings", {}).get("database", "opportunities.db"))
    if not Path(db_path).is_absolute():
        db_path = str(SCRIPT_DIR / db_path)

    db = Database(db_path)

    # Clear scores if rescoring
    if args.rescore:
        db.conn.execute("DELETE FROM match_scores")
        db.commit()
        log.info("Cleared all existing scores for re-scoring.")

    # Get unscored opportunities
    if args.limit == 0:
        opps = db.get_all_unscored_opportunities()
    else:
        opps = db.get_unscored_opportunities(limit=args.limit)

    if not opps:
        log.info("No unscored opportunities found. Everything is up to date!")
        stats = db.get_scoring_stats()
        log.info(f"Scoring stats: {stats}")
        db.close()
        return

    log.info(f"Found {len(opps)} unscored opportunities to process")
    log.info(f"Using model: {model}")

    if args.dry_run:
        log.info("DRY RUN — showing opportunities that would be scored:")
        for o in opps:
            log.info(f"  [{o['id']}] {o['title'][:70]} | {o['naics_code']} | {o['office_code']}")
        db.close()
        return

    # Score each opportunity
    scored = 0
    failed = 0
    total_score = 0
    start_time = time.time()

    for i, opp in enumerate(opps):
        title_short = (opp["title"] or "Untitled")[:60]
        log.info(f"\n[{i+1}/{len(opps)}] Scoring: {title_short}...")

        score = score_opportunity_via_ollama(dict(opp), model)

        if score:
            db.insert_match_score(score)
            db.commit()
            save_scoring_run(score, dict(opp))
            scored += 1
            total_score += score["overall_score"]

            emoji = "🟢" if score["overall_score"] >= 70 else ("🟡" if score["overall_score"] >= 40 else "🔴")
            log.info(f"  {emoji} Score: {score['overall_score']}/100 "
                     f"(D:{score['domain_score']} C:{score['capability_score']} "
                     f"N:{score['naics_score']} S:{score['set_aside_fit']})")
            log.info(f"  → {score['rationale'][:120]}")
        else:
            failed += 1
            log.warning(f"  ⚠️  Failed to score")

    elapsed = time.time() - start_time
    avg = total_score / scored if scored else 0

    log.info(f"\n{'='*60}")
    log.info(f"SCORING COMPLETE")
    log.info(f"  Scored: {scored} | Failed: {failed} | Time: {elapsed:.0f}s")
    log.info(f"  Average score: {avg:.1f}/100")
    log.info(f"{'='*60}")

    # Print final stats
    stats = db.get_scoring_stats()
    log.info(f"Overall: {stats['scored']}/{stats['total_opportunities']} scored, "
             f"avg {stats['avg_score']}, "
             f"{stats['high_matches_70plus']} high matches, "
             f"{stats['medium_matches_40_69']} medium matches")

    db.close()



if __name__ == "__main__":
    main()
