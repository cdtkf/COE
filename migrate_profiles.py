#!/usr/bin/env python3
"""
Migrate existing profile JSONs into the proposed_records staging table.

Reads each profiles/profile_*.json file and creates individual proposed_records
for: the proposal itself, each service area, each competency, each past
performance, and each domain experience.

Usage:
    python migrate_profiles.py
"""

import json
import glob
from coe.database import get_session
from coe.models import ProposedRecord


def load_profiles():
    """Load all profile JSON files."""
    files = sorted(glob.glob("profiles/profile_*.json"))
    profiles = []
    for f in files:
        with open(f) as fh:
            data = json.load(fh)
            profiles.append(data)
        print(f"  Loaded: {f}")
    return profiles


def create_proposed_records(profile):
    """
    Turn one profile JSON into a list of ProposedRecord objects.
    Each service area, competency, etc. gets its own row.
    """
    source = profile.get("source_file", "unknown")
    records = []

    # 1. The proposal itself
    records.append(ProposedRecord(
        record_type="proposal",
        source_file=source,
        payload=json.dumps({
            "name": profile.get("proposal_name", ""),
            "source_file": source,
            "agency": profile.get("domains", [None])[0],  # Primary domain as agency
            "naics_codes": profile.get("naics_codes", []),
            "set_aside_qualifications": profile.get("set_aside_qualifications", []),
            "contract_vehicles": profile.get("contract_vehicles", []),
            "differentiators": profile.get("differentiators", []),
            "keywords": profile.get("keywords", []),
        }),
    ))

    # 2. Service areas — one record each
    for sa in profile.get("service_areas", []):
        records.append(ProposedRecord(
            record_type="service_area",
            source_file=source,
            payload=json.dumps({
                "name": sa,
                "proposal_name": profile.get("proposal_name", ""),
            }),
        ))

    # 3. Technical competencies — one record each
    for comp in profile.get("technical_competencies", []):
        records.append(ProposedRecord(
            record_type="competency",
            source_file=source,
            payload=json.dumps({
                "name": comp,
                "proposal_name": profile.get("proposal_name", ""),
            }),
        ))

    # 4. Past performances — one record each
    for pp in profile.get("past_performance", []):
        records.append(ProposedRecord(
            record_type="past_performance",
            source_file=source,
            payload=json.dumps({
                "project_name": pp.get("contract_name", "Unknown"),
                "agency": pp.get("agency", ""),
                "description": pp.get("outcome", ""),
                "proposal_name": profile.get("proposal_name", ""),
            }),
        ))

    # 5. Domain experiences — one record each
    for domain in profile.get("domains", []):
        records.append(ProposedRecord(
            record_type="domain_experience",
            source_file=source,
            payload=json.dumps({
                "domain": domain,
                "depth": "moderate",  # Default; you'll adjust during review
                "proposal_name": profile.get("proposal_name", ""),
            }),
        ))

    return records


def main():
    print("Loading profile JSONs...")
    profiles = load_profiles()
    print(f"\nFound {len(profiles)} profiles.\n")

    session = get_session()
    total = 0

    try:
        for profile in profiles:
            name = profile.get("proposal_name", "unknown")
            records = create_proposed_records(profile)
            for r in records:
                session.add(r)
            total += len(records)
            print(f"  {name}: {len(records)} records staged")

        session.commit()
        print(f"\nDone! {total} total records inserted into proposed_records.")
        print("Next step: review and promote these records.")

    except Exception as e:
        session.rollback()
        print(f"\nError: {e}")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()