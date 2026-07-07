"""U.S. Defense Spending: Trend & Forecast — web app."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import core

st.set_page_config(page_title="U.S. Defense Spending: Trend & Forecast", layout="wide")

st.markdown(
    """
    <style>
    .stApp { background-color: #07101D; }
    [data-testid="stSidebar"] { background-color: #0B1220; border-right: 1px solid #243248; }
    [data-testid="stSidebar"] h2 { color: #F8FAFC; font-size: 1rem; }
    .eyebrow {
        color: #38BDF8; font-weight: 600; font-size: 0.78rem;
        letter-spacing: 0.08em; text-transform: uppercase; margin-bottom: 2px;
    }
    .app-title { font-size: 2.1rem; font-weight: 700; color: #F8FAFC; margin: 0 0 4px 0; }
    .app-subtitle { color: #94A3B8; font-size: 0.95rem; margin: 0 0 6px 0; }
    div[data-testid="stMetric"] {
        background-color: #101B2D; border: 1px solid #243248; border-radius: 8px; padding: 14px 16px 10px 16px;
    }
    div[data-testid="stMetricLabel"] > div {
        color: #8291A7 !important; font-size: 0.72rem !important;
        text-transform: uppercase; letter-spacing: 0.05em; font-weight: 600;
    }
    div[data-testid="stMetricValue"] { color: #F8FAFC !important; }
    div[data-testid="stMetricDelta"] { color: #4ADE80 !important; }
    .stTabs [data-baseweb="tab-list"] { gap: 4px; border-bottom: 1px solid #243248; }
    .stTabs [data-baseweb="tab"] {
        background-color: transparent; border-radius: 6px 6px 0 0;
        color: #94A3B8; padding: 10px 22px; font-weight: 600;
    }
    .stTabs [aria-selected="true"] { background-color: #1E293B !important; color: #F8FAFC !important; }
    .panel-heading {
        color: #CBD5E1; font-weight: 700; font-size: 0.85rem;
        text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 10px;
    }
    .status-line { font-size: 0.85rem; margin-bottom: 4px; }
    </style>
    """,
    unsafe_allow_html=True,
)


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


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def load_category_breakdown(fiscal_year):
    try:
        by_type, by_branch, accounts = core.fetch_federal_account_breakdown(fiscal_year)
        return by_type, by_branch, accounts, None
    except Exception as exc:
        return {}, {}, [], str(exc)


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def load_naics_breakdown(fiscal_year):
    try:
        return core.fetch_naics_breakdown(fiscal_year), None
    except Exception as exc:
        return [], str(exc)


st.markdown('<div class="eyebrow">U.S. DEFENSE SPENDING</div>', unsafe_allow_html=True)
st.markdown('<p class="app-title">Historical Trend and Adjustable Forecast</p>', unsafe_allow_html=True)
st.markdown('<p class="app-subtitle">Treasury National Defense outlays by federal fiscal year</p>', unsafe_allow_html=True)

with st.spinner("Loading Treasury and OMB data..."):
    annual_rows, current, monthly, source_status, policy_data, bill_updates = load_data()

status_color = "#4ADE80" if source_status.startswith("Live") else "#F59E0B"
st.markdown(f'<div class="status-line" style="color:{status_color};">{source_status}</div>', unsafe_allow_html=True)

years = [str(year) for year, _ in annual_rows]
scenario_keys = list(policy_data.get("scenarios", {}).keys()) or ["baseline"]

with st.sidebar:
    st.header("Graph range")
    default_start_index = max(0, len(years) - core.HISTORY_YEARS)
    start_year = st.selectbox("Start fiscal year", years, index=default_start_index)
    forecast_years = st.slider("Forecast years", min_value=1, max_value=10, value=5)
    scenario = st.selectbox("Scenario", scenario_keys, format_func=lambda key: core.SCENARIO_LABELS.get(key, key))
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

st.write("")
tab_chart, tab_table, tab_categories = st.tabs(["Trend chart", "Data table", "Spending categories"])

with tab_chart:
    fig = go.Figure()
    band_years = [actual_visible[-1].year] + [p.year for p in forecast]
    band_upper = [actual_visible[-1].value] + [p.upper for p in forecast]
    band_lower = [actual_visible[-1].value] + [p.lower for p in forecast]
    fig.add_trace(go.Scatter(
        x=band_years + band_years[::-1], y=band_upper + band_lower[::-1],
        fill="toself", fillcolor="rgba(245, 158, 11, 0.18)",
        line=dict(width=0), hoverinfo="skip", showlegend=False, name="95% range",
    ))
    fig.add_trace(go.Scatter(
        x=[p.year for p in actual_visible], y=[p.value for p in actual_visible],
        mode="lines+markers", name="Actual", line=dict(color="#38BDF8", width=3), marker=dict(size=6, color="#38BDF8"),
    ))
    fig.add_trace(go.Scatter(
        x=[actual_visible[-1].year] + [p.year for p in forecast],
        y=[actual_visible[-1].value] + [p.value for p in forecast],
        mode="lines+markers", name="Forecast", line=dict(color="#F59E0B", width=3, dash="dash"), marker=dict(size=6, color="#F59E0B"),
    ))
    fig.update_layout(
        height=480, margin=dict(l=10, r=10, t=20, b=10),
        paper_bgcolor="#0B1220", plot_bgcolor="#0B1220",
        font=dict(color="#A9B6C9", family="Segoe UI, Arial, sans-serif", size=12),
        yaxis=dict(title="USD billions", gridcolor="#263348", zerolinecolor="#263348"),
        xaxis=dict(title="Fiscal year", gridcolor="#263348", zerolinecolor="#263348"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(color="#CBD5E1")),
        hovermode="x unified",
    )
    with st.container(border=True):
        st.plotly_chart(fig, use_container_width=True)

    st.write("")
    left, right = st.columns(2)
    with left:
        with st.container(border=True):
            st.markdown('<div class="panel-heading">Model (regime-aware ensemble)</div>', unsafe_allow_html=True)
            st.markdown(core.format_model_panel(model_info))
    with right:
        with st.container(border=True):
            st.markdown('<div class="panel-heading">Policy &amp; real-world adjustments</div>', unsafe_allow_html=True)
            st.markdown(core.format_policy_panel(policy_notes, bill_updates, policy_data, scenario))

    st.write("")
    with st.container(border=True):
        st.markdown('<div class="panel-heading">Yearly data</div>', unsafe_allow_html=True)
        table_rows = (
            [{"Year": p.year, "USD billions": round(p.value, 1), "Type": p.kind} for p in actual_visible]
            + [{"Year": current[0], "USD billions": round(current[1], 1), "Type": "FYTD (partial)"}]
            + [{"Year": p.year, "USD billions": round(p.value, 1), "Type": p.kind} for p in forecast]
        )
        st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True, height=280)
        csv_bytes = pd.DataFrame(table_rows).to_csv(index=False).encode("utf-8")
        st.download_button("Export yearly data as CSV", csv_bytes, file_name="GovernmentDefenceSpending_Analysis.csv", mime="text/csv")

with tab_table:
    st.markdown('<div class="panel-heading">Annual and monthly outlays</div>', unsafe_allow_html=True)
    st.caption("Expand to view October through September. Forecast months are estimates based on recent seasonal spending patterns.")

    monthly_forecasts = core.forecast_monthly_values(forecast, monthly)
    monthly_by_year: dict[int, list] = {}
    for record in monthly:
        monthly_by_year.setdefault(record.fiscal_year, []).append(record)

    with st.container(border=True):
        st.markdown("**Model forecasts**")
        for point in forecast:
            with st.expander(f"FY {point.year} forecast — {core.money(point.value)}"):
                st.dataframe(
                    pd.DataFrame([{"Month": label, "USD billions": round(value, 2)} for label, value in monthly_forecasts.get(point.year, [])]),
                    use_container_width=True, hide_index=True,
                )

    st.write("")
    with st.container(border=True):
        st.markdown(f"**Historical National Defense outlays — 1940 to present**  \nOMB annual history; Treasury monthly detail through {current[2]}")
        current_year_months = sorted(monthly_by_year.get(current[0], []), key=lambda m: m.record_date)
        with st.expander(f"FY {current[0]} current — {core.money(current[1])} (FYTD partial)", expanded=True):
            st.dataframe(
                pd.DataFrame([{"Month": f"{m.month_label} {m.record_date[:4]}", "USD billions": round(m.amount, 2), "FYTD total": round(m.fytd, 1)} for m in current_year_months]),
                use_container_width=True, hide_index=True,
            )
        for year, total in sorted(annual_rows, reverse=True):
            records = sorted(monthly_by_year.get(year, []), key=lambda m: m.record_date)
            with st.expander(f"FY {year} — {core.money(total)}"):
                if records:
                    st.dataframe(
                        pd.DataFrame([{"Month": f"{m.month_label} {m.record_date[:4]}", "USD billions": round(m.amount, 2), "FYTD total": round(m.fytd, 1)} for m in records]),
                        use_container_width=True, hide_index=True,
                    )
                else:
                    st.caption("OMB Historical Table 3.1 annual total; monthly detail unavailable.")

with tab_categories:
    st.markdown('<div class="panel-heading">Spending categories</div>', unsafe_allow_html=True)
    st.caption(
        "Sourced from USAspending.gov rather than Treasury/OMB. Appropriation-type and branch are derived by "
        "matching DoD's own federal account names (e.g. “Research, Development, Test, and Evaluation, Air Force”), "
        "not an official USAspending field. Industry sector is contract-level data with roughly a 90-day publication "
        "lag, and the sector labels are our own grouping of NAICS codes, not a government category."
    )

    completed_years = sorted({year for year, _ in annual_rows if year < current[0]}, reverse=True)
    category_years = [y for y in completed_years if y >= 2017][:10] or completed_years[:10]
    category_fy = st.selectbox("Fiscal year", category_years, key="category_fy")

    by_type, by_branch, accounts, category_error = load_category_breakdown(category_fy)

    if category_error:
        st.warning(f"Category breakdown unavailable for FY {category_fy} right now ({category_error}). Try a different fiscal year or refresh later.")
    else:
        col_type, col_branch = st.columns(2)
        with col_type:
            with st.container(border=True):
                st.markdown(f"**By appropriation type — FY {category_fy}**")
                type_items = sorted(by_type.items(), key=lambda kv: -kv[1])
                fig_type = go.Figure(go.Bar(
                    x=[v for _, v in type_items], y=[k for k, _ in type_items],
                    orientation="h", marker_color="#38BDF8",
                    text=[core.money(v) for _, v in type_items], textposition="outside",
                ))
                fig_type.update_layout(
                    height=340, margin=dict(l=10, r=10, t=10, b=10),
                    paper_bgcolor="#0B1220", plot_bgcolor="#0B1220",
                    font=dict(color="#A9B6C9", family="Segoe UI, Arial, sans-serif", size=12),
                    xaxis=dict(title="USD billions", gridcolor="#263348"),
                    yaxis=dict(autorange="reversed"),
                )
                st.plotly_chart(fig_type, use_container_width=True)
        with col_branch:
            with st.container(border=True):
                st.markdown(f"**By military branch — FY {category_fy}**")
                branch_items = sorted(by_branch.items(), key=lambda kv: -kv[1])
                fig_branch = go.Figure(go.Bar(
                    x=[v for _, v in branch_items], y=[k for k, _ in branch_items],
                    orientation="h", marker_color="#F59E0B",
                    text=[core.money(v) for _, v in branch_items], textposition="outside",
                ))
                fig_branch.update_layout(
                    height=340, margin=dict(l=10, r=10, t=10, b=10),
                    paper_bgcolor="#0B1220", plot_bgcolor="#0B1220",
                    font=dict(color="#A9B6C9", family="Segoe UI, Arial, sans-serif", size=12),
                    xaxis=dict(title="USD billions", gridcolor="#263348"),
                    yaxis=dict(autorange="reversed"),
                )
                st.plotly_chart(fig_branch, use_container_width=True)

        st.write("")
        with st.container(border=True):
            st.markdown("**All federal accounts this fiscal year**")
            st.dataframe(
                pd.DataFrame([{"Account": a["name"], "Appropriation type": a["type"], "Branch": a["branch"], "USD billions": round(a["outlay_billions"], 2)} for a in sorted(accounts, key=lambda a: -a["outlay_billions"])]),
                use_container_width=True, hide_index=True, height=320,
            )

    st.write("")
    with st.container(border=True):
        st.markdown(f"**By industry sector (NAICS) — FY {category_fy} — experimental**")
        naics_results, naics_error = load_naics_breakdown(category_fy)
        if naics_error:
            st.warning(f"Industry-sector breakdown unavailable right now ({naics_error}). This is the most experimental of the three views and may need adjustment once we start narrowing to sectors relevant to a specific contractor.")
        elif not naics_results:
            st.caption("No contract-category data returned for this fiscal year.")
        else:
            st.dataframe(
                pd.DataFrame([{"NAICS category": r["name"], "USD billions": round(r["amount"], 2)} for r in naics_results]),
                use_container_width=True, hide_index=True,
            )
