"""U.S. Defense Spending: Trend & Forecast — web app.

This is the browser-based counterpart to the Windows desktop app. It uses
the same forecasting engine (core.py) — the ensemble model, regime-aware
training window, CBO baseline blending, Monte Carlo bands, and scenario
adjustment are all identical to the desktop version. The interface is
styled to TEC Solutions' actual brand palette (see
TecSolutions_MasterFormatGuide_V2.md): white/light-grey backgrounds, black
body text, maroon headings and accents, grey secondary text — rather than
the dark navy theme used in earlier drafts.

Run locally with:  streamlit run app.py
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import core

st.set_page_config(page_title="U.S. Defense Spending: Trend & Forecast", layout="wide")

# --- TEC Solutions brand palette (from TecSolutions_MasterFormatGuide_V2.md) ---
MAROON_FOOTER = "#751A15"   # footer/band maroon — primary data series, buttons
MAROON_TITLE = "#80292B"    # logo/title maroon — headings, panel titles
MAROON_ACCENT = "#8C1E18"   # accent maroon — secondary emphasis
GREY_SECONDARY = "#6F7277"  # secondary grey — muted text, secondary data series
GREY_LIGHT = "#D3D4D3"      # light grey — rules, borders, frames
PAGE_BG = "#F7F7F7"         # very light grey page background (easier on the eyes than stark white)
CARD_BG = "#FFFFFF"         # white card background, layered above the page background

st.markdown(
    f"""
    <style>
    .stApp {{ background-color: {PAGE_BG}; }}
    [data-testid="stSidebar"] {{ background-color: {PAGE_BG}; border-right: 2px solid {MAROON_FOOTER}; }}
    [data-testid="stSidebar"] h2 {{ color: {MAROON_TITLE}; font-size: 1rem; }}
    [data-testid="stSidebar"] label {{ color: #000000 !important; }}
    [data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"] {{
        background-color: {CARD_BG}; margin-bottom: 14px;
    }}
    [data-testid="stSidebar"] .panel-heading {{ margin-bottom: 12px; }}
    [data-testid="stSidebar"] [data-testid="stCaptionContainer"] {{ margin-top: -6px; }}

    .eyebrow {{
        color: {GREY_SECONDARY}; font-weight: 700; font-size: 0.78rem;
        letter-spacing: 0.10em; text-transform: uppercase; margin-bottom: 2px;
    }}
    .app-title {{ font-size: 2.1rem; font-weight: 700; color: {MAROON_TITLE}; margin: 0 0 4px 0; }}
    .app-subtitle {{ color: {GREY_SECONDARY}; font-size: 0.95rem; margin: 0 0 6px 0; }}

    div[data-testid="stMetric"] {{
        background-color: {CARD_BG}; border: 1px solid {GREY_LIGHT}; border-left: 4px solid {MAROON_FOOTER};
        border-radius: 6px; padding: 14px 16px 10px 16px;
    }}
    div[data-testid="stMetricLabel"] > div {{
        color: {GREY_SECONDARY} !important; font-size: 0.72rem !important;
        text-transform: uppercase; letter-spacing: 0.05em; font-weight: 700;
    }}
    div[data-testid="stMetricValue"] {{ color: #000000 !important; }}
    div[data-testid="stMetricDelta"] {{ color: {MAROON_FOOTER} !important; }}

    div[data-testid="stRadio"] div[role="radiogroup"] {{ gap: 18px; border-bottom: 2px solid {GREY_LIGHT}; padding-bottom: 8px; }}
    div[data-testid="stRadio"] div[role="radiogroup"] label {{
        background-color: transparent; border-radius: 6px 6px 0 0;
        padding: 4px 6px; margin: 0;
    }}
    div[data-testid="stRadio"] div[role="radiogroup"] label p {{ color: {GREY_SECONDARY}; font-weight: 700; margin: 0; }}
    div[data-testid="stRadio"] div[role="radiogroup"] label:has(input:checked) {{ border-bottom: 3px solid {MAROON_FOOTER}; }}
    div[data-testid="stRadio"] div[role="radiogroup"] label:has(input:checked) p {{ color: {MAROON_TITLE}; }}

    .panel-heading {{
        color: {MAROON_TITLE}; font-weight: 700; font-size: 0.85rem;
        text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 10px;
    }}
    .status-line {{ font-size: 0.85rem; margin-bottom: 4px; }}

    [data-testid="stMarkdownContainer"] p, [data-testid="stMarkdownContainer"] li {{ color: #000000; }}
    [data-testid="stMarkdownContainer"] strong {{ color: {MAROON_TITLE}; }}

    div[data-testid="stExpander"] {{ border: 1px solid {GREY_LIGHT}; border-radius: 6px; background-color: {CARD_BG}; }}
    [data-testid="stVerticalBlockBorderWrapper"] {{ background-color: {CARD_BG}; }}

    button[kind="primary"], .stDownloadButton button {{ background-color: {MAROON_FOOTER}; border-color: {MAROON_FOOTER}; }}
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


# --- Header: eyebrow, title, subtitle ---
st.markdown('<div class="eyebrow">U.S. DEFENSE SPENDING</div>', unsafe_allow_html=True)
st.markdown('<p class="app-title">Historical Trend and Adjustable Forecast</p>', unsafe_allow_html=True)
st.markdown('<p class="app-subtitle">Treasury National Defense outlays by federal fiscal year</p>', unsafe_allow_html=True)

with st.spinner("Loading Treasury and OMB data..."):
    annual_rows, current, monthly, source_status, policy_data, bill_updates = load_data()

status_color = "#2E7D32" if source_status.startswith("Live") else MAROON_ACCENT
st.markdown(f'<div class="status-line" style="color:{status_color};">{source_status}</div>', unsafe_allow_html=True)

years = [str(year) for year, _ in annual_rows]
scenario_keys = list(policy_data.get("scenarios", {}).keys()) or ["baseline"]

completed_years = sorted({year for year, _ in annual_rows if year < current[0]}, reverse=True)
category_years = [y for y in completed_years if y >= 2017][:10] or completed_years[:10]

with st.sidebar:
    with st.container(border=True):
        st.markdown('<div class="panel-heading">Graph range</div>', unsafe_allow_html=True)
        default_start_index = max(0, len(years) - core.HISTORY_YEARS)
        start_year = st.selectbox("Start fiscal year", years, index=default_start_index)
        forecast_years = st.slider("Forecast years", min_value=1, max_value=10, value=5)
        scenario = st.selectbox("Scenario", scenario_keys, format_func=lambda key: core.SCENARIO_LABELS.get(key, key))
        if st.button("Refresh data", use_container_width=True, type="primary"):
            st.cache_data.clear()
            st.rerun()

    with st.container(border=True):
        st.markdown('<div class="panel-heading">Spending categories</div>', unsafe_allow_html=True)
        category_fy = st.selectbox("Fiscal year", category_years, key="category_fy")
        st.caption("Applies to the Spending categories and TEC relevance views.")

# Computed unconditionally (not inside a view branch) so both the Spending
# categories and TEC relevance views can use the same fiscal-year selection
# regardless of which view is currently active.
by_type, by_branch, accounts, category_error = load_category_breakdown(category_fy)
naics_results, naics_error = load_naics_breakdown(category_fy)

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
# Plain st.tabs() loses the active tab on any rerun triggered by a widget
# inside a non-first tab (a long-standing Streamlit limitation) — changing
# the fiscal year on the Spending categories tab would silently snap the
# view back to the Trend chart. A session_state-backed radio group styled
# as tabs keeps the selected view stable across reruns.
VIEW_LABELS = ["Trend chart", "Data table", "Spending categories", "TEC relevance"]
if "active_view" not in st.session_state:
    st.session_state["active_view"] = VIEW_LABELS[0]
active_view = st.radio(
    "View", VIEW_LABELS, horizontal=True, label_visibility="collapsed", key="active_view",
)

CHART_LAYOUT_DEFAULTS = dict(
    paper_bgcolor=CARD_BG, plot_bgcolor=CARD_BG,
    font=dict(color="#000000", family="Arial, Helvetica, sans-serif", size=12),
)

if active_view == "Trend chart":
    fig = go.Figure()
    band_years = [actual_visible[-1].year] + [p.year for p in forecast]
    band_upper = [actual_visible[-1].value] + [p.upper for p in forecast]
    band_lower = [actual_visible[-1].value] + [p.lower for p in forecast]
    fig.add_trace(go.Scatter(
        x=band_years + band_years[::-1], y=band_upper + band_lower[::-1],
        fill="toself", fillcolor="rgba(117, 26, 21, 0.12)",
        line=dict(width=0), hoverinfo="skip", showlegend=False, name="95% range",
    ))
    fig.add_trace(go.Scatter(
        x=[p.year for p in actual_visible], y=[p.value for p in actual_visible],
        mode="lines+markers", name="Actual",
        line=dict(color=MAROON_FOOTER, width=3), marker=dict(size=6, color=MAROON_FOOTER),
    ))
    fig.add_trace(go.Scatter(
        x=[actual_visible[-1].year] + [p.year for p in forecast],
        y=[actual_visible[-1].value] + [p.value for p in forecast],
        mode="lines+markers", name="Forecast",
        line=dict(color=GREY_SECONDARY, width=3, dash="dash"), marker=dict(size=6, color=GREY_SECONDARY),
    ))
    fig.update_layout(
        **CHART_LAYOUT_DEFAULTS,
        height=480, margin=dict(l=10, r=10, t=20, b=10),
        yaxis=dict(title="USD billions", gridcolor=GREY_LIGHT, zerolinecolor=GREY_LIGHT),
        xaxis=dict(title="Fiscal year", gridcolor=GREY_LIGHT, zerolinecolor=GREY_LIGHT),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(color="#000000")),
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

if active_view == "Data table":
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

if active_view == "Spending categories":
    st.markdown('<div class="panel-heading">Spending categories</div>', unsafe_allow_html=True)
    st.caption(
        "Sourced from USAspending.gov rather than Treasury/OMB. Appropriation-type and branch are derived by "
        "matching DoD's own federal account names (e.g. “Research, Development, Test, and Evaluation, Air Force”), "
        "not an official USAspending field. Industry sector is contract-level data with roughly a 90-day publication "
        "lag, and the sector labels are our own grouping of NAICS codes, not a government category."
    )

    if category_error:
        st.warning(f"Category breakdown unavailable for FY {category_fy} right now ({category_error}). Try a different fiscal year or refresh later.")
    else:
        col_type, col_branch = st.columns(2)
        with col_type:
            with st.container(border=True):
                st.markdown(f"**By appropriation type — FY {category_fy}**")
                type_items = sorted(by_type.items(), key=lambda kv: -kv[1])
                type_max = max((v for _, v in type_items), default=0)
                fig_type = go.Figure(go.Bar(
                    x=[v for _, v in type_items], y=[k for k, _ in type_items],
                    orientation="h", marker_color=MAROON_FOOTER, cliponaxis=False,
                    text=[core.money(v) for _, v in type_items], textposition="outside",
                ))
                fig_type.update_layout(
                    **CHART_LAYOUT_DEFAULTS,
                    height=340, margin=dict(l=10, r=60, t=10, b=10),
                    xaxis=dict(title="USD billions", gridcolor=GREY_LIGHT, range=[0, type_max * 1.22]),
                    yaxis=dict(autorange="reversed"),
                )
                st.plotly_chart(fig_type, use_container_width=True)
        with col_branch:
            with st.container(border=True):
                st.markdown(f"**By military branch — FY {category_fy}**")
                branch_items = sorted(by_branch.items(), key=lambda kv: -kv[1])
                branch_max = max((v for _, v in branch_items), default=0)
                fig_branch = go.Figure(go.Bar(
                    x=[v for _, v in branch_items], y=[k for k, _ in branch_items],
                    orientation="h", marker_color=GREY_SECONDARY, cliponaxis=False,
                    text=[core.money(v) for _, v in branch_items], textposition="outside",
                ))
                fig_branch.update_layout(
                    **CHART_LAYOUT_DEFAULTS,
                    height=340, margin=dict(l=10, r=60, t=10, b=10),
                    xaxis=dict(title="USD billions", gridcolor=GREY_LIGHT, range=[0, branch_max * 1.22]),
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
        if naics_error:
            st.warning(f"Industry-sector breakdown unavailable right now ({naics_error}). This is the most experimental of the three views and may need adjustment once we start narrowing to sectors relevant to a specific contractor.")
        elif not naics_results:
            st.caption("No contract-category data returned for this fiscal year.")
        else:
            st.dataframe(
                pd.DataFrame([{"NAICS category": r["name"], "USD billions": round(r["amount"], 2)} for r in naics_results]),
                use_container_width=True, hide_index=True,
            )

if active_view == "TEC relevance":
    st.markdown('<div class="panel-heading">TEC Solutions relevance</div>', unsafe_allow_html=True)
    st.caption(
        "Estimates what portion of this fiscal year's DoD spending falls into budget and industry "
        "categories aligned with TEC Solutions' service lines (SETA / engineering & R&D advisory "
        "support — primary NAICS 541330, 541420, 541611, 541715). This is a market-sizing estimate "
        "of addressable spending categories, not a measure of contracts actually awarded to TEC. "
        "Fiscal year is set by the selector in the sidebar."
    )

    tec = core.summarize_tec_relevance(by_type, naics_results)

    col_rdte, col_naics = st.columns(2)
    with col_rdte:
        with st.container(border=True):
            st.markdown(f"**RDT&E share of DoD outlays — FY {category_fy}**")
            if category_error:
                st.warning(f"Unavailable ({category_error}).")
            else:
                st.metric(
                    "RDT&E outlays",
                    core.money(tec["rdte_outlay"]),
                    f"{tec['rdte_share'] * 100:.1f}% of total DoD outlays",
                )
                st.caption(
                    "TEC's SETA/R&D advisory work is funded almost entirely through RDT&E "
                    "appropriations, so this is the best single proxy for the budget category TEC "
                    "operates in."
                )
    with col_naics:
        with st.container(border=True):
            st.markdown(f"**TEC-aligned NAICS categories — FY {category_fy}**")
            if naics_error:
                st.warning(f"Unavailable ({naics_error}).")
            elif not naics_results:
                st.caption("No contract-category data returned for this fiscal year.")
            else:
                st.metric(
                    "Matched NAICS spending",
                    core.money(tec["tec_naics_total"]),
                    f"{tec['tec_naics_share_of_sample'] * 100:.1f}% of top NAICS categories sampled",
                )
                st.caption(
                    "Matched against TEC's primary NAICS codes: 541330 (Engineering Services), "
                    "541420 (Industrial Design Services), 541611 (Admin & General Management "
                    "Consulting), 541715 (R&D in Physical, Engineering & Life Sciences). Share is out "
                    "of the top NAICS categories USAspending returned for this fiscal year, not all "
                    "of DoD spending."
                )

    if not category_error and not naics_error and tec["tec_naics_items"]:
        st.write("")
        with st.container(border=True):
            st.markdown("**Matched NAICS categories**")
            st.dataframe(
                pd.DataFrame([
                    {"NAICS category": item["name"], "Code": item.get("code") or "—", "USD billions": round(item["amount"], 2)}
                    for item in tec["tec_naics_items"]
                ]),
                use_container_width=True, hide_index=True,
            )

    st.write("")
    st.caption(
        "Caveats: RDT&E includes far more contractors than SETA-type advisory firms. NAICS-category "
        "matching is USAspending's own award-level industry classification and lags roughly 90 days; "
        "it identifies the category, not the recipient, so it does not confirm TEC itself won any of "
        "this spending. This section will be refined as TEC's actual sector focus is narrowed further."
    )

st.markdown(
    f'<p style="color:{GREY_SECONDARY}; font-size:0.75rem; margin-top:24px;">TEC Solutions LLC Proprietary Data</p>',
    unsafe_allow_html=True,
)
