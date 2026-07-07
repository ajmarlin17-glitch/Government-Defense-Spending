"""Core forecasting logic for U.S. Defense Spending Trend and Forecast."""

from __future__ import annotations

import csv
import io
import json
import math
import os
import random
import statistics
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DATA_URL = (
    "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/"
    "v1/accounting/mts/mts_table_9?"
    "filter=classification_desc:eq:National%20Defense&"
    "page%5Bsize%5D=500&sort=record_date"
)
HISTORY_YEARS = 10
FORECAST_YEARS = 5
DAMPING_PHI = 0.85
MC_TRIALS = 2000
MC_SEED = 42
CANDIDATE_WINDOWS = (6, 7, 8, 9, 10, 12, 15, 20)

CONGRESS_API_BASE = "https://api.congress.gov/v3"

STAGE_WEIGHTS = {
    "requested": 0.10, "committee": 0.20, "house_passed": 0.35,
    "senate_passed": 0.35, "cbo_baseline": 0.55, "conference": 0.65, "enacted": 1.0,
}

SCENARIO_LABELS = {
    "baseline": "Baseline",
    "elevated_tension": "Elevated geopolitical tension",
    "de_escalation": "De-escalation",
}


@dataclass(frozen=True)
class Point:
    year: int
    value: float
    kind: str = "Actual"
    lower: float | None = None
    upper: float | None = None


@dataclass(frozen=True)
class MonthlyRecord:
    fiscal_year: int
    record_date: str
    amount: float
    fytd: float

    @property
    def month_label(self) -> str:
        names = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
        return names[int(self.record_date[5:7]) - 1]


@dataclass(frozen=True)
class ModelResult:
    name: str
    intercept: float
    slope: float
    transformed: bool
    backtest_mape: float
    residual_std: float
    x_mean: float
    sxx: float
    count: int
    damping: float | None = None

    def _cumulative_slope(self, step: int) -> float:
        if self.damping is None:
            return self.slope * step
        phi = self.damping
        if phi >= 1.0:
            return self.slope * step
        return self.slope * phi * (1 - phi ** step) / (1 - phi)

    def predict(self, x: float, step: int | None = None) -> float:
        if self.damping is None or step is None:
            fitted = self.intercept + self.slope * x
        else:
            base = self.intercept + self.slope * (self.count - 1)
            fitted = base + self._cumulative_slope(step)
        return math.exp(fitted) if self.transformed else fitted


@dataclass(frozen=True)
class PolicyNote:
    year: int
    statistical_growth: float
    policy_growth: float
    policy_weight: float
    scenario_adjustment: float
    blended_growth: float
    policy_label: str | None
    policy_source: str | None


@dataclass(frozen=True)
class ModelInfo:
    best_name: str
    best_mape: float
    best_damped: bool
    window_years: int
    ensemble_mape: float
    weights: tuple


def app_base_dir() -> Path:
    return Path(__file__).resolve().parent


def _parse_treasury_records(records):
    usable = [r for r in records if r.get("record_fiscal_year") and r.get("current_fytd_rcpt_outly_amt")]
    if not usable:
        raise ValueError("Treasury returned no usable National Defense records.")
    usable.sort(key=lambda r: r["record_date"])
    completed = [
        (int(r["record_fiscal_year"]), float(r["current_fytd_rcpt_outly_amt"]) / 1_000_000_000)
        for r in usable
        if r.get("record_calendar_month") == "09"
    ]
    if len(completed) < HISTORY_YEARS:
        raise ValueError("Treasury returned fewer than 10 completed fiscal years.")
    latest = usable[-1]
    current = (
        int(latest["record_fiscal_year"]),
        float(latest["current_fytd_rcpt_outly_amt"]) / 1_000_000_000,
        latest["record_date"],
    )
    monthly = [
        MonthlyRecord(
            fiscal_year=int(r["record_fiscal_year"]),
            record_date=r["record_date"],
            amount=float(r.get("current_month_rcpt_outly_amt") or 0.0) / 1_000_000_000,
            fytd=float(r["current_fytd_rcpt_outly_amt"]) / 1_000_000_000,
        )
        for r in usable
    ]
    return completed, current, monthly


def parse_treasury_json(text: str):
    payload = json.loads(text)
    return _parse_treasury_records(payload.get("data", []))


def parse_treasury_csv(text: str):
    return _parse_treasury_records(list(csv.DictReader(io.StringIO(text))))


def snapshot_path() -> Path:
    return app_base_dir() / "Data" / "GovernmentDefenceSpending_TreasuryData_V3.csv"


def omb_history_path() -> Path:
    return app_base_dir() / "Data" / "GovernmentDefenceSpending_OMBAnnualData_V1.csv"


def policy_assumptions_path() -> Path:
    return app_base_dir() / "Config" / "GovernmentDefenceSpending_PolicyAssumptions_V2.json"


def load_omb_history() -> list[tuple[int, float]]:
    rows = []
    with omb_history_path().open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            rows.append((int(row["fiscal_year"]), float(row["usd_billions"])))
    return rows


def default_policy_assumptions() -> dict:
    return {"as_of": None, "appropriations": [], "bill_tracking": [], "scenarios": {"baseline": 0.0}}


def load_policy_assumptions() -> dict:
    try:
        data = json.loads(policy_assumptions_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return default_policy_assumptions()
    data.setdefault("appropriations", [])
    data.setdefault("bill_tracking", [])
    data.setdefault("scenarios", {"baseline": 0.0})
    data["scenarios"].setdefault("baseline", 0.0)
    return data


def load_congress_api_key(streamlit_secrets: dict | None = None) -> str | None:
    if streamlit_secrets:
        key = streamlit_secrets.get("congress_api_key")
        if key:
            return key
    try:
        data = json.loads((app_base_dir() / "Config" / "GovernmentDefenceSpending_ApiKeys_V1.json").read_text(encoding="utf-8"))
        return data.get("congress_api_key") or None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def fetch_bill_status(congress: int, bill_type: str, bill_number: int, api_key: str, timeout: int = 15) -> dict | None:
    url = f"{CONGRESS_API_BASE}/bill/{congress}/{bill_type}/{bill_number}?api_key={api_key}&format=json"
    request = urllib.request.Request(url, headers={"User-Agent": "DefenseSpendingWebApp/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    action = (payload.get("bill") or {}).get("latestAction") or {}
    if not action:
        return None
    return {"action_date": action.get("actionDate"), "text": action.get("text")}


def refresh_bill_tracking(policy_data: dict, api_key: str | None, timeout: int = 15) -> list[dict]:
    updates = []
    for tracker in policy_data.get("bill_tracking", []):
        bill_number = tracker.get("bill_number")
        status = None
        if api_key and bill_number:
            status = fetch_bill_status(tracker["congress"], tracker["bill_type"], bill_number, api_key, timeout)
        updates.append({**tracker, "live_status": status})
    return updates


def compute_policy_growth(rows, policy_data):
    actual_by_year = dict(rows)
    approps = policy_data.get("appropriations", [])
    chosen: dict[int, dict] = {}
    for approp in approps:
        fy = approp["fiscal_year"]
        weight = STAGE_WEIGHTS.get(approp.get("stage"), approp.get("weight", 0.0))
        if fy not in chosen or weight > chosen[fy]["_weight"]:
            entry = dict(approp)
            entry["_weight"] = weight
            if entry.get("outlay_billions") is not None:
                entry["_total"] = entry["outlay_billions"]
            else:
                entry["_total"] = (entry.get("discretionary_billions") or 0.0) + (entry.get("mandatory_billions") or 0.0)
            chosen[fy] = entry
    totals_by_year = {fy: entry["_total"] for fy, entry in chosen.items()}
    result: dict[int, tuple[float, float, str, str | None]] = {}
    for fy, entry in chosen.items():
        prior_fy = fy - 1
        if prior_fy in totals_by_year:
            base = totals_by_year[prior_fy]
        elif prior_fy in actual_by_year:
            base = actual_by_year[prior_fy]
        else:
            continue
        if base <= 0:
            continue
        growth = entry["_total"] / base - 1.0
        label = entry.get("label", f"FY{fy} ({entry.get('stage', 'unspecified')})")
        result[fy] = (growth, entry["_weight"], label, entry.get("source"))
    return result


def merge_annual_history(omb_rows, treasury_rows):
    merged = {year: value for year, value in omb_rows}
    merged.update({year: value for year, value in treasury_rows})
    return sorted(merged.items())


_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}


def _fetch_via_requests(timeout: int, attempts: int = 3, url: str = None):
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    session = requests.Session()
    retry = Retry(total=attempts, backoff_factor=1.5, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    response = session.get(url or DATA_URL, headers=_BROWSER_HEADERS, timeout=timeout)
    if not response.ok:
        snippet = response.text[:200].replace("\n", " ")
        raise requests.HTTPError(f"HTTP {response.status_code}: {snippet}")
    return response.text


def _fetch_via_urllib(timeout: int, attempts: int = 3, url: str = None):
    import time
    last_exc = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url or DATA_URL, headers=_BROWSER_HEADERS)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8-sig")
        except Exception as exc:
            last_exc = exc
            if attempt < attempts - 1:
                time.sleep(1.5 * (attempt + 1))
    raise last_exc


def _fetch_json_with_retries(url: str, timeout: int = 30):
    errors = []
    try:
        return _fetch_via_requests(timeout, url=url)
    except Exception as exc:
        errors.append(f"requests:{type(exc).__name__}")
    try:
        return _fetch_via_urllib(timeout, url=url)
    except Exception as exc:
        errors.append(f"urllib:{type(exc).__name__}")
    raise ConnectionError("/".join(errors))


def fetch_data(timeout: int = 30):
    try:
        text = _fetch_json_with_retries(DATA_URL, timeout)
        rows, current, monthly = parse_treasury_json(text)
        return rows, current, monthly, f"Live Treasury data through {current[2]}"
    except Exception as exc:
        error = str(exc)
    rows, current, monthly = parse_treasury_csv(snapshot_path().read_text(encoding="utf-8-sig"))
    return rows, current, monthly, f"Verified Treasury snapshot through {current[2]} (live fetch unavailable: {error})"


DOD_TOPTIER_CODE = "097"
USASPENDING_FEDERAL_ACCOUNT_URL = (
    "https://api.usaspending.gov/api/v2/agency/{toptier_code}/federal_account/"
    "?fiscal_year={fiscal_year}&limit=100&page={page}"
)
USASPENDING_NAICS_URL = "https://api.usaspending.gov/api/v2/search/spending_by_category/naics/"

APPROPRIATION_TYPE_PATTERNS = (
    ("Military Personnel", ("military personnel",)),
    ("Operation & Maintenance", ("operation and maintenance",)),
    ("Procurement", ("procurement", "shipbuilding and conversion", "aircraft procurement", "missile procurement", "weapons procurement", "ammunition procurement")),
    ("RDT&E", ("research, development, test",)),
    ("Military Construction & Housing", ("military construction", "family housing")),
    ("Working Capital Funds", ("working capital fund",)),
    ("Defense Health Program", ("defense health program",)),
    ("Retirement & Other", ("retirement fund", "payments to military retirement")),
)

BRANCH_PATTERNS = (
    ("Space Force", ("space force",)),
    ("Army", ("army",)),
    ("Navy", ("navy", "marine corps")),
    ("Air Force", ("air force",)),
)
DEFAULT_BRANCH = "Defense-Wide"
DEFAULT_APPROPRIATION_TYPE = "Other"


def _classify(name: str, patterns, default: str) -> str:
    lowered = name.lower()
    for label, keywords in patterns:
        if any(keyword in lowered for keyword in keywords):
            return label
    return default


def fetch_federal_account_breakdown(fiscal_year: int, toptier_code: str = DOD_TOPTIER_CODE, timeout: int = 30, max_pages: int = 5):
    """Pull every federal account for one fiscal year, paginating since
    USAspending caps this endpoint at 100 results per page (a request for
    more than that returns an HTTP error rather than just truncating)."""
    by_type: dict[str, float] = {}
    by_branch: dict[str, float] = {}
    accounts = []
    page = 1
    while page <= max_pages:
        url = USASPENDING_FEDERAL_ACCOUNT_URL.format(toptier_code=toptier_code, fiscal_year=fiscal_year, page=page)
        text = _fetch_json_with_retries(url, timeout)
        payload = json.loads(text)
        for account in payload.get("results", []):
            name = account.get("name", "")
            outlay = float(account.get("gross_outlay_amount") or 0.0) / 1_000_000_000
            appropriation_type = _classify(name, APPROPRIATION_TYPE_PATTERNS, DEFAULT_APPROPRIATION_TYPE)
            branch = _classify(name, BRANCH_PATTERNS, DEFAULT_BRANCH)
            by_type[appropriation_type] = by_type.get(appropriation_type, 0.0) + outlay
            by_branch[branch] = by_branch.get(branch, 0.0) + outlay
            accounts.append({"name": name, "type": appropriation_type, "branch": branch, "outlay_billions": outlay})
        if not payload.get("page_metadata", {}).get("hasNext"):
            break
        page += 1
    return by_type, by_branch, accounts


def fetch_naics_breakdown(fiscal_year: int, limit: int = 30, timeout: int = 30):
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    body = {
        "category": "naics",
        "filters": {
            "agencies": [{"type": "awarding", "tier": "toptier", "name": "Department of Defense"}],
            "time_period": [{"start_date": f"{fiscal_year - 1}-10-01", "end_date": f"{fiscal_year}-09-30"}],
        },
        "limit": limit,
    }
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1.5, status_forcelist=(429, 500, 502, 503, 504))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    headers = dict(_BROWSER_HEADERS)
    headers["Content-Type"] = "application/json"
    response = session.post(USASPENDING_NAICS_URL, json=body, headers=headers, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    results = payload.get("results", [])
    return [
        {
            "name": item.get("name") or item.get("code") or "Unknown",
            "code": str(item.get("code")) if item.get("code") is not None else None,
            "amount": float(item.get("amount") or 0.0) / 1_000_000_000,
        }
        for item in results
    ]


# --- TEC Solutions relevance ---------------------------------------------
# TEC Solutions LLC (tecsolutionsva.com) is a Systems Engineering & Technical
# Assistance (SETA) contractor supporting DoD/IC R&D programs. Its primary
# NAICS codes and their standard titles:
TEC_NAICS_LABELS = {
    "541330": "Engineering Services",
    "541420": "Industrial Design Services",
    "541611": "Administrative & General Management Consulting",
    "541715": "R&D in the Physical, Engineering & Life Sciences",
}
TEC_NAICS_CODES = tuple(TEC_NAICS_LABELS.keys())
# TEC's SETA/R&D advisory work is funded almost entirely out of RDT&E
# appropriations rather than procurement or O&M dollars.
TEC_RELEVANT_APPROPRIATION_TYPE = "RDT&E"


def _matches_tec_naics(item: dict) -> bool:
    code = (item.get("code") or "").strip()
    if code in TEC_NAICS_CODES:
        return True
    name = (item.get("name") or "").lower()
    if any(naics_code in name for naics_code in TEC_NAICS_CODES):
        return True
    return any(label.lower() in name for label in TEC_NAICS_LABELS.values())


def summarize_tec_relevance(by_type: dict, naics_results: list) -> dict:
    """Estimate what portion of DoD spending falls into budget/industry
    categories aligned with TEC Solutions' service lines. This is a market-
    sizing estimate of addressable categories — NOT a measure of contracts
    actually awarded to TEC, which this app does not attempt to look up."""
    total_outlay = sum(by_type.values())
    rdte_outlay = by_type.get(TEC_RELEVANT_APPROPRIATION_TYPE, 0.0)
    rdte_share = (rdte_outlay / total_outlay) if total_outlay else 0.0

    tec_naics_items = sorted(
        (item for item in naics_results if _matches_tec_naics(item)),
        key=lambda item: -item["amount"],
    )
    tec_naics_total = sum(item["amount"] for item in tec_naics_items)
    naics_sample_total = sum(item["amount"] for item in naics_results)
    tec_naics_share_of_sample = (tec_naics_total / naics_sample_total) if naics_sample_total else 0.0

    return {
        "total_outlay": total_outlay,
        "rdte_outlay": rdte_outlay,
        "rdte_share": rdte_share,
        "tec_naics_items": tec_naics_items,
        "tec_naics_total": tec_naics_total,
        "naics_sample_total": naics_sample_total,
        "tec_naics_share_of_sample": tec_naics_share_of_sample,
    }


def _ols(values, transformed):
    y = [math.log(v) for v in values] if transformed else values
    x = list(range(len(y)))
    x_mean = statistics.fmean(x)
    y_mean = statistics.fmean(y)
    sxx = sum((v - x_mean) ** 2 for v in x)
    slope = sum((a - x_mean) * (b - y_mean) for a, b in zip(x, y)) / sxx
    intercept = y_mean - slope * x_mean
    residuals = [b - (intercept + slope * a) for a, b in zip(x, y)]
    degrees = max(1, len(y) - 2)
    residual_std = math.sqrt(sum(r * r for r in residuals) / degrees)
    return intercept, slope, residual_std, x_mean, sxx


def _one_step_mape(values, transformed, damping=None):
    errors = []
    for stop in range(5, len(values)):
        intercept, slope, residual_std, x_mean, sxx = _ols(values[:stop], transformed)
        probe = ModelResult(name="", intercept=intercept, slope=slope, transformed=transformed, backtest_mape=0.0, residual_std=residual_std, x_mean=x_mean, sxx=sxx, count=stop, damping=damping)
        predicted = probe.predict(stop, step=1)
        errors.append(abs(values[stop] - predicted) / values[stop])
    return statistics.fmean(errors) * 100.0


def build_candidates(values):
    candidates = []
    for transformed, name, damping in ((False, "Linear trend", None), (True, "Log-linear growth trend", None), (True, "Damped log-linear trend", DAMPING_PHI)):
        intercept, slope, residual_std, x_mean, sxx = _ols(values, transformed)
        candidates.append(ModelResult(name=name, intercept=intercept, slope=slope, transformed=transformed, backtest_mape=_one_step_mape(values, transformed, damping), residual_std=residual_std, x_mean=x_mean, sxx=sxx, count=len(values), damping=damping))
    return candidates


def select_model(values):
    return min(build_candidates(values), key=lambda m: m.backtest_mape)


def ensemble_weights(candidates):
    inv = [1.0 / max(c.backtest_mape, 0.01) for c in candidates]
    total = sum(inv)
    return [v / total for v in inv]


def growth_std_estimate(model):
    if model.transformed:
        return model.residual_std
    fitted_end = model.intercept + model.slope * (model.count - 1)
    return model.residual_std / max(1.0, abs(fitted_end))


def select_training_window(rows, candidate_windows=CANDIDATE_WINDOWS):
    best = None
    seen: set[int] = set()
    for window in candidate_windows:
        window = min(window, len(rows))
        if window < 6 or window in seen:
            continue
        seen.add(window)
        values = [v for _, v in rows[-window:]]
        candidates = build_candidates(values)
        weights = ensemble_weights(candidates)
        ensemble_mape = sum(w * c.backtest_mape for w, c in zip(weights, candidates))
        if best is None or ensemble_mape < best[0]:
            best = (ensemble_mape, window, candidates, weights)
    return best[1], best[2], best[3], best[0]


def monte_carlo_bands(last_value, notes, growth_std, trials=MC_TRIALS, seed=MC_SEED):
    rng = random.Random(seed)
    results_by_step: list[list[float]] = [[] for _ in notes]
    for _ in range(trials):
        value = last_value
        for i, note in enumerate(notes):
            step = i + 1
            stat_noise = rng.gauss(0.0, growth_std * math.sqrt(step))
            policy_noise = rng.gauss(0.0, 0.008 + 0.02 * (1.0 - note.policy_weight)) if note.policy_weight > 0 else 0.0
            sampled_growth = note.blended_growth + stat_noise * (1 - note.policy_weight) + policy_noise * note.policy_weight
            value *= 1 + sampled_growth
            results_by_step[i].append(value)
    lowers: list[float] = []
    uppers: list[float] = []
    for series in results_by_step:
        series.sort()
        lowers.append(series[int(0.025 * len(series))])
        uppers.append(series[min(len(series) - 1, int(0.975 * len(series)))])
    return lowers, uppers


def analyze(rows, forecast_years=FORECAST_YEARS, policy_data=None, scenario="baseline", mc_trials=MC_TRIALS, mc_seed=MC_SEED):
    window, candidates, weights, ensemble_mape = select_training_window(rows)
    best = min(candidates, key=lambda c: c.backtest_mape)
    model_info = ModelInfo(best_name=best.name, best_mape=best.backtest_mape, best_damped=best.damping is not None, window_years=window, ensemble_mape=ensemble_mape, weights=tuple((c.name, c.backtest_mape, w) for c, w in zip(candidates, weights)))
    actual = [Point(year, value) for year, value in rows]
    policy_data = policy_data or default_policy_assumptions()
    policy_growth = compute_policy_growth(rows, policy_data)
    scenario_adjustment = policy_data.get("scenarios", {}).get(scenario, 0.0)
    last_year, last_value = rows[-1]
    forecast: list[Point] = []
    notes: list[PolicyNote] = []
    prev_blended = last_value
    prev_ensemble = last_value
    for step in range(1, forecast_years + 1):
        year = last_year + step
        ensemble_value = sum(w * c.predict(c.count - 1 + step, step=step) for c, w in zip(candidates, weights))
        stat_growth = (ensemble_value / prev_ensemble - 1.0) if prev_ensemble else 0.0
        policy_info = policy_growth.get(year)
        if policy_info:
            p_growth, p_weight, label, source = policy_info
        else:
            p_growth, p_weight, label, source = 0.0, 0.0, None, None
        blended_growth = stat_growth * (1 - p_weight) + p_growth * p_weight + scenario_adjustment
        blended_value = prev_blended * (1 + blended_growth)
        forecast.append(Point(year, blended_value, "Forecast"))
        notes.append(PolicyNote(year=year, statistical_growth=stat_growth, policy_growth=p_growth, policy_weight=p_weight, scenario_adjustment=scenario_adjustment, blended_growth=blended_growth, policy_label=label, policy_source=source))
        prev_blended = blended_value
        prev_ensemble = ensemble_value
    growth_std = sum(w * growth_std_estimate(c) for c, w in zip(candidates, weights))
    lowers, uppers = monte_carlo_bands(last_value, notes, growth_std, trials=mc_trials, seed=mc_seed)
    forecast = [Point(p.year, p.value, p.kind, lowers[i], uppers[i]) for i, p in enumerate(forecast)]
    return actual, forecast, model_info, notes


def forecast_monthly_values(forecast, monthly):
    by_year: dict[int, list[MonthlyRecord]] = {}
    for record in monthly:
        by_year.setdefault(record.fiscal_year, []).append(record)
    completed = [year for year, records in by_year.items() if len(records) == 12]
    completed = sorted(completed)[-5:]
    shares_by_position: list[list[float]] = [[] for _ in range(12)]
    for year in completed:
        records = sorted(by_year[year], key=lambda item: item.record_date)
        total = sum(record.amount for record in records)
        if total <= 0:
            continue
        for record in records:
            month = int(record.record_date[5:7])
            position = (month - 10) % 12
            shares_by_position[position].append(record.amount / total)
    shares = [statistics.fmean(values) if values else 1.0 / 12 for values in shares_by_position]
    normalizer = sum(shares)
    shares = [share / normalizer for share in shares]
    names = ("Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep")
    result: dict[int, list[tuple[str, float]]] = {}
    for point in forecast:
        months = []
        for position, (name, share) in enumerate(zip(names, shares)):
            calendar_year = point.year - 1 if position < 3 else point.year
            months.append((f"{name} {calendar_year}", point.value * share))
        result[point.year] = months
    return result


def format_model_panel(model_info: ModelInfo) -> str:
    lines = [
        f"**Training window:** last {model_info.window_years} fiscal years (chosen by backtest, not fixed)",
        f"**Best individual candidate:** {model_info.best_name} ({model_info.best_mape:.1f}% MAPE)",
        "",
        "**Ensemble blend** (weighted by inverse backtest error):",
    ]
    for name, mape, weight in model_info.weights:
        lines.append(f"- {name}: {mape:.1f}% MAPE, weight {weight * 100:.0f}%")
    lines.append(f"\n**Ensemble backtest MAPE:** {model_info.ensemble_mape:.1f}%")
    lines.append("\nThe shaded area is a Monte Carlo-based approximate 95% range that incorporates statistical, congressional/CBO, and scenario uncertainty.")
    return "\n".join(lines)


def format_policy_panel(policy_notes, bill_updates, policy_data, scenario) -> str:
    lines = []
    if not policy_notes:
        lines.append("No forecast years to adjust.")
    else:
        for note in policy_notes:
            if note.policy_weight > 0:
                lines.append(f"- **FY{note.year}:** statistical {note.statistical_growth * 100:+.1f}%/yr, policy {note.policy_growth * 100:+.1f}%/yr ({note.policy_label}, weight {note.policy_weight * 100:.0f}%) → blended {note.blended_growth * 100:+.1f}%/yr")
            else:
                lines.append(f"- **FY{note.year}:** statistical {note.statistical_growth * 100:+.1f}%/yr (no enacted/proposed/CBO figure yet) → blended {note.blended_growth * 100:+.1f}%/yr")
    scenario_adjustment = policy_data.get("scenarios", {}).get(scenario, 0.0)
    scenario_label = SCENARIO_LABELS.get(scenario, scenario)
    lines.append(f"\n**Scenario:** {scenario_label} ({scenario_adjustment * 100:+.1f} pp/yr, already included above)")
    as_of = policy_data.get("as_of") or "unknown"
    lines.append(f"\n*Congressional/CBO figures last verified: {as_of}.*")
    configured = [b for b in bill_updates if b.get("bill_number")]
    if not configured:
        lines.append("\nLive congressional bill-status tracking is not yet configured.")
    else:
        for bill in bill_updates:
            status = bill.get("live_status")
            if status:
                lines.append(f"\nLive check — {bill.get('label')}: {status.get('text')} ({status.get('action_date')})")
            elif bill.get("bill_number"):
                lines.append(f"\nLive check — {bill.get('label')}: unavailable right now, showing last verified figures.")
    return "\n".join(lines)


def money(value: float) -> str:
    return f"${value:,.1f}B"
