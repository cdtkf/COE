"""
app.py — Streamlit dashboard for the SAM.gov puller.

Run locally from the repo root:
    streamlit run coe/dashboard/app.py
(coe is now a proper package via pyproject.toml, so PYTHONPATH=. is no
longer needed.)

Layout:
    KPI row (always visible)
    └── Tabs:
        - Overview        general breakdowns (depts, set-aside, notice, time)
        - NAICS           sector rollup + per-code drill-down
        - Offices         department→office hierarchy + per-office drill-down
        - All opps        full searchable opportunity table

Configuration: this app reads the Postgres connection string from the
DATABASE_URL environment variable. On Streamlit Cloud, the variable is
populated from `st.secrets["DATABASE_URL"]` by the small bridge below,
which must run BEFORE any import of `coe.database` (directly or via
`coe.dashboard.queries`). Locally, set DATABASE_URL in your shell or
.env and the bridge is a no-op.
"""
import os
from datetime import date, timedelta
from typing import Optional

import streamlit as st

# ---- Streamlit secrets → environment bridge ----
# Must run BEFORE the `from coe.dashboard import queries` line below,
# because importing queries triggers `coe.database` which reads
# DATABASE_URL at module load time.
try:
    if "DATABASE_URL" in st.secrets and "DATABASE_URL" not in os.environ:
        os.environ["DATABASE_URL"] = st.secrets["DATABASE_URL"]
except FileNotFoundError:
    # No .streamlit/secrets.toml present (e.g. local dev without secrets
    # file). The env var or coe.database's default will be used instead.
    pass
# ------------------------------------------------

from coe.dashboard import queries
from coe.dashboard.export import df_to_xlsx_bytes

XLSX_MIME = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

st.set_page_config(page_title="COE — Puller Dashboard", layout="wide")

# ---- Authentication gate ----
# Shared-password gate: if `password` is in Streamlit secrets, prompt
# for it once per session; otherwise (local dev) skip the gate and
# surface a warning banner so the lack of auth is visible.
#
# Why a shared password instead of SSO? Setting up Microsoft Entra ID
# OIDC requires Application Administrator access in the ReefPoint tenant,
# which we don't have. For a small internal team viewing already-public
# SAM.gov data filtered to our offices, a shared password is enough to
# keep random internet visitors out — which is the actual threat model.
# Upgrade to SSO later if/when IT can register an Entra app.
import hmac  # noqa: E402  (kept next to its single user for context)


def _password_configured() -> bool:
    """True iff Streamlit secrets carry a `password` key."""
    try:
        return "password" in st.secrets
    except FileNotFoundError:
        return False


def _check_password(entered: str) -> bool:
    """Constant-time compare against the configured password.

    `hmac.compare_digest` doesn't short-circuit on the first mismatched
    character, so an attacker can't learn the password byte-by-byte
    from timing differences. Overkill for a five-person internal tool,
    but it's the canonical Streamlit pattern and a good habit.
    """
    expected = st.secrets.get("password", "")
    return hmac.compare_digest(entered.encode(), expected.encode())


if _password_configured():
    if not st.session_state.get("password_correct"):
        st.title("Contract Opportunity Engine")
        st.write("Enter the dashboard password to continue.")
        entered = st.text_input(
            "Password",
            type="password",
            key="pw_input",
        )
        if entered:
            if _check_password(entered):
                st.session_state["password_correct"] = True
                st.rerun()
            else:
                st.error("Incorrect password.")
        st.stop()

    # Authenticated — small sign-out control in the sidebar.
    with st.sidebar:
        if st.button("Sign out", key="pw_logout"):
            st.session_state["password_correct"] = False
            st.rerun()
else:
    st.warning(
        "Running without authentication. This is expected on a local "
        "dev machine (no `password` in `.streamlit/secrets.toml`); "
        "do not deploy this app to the public internet in this state.",
        icon="⚠️",
    )
# ---- End auth gate ----

st.title("Contract Opportunity Engine — Puller Dashboard")
st.caption("Live view into what the SAM.gov puller has collected.")

# --- Sidebar: filters --------------------------------------------------
st.sidebar.header("Filters")

active_only = st.sidebar.checkbox(
    "Active opportunities only",
    value=False,
    help="Restrict every section below to opportunities with active='Yes'.",
)

use_date_filter = st.sidebar.checkbox("Filter by posted date")
date_from: Optional[str] = None
date_to: Optional[str] = None
if use_date_filter:
    today = date.today()
    default_from = today - timedelta(days=30)
    d_from = st.sidebar.date_input("Posted since", value=default_from)
    d_to   = st.sidebar.date_input("Posted until", value=today)
    date_from = d_from.isoformat() if d_from else None
    date_to   = d_to.isoformat()   if d_to   else None

st.sidebar.caption(
    "Filters apply to every KPI, table, and chart below. "
    "Results are cached for 60 seconds."
)

# Bundle filter state for concise call-sites.
F = {"active_only": active_only, "date_from": date_from, "date_to": date_to}

# --- KPI row -----------------------------------------------------------
col1, col2, col3, col4 = st.columns(4)

with col1:
    total = queries.get_total_opportunities(**F)
    st.metric(label="Total opportunities", value=f"{total:,}")

with col2:
    active = queries.get_active_opportunities(date_from=date_from, date_to=date_to)
    st.metric(label="Active", value=f"{active:,}")

with col3:
    departments = queries.get_departments_covered(**F)
    st.metric(label="Departments covered", value=f"{departments:,}")

with col4:
    latest = queries.get_latest_pull_timestamp()
    latest_display = "Never" if latest is None else latest.strftime("%b %d, %I:%M %p")
    st.metric(label="Latest pull", value=latest_display)

# --- Tabbed body -------------------------------------------------------
tab_overview, tab_naics, tab_offices, tab_opps = st.tabs(
    ["Overview", "NAICS", "Offices", "All opportunities"]
)

# ----- Overview tab ----------------------------------------------------
with tab_overview:
    st.subheader("Breakdowns")

    row1_left, row1_right = st.columns(2)
    with row1_left:
        st.caption("Top 10 departments by opportunity count")
        st.bar_chart(queries.get_opportunities_by_department(**F), x="label", y="count")
    with row1_right:
        st.caption("Top 10 NAICS codes — see the NAICS tab for sector rollup + drill-down")
        st.bar_chart(queries.get_opportunities_by_naics(**F), x="label", y="count")

    row2_left, row2_right = st.columns(2)
    with row2_left:
        st.caption("Opportunities by set-aside type")
        st.bar_chart(queries.get_opportunities_by_set_aside(**F), x="label", y="count")
    with row2_right:
        st.caption("Opportunities by notice type")
        st.bar_chart(queries.get_opportunities_by_notice_type(**F), x="label", y="count")

    st.caption("Opportunities posted over time")
    st.line_chart(queries.get_opportunities_posted_by_day(**F), x="date", y="count")


# ----- NAICS tab -------------------------------------------------------
with tab_naics:
    st.subheader("By NAICS sector")
    st.caption(
        "Rolled up to the 2-digit NAICS sector — a cleaner top-level view than "
        "looking at every individual code."
    )

    sector_df = queries.get_opportunities_by_naics_sector(**F)
    if sector_df.empty:
        st.info("No opportunities with NAICS codes match your filters.")
    else:
        # Show as a table with an inline progress bar — readable labels
        # plus a visual scale, without horizontal-bar-chart gymnastics.
        sector_max = int(sector_df["count"].max())
        st.dataframe(
            sector_df[["label", "count"]],
            use_container_width=True,
            hide_index=True,
            column_config={
                "label": st.column_config.TextColumn("Sector", width="large"),
                "count": st.column_config.ProgressColumn(
                    "Opportunities",
                    format="%d",
                    min_value=0,
                    max_value=sector_max,
                ),
            },
        )
        st.download_button(
            label="Download as XLSX",
            data=df_to_xlsx_bytes(sector_df, sheet_name="NAICS sectors"),
            file_name="coe-naics-sectors.xlsx",
            mime=XLSX_MIME,
            key="dl_naics_sectors",
        )

    st.divider()
    st.subheader("By NAICS Codes")

    codes_df = queries.get_naics_codes_with_counts(**F)
    if codes_df.empty:
        st.info("No NAICS codes available with the current filters.")
    else:
        # The 'display' column already includes code + sector + count, so
        # the picker is self-explanatory without extra surrounding text.
        chosen_display = st.selectbox(
            "Pick a NAICS code",
            options=codes_df["display"].tolist(),
            index=0,
        )
        chosen_code = codes_df.loc[
            codes_df["display"] == chosen_display, "naics_code"
        ].iloc[0]

        opps_for_code = queries.get_opportunities_for_naics_code(chosen_code, **F)
        st.caption(
            f"{len(opps_for_code):,} opportunit"
            f"{'y' if len(opps_for_code) == 1 else 'ies'} for NAICS {chosen_code}"
        )
        st.dataframe(
            opps_for_code,
            use_container_width=True,
            hide_index=True,
            column_config={
                "title": st.column_config.TextColumn("Title", width="large"),
                "department": "Department",
                "office": "Office",
                "set_aside_type": "Set-aside",
                "posted_date": st.column_config.DateColumn("Posted", format="MMM D, YYYY"),
                "response_deadline": st.column_config.DatetimeColumn(
                    "Response due", format="MMM D, YYYY"
                ),
                "active": "Active?",
                "description_url": st.column_config.LinkColumn(
                    "SAM.gov link", display_text="Open"
                ),
            },
        )
        st.download_button(
            label="Download as XLSX",
            data=df_to_xlsx_bytes(
                opps_for_code, sheet_name=f"NAICS {chosen_code}"
            ),
            file_name=f"coe-naics-{chosen_code}-opps.xlsx",
            mime=XLSX_MIME,
            key=f"dl_naics_code_{chosen_code}",
        )


# ----- Offices tab -----------------------------------------------------
with tab_offices:
    st.subheader("By department")
    st.caption(
        "Each department's total opportunities and how many distinct offices "
        "have posted at least one."
    )

    dept_df = queries.get_opportunities_by_department_summary(**F)
    if dept_df.empty:
        st.info("No opportunities match your filters.")
    else:
        dept_max = int(dept_df["opps_count"].max())
        st.dataframe(
            dept_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "department": st.column_config.TextColumn("Department", width="large"),
                "opps_count": st.column_config.ProgressColumn(
                    "Total opps", format="%d", min_value=0, max_value=dept_max
                ),
                "active_opps": st.column_config.NumberColumn("Active", format="%d"),
                "office_count": st.column_config.NumberColumn("Offices", format="%d"),
            },
        )
        st.download_button(
            label="Download as XLSX",
            data=df_to_xlsx_bytes(dept_df, sheet_name="Departments"),
            file_name="coe-departments.xlsx",
            mime=XLSX_MIME,
            key="dl_departments",
        )

    st.divider()
    st.subheader("Drill into a department → office")

    hierarchy_df = queries.get_department_office_hierarchy(**F)
    if hierarchy_df.empty:
        st.info("No offices to show with the current filters.")
    else:
        departments = sorted(hierarchy_df["department"].unique().tolist())
        chosen_dept = st.selectbox("Department", options=departments, index=0)

        offices_in_dept = hierarchy_df[hierarchy_df["department"] == chosen_dept].copy()
        st.caption(
            f"{len(offices_in_dept):,} office"
            f"{'s' if len(offices_in_dept) != 1 else ''} in {chosen_dept}"
        )

        # Show all offices in this department as a quick-reference table.
        offices_max = int(offices_in_dept["opps_count"].max()) if not offices_in_dept.empty else 1
        st.dataframe(
            offices_in_dept[["office", "office_code", "opps_count", "active_opps"]],
            use_container_width=True,
            hide_index=True,
            column_config={
                "office": st.column_config.TextColumn("Office", width="large"),
                "office_code": "Code",
                "opps_count": st.column_config.ProgressColumn(
                    "Total opps", format="%d", min_value=0, max_value=offices_max
                ),
                "active_opps": st.column_config.NumberColumn("Active", format="%d"),
            },
        )
        st.download_button(
            label="Download as XLSX",
            data=df_to_xlsx_bytes(
                offices_in_dept[["office", "office_code", "opps_count", "active_opps"]],
                sheet_name="Offices",
            ),
            file_name=f"coe-offices-{chosen_dept[:30].replace(' ', '_')}.xlsx",
            mime=XLSX_MIME,
            key="dl_offices_in_dept",
        )

        # Office picker → opportunities.
        # Build labels that include the count so you can see size before picking.
        offices_in_dept["picker_label"] = offices_in_dept.apply(
            lambda r: f"{r['office']} ({r['opps_count']} opps)"
            + (f" — {r['office_code']}" if r['office_code'] else ""),
            axis=1,
        )
        chosen_office_label = st.selectbox(
            "Pick an office",
            options=offices_in_dept["picker_label"].tolist(),
            index=0,
        )
        chosen_row = offices_in_dept[
            offices_in_dept["picker_label"] == chosen_office_label
        ].iloc[0]

        # Prefer office_code (unique). Fall back to office name when code is blank.
        office_code = chosen_row["office_code"] or None
        office_name = chosen_row["office"] if not office_code else None

        opps_for_office = queries.get_opportunities_for_office(
            office_code=office_code, office_name=office_name, **F
        )
        st.caption(
            f"{len(opps_for_office):,} opportunit"
            f"{'y' if len(opps_for_office) == 1 else 'ies'} for "
            f"{chosen_row['office']}"
        )
        st.dataframe(
            opps_for_office,
            use_container_width=True,
            hide_index=True,
            column_config={
                "title": st.column_config.TextColumn("Title", width="large"),
                "department": "Department",
                "office": "Office",
                "naics_code": "NAICS",
                "set_aside_type": "Set-aside",
                "posted_date": st.column_config.DateColumn("Posted", format="MMM D, YYYY"),
                "response_deadline": st.column_config.DatetimeColumn(
                    "Response due", format="MMM D, YYYY"
                ),
                "active": "Active?",
                "description_url": st.column_config.LinkColumn(
                    "SAM.gov link", display_text="Open"
                ),
            },
        )
        # Office name can contain spaces / slashes — keep filenames sane.
        _office_slug = (
            (chosen_row["office_code"] or chosen_row["office"])
            .replace("/", "_")
            .replace(" ", "_")[:40]
        )
        st.download_button(
            label="Download as XLSX",
            data=df_to_xlsx_bytes(opps_for_office, sheet_name="Office opps"),
            file_name=f"coe-office-{_office_slug}-opps.xlsx",
            mime=XLSX_MIME,
            key="dl_office_opps",
        )

    # Tucked-away coverage table — same data as before, just out of the way.
    with st.expander("Office coverage (puller diagnostics)"):
        st.caption(
            "Which office-code queries have surfaced opportunities. Useful for "
            "spotting offices the puller hasn't seen activity from recently."
        )
        offices_df = queries.get_office_coverage(**F)
        st.dataframe(
            offices_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "office_code": "Code",
                "office_name": "Office",
                "opps_count": st.column_config.NumberColumn("Total opps", format="%d"),
                "active_opps": st.column_config.NumberColumn("Active opps", format="%d"),
                "first_discovered_at": st.column_config.DatetimeColumn(
                    "First discovered", format="MMM D, YYYY"
                ),
                "last_activity_at": st.column_config.DatetimeColumn(
                    "Last activity", format="MMM D, h:mm A"
                ),
            },
        )
        st.download_button(
            label="Download as XLSX",
            data=df_to_xlsx_bytes(offices_df, sheet_name="Office coverage"),
            file_name="coe-office-coverage.xlsx",
            mime=XLSX_MIME,
            key="dl_office_coverage",
        )


# ----- All opportunities tab ------------------------------------------
with tab_opps:
    st.subheader("Opportunities")
    opps_df = queries.get_all_opportunities(**F)
    st.caption(f"{len(opps_df):,} rows — sort, search, or resize columns as needed.")
    st.dataframe(
        opps_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "description_url": st.column_config.LinkColumn(
                "SAM.gov link", display_text="Open"
            ),
        },
    )
    st.download_button(
        label="Download as XLSX",
        data=df_to_xlsx_bytes(opps_df, sheet_name="All opportunities"),
        file_name="coe-all-opportunities.xlsx",
        mime=XLSX_MIME,
        key="dl_all_opps",
    )
