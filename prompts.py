"""
Prompt templates for Claude API calls in the SAM.gov matching system.
"""

# =============================================================================
# Profile Extraction Prompt (one-time, used with Opus for max quality)
# =============================================================================
PROFILE_EXTRACTION_SYSTEM = """You are an expert government contracting analyst. Your job is to extract a structured capability profile from a past proposal document. Be thorough and specific — the output will be used to match against future contract opportunities on SAM.gov.

Extract ONLY information that is clearly stated or strongly implied in the document. Do not fabricate capabilities."""

PROFILE_EXTRACTION_USER = """Analyze the following government proposal document and extract a structured capability profile as JSON.

<proposal_text>
{proposal_text}
</proposal_text>

Return a JSON object with EXACTLY these fields:

{{
  "proposal_name": "Short descriptive name for this proposal (e.g., 'DHA VAULTIS Data Governance')",
  "service_areas": ["Array of capability/service area strings — what services does this proposal offer?"],
  "domains": ["Array of domain/agency focus areas — which agencies, departments, or sectors? (e.g., 'VA', 'DHA', 'DoD Health', 'Army')"],
  "naics_codes": ["Array of NAICS codes mentioned or strongly implied, with descriptions (e.g., '541512 - Computer Systems Design Services')"],
  "technical_competencies": ["Array of specific tools, platforms, certifications, methodologies, and technologies mentioned"],
  "past_performance": [
    {{
      "agency": "Agency name",
      "contract_name": "Contract or project name",
      "outcome": "Brief description of what was delivered/achieved"
    }}
  ],
  "differentiators": ["Array of competitive advantage statements — what makes this proposer uniquely qualified?"],
  "keywords": ["Flat array of searchable terms extracted from all fields above — include acronyms, technical terms, and domain-specific vocabulary"],
  "contract_vehicles": ["Any contract vehicles, IDIQs, GWACs, or BPAs mentioned"],
  "set_aside_qualifications": ["Any set-aside categories mentioned (e.g., 'SDVOSB', 'Small Business')"]
}}

Be comprehensive but accurate. If a field has no relevant information in the document, use an empty array. Return ONLY the JSON object, no markdown formatting or explanation."""


# =============================================================================
# Opportunity Scoring Prompt (daily, used with Sonnet for cost efficiency)
# =============================================================================
SCORING_SYSTEM = """You are a strict, skeptical government contracting opportunity evaluator. Your job is to identify the small number of opportunities that genuinely match a company's capabilities — not to find excuses to score things highly.

Most opportunities will NOT be a good match. A score above 60 should be rare and only awarded when the work being asked for closely mirrors what the company has actually delivered before.

CRITICAL RULE — "Domain" does NOT mean agency familiarity:
Domain score reflects whether the TYPE OF WORK matches the company's expertise. The fact that a company has done IT work for the VA does NOT mean they should score well on VA construction, VA food services, VA equipment leases, or VA medical staffing. Those are completely different industries. Domain alignment requires both the right agency AND the right type of work.

Scoring dimensions:
- Capability Match (50%): Does the SOW/description ask for services the company has actually delivered?
  - Score 80-100: The work is nearly identical to past performance (e.g., data governance, health IT analytics, ICAM)
  - Score 50-79: The work partially overlaps — some relevant skills but not the core ask
  - Score 20-49: The company has adjacent skills but has never done this type of work
  - Score 0-19: The work is in a completely different industry (construction, equipment, medical staffing, food service, etc.)

- NAICS Fit (25%): Is the NAICS code one the company has won under or closely adjacent to?
  - Score 80-100: Exact match to a NAICS code in the profile (541512, 541513)
  - Score 40-79: Adjacent IT/consulting NAICS (541, 518, 519xxx IT-related)
  - Score 0-39: Completely different sector (construction=236xxx, equipment=532xxx, medical staffing=621xxx, telecom hardware=517xxx, hotel=721xxx, etc.)

- Domain Alignment (15%): Does the issuing agency/office match AND is the work type relevant to the company's past client base?
  - Do NOT award high domain scores just because the agency is familiar. The WORK must also align.
  - Score 80-100: Right agency + right type of work
  - Score 40-79: Right agency but different type of work than past performance
  - Score 0-39: Wrong agency or work type has no connection to past performance

- Set-Aside Qualification (10%): Does the company meet the set-aside requirements?
  - Score 100: Unrestricted, or company qualifies (SDVOSB set-aside and company is SDVOSB)
  - Score 50: Small business set-aside (company may qualify but SDVOSB preference not recognized)
  - Score 0: Set-aside that excludes this company

The company is a SDVOSB (Service-Disabled Veteran-Owned Small Business) specializing in federal health IT, data governance, and analytics. They do NOT do construction, facilities, equipment leasing, medical staffing, food service, satellite hardware, or any non-IT work."""

SCORING_USER = """Score this SAM.gov opportunity against the company's capability profile. Be strict and skeptical.

FIRST, ask yourself: "What work is actually being asked for in this opportunity?" Answer that before scoring anything.

<capability_profile>
{capability_profile}
</capability_profile>

<opportunity>
Title: {opp_title}
Solicitation Number: {opp_sol_number}
Notice Type: {opp_notice_type}
NAICS Code: {opp_naics}
Set-Aside: {opp_set_aside}
Office: {opp_office}
Agency: {opp_agency}
Description: {opp_description}
Response Deadline: {opp_deadline}
</opportunity>

Scoring rules:
- If the NAICS code is in construction (236xxx), facilities (237xxx, 238xxx), equipment rental (532xxx), medical/dental staffing (621xxx), food service (722xxx), hotels (721xxx), telecom hardware (517xxx), or any other non-IT sector: capability_score MUST be 20 or below, domain_score MUST be 40 or below.
- Do NOT list generic technical competencies (Agile, CI/CD, ETL, SQL) as alignment factors unless the SOW explicitly asks for those things.
- Only cite past performance as a match if the work is genuinely similar — not just that it was for the same agency.
- If the opportunity description is vague or missing, score conservatively (lean toward 30-50 range, not 70+).

Return a JSON object with EXACTLY these fields:

{{
  "work_summary": "1 sentence describing what work is actually being asked for in this opportunity",
  "overall_score": <0-100 integer — weighted composite: capability 50% + naics 25% + domain 15% + set_aside 10%>,
  "domain_score": <0-100 integer>,
  "capability_score": <0-100 integer>,
  "naics_score": <0-100 integer>,
  "set_aside_fit": <0-100 integer>,
  "rationale": "2-3 sentence explanation focused on the actual work being asked for and whether the company can do it",
  "matched_profiles": ["Array of proposal_name strings where the past work is genuinely similar — empty array if none"],
  "key_alignment_factors": ["Specific, SOW-grounded reasons this is a match — no generic capability lists"],
  "risk_factors": ["Specific gaps or mismatches — be direct if it's a wrong-industry opportunity"]
}}

Return ONLY the JSON object, no markdown formatting or explanation."""

# =============================================================================
# V2 Scoring Prompt — uses retrieval results instead of full profile
# =============================================================================
SCORING_USER_V2 = """Score this SAM.gov opportunity against the company's matched capabilities. Be strict and skeptical.

FIRST, ask yourself: "What work is actually being asked for in this opportunity?" Answer that before scoring anything.

The capabilities below were selected by a retrieval system as the most relevant matches from the company's full capability corpus. Each record includes a relevance score (0-1). If the top matches have low relevance scores or seem tangential to the opportunity, that is a strong signal this is NOT a good fit — score accordingly.

<matched_capabilities>
{retrieved_context}
</matched_capabilities>

<opportunity>
Title: {opp_title}
Solicitation Number: {opp_sol_number}
Notice Type: {opp_notice_type}
NAICS Code: {opp_naics}
Set-Aside: {opp_set_aside}
Office: {opp_office}
Agency: {opp_agency}
Description: {opp_description}
Response Deadline: {opp_deadline}
</opportunity>

Scoring rules:
- If the NAICS code is in construction (236xxx), facilities (237xxx, 238xxx), equipment rental (532xxx), medical/dental staffing (621xxx), food service (722xxx), hotels (721xxx), telecom hardware (517xxx), or any other non-IT sector: capability_score MUST be 20 or below, domain_score MUST be 40 or below.
- Do NOT list generic technical competencies (Agile, CI/CD, ETL, SQL) as alignment factors unless the SOW explicitly asks for those things.
- Only cite a matched capability or past performance as alignment if it is genuinely similar to the work being asked for — not just because retrieval surfaced it.
- If the opportunity description is vague or missing, score conservatively (lean toward 30-50 range, not 70+).
- If no matched capabilities are relevant to the actual work, capability_score should be below 30.

Return a JSON object with EXACTLY these fields:

{{{{
  "work_summary": "1 sentence describing what work is actually being asked for in this opportunity",
  "overall_score": <0-100 integer — weighted composite: capability 50% + naics 25% + domain 15% + set_aside 10%>,
  "domain_score": <0-100 integer>,
  "capability_score": <0-100 integer>,
  "naics_score": <0-100 integer>,
  "set_aside_fit": <0-100 integer>,
  "rationale": "2-3 sentence explanation focused on the actual work being asked for and whether the company can do it",
  "matched_capabilities_used": ["Names of specific capability records from the matched list that are"""