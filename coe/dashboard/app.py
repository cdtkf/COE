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