"""U.S. Defense Spending: Trend & Forecast — web app.

This is the browser-based counterpart to the Windows desktop app. It uses
the same forecasting engine (core.py) — the ensemble model, regime-aware
training window, CBO baseline blending, Monte Carlo bands, and scenario
adjustment are all identical to the desktop version. Only the interface is
different: this runs as a Streamlit app, so anyone with the link opens it in
a normal web browser with nothing to install.

Run locally with:  streamlit run app.py
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import core

st.set_page_config(page_title="U.S. Defense Spending: Trend & Forecast", layout="wide")


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def load_data():
    treasury_rows, current, monthly, source_status = core.fetch_data()
    omb_rows = core.load_omb_history()
    annual_rows = core.merge_annual_history(omb_rows, treasury_rows)
    policy_data = core.load_policy_assumptions()
    try:
        api_key = core.load_congress_api_key(dict(st.secrets) if hasattr(st, "secrets") else None)
        bill_updates = core.refresh_bill_tracking(policy_data, api_key)
    except Exception:
        bill_updates = [{**tracker, "live_status": None} for tracker in policy_data.get("bill_tracking", [])]
    return annual_rows, current, monthly, source_status, policy_data, bill_updates


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def run_analysis(annual_rows, forecast_years, policy_data, scenario):
    return core.analyze(annual_rows, forecast_years, policy_data, scenario)


st.title("U.S. Defense Spending: Trend & Forecast")
st.caption("Treasury National Defense outlays by federal fiscal year, with a regime-aware ensemble forecast blended with congressional/CBO figures.")

with st.spinner("Loading Treasury and OMB data..."):
    annual_rows, current, monthly, source_status, policy_data, bill_updates = load_data()

status_color = "green" if source_status.startswith("Live") else "orange"
st.markdown(f":{status_color}[{source_status}]")

years = [str(year) for year, _ in annual_rows]
scenario_keys = list(policy_data.get("scenarios", {}).keys()) or ["baseline"]

with st.sidebar:
    st.header("Graph range")
    default_start_index = max(0, len(years) - core.HISTORY_YEARS)
    start_year = st.selectbox("Start fiscal year", years, index=default_start_index)
    forecast_years = st.slider("Forecast years", min_value=1, max_value=10, value=5)
    scenario = st.selectbox(
        "Scenario",
        scenario_keys,
        format_func=lambda key: core.SCENARIO_LABELS.get(key, key),
    )
    if st.button("Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

actual, forecast, model_info, policy_notes = run_analysis(annual_rows, forecast_years, policy_data, scenario)
actual_visible = [p for p in actual if p.year >= int(start_year)]

first, latest = actual_visible[0], actual_visible[-1]
change_pct = (latest.value / first.value - 1.0) * 100.0

col1, col2, col3, col4 = st.columns(4)
col1.metric("Last full fiscal year", core.money(latest.value), f"FY {latest.year}")
col2.metric("Current fiscal year to date", core.money(current[1]), f"FY {current[0]}")
col3.metric("Selected range change", f"{change_pct:+.1f}%")
col4.metric("Forecast endpoint", core.money(forecast[-1].value), f"FY {forecast[-1].year}")

# --- Chart ---
fig = go.Figure()

band_years = [actual_visible[-1].year] + [p.year for p in forecast]
band_upper = [actual_visible[-1].value] + [p.upper for p in forecast]
band_lower = [actual_visible[-1].value] + [p.lower for p in forecast]
fig.add_trace(go.Scatter(
    x=band_years + band_years[::-1],
    y=band_upper + band_lower[::-1],
    fill="toself", fillcolor="rgba(245, 158, 11, 0.20)",
    line=dict(width=0), hoverinfo="skip", showlegend=False, name="95% range",
))

fig.add_trace(go.Scatter(
    x=[p.year for p in actual_visible], y=[p.value for p in actual_visible],
    mode="lines+markers", name="Actual", line=dict(color="#38BDF8", width=3),
))
fig.add_trace(go.Scatter(
    x=[actual_visible[-1].year] + [p.year for p in forecast],
    y=[actual_visible[-1].value] + [p.value for p in forecast],
    mode="lines+markers", name="Forecast", line=dict(color="#F59E0B", width=3, dash="dash"),
))
fig.update_layout(
    height=500, margin=dict(l=10, r=10, t=30, b=10),
    yaxis_title="USD billions", xaxis_title="Fiscal year",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    hovermode="x unified",
)
st.plotly_chart(fig, use_container_width=True)

# --- Model and policy panels ---
left, right = st.columns(2)
with left:
    st.subheader("Model (regime-aware ensemble)")
    st.markdown(core.format_model_panel(model_info))
with right:
    st.subheader("Policy & real-world adjustments")
    st.markdown(core.format_policy_panel(policy_notes, bill_updates, policy_data, scenario))

# --- Tables ---
st.subheader("Yearly data")
table_rows = (
    [{"Year": p.year, "USD billions": round(p.value, 1), "Type": p.kind} for p in actual_visible]
    + [{"Year": current[0], "USD billions": round(current[1], 1), "Type": "FYTD (partial)"}]
    + [{"Year": p.year, "USD billions": round(p.value, 1), "Type": p.kind} for p in forecast]
)
st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)

with st.expander("Monthly detail (current fiscal year and forecast allocation)"):
    monthly_forecasts = core.forecast_monthly_values(forecast, monthly)
    current_year_months = sorted(
        [m for m in monthly if m.fiscal_year == current[0]], key=lambda m: m.record_date
    )
    st.markdown(f"**FY {current[0]} actuals to date**")
    st.dataframe(
        pd.DataFrame(
            [{"Month": f"{m.month_label} {m.record_date[:4]}", "USD billions": round(m.amount, 2), "FYTD total": round(m.fytd, 1)} for m in current_year_months]
        ),
        use_container_width=True, hide_index=True,
    )
    for point in forecast:
        st.markdown(f"**FY {point.year} forecast allocation**")
        st.dataframe(
            pd.DataFrame(
                [{"Month": label, "USD billions": round(value, 2)} for label, value in monthly_forecasts.get(point.year, [])]
            ),
            use_container_width=True, hide_index=True,
        )

csv_bytes = pd.DataFrame(table_rows).to_csv(index=False).encode("utf-8")
st.download_button("Export yearly data as CSV", csv_bytes, file_name="GovernmentDefenceSpending_Analysis.csv", mime="text/csv")
