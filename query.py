#!/usr/bin/env python3
"""
SAM.gov Opportunities Query Utility

Quick way to browse and search the local opportunities database.

Usage:
    python query.py                          # Show summary stats
    python query.py list                     # List recent opportunities
    python query.py list --office 36C10B     # Filter by office code
    python query.py list --naics 541512      # Filter by NAICS code
    python query.py list --days 7            # Show last 7 days
    python query.py list --search "cyber"    # Search titles
    python query.py list --active            # Only active/open opportunities
    python query.py list --limit 50          # Show up to 50 results
    python query.py offices                  # Show per-office counts
    python query.py detail <notice_id>       # Show full details for one opportunity
    python query.py history                  # Show recent pull history
    python query.py export                   # Export all to CSV
    python query.py export --office 36C10B   # Export filtered to CSV
"""

import sys
import csv
import json
import sqlite3
import argparse
from datetime import datetime, timedelta
from pathlib import Path


DB_PATH = Path(__file__).parent / "opportunities.db"


def get_connection():
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}")
        print("Run sam_puller.py first to create it.")
        sys.exit(1)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def cmd_stats(args):
    """Show summary statistics."""
    conn = get_connection()

    total = conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0]
    active = conn.execute("SELECT COUNT(*) FROM opportunities WHERE active = 'Yes'").fetchone()[0]

    print(f"\n{'='*60}")
    print(f"  SAM.gov Opportunities Database")
    print(f"{'='*60}")
    print(f"  Total opportunities:  {total}")
    print(f"  Active (open):        {active}")
    print(f"  Closed/archived:      {total - active}")

    # Latest pull
    latest = conn.execute(
        "SELECT pulled_at, opportunities_found, new_opportunities "
        "FROM pull_history WHERE status = 'success' ORDER BY pulled_at DESC LIMIT 1"
    ).fetchone()
    if latest:
        print(f"\n  Last pull:            {latest['pulled_at'][:19]}")
        print(f"  Last pull found:      {latest['opportunities_found']} fetched, "
              f"{latest['new_opportunities']} new")

    # Per notice type
    print(f"\n  By notice type:")
    rows = conn.execute(
        "SELECT notice_type, COUNT(*) as cnt FROM opportunities "
        "GROUP BY notice_type ORDER BY cnt DESC"
    ).fetchall()
    for row in rows:
        print(f"    {row['notice_type'] or 'Unknown':<35} {row['cnt']:>5}")

    # Per office
    print(f"\n  By office:")
    rows = conn.execute(
        "SELECT office_code, COUNT(*) as cnt FROM opportunity_offices "
        "GROUP BY office_code ORDER BY cnt DESC"
    ).fetchall()
    for row in rows:
        print(f"    {row['office_code']:<15} {row['cnt']:>5}")

    print(f"{'='*60}\n")
    conn.close()


def cmd_list(args):
    """List opportunities with optional filters."""
    conn = get_connection()

    conditions = []
    params = []

    if args.office:
        conditions.append(
            "o.id IN (SELECT opportunity_id FROM opportunity_offices WHERE office_code = ?)"
        )
        params.append(args.office)

    if args.naics:
        conditions.append("o.naics_code = ?")
        params.append(args.naics)

    if args.search:
        conditions.append("o.title LIKE ?")
        params.append(f"%{args.search}%")

    if args.active:
        conditions.append("o.active = 'Yes'")

    if args.days:
        cutoff = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
        conditions.append("o.posted_date >= ?")
        params.append(cutoff)

    if args.notice_type:
        conditions.append("o.notice_type = ?")
        params.append(args.notice_type)

    if args.set_aside:
        conditions.append("o.set_aside_code = ?")
        params.append(args.set_aside)

    where = " AND ".join(conditions) if conditions else "1=1"
    limit = args.limit or 25

    query = f"""
        SELECT o.* FROM opportunities o
        WHERE {where}
        ORDER BY o.posted_date DESC
        LIMIT ?
    """
    params.append(limit)

    rows = conn.execute(query, params).fetchall()

    if not rows:
        print("\nNo opportunities found matching your filters.\n")
        conn.close()
        return

    print(f"\n{'='*90}")
    print(f"  Showing {len(rows)} opportunities (limit: {limit})")
    print(f"{'='*90}\n")

    for row in rows:
        deadline = row['response_deadline'] or "N/A"
        set_aside = row['set_aside_type'] or "Unrestricted"
        naics = row['naics_code'] or "N/A"

        print(f"  {row['title'][:75]}")
        print(f"    Sol#: {row['solicitation_number'] or 'N/A':<25} "
              f"Type: {row['notice_type'] or 'N/A'}")
        print(f"    Posted: {row['posted_date']:<15} "
              f"Deadline: {deadline:<15} "
              f"NAICS: {naics}")
        print(f"    Set-aside: {set_aside:<30} "
              f"Office: {row['office_code']}")
        print(f"    Link: {row['description_url'] or 'N/A'}")
        print()

    conn.close()


def cmd_offices(args):
    """Show per-office breakdown."""
    conn = get_connection()

    rows = conn.execute("""
        SELECT
            oo.office_code,
            COUNT(*) as total,
            SUM(CASE WHEN o.active = 'Yes' THEN 1 ELSE 0 END) as active
        FROM opportunity_offices oo
        JOIN opportunities o ON o.id = oo.opportunity_id
        GROUP BY oo.office_code
        ORDER BY total DESC
    """).fetchall()

    if not rows:
        print("\nNo data yet. Run sam_puller.py first.\n")
        conn.close()
        return

    print(f"\n{'='*50}")
    print(f"  {'Office Code':<15} {'Total':>8} {'Active':>8}")
    print(f"{'='*50}")
    for row in rows:
        print(f"  {row['office_code']:<15} {row['total']:>8} {row['active']:>8}")

    total_all = sum(r['total'] for r in rows)
    active_all = sum(r['active'] for r in rows)
    print(f"{'─'*50}")
    print(f"  {'TOTAL':<15} {total_all:>8} {active_all:>8}")
    print(f"{'='*50}\n")

    conn.close()


def cmd_detail(args):
    """Show full details for a single opportunity."""
    conn = get_connection()

    row = conn.execute(
        "SELECT * FROM opportunities WHERE notice_id = ? OR solicitation_number = ?",
        (args.id, args.id)
    ).fetchone()

    if not row:
        print(f"\nNo opportunity found with ID or solicitation number: {args.id}\n")
        conn.close()
        return

    print(f"\n{'='*70}")
    print(f"  {row['title']}")
    print(f"{'='*70}")

    fields = [
        ("Notice ID", row['notice_id']),
        ("Solicitation #", row['solicitation_number']),
        ("Notice Type", row['notice_type']),
        ("Base Type", row['base_type']),
        ("Posted Date", row['posted_date']),
        ("Response Deadline", row['response_deadline']),
        ("Archive Date", row['archive_date']),
        ("Office Code", row['office_code']),
        ("Department", row['department']),
        ("NAICS Code", row['naics_code']),
        ("Classification Code", row['classification_code']),
        ("Set-Aside Type", row['set_aside_type']),
        ("Set-Aside Code", row['set_aside_code']),
        ("Award Number", row['award_number']),
        ("Award Amount", row['award_amount']),
        ("Awardee", row['awardee_name']),
        ("Place of Performance", f"{row['pop_city'] or ''}, {row['pop_state'] or ''}".strip(", ")),
        ("Active", row['active']),
        ("First Seen", row['first_seen_at'][:19] if row['first_seen_at'] else ""),
        ("Last Updated", row['last_updated_at'][:19] if row['last_updated_at'] else ""),
        ("SAM.gov Link", row['description_url']),
    ]

    for label, value in fields:
        if value:
            print(f"  {label + ':':<22} {value}")

    # Show which offices found this
    offices = conn.execute(
        "SELECT office_code FROM opportunity_offices WHERE opportunity_id = ?",
        (row['id'],)
    ).fetchall()
    if offices:
        codes = ", ".join(o['office_code'] for o in offices)
        print(f"  {'Matched Offices:':<22} {codes}")

    print(f"{'='*70}\n")
    conn.close()


def cmd_history(args):
    """Show recent pull history."""
    conn = get_connection()

    rows = conn.execute(
        "SELECT * FROM pull_history ORDER BY pulled_at DESC LIMIT ?",
        (args.limit or 20,)
    ).fetchall()

    if not rows:
        print("\nNo pull history yet.\n")
        conn.close()
        return

    print(f"\n{'='*80}")
    print(f"  {'Pulled At':<22} {'Office':<15} {'Found':>7} {'New':>7} "
          f"{'Updated':>7} {'Status':<8}")
    print(f"{'='*80}")
    for row in rows:
        print(f"  {row['pulled_at'][:19]:<22} {row['office_code']:<15} "
              f"{row['opportunities_found']:>7} {row['new_opportunities']:>7} "
              f"{row['updated_opportunities']:>7} {row['status']:<8}")
    print(f"{'='*80}\n")

    conn.close()


def cmd_export(args):
    """Export opportunities to CSV."""
    conn = get_connection()

    conditions = []
    params = []

    if args.office:
        conditions.append(
            "o.id IN (SELECT opportunity_id FROM opportunity_offices WHERE office_code = ?)"
        )
        params.append(args.office)

    if args.active:
        conditions.append("o.active = 'Yes'")

    if args.days:
        cutoff = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
        conditions.append("o.posted_date >= ?")
        params.append(cutoff)

    where = " AND ".join(conditions) if conditions else "1=1"

    rows = conn.execute(f"""
        SELECT o.notice_id, o.title, o.solicitation_number, o.notice_type,
               o.posted_date, o.response_deadline, o.office_code,
               o.naics_code, o.classification_code, o.set_aside_type,
               o.set_aside_code, o.department,
               o.award_number, o.award_amount, o.awardee_name,
               o.pop_city, o.pop_state, o.active, o.description_url
        FROM opportunities o
        WHERE {where}
        ORDER BY o.posted_date DESC
    """, params).fetchall()

    if not rows:
        print("\nNo opportunities found to export.\n")
        conn.close()
        return

    # Determine output filename
    output = args.output or f"opportunities_export_{datetime.now().strftime('%Y%m%d')}.csv"
    output_path = Path(__file__).parent / output

    headers = rows[0].keys()
    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))

    print(f"\nExported {len(rows)} opportunities to {output_path}\n")
    conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Query the SAM.gov opportunities database"
    )
    subparsers = parser.add_subparsers(dest="command")

    # Default (no subcommand) shows stats
    # --- list ---
    list_parser = subparsers.add_parser("list", help="List opportunities")
    list_parser.add_argument("--office", help="Filter by office code")
    list_parser.add_argument("--naics", help="Filter by NAICS code")
    list_parser.add_argument("--search", help="Search in titles")
    list_parser.add_argument("--active", action="store_true", help="Only active opportunities")
    list_parser.add_argument("--days", type=int, help="Only show last N days")
    list_parser.add_argument("--notice-type", help="Filter by notice type")
    list_parser.add_argument("--set-aside", help="Filter by set-aside code")
    list_parser.add_argument("--limit", type=int, default=25, help="Max results (default: 25)")

    # --- offices ---
    subparsers.add_parser("offices", help="Show per-office counts")

    # --- detail ---
    detail_parser = subparsers.add_parser("detail", help="Show details for one opportunity")
    detail_parser.add_argument("id", help="Notice ID or solicitation number")

    # --- history ---
    history_parser = subparsers.add_parser("history", help="Show pull history")
    history_parser.add_argument("--limit", type=int, default=20, help="Max rows")

    # --- export ---
    export_parser = subparsers.add_parser("export", help="Export to CSV")
    export_parser.add_argument("--office", help="Filter by office code")
    export_parser.add_argument("--active", action="store_true", help="Only active")
    export_parser.add_argument("--days", type=int, help="Only last N days")
    export_parser.add_argument("--output", help="Output filename (default: auto-named)")

    args = parser.parse_args()

    if args.command is None:
        cmd_stats(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "offices":
        cmd_offices(args)
    elif args.command == "detail":
        cmd_detail(args)
    elif args.command == "history":
        cmd_history(args)
    elif args.command == "export":
        cmd_export(args)


if __name__ == "__main__":
    main()
