# SAM.gov Contract Opportunities Puller

Automated daily puller that fetches new federal contract opportunities from SAM.gov, filters them to 17 tracked government offices, and stores them in a local SQLite database. Built for ReefPoint Group's contract opportunity matching pipeline.

## How It Works

1. Pulls all recently posted opportunities from the SAM.gov Opportunities API (v2)
2. Filters client-side by matching `fullParentPathCode` against your tracked office codes
3. Upserts into SQLite with deduplication (won't store the same opportunity twice)
4. Logs a per-office breakdown of what was found
5. Runs daily at 7am via a Cowork scheduled task

## Project Structure

```
sam-gov-puller/
├── config.yaml        # API key + tracked office codes + settings
├── sam_puller.py       # Main script — orchestrates the daily pull
├── sam_client.py       # SAM.gov API client (auth, pagination, rate limiting)
├── db.py               # SQLite database schema + helpers
├── query.py            # CLI utility to browse/search/export the database
├── requirements.txt    # Python dependencies
├── opportunities.db    # SQLite database (auto-created on first run)
└── logs/               # Daily log files
```

## Setup

### Prerequisites
- Python 3.10+
- A free SAM.gov API key from https://api.sam.gov

### Install

```bash
cd sam-gov-puller
pip install -r requirements.txt
```

### Configure

Edit `config.yaml`:
- Set your `api_key`
- Add/remove office codes under `offices:`

## Usage

### Daily Pull

```bash
python3 sam_puller.py                      # Normal run (incremental since last pull)
python3 sam_puller.py --force-lookback 10  # Pull last 10 days
python3 sam_puller.py --reset              # Wipe DB and start fresh
python3 sam_puller.py --reset --force-lookback 30  # Fresh start with 30-day backfill
```

### Query the Database

```bash
python3 query.py                            # Summary dashboard
python3 query.py list                       # List recent opportunities
python3 query.py list --office 36C10B       # Filter by office
python3 query.py list --search "cyber"      # Search titles
python3 query.py list --notice-type "Solicitation"  # RFPs only
python3 query.py list --notice-type "Sources Sought" # RFIs only
python3 query.py list --active --days 7     # Active opps from last week
python3 query.py offices                    # Per-office breakdown
python3 query.py detail <notice_id>         # Full details for one opportunity
python3 query.py history                    # Pull run history
python3 query.py export                     # Export all to CSV
python3 query.py export --office 36C776     # Export filtered to CSV
```

### Scheduled Task

A Cowork scheduled task (`sam-gov-daily-pull`) runs the puller every day at 7am. It appears in the Scheduled section of the Claude sidebar. You can also trigger it manually from there.

## Tracked Offices (17)

| Code | Office |
|------|--------|
| 36C10A | VA TAC Austin |
| 36C10B | VA Technology Acquisition Center NJ |
| 36C10D | Veterans Benefit Administration VBA |
| 36C10G | VA Strategic Acquisition Center Fredericksburg |
| 36C10X | VA Strategic Acquisition Center Frederick |
| 36C776 | PCAC |
| FA4484 | Air National Guard HQ |
| FA7014 | Air Force Medical Service Base Specific |
| FA8052 | Air Force Medical Service |
| HT0011 | DHA |
| SP4701 | Defense Logistics Agency |
| SP4703 | Defense Logistics Agency 2 |
| W6QK ACC-APG | Army Contracting Command |
| W81K04 | Army Health Contracting Activity |
| W912DY | US Army USACE |
| W912JA | National Guard Bureau Alabama |
| W912LQ | National Guard Bureau Virginia |

To add a new office, append to `config.yaml`:
```yaml
  - code: "NEWCODE"
    name: "Office Name"
```

## API Notes

- **Rate limits:** SAM.gov imposes daily rate limits per API key. A typical daily run uses 2-3 API calls and won't hit limits. Backfills of 10+ days may require a second key.
- **No server-side office filter:** The SAM.gov API does not support filtering by office code. We pull all recent opportunities and filter locally.
- **Partial results:** If rate-limited mid-pull, the script saves whatever it already fetched rather than losing everything.
- **Two API keys** are configured in `config.yaml` (work + personal). Swap the commented line if one is rate-limited.

## Database Schema

**opportunities** — One row per unique contract opportunity. Keyed on `notice_id`. Stores title, solicitation number, NAICS, PSC code, set-aside, agency hierarchy, dates, award info, place of performance, SAM.gov link, and the full raw API JSON.

**opportunity_offices** — Junction table linking opportunities to the office codes that surfaced them.

**pull_history** — Log of every pull attempt with counts and timing.

## Project Status

### Phase 1 — SAM.gov Puller ✅ Complete
- [x] Project scaffolding and config
- [x] SAM.gov API client with pagination and rate limiting
- [x] SQLite database schema designed for future matching system
- [x] Main puller script with incremental pulls (only fetches what's new since last run)
- [x] Client-side office filtering via `fullParentPathCode`
- [x] DB query and export utility
- [x] Cowork scheduled task running daily at 7am
- [x] Partial result saving if rate-limited mid-pull
- [x] 98 opportunities in database, 10-day backfill complete

### Phase 2 — AI Capability Matching 🔜 Next
- [ ] Extract capability profiles from past proposal PDFs in `ReefPoint_Claude/Proposals/`
- [ ] Build scoring engine using Claude to match new opportunities against profiles
- [ ] Daily output: ranked CSV of top-matching opportunities with match reasoning
- [ ] Future: Feed into ProposalHub dashboard

## Known Issues & Quirks

**4 offices return zero matches** — VA TAC Austin (36C10A), Air Force Medical Service (FA8052), Army Contracting Command (W6QK ACC-APG), National Guard Bureau Alabama/Virginia (W912JA, W912LQ). These may post infrequently, or their codes may appear differently in `fullParentPathCode`. Monitor over time.

**W6QK ACC-APG has a space in the code** — This may cause matching issues since `fullParentPathCode` segments are split on `.`. May need special handling.

**SAM.gov API rate limits** — The daily limit resets at midnight GMT. A typical daily run won't hit it (~3 API calls), but backfills of 10+ days will. Two API keys are configured in `config.yaml` — swap if one is limited. The rate limit `Retry-After` header is returned as a date string, not seconds, which is handled correctly in `sam_client.py`.

**First API page is slow** — The first request in a session can take 2-3 minutes due to SAM.gov server response time. Subsequent pages are fast (~1s each). This is normal.

## Next Phase

Phase 2 will add AI-powered capability matching: using Claude to read ReefPoint's past proposal PDFs, extract a capability profile, then score and rank each new SAM.gov opportunity against that profile. Daily output will be a CSV of top matches with an explanation of why each one matches. Located in `ReefPoint_Claude/Proposals/` (9 PDFs).
