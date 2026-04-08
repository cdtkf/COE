#!/usr/bin/env python3
"""
extract_profiles.py — One-time capability profile extraction from ReefPoint proposal PDFs.

Reads proposal PDFs from the configured directory, extracts text with pdfplumber,
sends to a local Ollama LLM for structured profile extraction, and saves results to JSON.

Requirements:
    pip install pdfplumber pyyaml requests
    Ollama must be installed and running: https://ollama.com

Usage:
    python extract_profiles.py                  # Extract all proposals
    python extract_profiles.py --pdf "file.pdf" # Extract from one specific PDF
    python extract_profiles.py --skip-llm       # Extract text only (no LLM call)
    python extract_profiles.py --reextract      # Force re-extraction even if profiles exist
    python extract_profiles.py --model gemma3   # Use a specific Ollama model
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import pdfplumber
import requests
import yaml

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.yaml"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# Default Ollama settings
OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "llama3.1"


def load_config() -> dict:
    """Load config.yaml and return the full config dict."""
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def check_ollama_running() -> bool:
    """Check if Ollama is running and accessible."""
    try:
        r = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        return r.status_code == 200
    except requests.ConnectionError:
        return False


def check_model_available(model: str) -> bool:
    """Check if the specified model is pulled in Ollama."""
    try:
        r = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        if r.status_code == 200:
            models = [m["name"].split(":")[0] for m in r.json().get("models", [])]
            return model in models
        return False
    except requests.ConnectionError:
        return False


# ---------------------------------------------------------------------------
# PDF Text Extraction
# ---------------------------------------------------------------------------
def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract all text from a PDF using pdfplumber."""
    log.info(f"Extracting text from: {Path(pdf_path).name}")
    text_parts = []

    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
        log.info(f"  → {total_pages} pages")

        for i, page in enumerate(pdf.pages):
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)

            # Also extract table data if present
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    if row:
                        cells = [str(cell).strip() for cell in row if cell]
                        if cells:
                            text_parts.append(" | ".join(cells))

    full_text = "\n\n".join(text_parts)
    log.info(f"  → Extracted {len(full_text):,} characters")
    return full_text


# ---------------------------------------------------------------------------
# Ollama Profile Extraction
# ---------------------------------------------------------------------------
def extract_profile_via_ollama(proposal_text: str, pdf_name: str, model: str) -> dict:
    """Send extracted text to local Ollama LLM and get a structured profile back."""
    from prompts import PROFILE_EXTRACTION_SYSTEM, PROFILE_EXTRACTION_USER

    # Ollama models typically have smaller context windows than cloud models.
    # Most models handle 8K-128K tokens. We'll send up to ~60K chars (~15K tokens)
    # which fits comfortably in llama3.1's 128K context.
    # For smaller-context models, reduce this.
    max_chars = 60_000
    if len(proposal_text) > max_chars:
        log.warning(f"  → Truncating from {len(proposal_text):,} to {max_chars:,} chars")
        proposal_text = proposal_text[:max_chars]

    prompt = PROFILE_EXTRACTION_USER.format(proposal_text=proposal_text)
    full_prompt = f"{PROFILE_EXTRACTION_SYSTEM}\n\n{prompt}"

    log.info(f"  → Sending to Ollama ({model}) for profile extraction...")
    log.info(f"  → Input size: ~{len(full_prompt):,} chars")
    start = time.time()

    try:
        r = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": model,
                "prompt": full_prompt,
                "stream": False,
                "options": {
                    "temperature": 0.0,
                    "num_predict": 8192,    # max output tokens
                },
            },
            timeout=600,  # 10 min timeout — large docs take time locally
        )
        r.raise_for_status()
    except requests.ConnectionError:
        log.error("  → Cannot connect to Ollama. Is it running? Start with: ollama serve")
        return None
    except requests.Timeout:
        log.error("  → Ollama request timed out after 10 minutes.")
        return None
    except requests.HTTPError as e:
        log.error(f"  → Ollama HTTP error: {e}")
        return None

    elapsed = time.time() - start
    result = r.json()
    response_text = result.get("response", "").strip()

    log.info(f"  → Response received in {elapsed:.1f}s")

    if not response_text:
        log.error("  → Empty response from Ollama")
        return None

    # Strip markdown code fences if present
    if "```" in response_text:
        # Extract content between first ``` and last ```
        match = re.search(r'```(?:json)?\s*\n?(.*?)```', response_text, re.DOTALL)
        if match:
            response_text = match.group(1).strip()

    # Try to find JSON object in response (model might include extra text)
    json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
    if json_match:
        response_text = json_match.group(0)

    try:
        profile = json.loads(response_text)
    except json.JSONDecodeError as e:
        log.error(f"  → Failed to parse JSON response: {e}")
        log.error(f"  → Raw response (first 500 chars):\n{response_text[:500]}")
        # Save raw response for debugging
        debug_path = SCRIPT_DIR / "profiles" / f"debug_{Path(pdf_name).stem}.txt"
        debug_path.write_text(result.get("response", ""))
        log.error(f"  → Full response saved to {debug_path}")
        return None

    # Add source file metadata
    profile["source_file"] = pdf_name

    # Log some stats
    eval_count = result.get("eval_count", 0)
    prompt_eval_count = result.get("prompt_eval_count", 0)
    log.info(f"  → Tokens: {prompt_eval_count:,} prompt / {eval_count:,} generated")

    return profile


# ---------------------------------------------------------------------------
# Composite Profile Builder
# ---------------------------------------------------------------------------
def build_composite_profile(individual_profiles: list) -> dict:
    """Merge individual proposal profiles into a single composite profile.

    Consolidates capabilities across all proposals and tracks frequency
    (capabilities mentioned in multiple proposals score higher in matching).
    """
    log.info("Building composite capability profile...")

    composite = {
        "company": "ReefPoint Group",
        "set_aside_qualifications": ["SDVOSB"],
        "total_proposals_analyzed": len(individual_profiles),
        "proposal_names": [],
        "service_areas": {},
        "domains": {},
        "naics_codes": {},
        "technical_competencies": {},
        "past_performance": [],
        "differentiators": {},
        "keywords": {},
        "contract_vehicles": {},
    }

    seen_performance = set()

    for profile in individual_profiles:
        name = profile.get("proposal_name", "Unknown")
        composite["proposal_names"].append(name)

        # Count frequency of each capability across proposals
        for field in ["service_areas", "domains", "naics_codes",
                      "technical_competencies", "differentiators",
                      "keywords", "contract_vehicles"]:
            for item in profile.get(field, []):
                if not isinstance(item, str):
                    item = str(item)
                item_lower = item.strip().lower()
                if item_lower:
                    if item_lower not in {k.lower() for k in composite[field]}:
                        composite[field][item.strip()] = 1
                    else:
                        for k in composite[field]:
                            if k.lower() == item_lower:
                                composite[field][k] += 1
                                break

        # Deduplicate past performance
        for perf in profile.get("past_performance", []):
            if isinstance(perf, dict):
                key = (perf.get("agency", ""), perf.get("contract_name", ""))
                if key not in seen_performance:
                    seen_performance.add(key)
                    composite["past_performance"].append(perf)

        # Merge set-aside qualifications
        for sa in profile.get("set_aside_qualifications", []):
            if isinstance(sa, str) and sa.strip() and sa.strip() not in composite["set_aside_qualifications"]:
                composite["set_aside_qualifications"].append(sa.strip())

    # Sort each frequency dict by count (most common first)
    for field in ["service_areas", "domains", "naics_codes",
                  "technical_competencies", "differentiators",
                  "keywords", "contract_vehicles"]:
        composite[field] = dict(
            sorted(composite[field].items(), key=lambda x: x[1], reverse=True)
        )

    log.info(f"  → {len(composite['service_areas'])} unique service areas")
    log.info(f"  → {len(composite['domains'])} unique domains")
    log.info(f"  → {len(composite['naics_codes'])} unique NAICS codes")
    log.info(f"  → {len(composite['technical_competencies'])} unique technical competencies")
    log.info(f"  → {len(composite['past_performance'])} past performance entries")

    return composite


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Extract capability profiles from proposal PDFs")
    parser.add_argument("--pdf", help="Process a single PDF file (filename only)")
    parser.add_argument("--skip-llm", action="store_true",
                        help="Extract text only, skip LLM call")
    parser.add_argument("--reextract", action="store_true",
                        help="Force re-extraction even if profiles already exist")
    parser.add_argument("--proposals-dir",
                        help="Override proposals directory path")
    parser.add_argument("--model", default=None,
                        help=f"Ollama model to use (default: from config or {DEFAULT_MODEL})")
    args = parser.parse_args()

    config = load_config()
    matching_cfg = config.get("matching", {})

    # Determine model
    model = args.model or matching_cfg.get("ollama_model", DEFAULT_MODEL)

    # Check Ollama is running (unless skipping LLM)
    if not args.skip_llm:
        if not check_ollama_running():
            log.error("Ollama is not running!")
            log.error("Install it from https://ollama.com then start it.")
            log.error("Or run with --skip-llm to just extract text.")
            sys.exit(1)

        if not check_model_available(model):
            log.error(f"Model '{model}' is not available in Ollama.")
            log.error(f"Pull it with: ollama pull {model}")
            log.error("Available models with good JSON extraction:")
            log.error("  ollama pull llama3.1      (8B, good balance)")
            log.error("  ollama pull gemma3        (27B, excellent quality)")
            log.error("  ollama pull mistral       (7B, fast)")
            sys.exit(1)

        log.info(f"Using Ollama model: {model}")

    # Determine proposals directory
    proposals_dir = args.proposals_dir or matching_cfg.get(
        "proposals_dir", "../../Proposals"
    )
    proposals_path = (SCRIPT_DIR / proposals_dir).resolve()

    if not proposals_path.exists():
        log.error(f"Proposals directory not found: {proposals_path}")
        sys.exit(1)

    # Determine output path
    profiles_path = Path(matching_cfg.get(
        "profiles_path", "profiles/capability_profiles.json"
    ))
    if not profiles_path.is_absolute():
        profiles_path = SCRIPT_DIR / profiles_path

    profiles_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing profiles if any
    existing_profiles = {}
    if profiles_path.exists() and not args.reextract:
        with open(profiles_path) as f:
            data = json.load(f)
            for p in data.get("individual_profiles", []):
                existing_profiles[p.get("source_file", "")] = p
        log.info(f"Loaded {len(existing_profiles)} existing profiles")

    # Find PDFs to process
    pdf_files = sorted(proposals_path.glob("*.pdf"))
    if args.pdf:
        pdf_files = [p for p in pdf_files if p.name == args.pdf]
        if not pdf_files:
            log.error(f"PDF not found: {args.pdf}")
            sys.exit(1)

    log.info(f"Found {len(pdf_files)} proposal PDFs in {proposals_path}")

    # Process each PDF
    individual_profiles = list(existing_profiles.values())
    new_count = 0

    for i, pdf_path in enumerate(pdf_files):
        if pdf_path.name in existing_profiles and not args.reextract:
            log.info(f"Skipping {pdf_path.name} (already extracted)")
            continue

        log.info(f"\n{'='*60}")
        log.info(f"Processing ({i+1}/{len(pdf_files)}): {pdf_path.name}")
        log.info(f"{'='*60}")

        # Step 1: Extract text
        text = extract_text_from_pdf(str(pdf_path))

        if not text.strip():
            log.warning(f"  → No text extracted from {pdf_path.name}, skipping")
            continue

        # Save extracted text for reference
        text_path = SCRIPT_DIR / "profiles" / f"text_{pdf_path.stem}.txt"
        text_path.write_text(text)
        log.info(f"  → Text saved to {text_path.name}")

        if args.skip_llm:
            log.info("  → Skipping LLM call (--skip-llm)")
            continue

        # Step 2: Send to Ollama for profile extraction
        profile = extract_profile_via_ollama(text, pdf_path.name, model)

        if profile:
            individual_profiles = [
                p for p in individual_profiles
                if p.get("source_file") != pdf_path.name
            ]
            individual_profiles.append(profile)
            new_count += 1

            # Save individual profile
            indiv_path = SCRIPT_DIR / "profiles" / f"profile_{pdf_path.stem}.json"
            with open(indiv_path, "w") as f:
                json.dump(profile, f, indent=2)
            log.info(f"  → Individual profile saved to {indiv_path.name}")

            # Save progress after each successful extraction
            # (so we don't lose work if something fails later)
            partial_output = {
                "metadata": {
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "total_proposals": len(individual_profiles),
                    "new_extractions": new_count,
                    "status": "in_progress",
                },
                "composite_profile": build_composite_profile(individual_profiles),
                "individual_profiles": individual_profiles,
            }
            with open(profiles_path, "w") as f:
                json.dump(partial_output, f, indent=2)
            log.info(f"  → Progress saved ({len(individual_profiles)} profiles so far)")
        else:
            log.warning(f"  → Failed to extract profile from {pdf_path.name}")

    if args.skip_llm:
        log.info("\nText extraction complete. Run again without --skip-llm to generate profiles.")
        return

    if not individual_profiles:
        log.warning("No profiles extracted. Nothing to save.")
        return

    # Final composite profile
    composite = build_composite_profile(individual_profiles)

    output = {
        "metadata": {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "total_proposals": len(individual_profiles),
            "new_extractions": new_count,
            "model_used": model,
            "status": "complete",
        },
        "composite_profile": composite,
        "individual_profiles": individual_profiles,
    }

    with open(profiles_path, "w") as f:
        json.dump(output, f, indent=2)

    log.info(f"\n{'='*60}")
    log.info(f"COMPLETE — {len(individual_profiles)} profiles saved to {profiles_path}")
    log.info(f"  → {new_count} new, {len(individual_profiles) - new_count} existing")
    log.info(f"{'='*60}")


if __name__ == "__main__":
    main()
