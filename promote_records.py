#!/usr/bin/env python3
"""
Auto-promote all pending proposed_records into the real capability tables.

Creates proposals first (needed for foreign keys), then service areas and
competencies (with deduplication), then past performances and domain
experiences, and finally links proposals to service areas and competencies
via junction tables.

Usage:
    python promote_records.py
"""

import json
from datetime import datetime, timezone

from coe.database import get_session
from coe.models import (
    Proposal, ServiceArea, TechnicalCompetency,
    PastPerformance, DomainExperience,
    ProposalServiceArea, ProposalCompetency,
    ProposedRecord,
)


def main():
    session = get_session()

    try:
        pending = session.query(ProposedRecord).filter_by(status="pending").all()
        print(f"Found {len(pending)} pending records.\n")

        # Group by type
        by_type = {}
        for r in pending:
            by_type.setdefault(r.record_type, []).append(r)

        # --- 1. Proposals ---
        proposal_map = {}  # proposal_name -> Proposal object
        for rec in by_type.get("proposal", []):
            data = json.loads(rec.payload)
            proposal = Proposal(
                name=data["name"],
                source_file=data["source_file"],
                agency=data.get("agency"),
                naics_codes=json.dumps(data.get("naics_codes", [])),
                set_aside_qualifications=json.dumps(data.get("set_aside_qualifications", [])),
            )
            session.add(proposal)
            session.flush()  # Get the ID assigned
            proposal_map[data["name"]] = proposal

            rec.status = "approved"
            rec.promoted_id = proposal.id
            rec.promoted_table = "proposals"
            rec.reviewed_at = datetime.now(timezone.utc)
            print(f"  Proposal: {data['name']} -> id={proposal.id}")

        # --- 2. Service Areas (deduplicated) ---
        sa_map = {}  # name -> ServiceArea object
        for rec in by_type.get("service_area", []):
            data = json.loads(rec.payload)
            name = data["name"]
            if name not in sa_map:
                # Check if it already exists in DB
                existing = session.query(ServiceArea).filter_by(name=name).first()
                if existing:
                    sa_map[name] = existing
                else:
                    sa = ServiceArea(name=name)
                    session.add(sa)
                    session.flush()
                    sa_map[name] = sa

            rec.status = "approved"
            rec.promoted_id = sa_map[name].id
            rec.promoted_table = "service_areas"
            rec.reviewed_at = datetime.now(timezone.utc)

            # Link to proposal via junction table
            proposal_name = data.get("proposal_name")
            if proposal_name and proposal_name in proposal_map:
                link = ProposalServiceArea(
                    proposal_id=proposal_map[proposal_name].id,
                    service_area_id=sa_map[name].id,
                )
                session.merge(link)  # merge avoids duplicate key errors

        print(f"  Service areas: {len(sa_map)} unique")

        # --- 3. Technical Competencies (deduplicated) ---
        comp_map = {}  # name -> TechnicalCompetency object
        for rec in by_type.get("competency", []):
            data = json.loads(rec.payload)
            name = data["name"]
            if name not in comp_map:
                existing = session.query(TechnicalCompetency).filter_by(name=name).first()
                if existing:
                    comp_map[name] = existing
                else:
                    comp = TechnicalCompetency(name=name)
                    session.add(comp)
                    session.flush()
                    comp_map[name] = comp

            rec.status = "approved"
            rec.promoted_id = comp_map[name].id
            rec.promoted_table = "technical_competencies"
            rec.reviewed_at = datetime.now(timezone.utc)

            # Link to proposal
            proposal_name = data.get("proposal_name")
            if proposal_name and proposal_name in proposal_map:
                link = ProposalCompetency(
                    proposal_id=proposal_map[proposal_name].id,
                    competency_id=comp_map[name].id,
                )
                session.merge(link)

        print(f"  Competencies: {len(comp_map)} unique")

        # --- 4. Past Performances ---
        pp_count = 0
        for rec in by_type.get("past_performance", []):
            data = json.loads(rec.payload)
            proposal_name = data.get("proposal_name")
            proposal = proposal_map.get(proposal_name)
            if not proposal:
                print(f"  WARNING: No proposal found for past performance: {proposal_name}")
                continue

            pp = PastPerformance(
                proposal_id=proposal.id,
                project_name=data.get("project_name", "Unknown"),
                agency=data.get("agency"),
                description=data.get("description"),
            )
            session.add(pp)
            session.flush()

            rec.status = "approved"
            rec.promoted_id = pp.id
            rec.promoted_table = "past_performances"
            rec.reviewed_at = datetime.now(timezone.utc)
            pp_count += 1

        print(f"  Past performances: {pp_count}")

        # --- 5. Domain Experiences ---
        de_count = 0
        for rec in by_type.get("domain_experience", []):
            data = json.loads(rec.payload)
            proposal_name = data.get("proposal_name")
            proposal = proposal_map.get(proposal_name)
            if not proposal:
                print(f"  WARNING: No proposal found for domain experience: {proposal_name}")
                continue

            de = DomainExperience(
                proposal_id=proposal.id,
                domain=data.get("domain", "Unknown"),
                depth=data.get("depth", "moderate"),
            )
            session.add(de)
            session.flush()

            rec.status = "approved"
            rec.promoted_id = de.id
            rec.promoted_table = "domain_experiences"
            rec.reviewed_at = datetime.now(timezone.utc)
            de_count += 1

        print(f"  Domain experiences: {de_count}")

        session.commit()
        print(f"\nDone! All records promoted.")

    except Exception as e:
        session.rollback()
        print(f"\nError: {e}")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()