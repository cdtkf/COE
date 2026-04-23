"""
app.py —> Streamlit dashboard for the SAM.gov puller.

Run from the repo root:
    PYTHONPATH=. streamlit run coe/dashboard/app.py
"""
import streamlit as st

from coe.dashboard import queries

st.set_page_config(page_title="COE — Puller Dashboard", layout="wide")

st.title("Contract Opportunity Engine — Puller Dashboard")
st.caption("Live view into what the SAM.gov puller has collected.")

# --- KPI row ----------------------------------------------------------- 
col1, col2, col3, col4 = st.columns(4)

with col1:
    total = queries.get_total_opportunities()
    st.metric(label="Total opportunities", value=f"{total:,}")

with col2:
    active = queries.get_active_opportunities()
    st.metric(label="Active", value=f"{active:,}")

with col3:
    departments = queries.get_departments_covered()
    st.metric(label="Departments covered", value=f"{departments:,}")

with col4:
    latest = queries.get_latest_pull_timestamp()
    if latest is None:
        latest_display = "Never"
    else:
        latest_display = latest.strftime("%b %d, %I:%M %p")
    st.metric(label="Last pull", value=latest_display)

# --- Opportunity browser -----------------------------------------------------------
st.divider()
st.header("Opportunities")

opps_df = queries.get_all_opportunities()
st.caption(f"{len(opps_df):,} rows - sort, search, or resize columns as needed.")

st.dataframe(
    opps_df,
    use_container_width=True,
    hide_index=True,
    column_config={
        "description_url": st.column_config.LinkColumn(
            "SAM.gov link", 
            display_text="Open", 
        ), 
    },
)

# --- Breakdowns -----------------------------------------------------------
st.divider()
st.subheader("Breakdowns")

# Row 1: top departments and top NAICS
row1_left, row1_right = st.columns(2)
with row1_left:
    st.caption("Top 10 departments by opportunity count")
    dept_df = queries.get_opportunities_by_department()
    st.bar_chart(dept_df, x="label", y="count", x_label="Dept. Codes", y_label="Active opportunities")
with row1_right:
    st.caption("Top 10 NAICS codes by opportunity count")
    naics_df = queries.get_opportunities_by_naics()
    st.bar_chart(naics_df, x="label", y="count", x_label="NAICS Codes", y_label="Active opportunities")

# Row 2: set-aside and notice type
row2_left, row2_right = st.columns(2)
with row2_left:
    st.caption("Opportunities by set-aside type")
    sa_df = queries.get_opportunities_by_set_aside()
    st.bar_chart(sa_df, x="label", y="count", x_label="Set-Aside Types", y_label="Active opportunities")
with row2_right:
    st.caption("Opportunities by notice type")
    nt_df = queries.get_opportunities_by_notice_type()
    st.bar_chart(nt_df, x="label", y="count", x_label="Notice Types", y_label="Active opportunities")

# Trend: posted-date timeline
st.caption("Opportunities posted over time")
trend_df = queries.get_opportunities_posted_by_day()
st.line_chart(trend_df, x="date", y="count")


# --- Office coverage ---------------------------------------------------

st.divider()
st.subheader("Office coverage")

offices_df = queries.get_office_coverage()
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
            "First discovered",
            format="MMM D, YYYY",
        ),
        "last_activity_at": st.column_config.DatetimeColumn(
            "Last activity",
            format="MMM D, h:mm A",
        ),
    },
)