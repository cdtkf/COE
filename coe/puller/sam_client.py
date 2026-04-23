"""
SAM.gov Contract Opportunities API Client

Handles authentication, querying, pagination, rate limiting, and
client-side office filtering against the SAM.gov Opportunities API v2.

Note: The SAM.gov API does not support server-side filtering by office code.
We pull all recent opportunities and filter client-side by matching the
office code against the fullParentPathCode field in the response.
"""

import time
import logging
from datetime import datetime, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# SAM.gov Opportunities API v2
BASE_URL = "https://api.sam.gov/opportunities/v2/search"

# All notice types: solicitation, presolicitation, sources sought,
# combined synopsis, intent to bundle, special notice, sale of surplus
NOTICE_TYPES = "o,p,r,k,i,s,g"


class SAMClientError(Exception):
    """Raised when the SAM.gov API returns an error."""
    pass


class SAMClient:
    """Client for the SAM.gov Contract Opportunities API."""

    def __init__(self, api_key: str, request_delay: float = 1.0):
        """
        Args:
            api_key: Your SAM.gov API key.
            request_delay: Seconds to wait between API requests.
        """
        self.api_key = api_key
        self.request_delay = request_delay
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
        })
        self._last_request_time = 0.0

    def _throttle(self):
        """Enforce delay between API calls to respect rate limits."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.request_delay:
            time.sleep(self.request_delay - elapsed)
        self._last_request_time = time.time()

    def _make_request(self, params: dict) -> dict:
        """
        Make a single API request with error handling and retry on 429.

        Args:
            params: Query parameters for the API call.

        Returns:
            Parsed JSON response dict.
        """
        self._throttle()

        # API key goes as a query parameter
        params["api_key"] = self.api_key

        try:
            response = self.session.get(BASE_URL, params=params, timeout=60)
        except requests.RequestException as e:
            raise SAMClientError(f"Network error calling SAM.gov API: {e}")

        # Handle rate limiting with retry
        if response.status_code == 429:
            retry_header = response.headers.get("Retry-After", "60")
            try:
                # Try parsing as seconds first
                retry_seconds = int(retry_header)
            except ValueError:
                # It's a date string like "Tue, 31 Mar 2026 00:00:00 GMT"
                try:
                    from email.utils import parsedate_to_datetime
                    retry_dt = parsedate_to_datetime(retry_header)
                    retry_seconds = max(1, int((retry_dt - datetime.now(retry_dt.tzinfo)).total_seconds()))
                except Exception:
                    retry_seconds = 60  # Safe fallback
            # If the wait is more than 5 minutes, don't block — raise so caller can
            # handle partial results
            if retry_seconds > 300:
                raise SAMClientError(
                    f"Rate limited by SAM.gov — retry after {retry_seconds}s "
                    f"({retry_seconds // 3600}h {(retry_seconds % 3600) // 60}m). "
                    f"Try again later or use a different API key."
                )
            logger.warning(f"Rate limited by SAM.gov. Waiting {retry_seconds}s...")
            time.sleep(retry_seconds)
            return self._make_request(params)

        if response.status_code != 200:
            raise SAMClientError(
                f"SAM.gov API returned {response.status_code}: {response.text[:500]}"
            )

        try:
            return response.json()
        except ValueError:
            raise SAMClientError(f"Invalid JSON response from SAM.gov: {response.text[:500]}")

    def fetch_recent_opportunities(
        self,
        posted_from: datetime,
        posted_to: Optional[datetime] = None,
        limit: int = 10000,
    ) -> tuple[list[dict], int]:
        """
        Fetch all recent contract opportunities within a date range.
        Handles pagination automatically.

        Args:
            posted_from: Only return opportunities posted on or after this date.
            posted_to: Only return opportunities posted on or before this date.
                        Defaults to today.
            limit: Max total opportunities to return.

        Returns:
            Tuple of (list of opportunity dicts, total records available).
        """
        if posted_to is None:
            posted_to = datetime.now()

        # API date format: MM/dd/yyyy
        date_from = posted_from.strftime("%m/%d/%Y")
        date_to = posted_to.strftime("%m/%d/%Y")

        all_opportunities = []
        offset = 0
        page_size = 1000  # API max per page
        total_records = 0

        logger.info(f"Fetching all opportunities from {date_from} to {date_to}")

        while offset < limit:
            params = {
                "postedFrom": date_from,
                "postedTo": date_to,
                "ntype": NOTICE_TYPES,
                "limit": page_size,
                "offset": offset,
            }

            try:
                data = self._make_request(params)
            except SAMClientError as e:
                if all_opportunities:
                    logger.warning(
                        f"API error at offset {offset}, returning {len(all_opportunities)} "
                        f"partial results: {e}"
                    )
                    return all_opportunities, total_records
                raise

            opportunities = data.get("opportunitiesData", [])
            total_records = data.get("totalRecords", 0)

            if not opportunities:
                break

            all_opportunities.extend(opportunities)

            logger.info(
                f"  Fetched {len(all_opportunities)}/{total_records} total opportunities "
                f"(page at offset {offset})"
            )

            # Stop if we've gotten everything or hit our limit
            if len(all_opportunities) >= total_records:
                break
            if len(all_opportunities) >= limit:
                all_opportunities = all_opportunities[:limit]
                break

            offset += page_size

        logger.info(f"Total fetched: {len(all_opportunities)} of {total_records} available")
        return all_opportunities, total_records

    @staticmethod
    def match_office(opportunity: dict, office_codes: set[str]) -> list[str]:
        """
        Check if an opportunity belongs to any of the tracked offices.
        Matches against fullParentPathCode which contains the org hierarchy.

        Example fullParentPathCode:
            "097.97AS.DLA AVIATION.DLA AV RICHMOND.SPE4A6"
        The office code is typically the last segment, but we check all
        segments to be safe.

        Args:
            opportunity: Raw opportunity dict from the API.
            office_codes: Set of office codes to match against.

        Returns:
            List of matching office codes (usually 1, but could be multiple
            if the org hierarchy contains multiple tracked codes).
        """
        path_code = opportunity.get("fullParentPathCode", "")
        if not path_code:
            return []

        # Split the path into segments and check each against our office codes
        segments = path_code.split(".")
        matched = []
        for code in office_codes:
            for segment in segments:
                if code == segment or segment.startswith(code) or code in segment:
                    matched.append(code)
                    break

        return matched

    def parse_opportunity(self, raw: dict) -> dict:
        """
        Normalize a raw API opportunity into a clean dict for storage.
        Extracts the fields we care about and handles missing data gracefully.

        Args:
            raw: Raw opportunity dict from the API response.

        Returns:
            Cleaned opportunity dict ready for database insertion.
        """
        # Extract award info (nested structure)
        award = raw.get("award") or {}
        awardee = award.get("awardee") or {}

        # Extract place of performance (nested, can be None)
        pop = raw.get("placeOfPerformance") or {}
        pop_city = pop.get("city") or {}
        pop_state = pop.get("state") or {}

        # Extract the office code from fullParentPathCode (last segment)
        path_code = raw.get("fullParentPathCode", "")
        office_code = path_code.split(".")[-1] if path_code else ""

        return {
            "notice_id": raw.get("noticeId", ""),
            "title": raw.get("title", ""),
            "solicitation_number": raw.get("solicitationNumber", ""),
            "full_parent_path_name": raw.get("fullParentPathName", ""),
            "full_parent_path_code": path_code,
            "department": path_code.split(".")[0] if path_code else "",
            "sub_tier": raw.get("subTier", ""),
            "office": raw.get("office", ""),
            "office_code": office_code,
            "posted_date": raw.get("postedDate", ""),
            "response_deadline": raw.get("responseDeadLine", ""),
            "archive_date": raw.get("archiveDate", ""),
            "naics_code": raw.get("naicsCode", ""),
            "classification_code": raw.get("classificationCode", ""),
            "set_aside_type": raw.get("typeOfSetAsideDescription", ""),
            "set_aside_code": raw.get("typeOfSetAside", ""),
            "notice_type": raw.get("type", ""),
            "base_type": raw.get("baseType", ""),
            "description_url": raw.get("uiLink", ""),
            "award_number": award.get("number", ""),
            "award_amount": award.get("amount"),
            "awardee_name": awardee.get("name", ""),
            "place_of_performance_city": pop_city.get("name", "")
                if isinstance(pop_city, dict) else "",
            "place_of_performance_state": pop_state.get("code", "")
                if isinstance(pop_state, dict) else "",
            "active": raw.get("active", ""),
            "raw_json": raw,  # Store the full response for future matching needs
        }
