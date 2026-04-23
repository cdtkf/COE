#!/usr/bin/env python3
"""
compare_pipelines.py — Side-by-side comparison of OLD (whole-profile) vs NEW
(retrieval-augmented) scoring pipelines on the same opportunities.

Phase 1 exit criterion: confirm the rearchitected pipeline is actually more
accurate on cases where the old pipeline inflated scores (construction,
equipment leases, etc.) without regressing on legitimate matches.

Outputs:
  - comparison_<timestamp>.csv      row-per-opportunity with both scores
  - comparison_<timestamp>.md       human-readable side-by-side report

Usage:
    python compare_pipelines.py                 # 20 opps, stratified sample
    python compare_pipelines.py --limit 10
    python compare_pipelines.py --ids 123,456   # specific opportunity ids
    python compare_pipelines.py --model llama3.1
"""

import argparse
import csv
import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

from coe.puller.sqlite_db import Database
from coe.scoring.prompts import SCORING_SYSTEM, SCORING_USER, SCORING_USER_V2
from coe.scoring.retrieval import retrieve
from coe.scoring.matcher import (
    CONFIG_PATH,
    OLLAMA_BASE_URL,
    DEFAULT_MODEL,
    load_config,
    load_capability_profile,
    build_profile_summary,
    extract_description_from_raw,
    build_retrieved_context,
)

SCRIPT_DIR = Path(__file__).resolve().parent

# NAICS prefixes the old pipeline is known to inflate — used for stratified sampling.
SUSPECT_NAICS_PREFIXES = ("236", "237", "238", "532", "621", "722", "721", "517")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def call_ollama(prompt: str, model: str) -> dict | None:
    """Call Ollama and parse JSON from the response."""
    try:
        r = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.0, "num_predict": 2048},
            },
            timeout=300,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        log.warning(f"Ollama call failed: {e}")
        return None

    text = r.json().get("response", "").strip()
    if not text:
        return None

    if "```" in text:
        m = re.search(r'```(?:json)?\s*\n?(.*?)```', text, re.DOTALL)
        if m:
            text = m.group(1).strip()
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        text = m.group(0)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.warning("Failed to parse JSON from Ollama response")
        return None


def clamp(val, lo=0, hi=100) -> int:
    try:
        return max(lo, min(hi, int(val)))
    except (ValueError, TypeError):
        return 0


def compute_overall(capability: int, naics: int, domain: int, set_aside: int) -> int:
    overall = int(
        capability * 0.50 + naics * 0.25 + domain * 0.15 + set_aside * 0.10
    )
    if capability <= 20:
        overall = min(overall, 35)
    return overall


def score_old(opp: dict, profile_summary: str, model: str) -> dict | None:
    """Run the OLD pipeline: whole-profile-in-prompt via SCORING_USER."""
    description = extract_description_from_raw(opp.get("raw_json", "{}"))
    user_prompt = SCORING_USER.format(
        capability_profile=profile_summary,
        opp_title=opp.get("title", "N/A"),
        opp_sol_number=opp.get("solicitation_number", "N/A"),
        opp_notice_type=opp.get("base_type", opp.get("notice_type", "N/A")),
        opp_naics=opp.get("naics_code", "N/A"),
        opp_set_aside=opp.get("set_aside_type", "Unrestricted") or "Unrestricted",
        opp_office=f"{opp.get('office', 'N/A')} ({opp.get('office_code', '')})",
        opp_agency=opp.get("department", "N/A"),
        opp_description=description or "No description available",
        opp_deadline=opp.get("response_deadline", "N/A"),
    )
    data = call_ollama(f"{SCORING_SYSTEM}\n\n{user_prompt}", model)
    if not data:
        return None

    cap = clamp(data.get("capability_score", 0))
    dom = clamp(data.get("domain_score", 0))
    nai = clamp(data.get("naics_score", 0))
    sa = clamp(data.get("set_aside_fit", 0))
    return {
        "overall_score": compute_overall(cap, nai, dom, sa),
        "capability_score": cap,
        "domain_score": dom,
        "naics_score": nai,
        "set_aside_fit": sa,
        "work_summary": data.get("work_summary", ""),
        "rationale": data.get("rationale", ""),
        "key_alignment_factors": data.get("key_alignment_factors", []),
        "risk_factors": data.get("risk_factors", []),
    }


def score_new(opp: dict, model: str) -> dict | None:
    """Run the NEW pipeline: retrieval-augmented via SCORING_USER_V2."""
    description = extract_description_from_raw(opp.get("raw_json", "{}"))
    title = opp.get("title", "N/A")
    query = f"{title} {description}"[:2000]

    try:
        retrieved = retrieve(query, use_reranker=True)
    except Exception as e:
        log.warning(f"Retrieval failed: {e}")
        retrieved = []

    retrieved_context = build_retrieved_context(retrieved)

    user_prompt = SCORING_USER_V2.format(
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
    data = call_ollama(f"{SCORING_SYSTEM}\n\n{user_prompt}", model)
    if not data:
        return None

    cap = clamp(data.get("capability_score", 0))
    dom = clamp(data.get("domain_score", 0))
    nai = clamp(data.get("naics_score", 0))
    sa = clamp(data.get("set_aside_fit", 0))
    return {
        "overall_score": compute_overall(cap, nai, dom, sa),
        "capability_score": cap,
        "domain_score": dom,
        "naics_score": nai,
        "set_aside_fit": sa,
        "work_summary": data.get("work_summary", ""),
        "rationale": data.get("rationale", ""),
        "key_alignment_factors": data.get("key_alignment_factors", []),
        "risk_factors": data.get("risk_factors", []),
        "retrieved_count": len(retrieved),
        "retrieved_names": [r.name for r in retrieved[:10]],
    }


def stratified_sample(db: Database, limit: int) -> list[dict]:
    """Pull a mix of suspect-NAICS and non-suspect opportunities so the diff
    surfaces cases where the two pipelines disagree meaningfully."""
    rows = db.conn.execute(
        """SELECT * FROM opportunities
           WHERE active = 'Yes'
           ORDER BY posted_date DESC
           LIMIT ?""",
        (limit * 8,),
    ).fetchall()

    suspect, clean = [], []
    for r in rows:
        naics = str(r["naics_code"] or "")
        if naics.startswith(SUSPECT_NAICS_PREFIXES):
            suspect.append(r)
        else:
            clean.append(r)

    half = limit // 2
    picked = suspect[:half] + clean[: limit - len(suspect[:half])]
    return [dict(r) for r in picked[:limit]]


def load_by_ids(db: Database, ids: list[int]) -> list[dict]:
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    rows = db.conn.execute(
        f"SELECT * FROM opportunities WHERE id IN ({placeholders})", ids
    ).fetchall()
    return [dict(r) for r in rows]


def write_csv(path: Path, results: list[dict]) -> None:
    fields = [
        "opp_id", "title", "naics_code", "set_aside_type",
        "old_overall", "new_overall", "delta_overall",
        "old_capability", "new_capability",
        "old_naics", "new_naics",
        "old_domain", "new_domain",
        "old_set_aside", "new_set_aside",
        "new_retrieved_count",
        "old_rationale", "new_rationale",
    ]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in results:
            w.writerow({k: row.get(k, "") for k in fields})


def write_markdown(path: Path, results: list[dict], model: str) -> None:
    lines = [
        f"# Pipeline Comparison — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Model: `{model}` · Opportunities: {len(results)}",
        "",
        "## Summary",
    ]

    diffs = [r["delta_overall"] for r in results if r.get("delta_overall") is not None]
    if diffs:
        avg_delta = sum(diffs) / len(diffs)
        old_avg = sum(r["old_overall"] for r in results if r.get("old_overall") is not None) / len(diffs)
        new_avg = sum(r["new_overall"] for r in results if r.get("new_overall") is not None) / len(diffs)
        big_drops = sum(1 for d in diffs if d <= -15)
        big_jumps = sum(1 for d in diffs if d >= 15)
        lines += [
            f"- Old avg overall: **{old_avg:.1f}**",
            f"- New avg overall: **{new_avg:.1f}**",
            f"- Mean delta (new − old): **{avg_delta:+.1f}**",
            f"- Big drops (new ≤ old − 15): **{big_drops}**  ← expected on suspect NAICS",
            f"- Big jumps (new ≥ old + 15): **{big_jumps}**  ← investigate",
            "",
        ]

    lines += [
        "## Side-by-side",
        "",
        "| id | NAICS | Title | OLD | NEW | Δ |",
        "|----|-------|-------|-----|-----|---|",
    ]
    for r in sorted(results, key=lambda x: (x.get("delta_overall") or 0)):
        lines.append(
            f"| {r['opp_id']} | {r.get('naics_code','')} | {(r.get('title','') or '')[:60]} | "
            f"{r.get('old_overall','—')} | {r.get('new_overall','—')} | "
            f"{r.get('delta_overall','—'):+d} |" if r.get("delta_overall") is not None
            else f"| {r['opp_id']} | {r.get('naics_code','')} | {(r.get('title','') or '')[:60]} | "
                 f"{r.get('old_overall','—')} | {r.get('new_overall','—')} | — |"
        )

    lines += ["", "## Per-opportunity detail", ""]
    for r in sorted(results, key=lambda x: (x.get("delta_overall") or 0)):
        lines += [
            f"### [{r['opp_id']}] {r.get('title','')}",
            f"- NAICS: `{r.get('naics_code','')}` · Set-aside: `{r.get('set_aside_type','')}`",
            f"- Overall: OLD **{r.get('old_overall','—')}** → NEW **{r.get('new_overall','—')}** "
            f"(Δ {r.get('delta_overall','—')})",
            f"- Breakdown (C/N/D/S): OLD {r.get('old_capability','—')}/{r.get('old_naics','—')}/"
            f"{r.get('old_domain','—')}/{r.get('old_set_aside','—')} → "
            f"NEW {r.get('new_capability','—')}/{r.get('new_naics','—')}/"
            f"{r.get('new_domain','—')}/{r.get('new_set_aside','—')}",
            "",
            f"**OLD rationale:** {r.get('old_rationale','—')}",
            "",
            f"**NEW rationale:** {r.get('new_rationale','—')}",
            "",
            f"**NEW retrieved ({r.get('new_retrieved_count',0)}):** "
            f"{', '.join(r.get('new_retrieved_names', [])) or '—'}",
            "",
            "---",
            "",
        ]

    path.write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description="Compare OLD vs NEW scoring pipelines")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--ids", type=str, default=None,
                        help="Comma-separated opportunity ids (overrides --limit sampling)")
    parser.add_argument("--model", default=None)
    parser.add_argument("--out-dir", default=str(SCRIPT_DIR / "logs"))
    args = parser.parse_args()

    config = load_config()
    matching_cfg = config.get("matching", {})
    model = args.model or matching_cfg.get("ollama_model", DEFAULT_MODEL)

    try:
        r = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        r.raise_for_status()
    except Exception:
        log.error("Ollama is not running. Start with: ollama serve")
        sys.exit(1)

    db_path = matching_cfg.get("database", config.get("settings", {}).get("database", "opportunities.db"))
    if not Path(db_path).is_absolute():
        db_path = str(SCRIPT_DIR / db_path)
    db = Database(db_path)

    profile = load_capability_profile(config)
    profile_summary = build_profile_summary(profile)

    if args.ids:
        ids = [int(x) for x in args.ids.split(",") if x.strip()]
        opps = load_by_ids(db, ids)
    else:
        opps = stratified_sample(db, args.limit)

    if not opps:
        log.error("No opportunities to compare.")
        db.close()
        sys.exit(1)

    log.info(f"Comparing {len(opps)} opportunities with model={model}")

    results = []
    start = time.time()
    for i, opp in enumerate(opps, 1):
        title = (opp.get("title") or "")[:60]
        log.info(f"[{i}/{len(opps)}] {title}")

        t0 = time.time()
        old = score_old(opp, profile_summary, model)
        t_old = time.time() - t0

        t0 = time.time()
        new = score_new(opp, model)
        t_new = time.time() - t0

        row = {
            "opp_id": opp["id"],
            "title": opp.get("title", ""),
            "naics_code": opp.get("naics_code", ""),
            "set_aside_type": opp.get("set_aside_type", ""),
        }
        if old:
            row.update({
                "old_overall": old["overall_score"],
                "old_capability": old["capability_score"],
                "old_naics": old["naics_score"],
                "old_domain": old["domain_score"],
                "old_set_aside": old["set_aside_fit"],
                "old_rationale": old["rationale"],
            })
        if new:
            row.update({
                "new_overall": new["overall_score"],
                "new_capability": new["capability_score"],
                "new_naics": new["naics_score"],
                "new_domain": new["domain_score"],
                "new_set_aside": new["set_aside_fit"],
                "new_rationale": new["rationale"],
                "new_retrieved_count": new.get("retrieved_count", 0),
                "new_retrieved_names": new.get("retrieved_names", []),
            })
        if old and new:
            row["delta_overall"] = new["overall_score"] - old["overall_score"]

        log.info(
            f"  OLD={row.get('old_overall','FAIL')} ({t_old:.1f}s)  "
            f"NEW={row.get('new_overall','FAIL')} ({t_new:.1f}s)  "
            f"Δ={row.get('delta_overall','—')}"
        )
        results.append(row)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"comparison_{stamp}.csv"
    md_path = out_dir / f"comparison_{stamp}.md"
    write_csv(csv_path, results)
    write_markdown(md_path, results, model)

    elapsed = time.time() - start
    log.info(f"Done in {elapsed:.0f}s")
    log.info(f"  CSV:      {csv_path}")
    log.info(f"  Markdown: {md_path}")

    db.close()


if __name__ == "__main__":
    main()
