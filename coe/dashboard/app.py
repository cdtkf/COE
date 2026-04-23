"""
app.py — Streamlit dashboard for the SAM.gov puller.

Run from the repo root:
    PYTHONPATH=. streamlit run coe/dashboard/app.py
"""
from datetime import date, timedelta
from typing import Optional

import streamlit as st

from coe.dashboard import queries

st.set_page_config(page_title="COE — Puller Dashboard", layout="wide")

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

# --- Opportunity browser -----------------------------------------------
st.divider()
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

# --- Breakdowns --------------------------------------------------------
st.divider()
st.subheader("Breakdowns")

row1_left, row1_right = st.columns(2)
with row1_left:
    st.caption("Top 10 departments by opportunity count")
    st.bar_chart(queries.get_opportunities_by_department(**F), x="label", y="count")
with row1_right:
    st.caption("Top 10 NAICS codes by opportunity count")
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

# --- Office coverage ---------------------------------------------------
st.divider()
st.subheader("Office coverage")
offices_df = queries.get_office_coverage(**F)
st.caption(
    f"{len(offices_df):,} offices have surfaced at least one opportunity — "
    "sorted by total opp count."
)
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