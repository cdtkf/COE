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
total = queries.get_total_opportunities()
st.metric(label="Total opportunities", value=f"{total:,}")