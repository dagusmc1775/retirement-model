# version: hard-target-depletion-v22
# version: override-valuation-columns
# version: target-trad-override-v3-relaxed-cap
# version: target-trad-override-handoff-fix
# version: target-trad-balance-override-cap
# version: target-trad-balance-goal
# version: nc-state-tax-clean-base-v2
# version: nc-state-tax-clean-base
import copy
import datetime as dt
import hashlib
import json
import math

import streamlit as st
import pandas as pd
from bisect import bisect_left


# -----------------------------
# CONSTANTS
# -----------------------------
START_YEAR = 2026
END_YEAR = 2056
UNIFORM_LIFETIME_DIVISOR_73 = 26.5

ACA_CLIFF_MFJ = 84601.0
ACA_HEADROOM_BUFFER = 1.0

GOVERNOR_MIN_STEP_SIZE = 1000.0
APP_VERSION = "v205-modular-ss-scan-pipeline-shared-quick-full-ranking"
APP_STATE_VERSION = "v103"



def apply_app_state_version_guard() -> None:
    current_version = st.session_state.get("app_state_version")
    if current_version != APP_STATE_VERSION:
        preserved_page = st.session_state.get("app_page", "home")
        st.session_state.clear()
        st.session_state["app_state_version"] = APP_STATE_VERSION
        st.session_state["app_page"] = preserved_page


def render_app_state_controls() -> None:
    cols = st.columns([1, 5])
    with cols[0]:
        if st.button("Reset App State", key="reset_app_state_button", use_container_width=True):
            preserved_page = st.session_state.get("app_page", "home")
            st.session_state.clear()
            st.session_state["app_state_version"] = APP_STATE_VERSION
            st.session_state["app_page"] = preserved_page
            st.rerun()

def sanitize_governor_step_size(step_size: float) -> float:
    """
    Guard against stale session/load values from older app versions that may have
    saved a bracket-like number (for example 24) into the governor step size.
    The Break-Even Governor should never use a step size below $1,000.
    """
    try:
        return max(GOVERNOR_MIN_STEP_SIZE, float(step_size))
    except Exception:
        return GOVERNOR_MIN_STEP_SIZE


def sanitize_governor_max_conversion(max_conversion: float) -> float:
    """
    Guard against stale session/load values that accidentally saved a bracket-like
    percentage (for example 24) into the max conversion field. The Break-Even
    Governor's UI uses dollar amounts in $5,000 increments, so tiny positive values
    are almost certainly invalid state rather than intentional user input.
    """
    try:
        value = float(max_conversion)
    except Exception:
        return 300000.0
    if value <= 0:
        return 0.0
    if value < 1000.0:
        return 300000.0
    return value

IRMAA_FIRST_CLIFF_MFJ = 218000.0

DEFAULT_APP_STATE = {
    "app_page": "home",
    "annual_calc_aca_lives": 0,
    "annual_calc_conversions_done": 0.0,
    "annual_calc_earned_income": 0.0,
    "annual_calc_filing_status": "MFJ",
    "annual_calc_income_buffer": 0.0,
    "annual_calc_ira_withdrawals": 0.0,
    "annual_calc_ltcg": 0.0,
    "annual_calc_max_additional_conversion": 0.0,
    "annual_calc_medicare_lives": 0,
    "annual_calc_other_income": 0.0,
    "annual_calc_qualified_dividends": 0.0,
    "annual_calc_standard_deduction": 0.0,
    "annual_calc_state_tax_rate": 0.0,
    "annual_calc_step_size": 1000.0,
    "annual_calc_target_bracket": "22%",
    "annual_calc_total_ss": 0.0,
    "annual_calc_use_aca_guardrail": True,
    "annual_calc_use_bracket_guardrail": True,
    "annual_calc_use_irmaa_guardrail": True,
    "annual_calc_year": START_YEAR,
    "annual_total_ss_for_year": 0.0,
    "annual_external_other_ordinary_income": 0.0,
    "annual_other_ordinary_income": 0.0,
    "annual_realized_ltcg_so_far": 0.0,
    "annual_target_bracket": "22%",
    "annual_income_safety_buffer": 0.0,
    "annual_step_size": 1000.0,
    "annual_max_conversion": 200000.0,
    "annual_apply_bracket_guardrail": True,
    "annual_apply_aca_guardrail": True,
    "annual_apply_irmaa_guardrail": True,
    "annual_conversion": 0.0,
    "annual_spending": 0.0,
    "brokerage": 0.0,
    "brokerage_basis": 0.0,
    "cash": 0.0,
    "cash_sweep_threshold": 0.0,
    "conversion_tax_funding_policy": "Cash then Brokerage",
    "earned_income_annual": 0.0,
    "earned_income_end_year": START_YEAR,
    "earned_income_start_year": START_YEAR,
    "go_go_end_age": 70,
    "go_go_multiplier": 1.0,
    "growth_pct": 0.0,
    "max_conversion": 0.0,
    "no_go_multiplier": 1.2,
    "optimizer_rerun_best_validation": False,
    "owner_claim_age": 62,
    "owner_current_age": 0,
    "owner_ss_base": 0.0,
    "post_aca_target_bracket": "22%",
    "primary_aca_end_year": START_YEAR,
    "retirement_smile_enabled": False,
    "rmd_era_target_bracket": "22%",
    "roth": 0.0,
    "run_ss_optimizer_toggle": False,
    "slow_go_end_age": 80,
    "slow_go_multiplier": 0.85,
    "spending_inflation_rate_pct": 0.0,
    "spouse_aca_end_year": START_YEAR,
    "spouse_claim_age": 62,
    "spouse_current_age": 0,
    "spouse_ss_base": 0.0,
    "state_tax_rate": 0.0399,
    "planning_profile": "Balanced",
    "preference_maximize_social_security": False,
    "preference_minimize_trad_ira_for_heirs": False,
    "preference_income_stability_focus": False,
    "step_size": 1000.0,
    "integrity_mode": False,
    "strict_repeatability_check": False,
    "target_trad_balance": 0.0,
    "target_trad_balance_enabled": False,
    "target_trad_override_enabled": False,
    "target_trad_override_max_rate": 0.0,
    "target_after_tax_legacy_mode": "Maximize",
    "target_after_tax_legacy_custom": 10000000.0,
    "trad": 0.0,
    "trad_balance_penalty_lambda": 1.00,
    "validation_tolerance": 0.01,
}

SCENARIO_STATE_KEYS = [k for k in DEFAULT_APP_STATE.keys() if k != "app_page"]
PAGE_STATE_KEY_PREFIXES = {
    "annual": ["annual_calc_", "annual_"],
    "conversion": [
        "annual_", "brokerage", "cash", "conversion_tax_funding_policy", "earned_income_annual",
        "earned_income_end_year", "earned_income_start_year", "go_go_", "growth_pct", "max_conversion",
        "no_go_", "optimizer_", "owner_", "post_aca_", "primary_aca_end_year", "retirement_smile_",
        "rmd_era_", "roth", "run_ss_optimizer_toggle", "slow_go_", "spending_inflation_rate_pct",
        "spouse_", "state_tax_rate", "step_size", "integrity_mode", "strict_repeatability_check", "target_trad_",
        "trad", "validation_tolerance", "preference_", "target_after_tax_legacy"
    ],
}


def format_dollars(value: float) -> str:
    try:
        return f"${float(value):,.0f}"
    except Exception:
        return str(value)


def format_signed_dollars(value: float) -> str:
    try:
        numeric = float(value)
        sign = "-" if numeric < 0 else "+"
        return f"{sign}${abs(numeric):,.0f}"
    except Exception:
        return str(value)


def describe_delta(label: str, value: float) -> str:
    try:
        numeric = float(value)
    except Exception:
        return f"changes {label} by {value}"
    if abs(numeric) < 1:
        return f"does not change {label} (${0:,.0f})"
    if numeric > 0:
        return f"increases {label} by ${abs(numeric):,.0f}"
    return f"decreases {label} by ${abs(numeric):,.0f}"


def format_percent(value: float) -> str:
    try:
        return f"{float(value):.2%}"
    except Exception:
        return str(value)


def _coerce_numeric_or_none(value):
    try:
        if value is None:
            return None
        if isinstance(value, str) and value.strip() == "":
            return None
        return float(value)
    except Exception:
        return None


def _extract_display_conversion_value(row: pd.Series):
    candidate_keys = [
        "Chosen Conversion",
        "Conversion Income Component",
        "selected_conversion",
        "chosen_conversion",
        "conversion_amount",
        "roth_conversion",
        "conversion",
        "conversion_income",
    ]
    values = {}
    for key in candidate_keys:
        if key in row.index:
            values[key] = row.get(key)

    for key in candidate_keys:
        if key in values:
            num = _coerce_numeric_or_none(values[key])
            if num is not None:
                target = _coerce_numeric_or_none(row.get("Target Bracket"))
                if target is not None and abs(num - target) < 1e-9 and num <= 50:
                    continue
                return num

    num = _coerce_numeric_or_none(row.get("Chosen Conversion"))
    return 0.0 if num is None else num


def build_chosen_path_display_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df

    working = df.copy()
    working["Chosen Conversion Display"] = working.apply(_extract_display_conversion_value, axis=1)

    if "Binding Constraint" not in working.columns:
        def _binding_constraint(row: pd.Series) -> str:
            target = str(row.get("Target Bracket", "") or "")
            if target == "ACA":
                return "ACA"
            return target if target else ""
        working["Binding Constraint"] = working.apply(_binding_constraint, axis=1)

    preferred_cols = [
        "Year",
        "Chosen Conversion Display",
        "Binding Constraint",
        "Target Bracket",
        "Current Marginal Tax Rate",
        "Effective Tax Rate",
        "SOY Trad",
        "EOY Trad",
        "SOY Roth",
        "EOY Roth",
        "SOY Brokerage",
        "EOY Brokerage",
        "EOY Brokerage Basis",
        "MAGI",
        "Taxable Income",
        "Federal Tax",
        "State Tax",
        "ACA Cost",
        "IRMAA Cost",
        "Net Worth",
    ]
    cols = [c for c in preferred_cols if c in working.columns]
    out = working[cols].copy()
    rename_map = {
        "Chosen Conversion Display": "Chosen Conversion ($)",
        "Binding Constraint": "Binding Constraint",
        "Target Bracket": "Target Bracket (%)",
        "Current Marginal Tax Rate": "Marginal Tax Rate (%)",
        "Effective Tax Rate": "Effective Tax Rate (%)",
        "SOY Trad": "Starting Traditional IRA",
        "EOY Trad": "Ending Traditional IRA",
        "SOY Roth": "Starting Roth",
        "EOY Roth": "Ending Roth",
        "SOY Brokerage": "Starting Brokerage",
        "EOY Brokerage": "Ending Brokerage",
        "EOY Brokerage Basis": "Ending Brokerage Basis",
    }
    out = out.rename(columns=rename_map)
    return out


def build_funding_debug_view_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    preferred_cols = [
        "Year",
        "Annual Spending Need",
        "Spending Funded From Cash",
        "Spending Funded From Brokerage",
        "Spending Brokerage Basis Used",
        "Brokerage Realized LTCG",
        "Spending Trad Withdrawal Component",
        "Spending Funded From Roth",
        "RMD Income Component",
        "Chosen Conversion",
        "Earned Income",
        "Total SS",
        "AGI",
        "MAGI",
        "Taxable Income",
        "Federal Tax",
        "State Tax",
        "ACA Cost",
        "IRMAA Cost",
        "Tax Funding Source",
    ]
    cols = [c for c in preferred_cols if c in df.columns]
    out = df[cols].copy()
    rename_map = {
        "Annual Spending Need": "Annual Spending Need",
        "Spending Funded From Cash": "Cash Used For Spending",
        "Spending Funded From Brokerage": "Brokerage Used For Spending",
        "Spending Brokerage Basis Used": "Brokerage Basis Used",
        "Brokerage Realized LTCG": "Realized LTCG",
        "Spending Trad Withdrawal Component": "Traditional IRA Used For Spending",
        "Spending Funded From Roth": "Roth Used For Spending",
        "RMD Income Component": "RMD Income",
        "Chosen Conversion": "Roth Conversion",
        "Earned Income": "Earned Income",
        "Total SS": "Total Social Security",
        "AGI": "AGI",
        "MAGI": "MAGI",
        "Taxable Income": "Taxable Income",
        "Federal Tax": "Federal Tax",
        "State Tax": "State Tax",
        "ACA Cost": "ACA Cost",
        "IRMAA Cost": "IRMAA Cost",
        "Tax Funding Source": "Tax Funding Source",
    }
    out = out.rename(columns=rename_map)
    return out


def summarize_funding_debug_view(df: pd.DataFrame) -> dict:
    if df is None or df.empty:
        return {}

    working = df.copy()
    numeric_cols = [
        "Cash Used For Spending",
        "Brokerage Used For Spending",
        "Brokerage Basis Used",
        "Realized LTCG",
        "Traditional IRA Used For Spending",
        "Roth Used For Spending",
        "RMD Income",
        "Roth Conversion",
        "Earned Income",
        "Total Social Security",
        "MAGI",
        "Annual Spending Need",
    ]
    for col in numeric_cols:
        if col in working.columns:
            working[col] = pd.to_numeric(working[col], errors="coerce").fillna(0.0)

    totals = {
        "cash": float(working.get("Cash Used For Spending", pd.Series(dtype=float)).sum()),
        "brokerage": float(working.get("Brokerage Used For Spending", pd.Series(dtype=float)).sum()),
        "basis": float(working.get("Brokerage Basis Used", pd.Series(dtype=float)).sum()),
        "ltcg": float(working.get("Realized LTCG", pd.Series(dtype=float)).sum()),
        "trad_spending": float(working.get("Traditional IRA Used For Spending", pd.Series(dtype=float)).sum()),
        "roth": float(working.get("Roth Used For Spending", pd.Series(dtype=float)).sum()),
        "rmd": float(working.get("RMD Income", pd.Series(dtype=float)).sum()),
        "ss": float(working.get("Total Social Security", pd.Series(dtype=float)).sum()),
        "earned": float(working.get("Earned Income", pd.Series(dtype=float)).sum()),
        "spending": float(working.get("Annual Spending Need", pd.Series(dtype=float)).sum()),
    }
    totals["trad_total"] = totals["trad_spending"] + totals["rmd"]

    refill_totals = {
        "Social Security": totals["ss"],
        "Earned Income": totals["earned"],
        "Brokerage": totals["brokerage"],
        "Traditional IRA": totals["trad_total"],
        "Roth": totals["roth"],
    }
    primary_refill_source = max(refill_totals.items(), key=lambda item: item[1])[0] if any(v > 0 for v in refill_totals.values()) else "None"

    basis_share = 0.0
    if totals["brokerage"] > 0:
        basis_share = min(1.0, max(0.0, totals["basis"] / totals["brokerage"]))

    low_magi_support_years = 0
    if {"Annual Spending Need", "MAGI"}.issubset(set(working.columns)):
        low_magi_support_years = int(((working["Annual Spending Need"] > 0) & (working["MAGI"] < working["Annual Spending Need"])).sum())

    roth_spending_years = int((working.get("Roth Used For Spending", pd.Series(dtype=float)) > 0).sum()) if "Roth Used For Spending" in working.columns else 0
    brokerage_years = int((working.get("Brokerage Used For Spending", pd.Series(dtype=float)) > 0).sum()) if "Brokerage Used For Spending" in working.columns else 0

    insights = [
        "Cash is a staging account in this model, so the key question is what source refilled cash before spending was paid."
    ]
    if totals["brokerage"] > 0:
        insights.append(
            f"Brokerage funded {format_dollars(totals['brokerage'])} of spending, and about {basis_share:.0%} of that came from basis rather than gains."
        )
    if totals["ltcg"] > 0 and totals["brokerage"] > 0:
        insights.append(
            f"Only {format_dollars(totals['ltcg'])} of brokerage spending showed up as realized LTCG."
        )
    if low_magi_support_years > 0:
        insights.append(
            f"In {low_magi_support_years} year(s), spending exceeded MAGI, which usually means basis, cash, or Roth supported spending without fully hitting income."
        )
    if roth_spending_years > 0:
        insights.append(
            f"Roth funded spending in {roth_spending_years} year(s)."
        )

    return {
        "Primary Cash Refill Source": primary_refill_source,
        "Total Spending": totals["spending"],
        "Brokerage Basis Share": basis_share,
        "Low MAGI Support Years": low_magi_support_years,
        "Roth Spending Years": roth_spending_years,
        "Brokerage Spending Years": brokerage_years,
        "Insights": insights,
    }


PROFILE_PRESETS = {
    "Balanced": {
        "weights": {"nw": 0.24, "legacy": 0.14, "trad": 0.18, "stability": 0.18, "risk": 0.10, "drag": 0.08, "trad_share": 0.08},
        "description": "You are balancing growth, tax efficiency, and long-term stability.",
        "bullets": [
            "avoid extreme strategies in either direction",
            "give moderate credit to tax efficiency and stability",
            "look for outcomes that reduce regret across multiple priorities",
        ],
        "tradeoff": "This approach may not produce the single highest projected net worth, but it is designed to be more well-rounded.",
    },
    "Growth": {
        "weights": {"nw": 0.52, "legacy": 0.10, "trad": 0.06, "stability": 0.12, "risk": 0.12, "drag": 0.04, "trad_share": 0.04},
        "description": "You are prioritizing maximum projected long-term wealth.",
        "bullets": [
            "favor strategies that keep more assets invested",
            "place less emphasis on shrinking Traditional IRA balances",
            "accept more reliance on market returns later in life",
        ],
        "tradeoff": "This approach may increase upside, but it can also increase future tax exposure and market dependence.",
    },
    "Tax-Efficient Stability": {
        "weights": {"nw": 0.10, "legacy": 0.20, "trad": 0.34, "stability": 0.14, "risk": 0.04, "drag": 0.22, "trad_share": 0.20},
        "description": "You are prioritizing tax efficiency, lower Traditional IRA burden, and more stable later-life income.",
        "bullets": [
            "favor strategies that improve Roth conversion opportunities",
            "penalize large Traditional IRA balances at death",
            "give more credit to delayed Social Security and stronger guaranteed income",
        ],
        "tradeoff": "This approach may sacrifice some upside in exchange for lower future tax burden and greater confidence later in retirement.",
    },
    "Legacy Focused": {
        "weights": {"nw": 0.01, "legacy": 0.76, "trad": 0.24, "stability": 0.05, "risk": 0.02, "drag": 0.08, "trad_share": 0.28},
        "description": "You are prioritizing what heirs are likely to keep after taxes, not just raw estate size.",
        "bullets": [
            "favor more tax-efficient assets at death",
            "penalize large remaining Traditional IRA balances",
            "accept some reduction in projected net worth if legacy quality improves",
        ],
        "tradeoff": "This approach may reduce maximum projected wealth somewhat, but it can improve after-tax inheritance value.",
    },
    "Spend With Confidence": {
        "weights": {"nw": 0.08, "legacy": 0.06, "trad": 0.12, "stability": 0.40, "risk": 0.20, "drag": 0.04, "trad_share": 0.04},
        "description": "You are prioritizing confidence, flexibility, and the ability to enjoy retirement spending safely.",
        "bullets": [
            "place more value on reliable income and stability",
            "reduce emphasis on maximizing wealth at death",
            "favor strategies that support spending without excessive fear of future shortfall",
        ],
        "tradeoff": "This approach may leave less money at death than a growth-focused strategy, but it is designed to support a more confident retirement lifestyle.",
    },
}

QUICK_STRATEGY_COMBOS = [(62, 62), (67, 67), (70, 70), (70, 67), (67, 70), (62, 67), (67, 62)]
QUICK_RECOMMENDATION_MAX_CONVERSION = 300000.0
QUICK_RECOMMENDATION_STEP_SIZE = 1000.0

HEIR_EFFECTIVE_TRAD_TAX_RATE = 0.40
TAX_EFFICIENT_EFFECTIVE_TRAD_TAX_RATE = 0.32
SOCIAL_SECURITY_PRESENT_VALUE_MULTIPLIER = 22.0
SURVIVOR_SOCIAL_SECURITY_PRESENT_VALUE_MULTIPLIER = 12.0


def normalize_series(values):
    vals = [float(v) for v in values]
    vmin = min(vals)
    vmax = max(vals)
    if abs(vmax - vmin) < 1e-9:
        return [0.5 for _ in vals]
    return [(v - vmin) / (vmax - vmin) for v in vals]


def qualitative_bucket(norm_value: float, reverse: bool = False) -> str:
    v = float(norm_value)
    if reverse:
        if v <= 0.33:
            return "Low"
        if v <= 0.66:
            return "Medium"
        return "High"
    if v >= 0.67:
        return "High"
    if v >= 0.34:
        return "Medium"
    return "Low"


def get_profile_summary(profile_name: str) -> dict:
    return PROFILE_PRESETS.get(profile_name, PROFILE_PRESETS["Balanced"])


def extract_scoring_preferences(source: dict | None) -> dict:
    source = source or {}
    return {
        "maximize_social_security": bool(source.get("preference_maximize_social_security", False)),
        "minimize_trad_ira_for_heirs": bool(source.get("preference_minimize_trad_ira_for_heirs", False)),
        "income_stability_focus": bool(source.get("preference_income_stability_focus", False)),
    }


def describe_active_scoring_preferences(preferences: dict) -> str:
    labels = []
    if preferences.get("maximize_social_security"):
        labels.append("Maximize Social Security")
    if preferences.get("minimize_trad_ira_for_heirs"):
        labels.append("Minimize Traditional IRA for heirs")
    if preferences.get("income_stability_focus"):
        labels.append("Income stability focus")
    return ", ".join(labels) if labels else "None"


def get_profile_default_tilts(profile_name: str) -> list[str]:
    default_map = {
        "Balanced": ["moderate income stability", "moderate tax efficiency", "moderate total wealth"],
        "Growth": ["maximum long-term wealth", "lighter Social Security emphasis", "lower penalty on Traditional IRA at death"],
        "Tax-Efficient Stability": ["lower lifetime tax drag", "lower Traditional IRA burden", "stronger late-life stability"],
        "Legacy Focused": ["higher after-tax inheritance", "smaller Traditional IRA for heirs", "cleaner inheritance structure"],
        "Spend With Confidence": ["higher guaranteed income", "income stability", "delayed Social Security"],
    }
    return default_map.get(profile_name, default_map["Balanced"])


def build_strategy_selection_summary(profile_name: str, preferences: dict) -> dict:
    defaults = get_profile_default_tilts(profile_name)
    modifier_lines = []
    note_lines = []

    if preferences.get("maximize_social_security"):
        modifier_lines.append("Maximize Social Security")
        if profile_name == "Growth":
            note_lines.append("This adds a delayed-Social-Security tilt on top of Growth. It does not turn Growth off, but it can pull rankings away from earlier-claim strategies.")
        elif profile_name in {"Spend With Confidence", "Tax-Efficient Stability"}:
            note_lines.append("This reinforces a trait the selected profile already tends to favor.")
    if preferences.get("minimize_trad_ira_for_heirs"):
        modifier_lines.append("Minimize Traditional IRA for heirs")
        if profile_name == "Legacy Focused":
            note_lines.append("This reinforces Legacy Focused and makes the ranking lean harder toward smaller Traditional IRA balances and lower heir tax drag.")
    if preferences.get("income_stability_focus"):
        modifier_lines.append("Income stability focus")
        if profile_name in {"Balanced", "Spend With Confidence"}:
            note_lines.append("This reinforces the profile's natural stability bias.")

    if not modifier_lines:
        modifier_lines = ["No extra modifiers selected"]
    if not note_lines:
        note_lines = ["Your profile sets the base ranking logic. Modifiers act as nudges on top of that base profile rather than replacing it."]

    return {
        "title": f"Current ranking lens: {profile_name}",
        "defaults": defaults,
        "modifiers": modifier_lines,
        "notes": note_lines,
    }


def scoring_preferences_match(current_profile: str, current_preferences: dict, prior_profile: str | None, prior_preferences: dict | None) -> bool:
    return str(current_profile or "") == str(prior_profile or "") and dict(current_preferences or {}) == dict(prior_preferences or {})


def render_optimizer_status_panel(inputs: dict, max_conversion: float, step_size: float, trad_balance_penalty_lambda: float, planning_profile: str, current_preferences: dict) -> None:
    last_result = get_current_result_payload("ss_optimizer_last_result")
    if last_result is None:
        st.info("No 81-combination Social Security optimizer result is currently stored in this session. Run the optimizer after setting your assumptions if you want a fresh strategy universe.")
        return

    optimizer_inputs_snapshot = copy.deepcopy(inputs)
    optimizer_inputs_snapshot.update({
        "max_conversion": max_conversion,
        "step_size": step_size,
        "trad_balance_penalty_lambda": trad_balance_penalty_lambda,
        "optimizer_is_profile_neutral": True,
    })
    facts_changed = inputs_are_stale("ss_optimizer", optimizer_inputs_snapshot)
    prior_preferences = last_result.get("scoring_preferences_snapshot", {})
    prior_profile = last_result.get("planning_profile_snapshot")
    ranking_changed = not scoring_preferences_match(planning_profile, current_preferences, prior_profile, prior_preferences)

    if facts_changed:
        st.error("Scenario facts changed since the last 81-combination run. Re-run the optimizer to regenerate the strategy universe before trusting the rankings below.")
    elif ranking_changed:
        st.warning("Your ranking lens changed since the last optimizer scoring snapshot. Use 'Re-rank Existing 81 Results' to refresh the Top 5 profile shortlists without rerunning the 81-combination engine.")
    else:
        st.success("Scenario facts and ranking preferences match the last 81-combination result. You can review the current rankings without rerunning anything.")

    cols = st.columns(3)
    cols[0].metric("Scenario facts", "Changed" if facts_changed else "Up to date")
    cols[1].metric("Ranking lens", "Changed" if ranking_changed else "Up to date")
    cols[2].metric("Last scoring profile", str(prior_profile or planning_profile))


def rerank_existing_optimizer_result(result_payload: dict, preferences: dict | None = None) -> dict:
    """
    Rebuild profile shortlists from an already-computed 81-row optimizer result set.
    This does not rerun the engine. It only reapplies profile scoring / modifiers.
    """
    if result_payload is None:
        return result_payload
    working = copy.deepcopy(result_payload)
    all_results_df = working.get("all_results_df")
    if all_results_df is None:
        return working
    if isinstance(all_results_df, pd.DataFrame):
        results_rows = all_results_df.to_dict("records")
    else:
        try:
            results_rows = pd.DataFrame(all_results_df).to_dict("records")
        except Exception:
            return working
    working["profile_shortlists"] = build_profile_shortlists_from_optimizer_rows(
        results_rows,
        preferences=preferences or {},
        scoring_context=build_strategy_scoring_context(inputs=collect_scenario_state(), metrics_list=results_rows),
    )
    working["scoring_preferences_snapshot"] = copy.deepcopy(preferences or {})
    return working


def estimate_social_security_present_value(final_household_ss_income: float, survivor_ss_income: float) -> float:
    return (
        max(0.0, float(final_household_ss_income)) * SOCIAL_SECURITY_PRESENT_VALUE_MULTIPLIER
        + max(0.0, float(survivor_ss_income)) * SURVIVOR_SOCIAL_SECURITY_PRESENT_VALUE_MULTIPLIER
    )


def get_break_even_governor_presets(profile_name: str, current_trad_balance: float) -> dict:
    current_trad_balance = float(current_trad_balance or 0.0)
    seeded_target = current_trad_balance * 0.25 if current_trad_balance > 0 else 0.0
    presets = {
        "Growth": {
            "target_trad_balance_enabled": False,
            "target_trad_override_enabled": False,
            "target_trad_override_max_rate": 0.0,
            "post_aca_target_bracket": "22%",
            "rmd_era_target_bracket": "22%",
            "preset_note": "Growth preset: lighter conversion pressure, more emphasis on keeping assets invested.",
        },
        "Balanced": {
            "target_trad_balance_enabled": False,
            "target_trad_override_enabled": False,
            "target_trad_override_max_rate": 0.0,
            "post_aca_target_bracket": "22%",
            "rmd_era_target_bracket": "22%",
            "preset_note": "Balanced preset: neutral conversion posture with moderate bracket targets.",
        },
        "Tax-Efficient Stability": {
            "target_trad_balance_enabled": True,
            "target_trad_balance": seeded_target,
            "target_trad_override_enabled": True,
            "target_trad_override_max_rate": 0.35,
            "post_aca_target_bracket": "24%",
            "rmd_era_target_bracket": "24%",
            "preset_note": "Tax-Efficient Stability preset: stronger Roth conversion push and more willingness to use conversion runway.",
        },
        "Legacy Focused": {
            "target_trad_balance_enabled": True,
            "target_trad_balance": seeded_target,
            "target_trad_override_enabled": True,
            "target_trad_override_max_rate": 0.40,
            "post_aca_target_bracket": "24%",
            "rmd_era_target_bracket": "24%",
            "preset_note": "Legacy Focused preset: stronger pressure to reduce Traditional IRA and improve after-tax inheritance structure.",
        },
        "Spend With Confidence": {
            "target_trad_balance_enabled": True,
            "target_trad_balance": seeded_target,
            "target_trad_override_enabled": True,
            "target_trad_override_max_rate": 0.30,
            "post_aca_target_bracket": "22%",
            "rmd_era_target_bracket": "22%",
            "preset_note": "Spend With Confidence preset: emphasizes steadier income support while keeping conversion settings moderate.",
        },
    }
    return presets.get(profile_name, presets["Balanced"])


def apply_break_even_governor_profile_presets(profile_name: str, current_trad_balance: float, force: bool = False) -> None:
    preset = get_break_even_governor_presets(profile_name, current_trad_balance)
    for key, value in preset.items():
        if key == "preset_note":
            continue
        if force or key not in st.session_state:
            st.session_state[key] = value
    st.session_state["selected_recommendation_profile"] = profile_name
    st.session_state["break_even_governor_preset_note"] = preset.get("preset_note", "")
    st.session_state["break_even_governor_presets_applied"] = True


def build_profile_adjusted_inputs(profile_name: str, inputs: dict) -> tuple[dict, dict]:
    adjusted = copy.deepcopy(inputs)
    preset = get_break_even_governor_presets(profile_name, float(adjusted.get("trad", 0.0)))
    for key, value in preset.items():
        if key == "preset_note":
            continue
        adjusted[key] = copy.deepcopy(value)
    adjusted["planning_profile"] = profile_name
    adjusted["selected_recommendation_profile"] = profile_name
    adjusted["break_even_governor_preset_note"] = preset.get("preset_note", "")
    return adjusted, preset


def build_stateless_quick_recommendation_inputs(current_inputs: dict, profile_name: str) -> tuple[dict, dict]:
    """
    Build a clean quick-recommendation input package from the live scenario inputs.
    Quick Scan should use the same core engine inputs as the Full 81 scan; the only
    intended difference is the reduced SS-claim candidate universe.
    """
    adjusted = copy.deepcopy(current_inputs)
    adjusted["planning_profile"] = profile_name
    adjusted["selected_recommendation_profile"] = profile_name
    return adjusted, {"preset_note": ""}


def build_ss_optimizer_result_row(
    run_result: dict,
    owner_age: int,
    spouse_age: int,
    trad_balance_penalty_lambda: float = 1.00,
) -> dict:
    metrics = build_strategy_metrics(run_result)
    trad_penalty_applied = float(trad_balance_penalty_lambda) * float(run_result["ending_trad_balance"])
    return {
        "Owner SS Age": int(owner_age),
        "Spouse SS Age": int(spouse_age),
        "Strategy": f"{int(owner_age)}/{int(spouse_age)}",
        "Final Net Worth": float(run_result["final_net_worth"]),
        "After-Tax Legacy": float(metrics["after_tax_legacy"]),
        "Effective Legacy Value": float(metrics.get("effective_legacy_value", metrics["after_tax_legacy"])),
        "Heir Tax Drag": float(metrics.get("heir_tax_drag", 0.0)),
        "Ending Roth Balance": float(metrics["ending_roth_balance"]),
        "Ending Brokerage Balance": float(metrics["ending_brokerage_balance"]),
        "Ending Cash Balance": float(metrics["ending_cash_balance"]),
        "Stability Value": float(metrics["stability_value"]),
        "Risk Value": float(metrics["risk_value"]),
        "Final Household SS Income": float(metrics["final_household_ss_income"]),
        "Survivor SS Income": float(metrics["survivor_ss_income"]),
        "Social Security Present Value": float(metrics.get("social_security_present_value", 0.0)),
        "Total Federal Tax": float(run_result["total_federal_taxes"]),
        "Total State Tax": float(run_result.get("total_state_taxes", 0.0)),
        "Total ACA Cost": float(run_result["total_aca_cost"]),
        "Total IRMAA Cost": float(run_result["total_irmaa_cost"]),
        "Total Government Drag": float(run_result.get("total_government_drag", 0.0)),
        "Total Conversions": float(run_result["total_conversions"]),
        "Ending Traditional IRA Balance": float(run_result["ending_trad_balance"]),
        "First IRMAA Year": run_result["first_irmaa_year"],
        "Max MAGI": float(run_result["max_magi"]),
        "ACA Hit Years": int(run_result["aca_hit_years"]),
        "IRMAA Hit Years": int(run_result["irmaa_hit_years"]),
        "Traditional IRA Penalty Applied": trad_penalty_applied,
        "Score": float(run_result["final_net_worth"]) - trad_penalty_applied,
    }


def get_ss_scan_candidate_combos(scan_mode: str) -> list[tuple[int, int]]:
    mode = str(scan_mode or "").strip().lower()
    if mode == "quick":
        return [(int(a), int(b)) for a, b in QUICK_STRATEGY_COMBOS]
    if mode == "full":
        return [(owner_age, spouse_age) for owner_age in range(62, 71) for spouse_age in range(62, 71)]
    raise ValueError(f"Unknown SS scan mode: {scan_mode}")


def prepare_ss_scan_base_inputs(inputs: dict) -> dict:
    base_snapshot = copy.deepcopy(inputs or {})
    base_snapshot["integrity_mode"] = False
    base_snapshot["strict_repeatability_check"] = False
    return base_snapshot


def evaluate_single_ss_candidate(
    base_inputs: dict,
    owner_age: int,
    spouse_age: int,
    max_conversion: float,
    step_size: float,
    trad_balance_penalty_lambda: float = 0.0,
) -> dict:
    scenario_inputs = dict(base_inputs)
    scenario_inputs["owner_claim_age"] = int(owner_age)
    scenario_inputs["spouse_claim_age"] = int(spouse_age)
    run_result = run_model_break_even_governor(
        scenario_inputs,
        sanitize_governor_max_conversion(float(max_conversion)),
        sanitize_governor_step_size(float(step_size)),
    )
    return build_ss_optimizer_result_row(
        run_result,
        owner_age,
        spouse_age,
        trad_balance_penalty_lambda=trad_balance_penalty_lambda,
    )


def evaluate_ss_scan_candidates(
    base_inputs: dict,
    candidate_combos: list[tuple[int, int]],
    max_conversion: float,
    step_size: float,
    trad_balance_penalty_lambda: float = 0.0,
    progress_prefix: str = "Running SS scan",
    initial_results: list | None = None,
    start_index: int = 0,
    progress_callback=None,
    error_callback=None,
) -> tuple[list[dict], list[str]]:
    results = list(initial_results or [])
    errors: list[str] = []
    total = len(candidate_combos)
    progress_bar = st.progress(start_index / total if total else 0.0, text=f"{progress_prefix}...") if total else None
    try:
        for combo_index in range(start_index, total):
            owner_age, spouse_age = candidate_combos[combo_index]
            try:
                row = evaluate_single_ss_candidate(
                    base_inputs, owner_age, spouse_age, max_conversion, step_size, trad_balance_penalty_lambda=trad_balance_penalty_lambda
                )
                results.append(row)
                if progress_callback is not None:
                    progress_callback(combo_index, owner_age, spouse_age, results)
            except Exception as exc:
                errors.append(f"{owner_age}/{spouse_age}: {exc}")
                if error_callback is not None:
                    payload = error_callback(combo_index, owner_age, spouse_age, exc, results)
                    if payload is not None:
                        return payload, errors
            finally:
                if progress_bar is not None:
                    progress_bar.progress((combo_index + 1) / total, text=f"{progress_prefix}... {combo_index + 1}/{total}")
    finally:
        if progress_bar is not None:
            progress_bar.empty()
    return results, errors


def score_rank_ss_scan_rows(
    results_rows: list[dict],
    inputs: dict,
    selected_profile_name: str,
    preferences: dict | None = None,
    trad_balance_penalty_lambda: float = 0.0,
    shortlist_top_n: int = 10,
) -> dict:
    return build_scored_strategy_outputs(
        results_rows,
        inputs=inputs,
        selected_profile_name=selected_profile_name,
        preferences=preferences or {},
        trad_balance_penalty_lambda=trad_balance_penalty_lambda,
        shortlist_top_n=shortlist_top_n,
    )


def build_ss_optimizer_fact_rows(
    inputs: dict,
    combos: list[tuple[int, int]],
    max_conversion: float,
    step_size: float,
    trad_balance_penalty_lambda: float = 1.00,
    progress_text: str | None = None,
    cache_namespace: str = "_ss_optimizer_fact_rows_cache",
) -> tuple[list[dict], list[str]]:
    cache = st.session_state.setdefault(cache_namespace, {})
    base_snapshot = copy.deepcopy(inputs)
    base_snapshot["integrity_mode"] = False
    base_snapshot["strict_repeatability_check"] = False
    fact_key = build_scenario_fingerprint(
        {**base_snapshot, "combos": [(int(a), int(b)) for a, b in combos], "trad_balance_penalty_lambda": float(trad_balance_penalty_lambda)},
        max_conversion,
        step_size,
    )
    cached = cache.get(fact_key)
    if isinstance(cached, dict):
        cached_rows = copy.deepcopy(cached.get("rows", []))
        cached_errors = copy.deepcopy(cached.get("errors", []))
        if cached_rows:
            return cached_rows, cached_errors

    rows: list[dict] = []
    errors: list[str] = []
    total = len(combos)
    progress_bar = st.progress(0.0, text=progress_text or "Running optimizer...") if total else None
    for idx, (owner_age, spouse_age) in enumerate(combos, start=1):
        scenario_inputs = dict(base_snapshot)
        scenario_inputs["owner_claim_age"] = int(owner_age)
        scenario_inputs["spouse_claim_age"] = int(spouse_age)
        try:
            run_result = run_model_break_even_governor(scenario_inputs, max_conversion, step_size)
            rows.append(build_ss_optimizer_result_row(run_result, owner_age, spouse_age, trad_balance_penalty_lambda=trad_balance_penalty_lambda))
        except Exception as exc:
            errors.append(f"{owner_age}/{spouse_age}: {exc}")
        finally:
            if progress_bar is not None:
                progress_bar.progress(idx / total, text=(progress_text or "Running optimizer...").split('...')[0] + f"... {idx}/{total}")
    if progress_bar is not None:
        progress_bar.empty()
    cache[fact_key] = {"rows": copy.deepcopy(rows), "errors": copy.deepcopy(errors)}
    if len(cache) > 6:
        while len(cache) > 6:
            cache.pop(next(iter(cache)))
    return rows, errors


def build_quick_recommendation_fact_rows(base_inputs: dict, quick_max_conversion: float, quick_step_size: float) -> tuple[list[dict], list[str]]:
    """
    Build or reuse the quick-scan fact set used by SS Optimizer Quick Scan.
    This intentionally tests only a small anchor set so the quick scan stays fast.
    The underlying per-strategy evaluation path must match the Full 81 scan.
    """
    result_rows, errors = build_ss_optimizer_fact_rows(
        base_inputs,
        QUICK_STRATEGY_COMBOS,
        quick_max_conversion,
        quick_step_size,
        trad_balance_penalty_lambda=float(base_inputs.get("trad_balance_penalty_lambda", DEFAULT_APP_STATE["trad_balance_penalty_lambda"])),
        progress_text="Running Quick Scan... 0/7",
        cache_namespace="_quick_recommendation_factset_cache",
    )
    metric_rows: list[dict] = []
    for row in result_rows:
        metric_rows.append({
            "Strategy": str(row.get("Strategy", "")),
            "Owner SS Age": int(row.get("Owner SS Age", 0)),
            "Spouse SS Age": int(row.get("Spouse SS Age", 0)),
            "final_net_worth": float(row.get("Final Net Worth", 0.0)),
            "after_tax_legacy": float(row.get("After-Tax Legacy", 0.0)),
            "effective_legacy_value": float(row.get("Effective Legacy Value", row.get("After-Tax Legacy", 0.0))),
            "heir_tax_drag": float(row.get("Heir Tax Drag", 0.0)),
            "ending_traditional_ira_balance": float(row.get("Ending Traditional IRA Balance", 0.0)),
            "ending_roth_balance": float(row.get("Ending Roth Balance", 0.0)),
            "ending_brokerage_balance": float(row.get("Ending Brokerage Balance", 0.0)),
            "ending_cash_balance": float(row.get("Ending Cash Balance", 0.0)),
            "stability_value": float(row.get("Stability Value", 0.0)),
            "risk_value": float(row.get("Risk Value", 0.0)),
            "final_household_ss_income": float(row.get("Final Household SS Income", 0.0)),
            "survivor_ss_income": float(row.get("Survivor SS Income", 0.0)),
            "social_security_present_value": float(row.get("Social Security Present Value", 0.0)),
            "Total Government Drag": float(row.get("Total Government Drag", 0.0)),
            "Total Conversions": float(row.get("Total Conversions", 0.0)),
            "Total Federal Tax": float(row.get("Total Federal Tax", 0.0)),
            "Total State Tax": float(row.get("Total State Tax", 0.0)),
            "Total ACA Cost": float(row.get("Total ACA Cost", 0.0)),
            "Total IRMAA Cost": float(row.get("Total IRMAA Cost", 0.0)),
            "First IRMAA Year": row.get("First IRMAA Year"),
            "Max MAGI": float(row.get("Max MAGI", 0.0)),
            "ACA Hit Years": int(row.get("ACA Hit Years", 0)),
            "IRMAA Hit Years": int(row.get("IRMAA Hit Years", 0)),
        })
    return metric_rows, errors

    metric_rows: list[dict] = []
    errors: list[str] = []
    total_quick_combos = len(QUICK_STRATEGY_COMBOS)
    quick_progress = st.progress(0.0, text=f"Running Quick Scan... 0/{total_quick_combos}")
    for idx, (owner_age, spouse_age) in enumerate(QUICK_STRATEGY_COMBOS, start=1):
            try:
                scenario_inputs = copy.deepcopy(base_inputs)
                scenario_inputs["owner_claim_age"] = int(owner_age)
                scenario_inputs["spouse_claim_age"] = int(spouse_age)
                run_result = run_model_break_even_governor(scenario_inputs, quick_max_conversion, quick_step_size)
                metrics = build_strategy_metrics(run_result)
                metric_rows.append({
                    **metrics,
                    "Strategy": f"{owner_age}/{spouse_age}",
                    "Owner SS Age": int(owner_age),
                    "Spouse SS Age": int(spouse_age),
                    "Final Net Worth": float(metrics["final_net_worth"]),
                    "After-Tax Legacy": float(metrics["after_tax_legacy"]),
                    "Effective Legacy Value": float(metrics.get("effective_legacy_value", metrics["after_tax_legacy"])),
                    "Heir Tax Drag": float(metrics.get("heir_tax_drag", 0.0)),
                    "Ending Traditional IRA Balance": float(metrics["ending_traditional_ira_balance"]),
                    "Roth @ End": float(metrics["ending_roth_balance"]),
                    "Ending Roth Balance": float(metrics["ending_roth_balance"]),
                    "Brokerage @ End": float(metrics["ending_brokerage_balance"]),
                    "Ending Brokerage Balance": float(metrics["ending_brokerage_balance"]),
                    "Ending Cash Balance": float(metrics["ending_cash_balance"]),
                    "Stability Value": float(metrics["stability_value"]),
                    "Risk Value": float(metrics["risk_value"]),
                    "Final Household SS Income": float(metrics["final_household_ss_income"]),
                    "Survivor SS Income": float(metrics["survivor_ss_income"]),
                    "Social Security Present Value": float(metrics.get("social_security_present_value", 0.0)),
                    "Total Federal Tax": float(run_result.get("total_federal_taxes", 0.0)),
                    "Total State Tax": float(run_result.get("total_state_taxes", 0.0)),
                    "Total ACA Cost": float(run_result.get("total_aca_cost", 0.0)),
                    "Total IRMAA Cost": float(run_result.get("total_irmaa_cost", 0.0)),
                    "Total Government Drag": float(run_result.get("total_government_drag", 0.0)),
                    "Total Conversions": float(run_result.get("total_conversions", 0.0)),
                    "Max MAGI": float(run_result.get("max_magi", 0.0)),
                    "ACA Hit Years": int(run_result.get("aca_hit_years", 0)),
                    "IRMAA Hit Years": int(run_result.get("irmaa_hit_years", 0)),
                    "First IRMAA Year": run_result.get("first_irmaa_year"),
                })
            except Exception as exc:
                errors.append(f"{owner_age}/{spouse_age}: {exc}")
            finally:
                quick_progress.progress(idx / total_quick_combos, text=f"Running Quick Scan... {idx}/{total_quick_combos}")

    quick_progress.empty()
    cache[fact_key] = {"metric_rows": copy.deepcopy(metric_rows), "errors": copy.deepcopy(errors)}
    if len(cache) > 6:
        while len(cache) > 6:
            cache.pop(next(iter(cache)))
    return metric_rows, errors


def build_strategy_metrics(run_result: dict) -> dict:
    df = run_result["df"]
    last = df.iloc[-1]
    ending_trad = float(run_result.get("ending_trad_balance", last.get("EOY Trad", 0.0)))
    ending_roth = float(last.get("EOY Roth", 0.0))
    ending_brokerage = float(last.get("EOY Brokerage", 0.0))
    ending_cash = float(last.get("EOY Cash", 0.0))
    ending_owner_ss = float(last.get("Owner SS", 0.0))
    ending_spouse_ss = float(last.get("Spouse SS", 0.0))
    final_household_ss = float(last.get("Total SS", ending_owner_ss + ending_spouse_ss))
    survivor_ss = max(ending_owner_ss, ending_spouse_ss)
    min_liquid_assets = float((df["EOY Roth"] + df["EOY Brokerage"] + df["EOY Cash"]).min())
    after_tax_legacy = ending_roth + ending_cash + 0.95 * ending_brokerage + (1.0 - TAX_EFFICIENT_EFFECTIVE_TRAD_TAX_RATE) * ending_trad
    effective_legacy_value = ending_roth + ending_cash + 0.95 * ending_brokerage + (1.0 - HEIR_EFFECTIVE_TRAD_TAX_RATE) * ending_trad
    heir_tax_drag = ending_trad * HEIR_EFFECTIVE_TRAD_TAX_RATE
    stability_value = final_household_ss + 0.5 * survivor_ss
    social_security_present_value = estimate_social_security_present_value(final_household_ss, survivor_ss)
    risk_value = -min_liquid_assets
    return {
        "final_net_worth": float(run_result["final_net_worth"]),
        "after_tax_legacy": float(after_tax_legacy),
        "effective_legacy_value": float(effective_legacy_value),
        "heir_tax_drag": float(heir_tax_drag),
        "ending_traditional_ira_balance": float(ending_trad),
        "ending_roth_balance": float(ending_roth),
        "ending_brokerage_balance": float(ending_brokerage),
        "ending_cash_balance": float(ending_cash),
        "stability_value": float(stability_value),
        "risk_value": float(risk_value),
        "final_household_ss_income": float(final_household_ss),
        "survivor_ss_income": float(survivor_ss),
        "social_security_present_value": float(social_security_present_value),
    }


def resolve_target_after_tax_legacy(mode: str, custom_value: float) -> float | None:
    mapping = {
        "Maximize": None,
        "$5M": 5_000_000.0,
        "$10M": 10_000_000.0,
        "$20M": 20_000_000.0,
    }
    if mode in mapping:
        return mapping[mode]
    try:
        return max(0.0, float(custom_value))
    except Exception:
        return None


def optimize_spending_for_target_legacy(inputs: dict, max_conversion: float, step_size: float, target_legacy: float) -> dict:
    """
    Find the highest base annual spending that still meets the requested after-tax legacy target,
    using the current SS ages and Break-Even Governor settings.
    """
    max_conversion = sanitize_governor_max_conversion(max_conversion)
    step_size = sanitize_governor_step_size(step_size)
    target_legacy = float(target_legacy)
    base_inputs = copy.deepcopy(inputs)
    current_spending = float(base_inputs.get("annual_spending", 0.0))
    cache: dict[float, dict] = {}

    def evaluate(spending: float) -> dict:
        rounded_spending = float(max(0.0, round(spending / 1000.0) * 1000.0))
        if rounded_spending in cache:
            return cache[rounded_spending]
        scenario_inputs = copy.deepcopy(base_inputs)
        scenario_inputs["annual_spending"] = rounded_spending
        run_result = run_model_break_even_governor(scenario_inputs, max_conversion, step_size)
        metrics = build_strategy_metrics(run_result)
        payload = {
            "annual_spending": rounded_spending,
            "run_result": run_result,
            "metrics": metrics,
        }
        cache[rounded_spending] = payload
        return payload

    baseline = evaluate(current_spending)
    if float(baseline["metrics"]["after_tax_legacy"]) < target_legacy:
        return {
            "target_legacy": target_legacy,
            "status": "not_achievable_from_current_plan",
            "baseline": baseline,
            "optimized": baseline,
            "search_runs": len(cache),
        }

    low = current_spending
    high = max(current_spending * 3.0, current_spending + 50_000.0, 120_000.0)
    high_eval = evaluate(high)
    upper_cap = max(500_000.0, current_spending + 250_000.0)
    while float(high_eval["metrics"]["after_tax_legacy"]) >= target_legacy and high < upper_cap:
        low = high
        high = min(upper_cap, high * 1.5)
        if high <= low:
            break
        high_eval = evaluate(high)

    best = baseline if float(baseline["metrics"]["after_tax_legacy"]) >= target_legacy else None
    low_bound = current_spending
    high_bound = high

    for _ in range(16):
        if high_bound - low_bound <= 1000.0:
            break
        mid = (low_bound + high_bound) / 2.0
        mid_eval = evaluate(mid)
        if float(mid_eval["metrics"]["after_tax_legacy"]) >= target_legacy:
            best = mid_eval
            low_bound = float(mid_eval["annual_spending"])
        else:
            high_bound = float(mid_eval["annual_spending"])

    if best is None:
        best = baseline

    return {
        "target_legacy": target_legacy,
        "status": "ok",
        "baseline": baseline,
        "optimized": best,
        "search_runs": len(cache),
    }


def build_profile_shortlists_from_optimizer_rows(results_rows: list[dict], top_n: int = 5, preferences: dict | None = None, trad_balance_penalty_lambda: float = 0.0, scoring_context: dict | None = None) -> dict[str, pd.DataFrame]:
    if not results_rows:
        return {}

    metric_rows = []
    for row in results_rows:
        metric_rows.append({
            "Strategy": f"{int(row['Owner SS Age'])}/{int(row['Spouse SS Age'])}",
            "Owner SS Age": int(row["Owner SS Age"]),
            "Spouse SS Age": int(row["Spouse SS Age"]),
            "final_net_worth": float(row.get("Final Net Worth", 0.0)),
            "after_tax_legacy": float(row.get("After-Tax Legacy", 0.0)),
            "effective_legacy_value": float(row.get("Effective Legacy Value", row.get("After-Tax Legacy", 0.0))),
            "heir_tax_drag": float(row.get("Heir Tax Drag", 0.0)),
            "ending_traditional_ira_balance": float(row.get("Ending Traditional IRA Balance", 0.0)),
            "ending_roth_balance": float(row.get("Ending Roth Balance", 0.0)),
            "ending_brokerage_balance": float(row.get("Ending Brokerage Balance", 0.0)),
            "ending_cash_balance": float(row.get("Ending Cash Balance", 0.0)),
            "stability_value": float(row.get("Stability Value", 0.0)),
            "risk_value": float(row.get("Risk Value", 0.0)),
            "final_household_ss_income": float(row.get("Final Household SS Income", 0.0)),
            "survivor_ss_income": float(row.get("Survivor SS Income", 0.0)),
            "social_security_present_value": float(row.get("Social Security Present Value", estimate_social_security_present_value(float(row.get("Final Household SS Income", 0.0)), float(row.get("Survivor SS Income", 0.0))))),
            "Total Government Drag": float(row.get("Total Government Drag", 0.0)),
            "Total Conversions": float(row.get("Total Conversions", 0.0)),
            "Total Federal Tax": float(row.get("Total Federal Tax", 0.0)),
            "Total State Tax": float(row.get("Total State Tax", 0.0)),
            "Total ACA Cost": float(row.get("Total ACA Cost", 0.0)),
            "Total IRMAA Cost": float(row.get("Total IRMAA Cost", 0.0)),
            "First IRMAA Year": row.get("First IRMAA Year"),
            "Max MAGI": float(row.get("Max MAGI", 0.0)),
            "ACA Hit Years": int(row.get("ACA Hit Years", 0)),
            "IRMAA Hit Years": int(row.get("IRMAA Hit Years", 0)),
        })

    shortlists = {}
    for profile_name in PROFILE_PRESETS.keys():
        ranked = score_strategy_metrics(metric_rows, profile_name, preferences=preferences, trad_balance_penalty_lambda=trad_balance_penalty_lambda, scoring_context=scoring_context)
        rows = []
        for idx, ranked_row in enumerate(ranked[:top_n], start=1):
            rows.append({
                "Rank": idx,
                "Strategy": ranked_row["Strategy"],
                "Owner SS Age": int(ranked_row["Owner SS Age"]),
                "Spouse SS Age": int(ranked_row["Spouse SS Age"]),
                "Score": float(ranked_row["score_100"]),
                "Net Worth": float(ranked_row["final_net_worth"]),
                "After-Tax Legacy": float(ranked_row["after_tax_legacy"]),
                "Effective Legacy Value": float(ranked_row.get("effective_legacy_value", ranked_row["after_tax_legacy"])),
                "Heir Tax Drag": float(ranked_row.get("heir_tax_drag", 0.0)),
                "Trad IRA @ End": float(ranked_row["ending_traditional_ira_balance"]),
                "Traditional IRA Share @ End": float(ranked_row.get("ending_traditional_ira_share", 0.0)),
                "Roth @ End": float(ranked_row["ending_roth_balance"]),
                "Brokerage @ End": float(ranked_row["ending_brokerage_balance"]),
                "Stability": ranked_row["stability_label"],
                "Risk": ranked_row["risk_label"],
                "Final Household SS Income": float(ranked_row["final_household_ss_income"]),
                "Survivor SS Income": float(ranked_row["survivor_ss_income"]),
                "Total Government Drag": float(ranked_row.get("Total Government Drag", 0.0)),
                "Total Conversions": float(ranked_row.get("Total Conversions", 0.0)),
                "First IRMAA Year": ranked_row.get("First IRMAA Year"),
                "NW Score +": float(ranked_row.get("nw_component", 0.0) * 100.0),
                "Legacy Score +": float(ranked_row.get("legacy_component", 0.0) * 100.0),
                "Stability Score +": float(ranked_row.get("stability_component", 0.0) * 100.0),
                "Trad Penalty -": float(ranked_row.get("trad_component", 0.0) * 100.0),
                "Trad Share Penalty -": float(ranked_row.get("trad_share_component", 0.0) * 100.0),
                "Gov Drag Penalty -": float(ranked_row.get("drag_component", 0.0) * 100.0),
                "Heir Tax Penalty -": float(ranked_row.get("heir_tax_component", 0.0) * 100.0),
                "Risk Penalty -": float(ranked_row.get("risk_component", 0.0) * 100.0),
            })
        shortlists[profile_name] = pd.DataFrame(rows)
    return shortlists


def reorder_ss_optimizer_results_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    preferred = [
        "Rank",
        "Owner SS Age",
        "Spouse SS Age",
        "Strategy",
        "Final Net Worth",
        "After-Tax Legacy",
        "Effective Legacy Value",
        "Heir Tax Drag",
        "Ending Roth Balance",
        "Ending Traditional IRA Balance",
        "Ending Brokerage Balance",
        "Ending Cash Balance",
        "Stability Value",
        "Final Household SS Income",
        "Survivor SS Income",
        "Social Security Present Value",
        "Total Federal Tax",
        "Total State Tax",
        "Total ACA Cost",
        "Total IRMAA Cost",
        "Total Government Drag",
        "Total Conversions",
        "First IRMAA Year",
        "Max MAGI",
        "ACA Hit Years",
        "IRMAA Hit Years",
        "Traditional IRA Penalty Applied",
        "Risk Value",
        "Score",
    ]
    ordered = [c for c in preferred if c in df.columns] + [c for c in df.columns if c not in preferred]
    return df.loc[:, ordered].copy()

def build_ranked_optimizer_results_df(
    results_rows: list[dict],
    profile_name: str,
    preferences: dict | None = None,
    trad_balance_penalty_lambda: float = 0.0,
    scoring_context: dict | None = None,
) -> pd.DataFrame:
    if not results_rows:
        return pd.DataFrame()

    scoring_payload = build_strategy_scoring_payload(
        results_rows,
        selected_profile_name=profile_name,
        preferences=preferences or {},
        trad_balance_penalty_lambda=trad_balance_penalty_lambda,
        scoring_context=scoring_context,
    )
    ranked = scoring_payload["selected_ranked_rows"]
    profile_score_maps = scoring_payload["profile_score_maps"]

    rows = []
    for idx, ranked_row in enumerate(ranked, start=1):
        rows.append({
            "Rank": idx,
            "Owner SS Age": int(ranked_row["Owner SS Age"]),
            "Spouse SS Age": int(ranked_row["Spouse SS Age"]),
            "Strategy": ranked_row["Strategy"],
            "Final Net Worth": float(ranked_row["final_net_worth"]),
            "After-Tax Legacy": float(ranked_row["after_tax_legacy"]),
            "Effective Legacy Value": float(ranked_row.get("effective_legacy_value", ranked_row["after_tax_legacy"])),
            "Heir Tax Drag": float(ranked_row.get("heir_tax_drag", 0.0)),
            "Ending Roth Balance": float(ranked_row["ending_roth_balance"]),
            "Ending Traditional IRA Balance": float(ranked_row["ending_traditional_ira_balance"]),
            "Ending Brokerage Balance": float(ranked_row["ending_brokerage_balance"]),
            "Ending Cash Balance": float(ranked_row["ending_cash_balance"]),
            "Stability Value": float(ranked_row["stability_value"]),
            "Risk Value": float(ranked_row["risk_value"]),
            "Final Household SS Income": float(ranked_row["final_household_ss_income"]),
            "Survivor SS Income": float(ranked_row["survivor_ss_income"]),
            "Social Security Present Value": float(ranked_row.get("social_security_present_value", 0.0)),
            "Total Federal Tax": float(ranked_row.get("Total Federal Tax", 0.0)),
            "Total State Tax": float(ranked_row.get("Total State Tax", 0.0)),
            "Total ACA Cost": float(ranked_row.get("Total ACA Cost", 0.0)),
            "Total IRMAA Cost": float(ranked_row.get("Total IRMAA Cost", 0.0)),
            "Total Government Drag": float(ranked_row.get("Total Government Drag", 0.0)),
            "Total Conversions": float(ranked_row.get("Total Conversions", 0.0)),
            "First IRMAA Year": ranked_row.get("First IRMAA Year"),
            "Max MAGI": float(ranked_row.get("Max MAGI", 0.0)),
            "ACA Hit Years": int(ranked_row.get("ACA Hit Years", 0)),
            "IRMAA Hit Years": int(ranked_row.get("IRMAA Hit Years", 0)),
            "Traditional IRA Penalty Applied": float(ranked_row.get("lambda_penalty_dollars", 0.0)),
            "Score": float(ranked_row["score_100"]),
            "Stability": ranked_row.get("stability_label", ""),
            "Risk": ranked_row.get("risk_label", ""),
            "Traditional IRA Share @ End": float(ranked_row.get("ending_traditional_ira_share", 0.0)),
            "NW Score +": float(ranked_row.get("nw_component", 0.0) * 100.0),
            "Legacy Score +": float(ranked_row.get("legacy_component", 0.0) * 100.0),
            "Stability Score +": float(ranked_row.get("stability_component", 0.0) * 100.0),
            "Trad Penalty -": float(ranked_row.get("trad_component", 0.0) * 100.0),
            "Trad Share Penalty -": float(ranked_row.get("trad_share_component", 0.0) * 100.0),
            "Gov Drag Penalty -": float(ranked_row.get("drag_component", 0.0) * 100.0),
            "Heir Tax Penalty -": float(ranked_row.get("heir_tax_component", 0.0) * 100.0),
            "Risk Penalty -": float(ranked_row.get("risk_component", 0.0) * 100.0),
            "Preference Bonus +": float(ranked_row.get("preference_bonus_component", 0.0) * 100.0),
            "Preference Penalty -": float(ranked_row.get("preference_penalty_component", 0.0) * 100.0),
            "Lambda Penalty -": float(ranked_row.get("lambda_penalty_score", 0.0) * 100.0),
            "Balanced Score": float(profile_score_maps.get("Balanced", {}).get(ranked_row["Strategy"], 0.0)),
            "Growth Score": float(profile_score_maps.get("Growth", {}).get(ranked_row["Strategy"], 0.0)),
            "Tax-Efficient Score": float(profile_score_maps.get("Tax-Efficient Stability", {}).get(ranked_row["Strategy"], 0.0)),
            "Legacy Score": float(profile_score_maps.get("Legacy Focused", {}).get(ranked_row["Strategy"], 0.0)),
            "Spend With Confidence Score": float(profile_score_maps.get("Spend With Confidence", {}).get(ranked_row["Strategy"], 0.0)),
        })

    ranked_df = pd.DataFrame(rows)
    if ranked_df.empty:
        return ranked_df
    return reorder_ss_optimizer_results_df(ranked_df)


def clamp01(value: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return 0.0


def safe_ratio(numerator: float, denominator: float, default: float = 0.0) -> float:
    try:
        denom = float(denominator)
        if abs(denom) <= 1e-12:
            return float(default)
        return float(numerator) / denom
    except Exception:
        return float(default)


def build_strategy_scoring_context(inputs: dict | None = None, metrics_list: list[dict] | None = None) -> dict:
    inputs = inputs or {}
    metrics_list = metrics_list or []
    starting_assets = float(
        inputs.get("trad", 0.0)
        + inputs.get("roth", 0.0)
        + inputs.get("brokerage", 0.0)
        + inputs.get("cash", 0.0)
    )
    if starting_assets <= 0.0 and metrics_list:
        derived_values = []
        for m in metrics_list:
            ending_total = float(m.get("ending_traditional_ira_balance", 0.0)) + float(m.get("ending_roth_balance", 0.0)) + float(m.get("ending_brokerage_balance", 0.0)) + float(m.get("ending_cash_balance", 0.0))
            if ending_total > 0.0:
                derived_values.append(ending_total / 9.0)
        if derived_values:
            starting_assets = sum(derived_values) / len(derived_values)
    starting_assets = max(1.0, starting_assets)
    annual_spending = float(inputs.get("annual_spending", 0.0) or 0.0)
    if annual_spending <= 0.0 and metrics_list:
        ss_values = [float(m.get("final_household_ss_income", 0.0)) for m in metrics_list if float(m.get("final_household_ss_income", 0.0)) > 0.0]
        if ss_values:
            annual_spending = max(ss_values)
    annual_spending = max(1.0, annual_spending)
    return {
        "starting_assets": float(starting_assets),
        "annual_spending": float(annual_spending),
        "wealth_floor_multiple": 5.0,
        "wealth_ceiling_multiple": 15.0,
        "legacy_floor_multiple": 4.5,
        "legacy_ceiling_multiple": 14.0,
        "drag_bad_ratio": 0.08,
        "trad_bad_multiple": 1.35,
        "heir_drag_bad_multiple": 0.50,
        "ss_present_value_multiple": 6.0,
        "risk_buffer_good_years": 15.0,
    }


def _absolute_profile_features(metrics: dict, scoring_context: dict) -> dict:
    starting_assets = max(1.0, float(scoring_context.get("starting_assets", 1.0)))
    annual_spending = max(1.0, float(scoring_context.get("annual_spending", 1.0)))
    final_net_worth = float(metrics.get("final_net_worth", 0.0))
    after_tax_legacy = float(metrics.get("after_tax_legacy", 0.0))
    effective_legacy_value = float(metrics.get("effective_legacy_value", after_tax_legacy))
    heir_tax_drag = float(metrics.get("heir_tax_drag", 0.0))
    ending_trad = float(metrics.get("ending_traditional_ira_balance", 0.0))
    ending_roth = float(metrics.get("ending_roth_balance", 0.0))
    ending_brokerage = float(metrics.get("ending_brokerage_balance", 0.0))
    ending_cash = float(metrics.get("ending_cash_balance", 0.0))
    ending_total = max(1.0, ending_trad + ending_roth + ending_brokerage + ending_cash)
    final_household_ss_income = float(metrics.get("final_household_ss_income", 0.0))
    survivor_ss_income = float(metrics.get("survivor_ss_income", 0.0))
    social_security_present_value = float(metrics.get("social_security_present_value", estimate_social_security_present_value(final_household_ss_income, survivor_ss_income)))
    min_liquid_assets = max(0.0, -float(metrics.get("risk_value", 0.0)))
    government_drag = float(metrics.get("Total Government Drag", 0.0))

    nw_multiple = safe_ratio(final_net_worth, starting_assets)
    legacy_multiple = safe_ratio(effective_legacy_value, starting_assets)
    trad_multiple = safe_ratio(ending_trad, starting_assets)
    heir_drag_multiple = safe_ratio(heir_tax_drag, starting_assets)
    drag_ratio = safe_ratio(government_drag, max(final_net_worth, starting_assets))
    trad_share = safe_ratio(ending_trad, ending_total)
    stability_ratio = safe_ratio(final_household_ss_income, annual_spending)
    survivor_ratio = safe_ratio(survivor_ss_income, annual_spending)
    ss_present_value_ratio = safe_ratio(social_security_present_value, starting_assets)
    risk_buffer_years = safe_ratio(min_liquid_assets, annual_spending)

    nw_score = clamp01(safe_ratio(nw_multiple - float(scoring_context.get("wealth_floor_multiple", 5.0)), float(scoring_context.get("wealth_ceiling_multiple", 15.0)) - float(scoring_context.get("wealth_floor_multiple", 5.0))))
    legacy_score = clamp01(safe_ratio(legacy_multiple - float(scoring_context.get("legacy_floor_multiple", 4.5)), float(scoring_context.get("legacy_ceiling_multiple", 14.0)) - float(scoring_context.get("legacy_floor_multiple", 4.5))))
    trad_penalty = clamp01(safe_ratio(trad_multiple, float(scoring_context.get("trad_bad_multiple", 1.35))))
    heir_tax_penalty = clamp01(safe_ratio(heir_drag_multiple, float(scoring_context.get("heir_drag_bad_multiple", 0.50))))
    drag_penalty = clamp01(safe_ratio(drag_ratio, float(scoring_context.get("drag_bad_ratio", 0.08))))
    stability_score = clamp01(0.50 * clamp01(safe_ratio(stability_ratio, 1.10)) + 0.30 * clamp01(safe_ratio(survivor_ratio, 0.95)) + 0.20 * clamp01(safe_ratio(ss_present_value_ratio, float(scoring_context.get("ss_present_value_multiple", 6.0)))))
    risk_penalty = clamp01(1.0 - clamp01(safe_ratio(risk_buffer_years, float(scoring_context.get("risk_buffer_good_years", 15.0)))))

    return {
        "nw_score": float(nw_score),
        "legacy_score": float(legacy_score),
        "trad_penalty": float(trad_penalty),
        "trad_share_penalty": float(trad_share),
        "drag_penalty": float(drag_penalty),
        "stability_score": float(stability_score),
        "risk_penalty": float(risk_penalty),
        "heir_tax_penalty": float(heir_tax_penalty),
        "ss_value_score": float(clamp01(safe_ratio(ss_present_value_ratio, float(scoring_context.get("ss_present_value_multiple", 6.0))))),
        "income_stability_score": float(clamp01(0.65 * clamp01(safe_ratio(stability_ratio, 1.10)) + 0.35 * clamp01(safe_ratio(survivor_ratio, 0.95)))),
        "ending_traditional_ira_share": float(trad_share),
    }


def score_strategy_metrics(
    metrics_list: list[dict],
    profile_name: str,
    preferences: dict | None = None,
    trad_balance_penalty_lambda: float = 0.0,
    scoring_context: dict | None = None,
) -> list[dict]:
    weights = get_profile_summary(profile_name)["weights"]
    preferences = preferences or {}
    trad_balance_penalty_lambda = max(0.0, float(trad_balance_penalty_lambda or 0.0))
    scoring_context = scoring_context or build_strategy_scoring_context(metrics_list=metrics_list)
    starting_assets = max(1.0, float(scoring_context.get("starting_assets", 1.0)))

    scored = []
    for metrics in metrics_list:
        features = _absolute_profile_features(metrics, scoring_context)
        nw_adjusted = features["nw_score"] ** 0.90
        legacy_adjusted = features["legacy_score"]
        stability_adjusted = features["stability_score"]
        trad_penalty = features["trad_penalty"]
        trad_share_penalty = features["trad_share_penalty"]
        drag_penalty = features["drag_penalty"]
        risk_penalty = features["risk_penalty"]
        heir_tax_penalty = features["heir_tax_penalty"]
        ss_value_score = features["ss_value_score"]

        if profile_name == "Legacy Focused":
            positive_score = (0.10 * weights["nw"] * nw_adjusted) + (weights["legacy"] * legacy_adjusted) + (weights["stability"] * (0.75 * stability_adjusted + 0.25 * ss_value_score))
            negative_score = (
                (weights["trad"] * (trad_penalty ** 1.25))
                + (1.10 * weights.get("trad_share", 0.0) * (trad_share_penalty ** 1.35))
                + (0.75 * weights.get("drag", 0.0) * drag_penalty)
                + (1.20 * weights["trad"] * (heir_tax_penalty ** 1.10))
                + (weights["risk"] * risk_penalty)
            )
        elif profile_name == "Spend With Confidence":
            positive_score = (weights["nw"] * nw_adjusted) + (weights["legacy"] * legacy_adjusted) + (weights["stability"] * (0.78 * stability_adjusted + 0.22 * ss_value_score))
            negative_score = (weights["trad"] * trad_penalty) + (weights["risk"] * risk_penalty) + (weights.get("drag", 0.0) * drag_penalty) + (weights.get("trad_share", 0.0) * trad_share_penalty)
        else:
            positive_score = (weights["nw"] * nw_adjusted) + (weights["legacy"] * legacy_adjusted) + (weights["stability"] * stability_adjusted)
            negative_score = (weights["trad"] * trad_penalty) + (weights["risk"] * risk_penalty) + (weights.get("drag", 0.0) * drag_penalty) + (weights.get("trad_share", 0.0) * trad_share_penalty)

        preference_bonus = 0.0
        preference_penalty = 0.0
        if preferences.get("maximize_social_security"):
            ss_bonus = 0.24 * (ss_value_score ** 1.10)
            if profile_name == "Legacy Focused":
                ss_bonus *= 1.20
            elif profile_name in ("Spend With Confidence", "Tax-Efficient Stability"):
                ss_bonus *= 1.10
            preference_bonus += ss_bonus
        if preferences.get("income_stability_focus"):
            preference_bonus += 0.10 * features["income_stability_score"]
        if preferences.get("minimize_trad_ira_for_heirs"):
            heir_structure_penalty = (0.24 * (trad_share_penalty ** 1.30)) + (0.24 * (heir_tax_penalty ** 1.10)) + (0.10 * trad_penalty)
            if profile_name == "Legacy Focused":
                heir_structure_penalty *= 1.25
            preference_penalty += heir_structure_penalty

        lambda_penalty_dollars = trad_balance_penalty_lambda * float(metrics.get("ending_traditional_ira_balance", 0.0))
        lambda_penalty_score = safe_ratio(lambda_penalty_dollars, starting_assets * 10.0)

        positive_score += preference_bonus
        negative_score += preference_penalty + lambda_penalty_score
        score = positive_score - negative_score

        scored.append({
            **metrics,
            "score": float(score),
            "score_100": float(score * 100.0),
            "lambda_penalty_dollars": float(lambda_penalty_dollars),
            "lambda_penalty_score": float(lambda_penalty_score),
            "stability_label": qualitative_bucket(stability_adjusted),
            "risk_label": qualitative_bucket(risk_penalty, reverse=True),
            "nw_component": float((0.10 * weights["nw"] * nw_adjusted) if profile_name == "Legacy Focused" else (weights["nw"] * nw_adjusted)),
            "legacy_component": float(weights["legacy"] * legacy_adjusted),
            "stability_component": float(weights["stability"] * stability_adjusted),
            "trad_component": float(weights["trad"] * trad_penalty),
            "drag_component": float((0.60 * weights.get("drag", 0.0) * drag_penalty) if profile_name == "Legacy Focused" else (weights.get("drag", 0.0) * drag_penalty)),
            "trad_share_component": float(weights.get("trad_share", 0.0) * trad_share_penalty),
            "risk_component": float(weights["risk"] * risk_penalty),
            "heir_tax_component": float((0.90 * weights["trad"] * heir_tax_penalty) if profile_name == "Legacy Focused" else 0.0),
            "ss_value_component": float(0.14 * ss_value_score if preferences.get("maximize_social_security") else 0.0),
            "preference_bonus_component": float(preference_bonus),
            "preference_penalty_component": float(preference_penalty),
            "social_security_present_value": float(metrics.get("social_security_present_value", estimate_social_security_present_value(float(metrics.get("final_household_ss_income", 0.0)), float(metrics.get("survivor_ss_income", 0.0))))),
            "positive_score": float(positive_score),
            "negative_score": float(negative_score),
            "ending_traditional_ira_share": float(features["ending_traditional_ira_share"]),
        })

    return sorted(scored, key=lambda x: x["score"], reverse=True)


def build_profile_score_maps(metrics_list: list[dict], scoring_context: dict | None = None, trad_balance_penalty_lambda: float = 0.0) -> dict[str, dict[str, float]]:
    scoring_context = scoring_context or build_strategy_scoring_context(metrics_list=metrics_list)
    out: dict[str, dict[str, float]] = {}
    for profile_name in PROFILE_PRESETS.keys():
        ranked = score_strategy_metrics(metrics_list, profile_name, preferences={}, trad_balance_penalty_lambda=trad_balance_penalty_lambda, scoring_context=scoring_context)
        out[profile_name] = {str(row.get("Strategy", "")): float(row.get("score_100", 0.0)) for row in ranked}
    return out


def build_strategy_scoring_payload(
    results_rows: list[dict],
    selected_profile_name: str,
    preferences: dict | None = None,
    trad_balance_penalty_lambda: float = 0.0,
    scoring_context: dict | None = None,
) -> dict:
    """
    Central scoring payload used by both Quick and Full SS scans.
    This keeps profile scoring, selected ranking, and profile score maps on one shared path.
    """
    metric_rows = []
    for row in results_rows:
        metric_rows.append({
            "Strategy": f"{int(row['Owner SS Age'])}/{int(row['Spouse SS Age'])}",
            "Owner SS Age": int(row["Owner SS Age"]),
            "Spouse SS Age": int(row["Spouse SS Age"]),
            "final_net_worth": float(row.get("Final Net Worth", 0.0)),
            "after_tax_legacy": float(row.get("After-Tax Legacy", 0.0)),
            "effective_legacy_value": float(row.get("Effective Legacy Value", row.get("After-Tax Legacy", 0.0))),
            "heir_tax_drag": float(row.get("Heir Tax Drag", 0.0)),
            "ending_traditional_ira_balance": float(row.get("Ending Traditional IRA Balance", 0.0)),
            "ending_roth_balance": float(row.get("Ending Roth Balance", 0.0)),
            "ending_brokerage_balance": float(row.get("Ending Brokerage Balance", 0.0)),
            "ending_cash_balance": float(row.get("Ending Cash Balance", 0.0)),
            "stability_value": float(row.get("Stability Value", 0.0)),
            "risk_value": float(row.get("Risk Value", 0.0)),
            "final_household_ss_income": float(row.get("Final Household SS Income", 0.0)),
            "survivor_ss_income": float(row.get("Survivor SS Income", 0.0)),
            "social_security_present_value": float(row.get("Social Security Present Value", estimate_social_security_present_value(float(row.get("Final Household SS Income", 0.0)), float(row.get("Survivor SS Income", 0.0))))),
            "Total Government Drag": float(row.get("Total Government Drag", 0.0)),
            "Total Conversions": float(row.get("Total Conversions", 0.0)),
            "Total Federal Tax": float(row.get("Total Federal Tax", 0.0)),
            "Total State Tax": float(row.get("Total State Tax", 0.0)),
            "Total ACA Cost": float(row.get("Total ACA Cost", 0.0)),
            "Total IRMAA Cost": float(row.get("Total IRMAA Cost", 0.0)),
            "First IRMAA Year": row.get("First IRMAA Year"),
            "Max MAGI": float(row.get("Max MAGI", 0.0)),
            "ACA Hit Years": int(row.get("ACA Hit Years", 0)),
            "IRMAA Hit Years": int(row.get("IRMAA Hit Years", 0)),
        })

    scoring_context = scoring_context or build_strategy_scoring_context(metrics_list=metric_rows)
    selected_ranked_rows = score_strategy_metrics(
        metric_rows,
        selected_profile_name,
        preferences=preferences or {},
        trad_balance_penalty_lambda=trad_balance_penalty_lambda,
        scoring_context=scoring_context,
    )
    profile_score_maps = build_profile_score_maps(
        metric_rows,
        scoring_context=scoring_context,
        trad_balance_penalty_lambda=trad_balance_penalty_lambda,
    )
    return {
        "metric_rows": metric_rows,
        "selected_ranked_rows": selected_ranked_rows,
        "profile_score_maps": profile_score_maps,
        "scoring_context": scoring_context,
    }


def build_scored_strategy_outputs(
    results_rows: list[dict],
    inputs: dict,
    selected_profile_name: str,
    preferences: dict | None = None,
    trad_balance_penalty_lambda: float = 0.0,
    shortlist_top_n: int = 10,
) -> dict:
    """
    Shared end-to-end scoring / ranking path for both Quick and Full SS scans.
    The only intended difference between Quick and Full is candidate generation.
    """
    safe_inputs = copy.deepcopy(inputs or {})
    scoring_context = build_strategy_scoring_context(inputs=safe_inputs, metrics_list=results_rows)
    ranked_df = build_ranked_optimizer_results_df(
        results_rows,
        selected_profile_name,
        preferences=preferences or {},
        trad_balance_penalty_lambda=trad_balance_penalty_lambda,
        scoring_context=scoring_context,
    )
    profile_shortlists = build_profile_shortlists_from_optimizer_rows(
        results_rows,
        top_n=max(1, int(shortlist_top_n)),
        preferences=preferences or {},
        trad_balance_penalty_lambda=trad_balance_penalty_lambda,
        scoring_context=scoring_context,
    )
    return {
        "scoring_context": scoring_context,
        "ranked_df": ranked_df,
        "profile_shortlists": profile_shortlists,
    }


def build_quick_profile_anchor_rows(ranked_rows: list[dict]) -> dict:
    if not ranked_rows:
        return {}

    def _as_float(row: dict, key: str) -> float:
        try:
            return float(row.get(key, 0.0))
        except Exception:
            return 0.0

    recommended = ranked_rows[0]
    best_growth = max(
        ranked_rows,
        key=lambda r: (
            _as_float(r, "Final Net Worth"),
            _as_float(r, "After-Tax Legacy"),
        ),
    )
    best_legacy = max(
        ranked_rows,
        key=lambda r: (
            _as_float(r, "After-Tax Legacy"),
            -_as_float(r, "Ending Traditional IRA Balance"),
            _as_float(r, "Effective Legacy Value"),
        ),
    )
    most_stable = max(
        ranked_rows,
        key=lambda r: (
            _as_float(r, "Final Household SS Income"),
            _as_float(r, "Survivor SS Income"),
            _as_float(r, "Stability Value"),
        ),
    )
    return {
        "recommended": recommended,
        "best_growth": best_growth,
        "best_legacy": best_legacy,
        "most_stable": most_stable,
    }


def generate_advisor_interpretation(profile_name: str, ranked_rows: list[dict]) -> str:
    if not ranked_rows:
        return "No recommendation is available yet."

    anchors = build_quick_profile_anchor_rows(ranked_rows)
    winner = anchors.get("recommended", ranked_rows[0])

    def _as_float(row: dict, key: str) -> float:
        try:
            return float(row.get(key, 0.0))
        except Exception:
            return 0.0

    def _pick_reference_row() -> tuple[dict, str]:
        if profile_name == "Growth":
            ref = anchors.get("most_stable", winner)
            if str(ref.get("Strategy", "")) == str(winner.get("Strategy", "")):
                alternatives = [r for r in ranked_rows if str(r.get("Strategy", "")) != str(winner.get("Strategy", ""))]
                if alternatives:
                    ref = max(
                        alternatives,
                        key=lambda r: (
                            _as_float(r, "Final Household SS Income"),
                            _as_float(r, "Survivor SS Income"),
                            _as_float(r, "Stability Value"),
                        ),
                    )
            return ref, "most stable option"

        ref = anchors.get("best_growth", winner)
        if str(ref.get("Strategy", "")) == str(winner.get("Strategy", "")):
            alternatives = [r for r in ranked_rows if str(r.get("Strategy", "")) != str(winner.get("Strategy", ""))]
            if alternatives:
                ref = max(
                    alternatives,
                    key=lambda r: (
                        _as_float(r, "Final Net Worth"),
                        _as_float(r, "After-Tax Legacy"),
                    ),
                )
        return ref, "best growth option"

    reference, reference_label = _pick_reference_row()

    winner_nw = _as_float(winner, "Final Net Worth")
    winner_trad = _as_float(winner, "Ending Traditional IRA Balance")
    winner_ss = _as_float(winner, "Final Household SS Income")
    winner_gov_drag = _as_float(winner, "Total Government Drag")

    reference_nw = _as_float(reference, "Final Net Worth")
    reference_trad = _as_float(reference, "Ending Traditional IRA Balance")
    reference_ss = _as_float(reference, "Final Household SS Income")
    reference_gov_drag = _as_float(reference, "Total Government Drag")

    nw_delta = winner_nw - reference_nw
    tax_delta = winner_gov_drag - reference_gov_drag
    income_delta = winner_ss - reference_ss
    trad_delta = winner_trad - reference_trad

    def _strategy_phrase() -> str:
        strategy = str(winner.get("Strategy", ""))
        if profile_name == "Growth":
            return f"{strategy} — an earlier-claiming tilt that leans toward growth"
        if profile_name == "Balanced":
            return f"{strategy} — a middle-ground claiming approach"
        return f"{strategy} — a delayed-claiming tilt toward stability and tax control"

    def _tradeoff_block() -> str:
        return "\n".join([
            f"- **Net worth:** {format_signed_dollars(nw_delta)}",
            f"- **Lifetime taxes / government drag:** {format_signed_dollars(tax_delta)}",
            f"- **Guaranteed income (Social Security):** {format_signed_dollars(income_delta)}/year",
            f"- **Ending Traditional IRA:** {format_signed_dollars(trad_delta)}",
        ])

    if profile_name == "Growth":
        meaning = "You are choosing maximum growth and upside over maximum guaranteed income later in life."
        why_lines = [
            "- Favors the highest projected ending wealth in this quick comparison",
            "- Keeps more capital invested earlier",
            "- Accepts lower guaranteed income later in exchange for more upside now",
        ]
        give_up_lines = [
            "- Less guaranteed income later in retirement",
            "- More reliance on portfolio withdrawals and market performance",
            "- A larger chance that more assets remain in Traditional IRA",
        ]
        estate_lines = [
            "- Usually leaves a larger gross estate",
            "- Can leave more assets in Traditional IRA",
            "- May create a higher future tax burden for heirs",
        ]
    elif profile_name == "Balanced":
        meaning = "You are choosing balance and flexibility instead of pushing hard for either maximum growth or maximum guaranteed income."
        why_lines = [
            "- Balances wealth creation and income stability",
            "- Keeps tax exposure more manageable over time",
            "- Avoids the most extreme claim-timing choices",
        ]
        give_up_lines = [
            "- Not the absolute highest-net-worth path",
            "- Not the absolute highest guaranteed-income path",
            "- Gives up some upside to reduce regret across competing priorities",
        ]
        estate_lines = [
            "- Leaves a more balanced mix of account types",
            "- Usually avoids the worst Traditional IRA overhang",
            "- Produces a steadier inheritance structure for heirs",
        ]
    else:
        meaning = "You are choosing income security and tax control over maximum projected ending wealth."
        why_lines = [
            "- Favors higher guaranteed lifetime income",
            "- Reduces long-term pressure from Traditional IRA balances and future withdrawals",
            "- Improves late-retirement stability in this quick comparison",
        ]
        give_up_lines = [
            "- Lower projected net worth than the most aggressive growth option",
            "- More reliance on portfolio withdrawals before Social Security starts",
            "- Less short-term flexibility in exchange for stronger later-life support",
        ]
        estate_lines = [
            "- Usually leaves less in Traditional IRA",
            "- Can improve after-tax inheritance quality",
            "- Better fits future estate or charitable planning",
        ]

    sections = [
        "**Recommended strategy**  ",
        _strategy_phrase(),
        "",
        "**Why this wins**  ",
        "\n".join(why_lines),
        "",
        "**What you give up**  ",
        "\n".join(give_up_lines),
        "",
        f"**Tradeoffs (vs {reference_label}: {reference.get('Strategy', '')})**  ",
        _tradeoff_block(),
        "",
        "**What this means**  ",
        meaning,
        "",
        "**Estate impact**  ",
        "\n".join(estate_lines),
    ]
    return "\n".join(sections)


def build_quick_anchor_comparison_df(ranked_rows: list[dict]) -> pd.DataFrame:
    anchors = build_quick_profile_anchor_rows(ranked_rows)
    if not anchors:
        return pd.DataFrame()

    rows = []
    for label, key in [("Recommended strategy", "recommended"), ("Highest net worth strategy", "best_growth"), ("Most stable strategy", "most_stable")]:
        row = anchors.get(key, {}) or {}
        rows.append({
            "Lens": label,
            "Strategy": row.get("Strategy", ""),
            "Net Worth": float(row.get("Final Net Worth", 0.0)),
            "After-Tax Legacy": float(row.get("After-Tax Legacy", 0.0)),
            "Lifetime taxes / government drag": float(row.get("Total Government Drag", 0.0)),
            "Final Household SS Income": float(row.get("Final Household SS Income", 0.0)),
            "Ending Traditional IRA": float(row.get("Ending Traditional IRA Balance", 0.0)),
        })
    return pd.DataFrame(rows)


def is_close_quick_result(ranked_rows: list[dict], tolerance_pct: float = 0.02) -> bool:
    if len(ranked_rows) < 2:
        return False
    top_score = float(ranked_rows[0].get("score", ranked_rows[0].get("Score", 0.0)))
    second_score = float(ranked_rows[1].get("score", ranked_rows[1].get("Score", 0.0)))
    denom = max(abs(top_score), 1e-9)
    return abs(top_score - second_score) / denom <= tolerance_pct


def generate_next_step_guidance(profile_name: str, ranked_rows: list[dict]) -> list[str]:
    if not ranked_rows:
        return []
    winner = ranked_rows[0]
    trad = float(winner.get("Ending Traditional IRA Balance", winner.get("ending_traditional_ira_balance", 0.0)))
    roth = float(winner.get("Roth @ End", winner.get("ending_roth_balance", 0.0)))
    brokerage = float(winner.get("Brokerage @ End", winner.get("ending_brokerage_balance", 0.0)))
    cash = float(winner.get("ending_cash_balance", 0.0))
    total = trad + roth + brokerage + cash
    guidance = []
    if total > 0:
        trad_pct = trad / total
        roth_pct = roth / total
        if trad_pct >= 0.40:
            guidance.append(
                f"About {trad_pct:.0%} of ending balances still sit in Traditional IRA. That means Social Security timing alone is not materially reducing the tax-deferred balance."
            )
            guidance.append(
                "Recommended next lever: open the Break-Even Governor with this Social Security strategy and test stronger Roth conversion settings, especially before RMD years."
            )
        if roth_pct < 0.30:
            guidance.append(
                f"Roth assets are still only about {roth_pct:.0%} of ending balances. More Roth conversion activity could improve tax flexibility and after-tax legacy quality."
            )
    if profile_name == "Legacy Focused":
        guidance.append(
            "For Legacy Focused results, use this recommendation as a Social Security starting point, then test whether more aggressive conversions can shrink Traditional IRA enough to make the remaining balance small or intentionally charitable."
        )
        guidance.append(
            "If you want to refine this further, check a small nearby set such as 70/70, 70/69, 70/68, 69/70, and 69/69 before deciding whether a full 81-strategy run adds enough value."
        )
    elif profile_name == "Tax-Efficient Stability":
        guidance.append(
            "For Tax-Efficient Stability, compare the winner against a few nearby delayed-claim options such as 70/69, 70/68, or 69/70 to confirm you are getting enough conversion runway and guaranteed income later in life."
        )
    elif profile_name == "Spend With Confidence":
        guidance.append(
            "For Spend With Confidence, use the winner as a base case for future spending analysis rather than chasing the absolute highest net worth result."
        )
    elif profile_name == "Balanced":
        guidance.append(
            "Because this profile often produces close calls, compare a small nearby cluster around the winner instead of jumping straight to all 81 combinations."
        )
    else:
        guidance.append(
            "If the recommendation looks reasonable, the best confirmation step is to test a few nearby claim-age combinations around the winner and compare balance composition, not just net worth."
        )
    return guidance


def run_quick_strategy_recommendation(inputs: dict, max_conversion: float, step_size: float, profile_name: str) -> dict:
    preferences = extract_scoring_preferences(inputs)
    base_inputs = prepare_ss_scan_base_inputs(inputs)
    quick_max_conversion = sanitize_governor_max_conversion(float(max_conversion))
    quick_step_size = sanitize_governor_step_size(float(step_size))
    trad_balance_penalty_lambda = float(inputs.get("trad_balance_penalty_lambda", DEFAULT_APP_STATE["trad_balance_penalty_lambda"]))

    quick_rows, errors = evaluate_ss_scan_candidates(
        base_inputs,
        get_ss_scan_candidate_combos("quick"),
        quick_max_conversion,
        quick_step_size,
        trad_balance_penalty_lambda=trad_balance_penalty_lambda,
        progress_prefix="Running Quick Scan",
    )
    if not quick_rows:
        raise RuntimeError("Quick strategy recommendation could not produce any valid strategy results.")

    shared_outputs = score_rank_ss_scan_rows(
        quick_rows,
        inputs=base_inputs,
        selected_profile_name=profile_name,
        preferences=preferences,
        trad_balance_penalty_lambda=trad_balance_penalty_lambda,
        shortlist_top_n=10,
    )
    ranked_df = shared_outputs["ranked_df"]
    ranked = ranked_df.to_dict("records")
    data_source = "quick_subset_shared_scoring_pipeline"

    explanation = generate_advisor_interpretation(profile_name, ranked)
    return {
        "profile_name": profile_name,
        "summary_df": ranked_df.head(10).copy(),
        "ranked_rows": ranked,
        "advisor_text": explanation,
        "close_result": is_close_quick_result(ranked),
        "next_step_guidance": generate_next_step_guidance(profile_name, ranked),
        "errors": errors,
        "data_source": data_source,
        "active_preferences_text": describe_active_scoring_preferences(preferences),
        "strategy_universe_size": len(quick_rows),
        "profile_shortlists": shared_outputs.get("profile_shortlists", {}),
        "quick_recommendation_input_state": copy.deepcopy(base_inputs),
        "quick_recommendation_source_scenario_state": copy.deepcopy(inputs),
    }


def get_ss_optimizer_combo_count() -> int:
    return 9 * 9


def clear_ss_optimizer_state(clear_last_result: bool = True) -> None:
    st.session_state["ss_optimizer_running"] = False
    st.session_state["ss_optimizer_interrupted"] = False
    st.session_state["ss_optimizer_error"] = None
    st.session_state["ss_optimizer_progress_index"] = 0
    st.session_state["ss_optimizer_partial_results"] = []
    st.session_state["ss_optimizer_last_completed"] = None
    if clear_last_result:
        st.session_state["ss_optimizer_last_result"] = None


def set_annual_std_deduction_default_callback() -> None:
    year = int(st.session_state.get("annual_calc_year", START_YEAR))
    filing_status = st.session_state.get("annual_calc_filing_status", "MFJ")
    default_val = float(get_annual_standard_deduction_default(year, filing_status))
    st.session_state["annual_calc_standard_deduction"] = default_val
    st.session_state["annual_calc_standard_deduction_auto"] = True


def set_annual_std_deduction_custom_callback() -> None:
    st.session_state["annual_calc_standard_deduction_auto"] = False


def mark_annual_std_deduction_custom_from_input() -> None:
    st.session_state["annual_calc_standard_deduction_auto"] = False


def get_annual_other_income_default_for_year(calc_year: int) -> float:
    """
    Legacy compatibility helper.
    Other ordinary income is now its own annual-page input and should not default
    from the earned-income schedule.
    """
    _ = calc_year
    return float(st.session_state.get("annual_other_ordinary_income", DEFAULT_APP_STATE["annual_other_ordinary_income"]))


def sync_annual_other_income_widget_from_shared_schedule(force: bool = False) -> None:
    """
    Legacy no-op. Kept only so older references do not break.
    The Conversion page earned-income schedule is the sole source of truth for earned income.
    """
    _ = force
    return None


def sync_shared_income_from_annual_widget() -> None:
    """
    Legacy no-op. The annual page must never overwrite the shared earned-income schedule.
    """
    return None


def get_annual_earned_income_resolved_for_year(year: int) -> float:
    amount = float(st.session_state.get("earned_income_annual", DEFAULT_APP_STATE["earned_income_annual"]))
    start_year = int(st.session_state.get("earned_income_start_year", DEFAULT_APP_STATE["earned_income_start_year"]))
    end_year = int(st.session_state.get("earned_income_end_year", DEFAULT_APP_STATE["earned_income_end_year"]))
    if start_year <= int(year) <= end_year:
        return amount
    return 0.0


def sync_conversion_earned_income_widget_state() -> None:
    """Keep conversion-page widget keys aligned with canonical earned-income schedule state."""
    current_signature = (
        float(st.session_state.get("earned_income_annual", DEFAULT_APP_STATE["earned_income_annual"])),
        int(st.session_state.get("earned_income_start_year", DEFAULT_APP_STATE["earned_income_start_year"])),
        int(st.session_state.get("earned_income_end_year", DEFAULT_APP_STATE["earned_income_end_year"])),
    )
    prior_signature = st.session_state.get("conversion_earned_income_source_signature")
    if prior_signature != current_signature:
        st.session_state["conversion_earned_income_annual_input"] = float(current_signature[0])
        st.session_state["conversion_earned_income_start_year_input"] = int(current_signature[1])
        st.session_state["conversion_earned_income_end_year_input"] = int(current_signature[2])
        st.session_state["conversion_earned_income_source_signature"] = current_signature


def on_conversion_earned_income_change() -> None:
    """Write conversion-page earned-income widget values back to canonical state."""
    annual = float(st.session_state.get("conversion_earned_income_annual_input", DEFAULT_APP_STATE["earned_income_annual"]))
    start_year = int(st.session_state.get("conversion_earned_income_start_year_input", DEFAULT_APP_STATE["earned_income_start_year"]))
    end_year = int(st.session_state.get("conversion_earned_income_end_year_input", DEFAULT_APP_STATE["earned_income_end_year"]))
    st.session_state["earned_income_annual"] = annual
    st.session_state["earned_income_start_year"] = start_year
    st.session_state["earned_income_end_year"] = end_year
    st.session_state["conversion_earned_income_source_signature"] = (annual, start_year, end_year)


def get_page_specific_state_keys(page: str) -> list[str]:
    prefixes = PAGE_STATE_KEY_PREFIXES.get(page, [])
    keys = []
    for key in SCENARIO_STATE_KEYS:
        if any(key.startswith(prefix) for prefix in prefixes):
            keys.append(key)

    if page == "annual":
        annual_extras = [
            "annual_external_other_ordinary_income",
            "annual_realized_ltcg_so_far",
            "annual_target_bracket",
            "annual_income_safety_buffer",
            "annual_max_conversion",
        ]
        for key in annual_extras:
            if key in SCENARIO_STATE_KEYS and key not in keys:
                keys.append(key)

    return keys


def collect_page_state(page: str) -> dict:
    ensure_default_state()
    keys = get_page_specific_state_keys(page)
    return {key: copy.deepcopy(st.session_state.get(key, DEFAULT_APP_STATE[key])) for key in keys}


def ensure_default_state() -> None:
    for key, value in DEFAULT_APP_STATE.items():
        if key not in st.session_state:
            st.session_state[key] = copy.deepcopy(value)


def preserve_session_state_across_pages() -> None:
    """
    Streamlit drops widget-backed session_state keys when those widgets are not
    rendered on a later page. Most of this app's canonical scenario values use
    the same keys as their widgets, so page switches can silently wipe loaded
    scenarios unless we detach those keys from widget cleanup on every run.
    """
    keys_to_preserve = set(SCENARIO_STATE_KEYS)
    keys_to_preserve.update({
        "app_page",
        "app_state_version",
        "state_tax_rate_pct_display",
        "target_trad_override_max_rate_pct_display",
        "conversion_earned_income_annual_input",
        "conversion_earned_income_start_year_input",
        "conversion_earned_income_end_year_input",
        "conversion_earned_income_source_signature",
        "annual_edit_long_range_assumptions",
        "annual_earned_income_display",
        "scenario_name_input",
        "loaded_scenario_name",
        "loaded_scenario_scope",
        "loaded_scenario_fingerprint",
        "loaded_scenario_app_version",
        "scenario_name_input_seed",
        "snapshot_viewer_payload",
        "snapshot_viewer_name",
        "snapshot_open_notice",
    })
    for key in keys_to_preserve:
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]


def collect_scenario_state() -> dict:
    ensure_default_state()
    return {key: copy.deepcopy(st.session_state.get(key, DEFAULT_APP_STATE[key])) for key in SCENARIO_STATE_KEYS}


def get_current_scenario_fingerprint() -> str:
    return build_scenario_fingerprint(collect_scenario_state())


def get_loaded_scenario_name() -> str:
    name = str(st.session_state.get("loaded_scenario_name", "") or "").strip()
    return name if name else "Unsaved session"


def scenario_has_unsaved_changes() -> bool:
    loaded_fp = st.session_state.get("loaded_scenario_fingerprint")
    if not loaded_fp:
        return False
    return str(loaded_fp) != get_current_scenario_fingerprint()


def set_loaded_scenario_identity(name: str | None, scope: str = "full", app_version: str | None = None) -> None:
    clean_name = str(name or "").strip() or "Loaded scenario"
    st.session_state["loaded_scenario_name"] = clean_name
    st.session_state["loaded_scenario_scope"] = str(scope or "full")
    st.session_state["loaded_scenario_app_version"] = str(app_version or APP_VERSION)
    st.session_state["loaded_scenario_fingerprint"] = get_current_scenario_fingerprint()


def clear_loaded_scenario_identity() -> None:
    st.session_state["loaded_scenario_name"] = ""
    st.session_state["loaded_scenario_scope"] = "full"
    st.session_state["loaded_scenario_app_version"] = APP_VERSION
    st.session_state["loaded_scenario_fingerprint"] = None


def clear_transient_recommendation_state() -> None:
    for key in [
        "selected_recommendation_strategy",
        "selected_recommendation_source",
        "selected_recommendation_profile",
        "break_even_governor_preset_note",
        "suppress_quick_recommendation_stale_once",
    ]:
        if key in st.session_state:
            st.session_state.pop(key, None)


def sync_scenario_name_widget_default() -> None:
    loaded_name = str(st.session_state.get("loaded_scenario_name", "") or "").strip()
    current_value = str(st.session_state.get("scenario_name_input", "") or "").strip()
    prior_seed = str(st.session_state.get("scenario_name_input_seed", "") or "").strip()
    if (not current_value) or (current_value == prior_seed):
        st.session_state["scenario_name_input"] = loaded_name
    st.session_state["scenario_name_input_seed"] = loaded_name


def render_scenario_identity_bar(current_page: str) -> None:
    scenario_name = get_loaded_scenario_name()
    active_strategy = f"{int(st.session_state.get('owner_claim_age', DEFAULT_APP_STATE['owner_claim_age']))}/{int(st.session_state.get('spouse_claim_age', DEFAULT_APP_STATE['spouse_claim_age']))}"
    profile = str(st.session_state.get("planning_profile", DEFAULT_APP_STATE.get("planning_profile", "Balanced")))
    parts = [f"**Scenario:** {scenario_name}", f"**Active SS Strategy:** {active_strategy}"]
    if current_page == "conversion":
        quick_result_snapshot = get_current_result_payload("quick_strategy_recommendation_result")
        quick_winner_strategy = None
        if quick_result_snapshot is not None:
            ranked_rows = quick_result_snapshot.get("ranked_rows", []) or []
            if ranked_rows:
                quick_winner_strategy = str(ranked_rows[0].get("Strategy", "")).strip() or None
        if quick_winner_strategy:
            parts.append(f"**Quick Rec Winner:** {quick_winner_strategy}")
        parts.append(f"**Profile:** {profile}")
    st.caption(" | ".join(parts))
    if scenario_has_unsaved_changes():
        st.warning("Current inputs differ from the loaded scenario.")


def sync_widget_state_from_canonical_state() -> None:
    """Keep UI-only widget keys aligned with the canonical saved scenario values."""
    if "target_trad_override_max_rate" in st.session_state:
        pct = float(st.session_state.get("target_trad_override_max_rate", 0.0)) * 100.0
        st.session_state["target_trad_override_max_rate_pct_display"] = f"{pct:.0f}%"
    sync_conversion_earned_income_widget_state()


def apply_scenario_state(state: dict) -> None:
    ensure_default_state()
    clear_transient_recommendation_state()
    for key in SCENARIO_STATE_KEYS:
        st.session_state[key] = copy.deepcopy(state.get(key, DEFAULT_APP_STATE[key]))
    if "annual_external_other_ordinary_income" in state and "annual_other_ordinary_income" not in state:
        try:
            st.session_state["annual_other_ordinary_income"] = float(state.get("annual_external_other_ordinary_income", 0.0))
        except Exception:
            pass
    # Keep the legacy field as a passive mirror for backward compatibility only.
    st.session_state["annual_external_other_ordinary_income"] = copy.deepcopy(
        st.session_state.get("annual_other_ordinary_income", DEFAULT_APP_STATE["annual_other_ordinary_income"])
    )
    sync_widget_state_from_canonical_state()
    loaded_owner_age = int(st.session_state.get("owner_claim_age", DEFAULT_APP_STATE["owner_claim_age"]))
    loaded_spouse_age = int(st.session_state.get("spouse_claim_age", DEFAULT_APP_STATE["spouse_claim_age"]))
    st.session_state["selected_recommendation_strategy"] = f"{loaded_owner_age}/{loaded_spouse_age}"


def reset_scenario_state() -> None:
    current_page = st.session_state.get("app_page", "home")
    apply_scenario_state({})
    clear_loaded_scenario_identity()
    st.session_state["app_page"] = current_page

def mark_result_state(result_key: str, inputs: dict) -> None:
    st.session_state[f"{result_key}_input_hash"] = build_scenario_fingerprint(inputs)

def inputs_are_stale(result_key: str, inputs: dict) -> bool:
    prior = st.session_state.get(f"{result_key}_input_hash")
    if not prior:
        return False
    return prior != build_scenario_fingerprint(inputs)

def render_stale_warning(result_key: str, inputs: dict, label: str) -> None:
    if inputs_are_stale(result_key, inputs):
        st.warning(f"{label} shown below was generated from an earlier set of inputs. Run it again to refresh the results.")


def tag_result_payload(result: dict, *, engine: str, inputs: dict | None = None) -> dict:
    payload = dict(result)
    payload["app_version"] = APP_VERSION
    payload["engine"] = engine
    if inputs is not None:
        payload["input_hash"] = build_scenario_fingerprint(inputs)
    return payload


def get_current_result_payload(session_key: str):
    result = st.session_state.get(session_key)
    if result is None:
        return None
    if not isinstance(result, dict):
        st.session_state[session_key] = None
        return None
    if result.get("app_version") != APP_VERSION:
        st.session_state[session_key] = None
        return None
    return result


def should_suppress_quick_recommendation_stale_warning(current_inputs: dict) -> bool:
    """
    Suppress the quick-recommendation stale warning immediately after the user clicks
    through into the Break-Even Governor from a recommended strategy. In that case the
    recommendation table is still a valid snapshot; the user intentionally changed the
    SS claim ages by following the recommendation.
    """
    if not bool(st.session_state.get("suppress_quick_recommendation_stale_once", False)):
        return False
    selected_strategy = st.session_state.get("selected_recommendation_strategy")
    if not selected_strategy:
        return False
    try:
        owner_age_str, spouse_age_str = str(selected_strategy).split("/")
        owner_age = int(owner_age_str)
        spouse_age = int(spouse_age_str)
    except Exception:
        return False
    try:
        return (
            int(current_inputs.get("owner_claim_age", -1)) == owner_age
            and int(current_inputs.get("spouse_claim_age", -1)) == spouse_age
        )
    except Exception:
        return False


def build_scenario_export_payload(scope: str = "full", scenario_name: str | None = None) -> str:
    state = collect_scenario_state() if scope == "full" else collect_page_state(scope)
    clean_name = str(scenario_name or st.session_state.get("scenario_name_input", "") or get_loaded_scenario_name()).strip() or "retirement_model_scenario"
    payload = {
        "meta": {
            "app": "retirement_model",
            "version": APP_VERSION,
            "scope": scope,
            "scenario_name": clean_name,
        },
        "state": state,
    }
    return json.dumps(payload, indent=2)


def build_quick_recommendation_snapshot_payload(quick_result: dict, planning_profile: str) -> str:
    ranked_rows = quick_result.get("ranked_rows", []) or []
    recommended = ranked_rows[0] if ranked_rows else {}
    most_stable = max(
        ranked_rows,
        key=lambda r: (
            float(r.get("Final Household SS Income", r.get("final_household_ss_income", 0.0))),
            float(r.get("Survivor SS Income", r.get("survivor_ss_income", 0.0))),
            -float(r.get("Ending Traditional IRA Balance", r.get("ending_traditional_ira_balance", 0.0))),
        ),
        default={},
    )
    highest_nw = max(
        ranked_rows,
        key=lambda r: float(r.get("Final Net Worth", r.get("final_net_worth", 0.0))),
        default={},
    )
    scenario_name = get_loaded_scenario_name()
    snapshot_name = str(
        st.session_state.get("quick_snapshot_name_input", "")
        or f"{scenario_name} - {planning_profile}"
    ).strip() or f"{scenario_name} - {planning_profile}"
    quick_rec_input_state = copy.deepcopy(quick_result.get("quick_recommendation_input_state") or {})
    source_scenario_state = copy.deepcopy(quick_result.get("quick_recommendation_source_scenario_state") or {})

    payload = {
        "meta": {
            "app": "retirement_model",
            "snapshot_type": "quick_recommendation",
            "version": APP_VERSION,
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "scenario_name": scenario_name,
            "snapshot_name": snapshot_name,
            "planning_profile": str(planning_profile),
        },
        "summary": {
            "recommended_strategy": str(recommended.get("Strategy", "")),
            "most_stable_strategy": str(most_stable.get("Strategy", "")),
            "highest_net_worth_strategy": str(highest_nw.get("Strategy", "")),
            "advisor_text": str(quick_result.get("advisor_text", "")),
            "active_preferences_text": str(quick_result.get("active_preferences_text", "None")),
            "applied_preset_note": str(quick_result.get("applied_preset_note", "")),
        },
        "strategy_summary_rows": _json_safe(pd.DataFrame(quick_result.get("summary_df", pd.DataFrame())).to_dict("records")),
        "anchor_comparison_rows": _json_safe(build_quick_anchor_comparison_df(ranked_rows).to_dict("records")),
        "top_ranked_rows": _json_safe(ranked_rows[:5]),
        "scenario_state_used_for_quick_rec": _json_safe(source_scenario_state),
        "quick_recommendation_input_state": _json_safe(quick_rec_input_state),
    }
    return json.dumps(payload, indent=2)


def render_snapshot_summary_card(snapshot_payload: dict, heading: str = "Snapshot Preview") -> None:
    meta = snapshot_payload.get("meta", {}) if isinstance(snapshot_payload, dict) else {}
    summary = snapshot_payload.get("summary", {}) if isinstance(snapshot_payload, dict) else {}
    if not isinstance(meta, dict):
        meta = {}
    if not isinstance(summary, dict):
        summary = {}
    with st.container(border=True):
        st.markdown(f"**{heading}**")
        st.caption(
            f"Snapshot: {meta.get('snapshot_name', 'Unnamed snapshot')} | "
            f"Scenario: {meta.get('scenario_name', 'Unknown')} | "
            f"Generated: {meta.get('generated_at', 'Unknown')} | "
            f"App version: {meta.get('version', 'Unknown')}"
        )
        c1, c2, c3 = st.columns(3)
        c1.metric("Recommended", str(summary.get("recommended_strategy", "—")))
        c2.metric("Most stable", str(summary.get("most_stable_strategy", "—")))
        c3.metric("Highest net worth", str(summary.get("highest_net_worth_strategy", "—")))
        if meta.get("planning_profile"):
            st.write(f"Planning profile: {meta.get('planning_profile', '')}")
        if summary.get("active_preferences_text"):
            st.write(f"Preference modifiers: {summary.get('active_preferences_text')}")
        advisor_text = str(summary.get("advisor_text", "") or "")
        if advisor_text:
            st.write("Advisor interpretation")
            st.markdown(advisor_text)
        strategy_rows = snapshot_payload.get("strategy_summary_rows", []) if isinstance(snapshot_payload, dict) else []
        if strategy_rows:
            strategy_df = pd.DataFrame(strategy_rows)
            st.subheader("Quick Scan Summary")
            st.dataframe(
                strategy_df.style.format({
                    "Score": "{:.1f}",
                    "Net Worth": "${:,.0f}",
                    "After-Tax Legacy": "${:,.0f}",
                    "Trad IRA @ End": "${:,.0f}",
                    "Roth @ End": "${:,.0f}",
                    "Brokerage @ End": "${:,.0f}",
                    "Final Household SS Income": "${:,.0f}",
                    "Survivor SS Income": "${:,.0f}",
                }),
                use_container_width=True,
            )
        quick_input_state = snapshot_payload.get("quick_recommendation_input_state", {}) if isinstance(snapshot_payload, dict) else {}
        if isinstance(quick_input_state, dict) and quick_input_state:
            st.caption("This snapshot stores the exact input state used to generate the quick recommendation. It does not silently overwrite itself with current live governor settings.")
        rows = snapshot_payload.get("anchor_comparison_rows", []) if isinstance(snapshot_payload, dict) else []
        if rows:
            render_tradeoff_summary_columns_from_rows(rows)


def render_tradeoff_summary_columns_from_rows(rows: list[dict]) -> None:
    if not rows:
        return

    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        normalized.append({str(k): v for k, v in row.items()})
    if not normalized:
        return

    def _normalize_label(value: str) -> str:
        text = str(value or "").strip().lower()
        text = text.replace("-", " ").replace("_", " ")
        while "  " in text:
            text = text.replace("  ", " ")
        return text

    canonical_label_map = {
        "recommended strategy": "Recommended Strategy",
        "best legacy strategy": "Best Legacy Strategy",
        "most stable strategy": "Most Stable Strategy",
        "highest net worth strategy": "Highest Net Worth Strategy",
    }

    row_map = {}
    for r in normalized:
        raw_label = r.get("Column", r.get("Lens", r.get("Title", "")))
        canonical = canonical_label_map.get(_normalize_label(raw_label), str(raw_label or "").strip())
        row_map[canonical] = r

    def _get(label: str, *field_names, default=""):
        row = row_map.get(label, {})
        for field in field_names:
            if field in row and row.get(field) not in (None, ""):
                return row.get(field)
        return default

    def _as_float(value, default=0.0):
        try:
            return float(value)
        except Exception:
            return float(default)

    preferred_recommended_label = "Recommended Strategy" if "Recommended Strategy" in row_map else "Best Legacy Strategy"
    recommended_strategy = str(_get(preferred_recommended_label, "Strategy", default=""))

    def _render(col, title: str):
        if title not in row_map:
            with col:
                st.markdown(f"**{title}**")
                st.caption("Not available in this snapshot")
            return
        same_as_recommended = title != preferred_recommended_label and str(_get(title, "Strategy", default="")) == recommended_strategy
        with col:
            st.markdown(f"**{title}**")
            if same_as_recommended:
                st.caption("Same as recommended")
            st.write(str(_get(title, "Strategy", default="")))
            st.write(f"After-Tax Legacy: ${_as_float(_get(title, 'After-Tax Legacy', default=0.0)):,.0f}")
            st.write(f"Ending Trad IRA: ${_as_float(_get(title, 'Ending Traditional IRA', 'Ending Trad IRA', default=0.0)):,.0f}")
            st.write(f"Final Net Worth: ${_as_float(_get(title, 'Net Worth', 'Final Net Worth', default=0.0)):,.0f}")
            st.write(f"Household SS Income: ${_as_float(_get(title, 'Final Household SS Income', 'Household SS Income', default=0.0)):,.0f}")

    st.subheader("Tradeoff Summary")
    c1, c2, c3 = st.columns(3)
    _render(c1, preferred_recommended_label)
    if "Highest Net Worth Strategy" in row_map:
        _render(c2, "Most Stable Strategy")
        _render(c3, "Highest Net Worth Strategy")
    else:
        _render(c2, "Best Legacy Strategy")
        _render(c3, "Most Stable Strategy")


def open_snapshot_in_viewer(snapshot_payload: dict) -> None:
    st.session_state["snapshot_viewer_payload"] = copy.deepcopy(snapshot_payload)
    meta = snapshot_payload.get("meta", {}) if isinstance(snapshot_payload, dict) else {}
    name = meta.get("snapshot_name", "Snapshot Viewer") if isinstance(meta, dict) else "Snapshot Viewer"
    st.session_state["snapshot_viewer_name"] = str(name)
    st.session_state["snapshot_open_notice"] = f"Opened snapshot: {str(name)}"
    st.session_state["app_page"] = "snapshot"




def sanitize_export_filename(value: str, fallback: str = "file") -> str:
    raw = str(value or "").strip()
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in raw)
    while "--" in safe:
        safe = safe.replace("--", "-")
    safe = safe.strip("-_")
    return safe or fallback

def render_snapshot_open_controls() -> None:
    st.caption("Open a saved snapshot file to view a read-only saved recommendation report.")
    opened_snapshot = st.file_uploader("Open Snapshot", type=["json"], key="global_snapshot_open")
    open_col1, open_col2 = st.columns([1, 3])
    with open_col1:
        open_clicked = st.button("Open Snapshot File", use_container_width=True, disabled=opened_snapshot is None, key="open_snapshot_file_button")
    with open_col2:
        if opened_snapshot is not None:
            st.caption(f"Selected file: {opened_snapshot.name}")
    if open_clicked and opened_snapshot is not None:
        try:
            raw_bytes = opened_snapshot.getvalue()
            if not raw_bytes:
                raise ValueError("The selected file is empty.")
            opened_payload = json.loads(raw_bytes.decode("utf-8"))
            if str((opened_payload.get("meta", {}) or {}).get("snapshot_type", "")) != "quick_recommendation":
                raise ValueError("This file is not a Quick Recommendation snapshot.")
            open_snapshot_in_viewer(opened_payload)
            st.rerun()
        except Exception as exc:
            st.error(f"Could not open snapshot: {exc}")


def _export_df_records(df) -> list[dict]:
    if df is None:
        return []
    try:
        if isinstance(df, pd.DataFrame):
            return df.to_dict("records")
        return pd.DataFrame(df).to_dict("records")
    except Exception:
        return []


def build_ss_optimizer_export_payload(result: dict) -> str:
    payload = {
        "meta": {
            "export_type": "ss_optimizer_results",
            "app_version": APP_VERSION,
            "scenario_name": get_loaded_scenario_name(),
        },
        "context": {
            "planning_profile": st.session_state.get("planning_profile", DEFAULT_APP_STATE.get("planning_profile", "Balanced")),
            "preference_modifiers": extract_scoring_preferences(st.session_state),
            "active_ss_strategy": f"{int(st.session_state.get('owner_claim_age', DEFAULT_APP_STATE['owner_claim_age']))}/{int(st.session_state.get('spouse_claim_age', DEFAULT_APP_STATE['spouse_claim_age']))}",
            "scenario_state": collect_scenario_state(),
        },
        "results": {
            "completed": bool(result.get("completed", False)),
            "best_result": _json_safe(result.get("best_result")),
            "comparison_rows": _export_df_records(result.get("comparison_df")),
            "top_10_rows": _export_df_records(result.get("top_10_df")),
            "all_results_rows": _export_df_records(result.get("all_results_df")),
            "profile_shortlists": {k: _export_df_records(v) for k, v in (result.get("profile_shortlists", {}) or {}).items()},
        },
    }
    return json.dumps(_json_safe(payload), indent=2)


def build_break_even_export_payload(result: dict) -> str:
    summary_fields = [
        "final_net_worth", "total_federal_taxes", "total_state_taxes", "total_aca_cost",
        "total_irmaa_cost", "total_government_drag", "total_shortfall", "max_magi",
        "aca_hit_years", "irmaa_hit_years", "first_irmaa_year", "owner_ss_start",
        "spouse_ss_start", "household_rmd_start", "owner_claim_age", "spouse_claim_age",
        "total_conversions", "ending_trad_balance",
    ]
    payload = {
        "meta": {
            "export_type": "break_even_governor_results",
            "app_version": APP_VERSION,
            "scenario_name": get_loaded_scenario_name(),
        },
        "context": {
            "planning_profile": st.session_state.get("planning_profile", DEFAULT_APP_STATE.get("planning_profile", "Balanced")),
            "active_ss_strategy": f"{int(st.session_state.get('owner_claim_age', DEFAULT_APP_STATE['owner_claim_age']))}/{int(st.session_state.get('spouse_claim_age', DEFAULT_APP_STATE['spouse_claim_age']))}",
            "scenario_state": collect_scenario_state(),
        },
        "summary": {k: _json_safe(result.get(k)) for k in summary_fields},
        "chosen_path_rows": _export_df_records(result.get("df")),
        "decision_rows": _export_df_records(result.get("decision_df")),
        "validation": _json_safe(result.get("validation")),
        "validation_rerun_summary": _json_safe(result.get("validation_rerun_summary")),
    }
    return json.dumps(_json_safe(payload), indent=2)



def render_scenario_manager(current_page: str) -> None:
    with st.expander("Scenarios / Snapshots", expanded=False):
        st.caption("Open or save scenarios, and open saved recommendation snapshots from the same section.")

        st.markdown("**Scenarios**")
        upload_key = f"scenario_upload_{current_page}"
        opened_file = st.file_uploader("Open Scenario", type=["json"], key=upload_key)
        open_col1, open_col2 = st.columns([1, 3])
        with open_col1:
            open_clicked = st.button("Open Scenario File", use_container_width=True, disabled=opened_file is None, key=f"open_scenario_{current_page}")
        with open_col2:
            if opened_file is not None:
                st.caption(f"Selected file: {opened_file.name}")
        if open_clicked and opened_file is not None:
            try:
                payload = json.loads(opened_file.getvalue().decode("utf-8"))
                state = payload.get("state", payload)
                if not isinstance(state, dict):
                    raise ValueError("Opened JSON does not contain a valid scenario state.")
                meta = payload.get("meta", {}) if isinstance(payload, dict) else {}
                scope = str(meta.get("scope", "full"))
                if scope == "full":
                    apply_scenario_state(state)
                else:
                    ensure_default_state()
                    for key in get_page_specific_state_keys(scope):
                        if key in state:
                            st.session_state[key] = copy.deepcopy(state[key])
                set_loaded_scenario_identity(meta.get("scenario_name", opened_file.name.rsplit('.', 1)[0]), scope=scope, app_version=meta.get("version", APP_VERSION))
                st.session_state["app_page"] = current_page
                st.success(f"Scenario opened ({scope}): {get_loaded_scenario_name()}")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not open scenario: {exc}")

        sync_scenario_name_widget_default()
        st.text_input("Scenario name", key="scenario_name_input", placeholder="Baseline plan")
        export_name = str(st.session_state.get("scenario_name_input", "") or "retirement_model_scenario").strip() or "retirement_model_scenario"
        safe_filename = f"scenario__{sanitize_export_filename(export_name, 'retirement-model-scenario')}__v124"
        st.download_button(
            "Save Scenario",
            data=build_scenario_export_payload("full", export_name),
            file_name=f"{safe_filename}.json",
            mime="application/json",
            use_container_width=True,
        )

        st.divider()
        st.markdown("**Snapshots**")
        render_snapshot_open_controls()



# -----------------------------
# YEARLY TABLES
# Update these over time.
# If a specific year is missing, the model uses the latest prior year available.
# -----------------------------

STANDARD_DEDUCTION_BY_YEAR = {
    2026: 32200.0,
}

FEDERAL_BRACKETS_MFJ_BY_YEAR = {
    2026: [
        (0, 0.10),
        (23200, 0.12),
        (94300, 0.22),
        (201050, 0.24),
        (383900, 0.32),
        (487450, 0.35),
        (731200, 0.37),
    ]
}

LTCG_BRACKETS_MFJ_BY_YEAR = {
    2026: [
        (0, 0.00),
        (96950, 0.15),
        (600050, 0.20),
    ]
}

BRACKET_TOPS_MFJ_BY_YEAR = {
    2026: {
        "10%": 23200.0,
        "12%": 94300.0,
        "22%": 201050.0,
        "24%": 383900.0,
    }
}

IRMAA_TABLE_BY_YEAR = {
    2026: [
        (-1, 218000, 0.0),
        (218000, 274000, 96.0),
        (274000, 342000, 240.0),
        (342000, 410000, 385.0),
        (410000, 750000, 530.0),
        (750000, 99999999, 578.0),
    ]
}

# Overall annual ACA cost from your Healthcare.gov pulls
ACA_COST_TABLES = {
    "2_person": {
        2026: [
            (30000, 1104),
            (31000, 1224),
            (32000, 1356),
            (33000, 1476),
            (34000, 1596),
            (35000, 1716),
            (36000, 1848),
            (37000, 1992),
            (38000, 2124),
            (39000, 2280),
            (40000, 2424),
            (41000, 2580),
            (42000, 2736),
            (43000, 2880),
            (44000, 3024),
            (45000, 3180),
            (46000, 3324),
            (47000, 3480),
            (48000, 3636),
            (49000, 3804),
            (50000, 3960),
            (51000, 4128),
            (52000, 4308),
            (53000, 4476),
            (54000, 4644),
            (55000, 4800),
            (56000, 4968),
            (57000, 5148),
            (58000, 5316),
            (59000, 5496),
            (60000, 5676),
            (61000, 5856),
            (62000, 6036),
            (63000, 6228),
            (64000, 6372),
            (65000, 6468),
            (66000, 6564),
            (67000, 6672),
            (68000, 6768),
            (69000, 6864),
            (70000, 6972),
            (71000, 7068),
            (72000, 7164),
            (73000, 7260),
            (74000, 7368),
            (75000, 7464),
            (76000, 7560),
            (77000, 7668),
            (78000, 7764),
            (79000, 7860),
            (80000, 7968),
            (81000, 8064),
            (82000, 8160),
            (83000, 8256),
            (84000, 8364),
            (85000, 27996),
        ]
    },
    "1_person": {
        2026: [
            (30000, 1094),
            (31000, 1226),
            (32000, 1358),
            (33000, 1478),
            (34000, 1598),
            (35000, 1718),
            (36000, 1850),
            (37000, 1994),
            (38000, 2126),
            (39000, 2270),
            (40000, 2426),
            (41000, 2582),
            (42000, 2738),
            (43000, 2882),
            (44000, 3026),
            (45000, 3170),
            (46000, 3326),
            (47000, 3482),
            (48000, 3638),
            (49000, 3794),
            (50000, 3962),
            (51000, 4130),
            (52000, 4298),
            (53000, 4478),
            (54000, 4634),
            (55000, 4802),
            (56000, 4970),
            (57000, 5138),
            (58000, 5318),
            (59000, 5498),
            (60000, 5678),
            (61000, 5858),
            (62000, 6038),
            (63000, 6230),
            (64000, 6374),
            (65000, 6470),
            (66000, 6566),
            (67000, 6662),
            (68000, 6770),
            (69000, 6866),
            (70000, 6962),
            (71000, 7070),
            (72000, 7166),
            (73000, 7262),
            (74000, 7370),
            (75000, 7466),
            (76000, 7562),
            (77000, 7658),
            (78000, 7766),
            (79000, 7862),
            (80000, 7958),
            (81000, 8066),
            (82000, 8162),
            (83000, 8258),
            (84000, 8366),
            (85000, 29582),
        ]
    }
}


# -----------------------------

def _json_safe(value):
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, float):
        return round(float(value), 10)
    if isinstance(value, (int, str, bool)) or value is None:
        return value
    return str(value)


def build_scenario_fingerprint(inputs: dict, max_conversion: float | None = None, step_size: float | None = None) -> str:
    payload = {"inputs": _json_safe(copy.deepcopy(inputs))}
    if max_conversion is not None:
        payload["max_conversion"] = round(float(max_conversion), 10)
    if step_size is not None:
        payload["step_size"] = round(float(step_size), 10)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.md5(canonical.encode("utf-8")).hexdigest()[:12]


def _make_projection_cache_key(start_year: int, starting_state: dict, params: dict, first_year_conversion: float, later_year_conversion: float) -> str:
    payload = {
        "start_year": int(start_year),
        "starting_state": _json_safe({
            "trad": float(starting_state["trad"]),
            "roth": float(starting_state["roth"]),
            "brokerage": float(starting_state["brokerage"]),
            "brokerage_basis": float(starting_state.get("brokerage_basis", starting_state["brokerage"])),
            "cash": float(starting_state["cash"]),
        }),
        "params": _json_safe(params),
        "first_year_conversion": round(float(first_year_conversion), 10),
        "later_year_conversion": round(float(later_year_conversion), 10),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.md5(canonical.encode("utf-8")).hexdigest()


def _clone_projection_result(result: dict) -> dict:
    cloned = dict(result)
    if "df" in cloned and isinstance(cloned["df"], pd.DataFrame):
        cloned["df"] = cloned["df"].copy(deep=True)
    return cloned


CONSISTENCY_KEYS = [
    "final_net_worth",
    "ending_trad_balance",
    "total_conversions",
    "total_federal_taxes",
    "total_state_taxes",
    "total_aca_cost",
    "total_irmaa_cost",
    "total_government_drag",
    "total_shortfall",
    "max_magi",
]


def compare_summary_metrics(first: dict, second: dict, tol: float = 0.01) -> list[dict]:
    mismatches = []
    for key in CONSISTENCY_KEYS:
        a = float(first.get(key, 0.0))
        b = float(second.get(key, 0.0))
        if not math.isclose(a, b, rel_tol=0.0, abs_tol=float(tol)):
            mismatches.append({
                "Metric": key,
                "Run 1": a,
                "Run 2": b,
                "Delta": b - a,
            })
    return mismatches


def make_consistency_payload(first: dict, second: dict, tol: float = 0.01) -> dict:
    mismatches = compare_summary_metrics(first, second, tol=tol)
    return {
        "passed": len(mismatches) == 0,
        "tolerance": float(tol),
        "mismatch_count": len(mismatches),
        "mismatch_df": pd.DataFrame(mismatches) if mismatches else pd.DataFrame(columns=["Metric", "Run 1", "Run 2", "Delta"]),
    }


def run_governor_with_validation(inputs: dict, max_conversion: float, step_size: float, integrity_mode: bool = False, tol: float = 0.01) -> dict:
    step_size = sanitize_governor_step_size(step_size)
    result = run_model_break_even_governor(inputs, max_conversion, step_size)

    if integrity_mode:
        rerun = run_model_break_even_governor(copy.deepcopy(inputs), max_conversion, step_size)
        validation = make_consistency_payload(result, rerun, tol=tol)
        result["validation"] = validation
        result["validation_rerun_summary"] = {k: rerun.get(k) for k in CONSISTENCY_KEYS}
    else:
        result["validation"] = None
        result["validation_rerun_summary"] = None
    return result

# TABLE LOOKUP HELPERS
# -----------------------------
def get_latest_year_value(table_by_year: dict, year: int):
    available_years = sorted(table_by_year.keys())
    eligible = [y for y in available_years if y <= year]
    if not eligible:
        return table_by_year[available_years[0]]
    return table_by_year[max(eligible)]


def get_standard_deduction(year: int) -> float:
    return float(get_latest_year_value(STANDARD_DEDUCTION_BY_YEAR, year))


def get_federal_brackets(year: int):
    return get_latest_year_value(FEDERAL_BRACKETS_MFJ_BY_YEAR, year)


def get_bracket_tops(year: int):
    return get_latest_year_value(BRACKET_TOPS_MFJ_BY_YEAR, year)


def get_ltcg_brackets(year: int):
    return get_latest_year_value(LTCG_BRACKETS_MFJ_BY_YEAR, year)


def get_irmaa_table(year: int):
    return get_latest_year_value(IRMAA_TABLE_BY_YEAR, year)


def get_aca_cost_table(year: int, household_key: str):
    tables = ACA_COST_TABLES.get(household_key, {})
    if not tables:
        return None
    return get_latest_year_value(tables, year)


def interpolate_cost_from_table(income: float, points: list) -> float:
    income = float(income)
    points = sorted(points, key=lambda x: x[0])

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]

    if income <= xs[0]:
        return float(ys[0])
    if income >= xs[-1]:
        return float(ys[-1])

    idx = bisect_left(xs, income)
    if xs[idx] == income:
        return float(ys[idx])

    x0, y0 = xs[idx - 1], ys[idx - 1]
    x1, y1 = xs[idx], ys[idx]

    slope = (y1 - y0) / (x1 - x0)
    return float(y0 + slope * (income - x0))


# -----------------------------
# TAX HELPERS
# -----------------------------
def calculate_progressive_tax(taxable_income: float, year: int) -> float:
    taxable_income = max(0.0, float(taxable_income))
    tax = 0.0
    brackets = get_federal_brackets(year)

    for i, (lower_bound, rate) in enumerate(brackets):
        upper_bound = brackets[i + 1][0] if i + 1 < len(brackets) else float("inf")
        if taxable_income > lower_bound:
            amount_in_bracket = min(taxable_income, upper_bound) - lower_bound
            tax += amount_in_bracket * rate

    return max(0.0, tax)


def get_marginal_rate_from_taxable_income(taxable_income: float, year: int) -> float:
    taxable_income = max(0.0, float(taxable_income))
    brackets = get_federal_brackets(year)
    rate = brackets[0][1]

    for i, (lower_bound, bracket_rate) in enumerate(brackets):
        upper_bound = brackets[i + 1][0] if i + 1 < len(brackets) else float("inf")
        if lower_bound <= taxable_income <= upper_bound:
            rate = bracket_rate

    return float(rate)


def calculate_taxable_ss(total_ss: float, other_income: float) -> float:
    total_ss = max(0.0, float(total_ss))
    other_income = max(0.0, float(other_income))

    provisional_income = other_income + 0.5 * total_ss

    if provisional_income <= 32000:
        taxable_ss = 0.0
    elif provisional_income <= 44000:
        taxable_ss = 0.5 * (provisional_income - 32000)
    else:
        taxable_ss = 6000.0 + 0.85 * (provisional_income - 44000)

    return max(0.0, min(taxable_ss, 0.85 * total_ss))


def calculate_ltcg_tax(ordinary_taxable_income: float, ltcg_taxable_income: float, year: int) -> float:
    ordinary_taxable_income = max(0.0, float(ordinary_taxable_income))
    ltcg_taxable_income = max(0.0, float(ltcg_taxable_income))
    if ltcg_taxable_income <= 0.0:
        return 0.0

    brackets = get_ltcg_brackets(year)
    tax = 0.0
    remaining = ltcg_taxable_income
    stack_start = ordinary_taxable_income

    for i, (threshold, rate) in enumerate(brackets):
        next_threshold = brackets[i + 1][0] if i + 1 < len(brackets) else float("inf")
        band_start = max(stack_start, threshold)
        band_end = next_threshold
        available = max(0.0, band_end - band_start)
        if available <= 0.0:
            continue
        taxed_here = min(remaining, available)
        tax += taxed_here * rate
        remaining -= taxed_here
        stack_start = band_start + taxed_here
        if remaining <= 0.0:
            break

    return max(0.0, tax)


def calculate_federal_tax(other_ordinary_income: float, total_ss: float, year: int, realized_ltcg: float = 0.0) -> dict:
    other_ordinary_income = max(0.0, float(other_ordinary_income))
    realized_ltcg = max(0.0, float(realized_ltcg))
    taxable_ss = calculate_taxable_ss(total_ss, other_ordinary_income + realized_ltcg)
    agi = other_ordinary_income + taxable_ss + realized_ltcg

    standard_deduction = get_standard_deduction(year)
    ordinary_income_before_deduction = other_ordinary_income + taxable_ss
    ordinary_taxable_income = max(0.0, ordinary_income_before_deduction - standard_deduction)
    deduction_remaining_for_ltcg = max(0.0, standard_deduction - ordinary_income_before_deduction)
    ltcg_taxable_income = max(0.0, realized_ltcg - deduction_remaining_for_ltcg)
    taxable_income = ordinary_taxable_income + ltcg_taxable_income

    ordinary_tax = calculate_progressive_tax(ordinary_taxable_income, year)
    ltcg_tax = calculate_ltcg_tax(ordinary_taxable_income, ltcg_taxable_income, year)
    federal_tax = ordinary_tax + ltcg_tax
    marginal_rate = get_marginal_rate_from_taxable_income(ordinary_taxable_income, year)

    return {
        "taxable_ss": taxable_ss,
        "agi": agi,
        "taxable_income": taxable_income,
        "ordinary_taxable_income": ordinary_taxable_income,
        "ltcg_taxable_income": ltcg_taxable_income,
        "realized_ltcg": realized_ltcg,
        "ordinary_tax": ordinary_tax,
        "ltcg_tax": ltcg_tax,
        "federal_tax": federal_tax,
        "marginal_rate": marginal_rate,
    }


def calculate_magi(agi: float, year: int) -> float:
    # Placeholder for future MAGI add-backs. Keep this as the only MAGI entry point.
    _ = year
    return max(0.0, float(agi))


def get_aca_magi_limit(year: int, aca_lives: int) -> float:
    _ = year
    if aca_lives <= 0:
        return float('inf')
    return max(0.0, ACA_CLIFF_MFJ - ACA_HEADROOM_BUFFER)


def get_earned_income_for_year(year: int, params: dict) -> float:
    amount = float(params.get('earned_income_annual', 0.0))
    start_year = int(params.get('earned_income_start_year', START_YEAR))
    end_year = int(params.get('earned_income_end_year', START_YEAR - 1))
    if start_year <= year <= end_year:
        return amount
    return 0.0


def get_household_reference_age(year: int, params: dict) -> int:
    owner_age = int(params.get("owner_current_age", 0)) + (year - START_YEAR)
    spouse_age = int(params.get("spouse_current_age", 0)) + (year - START_YEAR)
    return max(owner_age, spouse_age)


def get_annual_spending_for_year(year: int, params: dict) -> float:
    base_spending = float(params.get("annual_spending", 0.0))
    if base_spending <= 0.0:
        return 0.0

    years_from_start = max(0, year - START_YEAR)
    spending_inflation_rate = float(params.get("spending_inflation_rate", 0.0))
    inflated_spending = base_spending * ((1 + spending_inflation_rate) ** years_from_start)

    if not bool(params.get("retirement_smile_enabled", False)):
        return max(0.0, inflated_spending)

    reference_age = get_household_reference_age(year, params)
    go_go_end_age = int(params.get("go_go_end_age", 70))
    slow_go_end_age = int(params.get("slow_go_end_age", 80))
    go_go_multiplier = float(params.get("go_go_multiplier", 1.00))
    slow_go_multiplier = float(params.get("slow_go_multiplier", 0.85))
    no_go_multiplier = float(params.get("no_go_multiplier", 1.20))

    if reference_age < go_go_end_age:
        multiplier = go_go_multiplier
    elif reference_age < slow_go_end_age:
        multiplier = slow_go_multiplier
    else:
        multiplier = no_go_multiplier

    return max(0.0, inflated_spending * multiplier)


# -----------------------------
# ACCOUNTING CONTRACT / VALIDATION
# -----------------------------
def approx_equal(a: float, b: float, tol: float = 0.01) -> bool:
    return abs(float(a) - float(b)) <= tol


def validate_row_accounting(row: dict, year: int) -> list:
    issues = []

    expected_other_ordinary = float(row["Conversion Income Component"] + row["Trad Withdrawal Income Component"] + row.get("Earned Income", 0.0))
    expected_agi = float(row["Other Ordinary Income"] + row["Taxable SS"] + row.get("Brokerage Realized LTCG", 0.0))
    expected_magi = calculate_magi(expected_agi, year)
    expected_taxable_income = max(0.0, row.get("Ordinary Taxable Income", 0.0) + row.get("LTCG Taxable Income", 0.0))
    expected_net_worth = float(row["EOY Trad"] + row["EOY Roth"] + row["EOY Brokerage"] + row["EOY Cash"])

    if not approx_equal(row["Other Ordinary Income"], expected_other_ordinary):
        issues.append("Other Ordinary Income does not equal conversion + taxable Trad withdrawals")
    if not approx_equal(row["AGI"], expected_agi):
        issues.append("AGI does not equal Other Ordinary Income + Taxable SS + Brokerage Realized LTCG")
    if not approx_equal(row["MAGI"], expected_magi):
        issues.append("MAGI does not match canonical MAGI function")
    if not approx_equal(row["Taxable Income"], expected_taxable_income):
        issues.append("Taxable Income does not equal Ordinary Taxable Income + LTCG Taxable Income")
    if not approx_equal(row["Net Worth"], expected_net_worth):
        issues.append("Net Worth does not equal ending balances sum")

    return issues


def run_contract_validation_suite() -> pd.DataFrame:
    scenarios = [
        {
            "Scenario": "Conversion only, no SS, no spending",
            "year": 2026,
            "state": {"trad": 100000.0, "roth": 0.0, "brokerage": 0.0, "cash": 0.0},
            "inputs": {
                "growth": 0.0,
                "annual_spending": 0.0,
                "conversion_tax_funding_policy": "Cash then Brokerage",
                "owner_current_age": 60,
                "spouse_current_age": 60,
                "owner_claim_age": 67,
                "spouse_claim_age": 67,
                "owner_ss_base": 0.0,
                "spouse_ss_base": 0.0,
                "earned_income_annual": 0.0,
                "earned_income_start_year": 2024,
                "earned_income_end_year": 2024,
                "primary_aca_end_year": 2024,
                "spouse_aca_end_year": 2024,
            },
            "conversion": 30000.0,
            "expected": {
                "Other Ordinary Income": 30000.0,
                "Taxable SS": 0.0,
                "AGI": 30000.0,
                "MAGI": 30000.0,
                "Taxable Income": 0.0,
            },
        },
        {
            "Scenario": "Conversion plus Trad spending withdrawal",
            "year": 2026,
            "state": {"trad": 100000.0, "roth": 0.0, "brokerage": 0.0, "cash": 0.0},
            "inputs": {
                "growth": 0.0,
                "annual_spending": 20000.0,
                "conversion_tax_funding_policy": "Cash then Brokerage",
                "owner_current_age": 60,
                "spouse_current_age": 60,
                "owner_claim_age": 67,
                "spouse_claim_age": 67,
                "owner_ss_base": 0.0,
                "spouse_ss_base": 0.0,
                "earned_income_annual": 0.0,
                "earned_income_start_year": 2024,
                "earned_income_end_year": 2024,
                "primary_aca_end_year": 2024,
                "spouse_aca_end_year": 2024,
            },
            "conversion": 30000.0,
            "expected": {
                "Conversion Income Component": 30000.0,
                "Trad Withdrawal Income Component": 20000.0,
                "Other Ordinary Income": 50000.0,
                "Taxable SS": 0.0,
                "AGI": 50000.0,
                "MAGI": 50000.0,
                "Taxable Income": 20000.0,
            },
        },
        {
            "Scenario": "SS taxation interacts with ordinary income",
            "year": 2032,
            "state": {"trad": 100000.0, "roth": 0.0, "brokerage": 0.0, "cash": 0.0},
            "inputs": {
                "growth": 0.0,
                "annual_spending": 0.0,
                "conversion_tax_funding_policy": "Cash then Brokerage",
                "owner_current_age": 60,
                "spouse_current_age": 60,
                "owner_claim_age": 67,
                "spouse_claim_age": 67,
                "owner_ss_base": 43000.0,
                "spouse_ss_base": 15000.0,
                "primary_aca_end_year": 2024,
                "spouse_aca_end_year": 2024,
            },
            "conversion": 30000.0,
            "expected": {
                "Other Ordinary Income": 30000.0,
                "Total SS": 58000.0,
                "Taxable SS": 18750.0,
                "AGI": 48750.0,
                "MAGI": 48750.0,
                "Taxable Income": 18750.0,
            },
        },
        {
            "Scenario": "ACA responds to MAGI for 2 lives",
            "year": 2026,
            "state": {"trad": 100000.0, "roth": 0.0, "brokerage": 0.0, "cash": 0.0},
            "inputs": {
                "growth": 0.0,
                "annual_spending": 0.0,
                "conversion_tax_funding_policy": "Cash then Brokerage",
                "owner_current_age": 60,
                "spouse_current_age": 60,
                "owner_claim_age": 67,
                "spouse_claim_age": 67,
                "owner_ss_base": 0.0,
                "spouse_ss_base": 0.0,
                "primary_aca_end_year": 2035,
                "spouse_aca_end_year": 2035,
            },
            "conversion": 60000.0,
            "expected": {
                "MAGI": 60000.0,
                "ACA Lives": 2,
                "ACA Cost": 5676.0,
            },
        },
        {
            "Scenario": "IRMAA stays zero below first cliff",
            "year": 2036,
            "state": {"trad": 300000.0, "roth": 0.0, "brokerage": 0.0, "cash": 0.0},
            "inputs": {
                "growth": 0.0,
                "annual_spending": 0.0,
                "conversion_tax_funding_policy": "Cash then Brokerage",
                "owner_current_age": 70,
                "spouse_current_age": 70,
                "owner_claim_age": 67,
                "spouse_claim_age": 67,
                "owner_ss_base": 0.0,
                "spouse_ss_base": 0.0,
                "earned_income_annual": 0.0,
                "earned_income_start_year": 2024,
                "earned_income_end_year": 2024,
                "primary_aca_end_year": 2024,
                "spouse_aca_end_year": 2024,
            },
            "conversion": 200000.0,
            "expected": {
                "MAGI": 200000.0,
                "Medicare Lives": 2,
                "IRMAA Cost": 0.0,
            },
        },
    ]

    results = []
    for scenario in scenarios:
        params = build_common_params(scenario["inputs"])
        _, row = simulate_one_year(scenario["year"], dict(scenario["state"]), params, scenario["conversion"])
        issues = validate_row_accounting(row, scenario["year"])
        mismatches = []
        for key, expected_value in scenario["expected"].items():
            actual_value = row[key]
            if isinstance(expected_value, (int, float)):
                if not approx_equal(actual_value, expected_value):
                    mismatches.append(f"{key}: expected {expected_value:,.2f}, got {float(actual_value):,.2f}")
            else:
                if actual_value != expected_value:
                    mismatches.append(f"{key}: expected {expected_value}, got {actual_value}")
        results.append({
            "Scenario": scenario["Scenario"],
            "Status": "PASS" if not issues and not mismatches else "FAIL",
            "Accounting Issues": " | ".join(issues) if issues else "",
            "Expectation Mismatches": " | ".join(mismatches) if mismatches else "",
            "AGI": float(row["AGI"]),
            "MAGI": float(row["MAGI"]),
            "Taxable Income": float(row["Taxable Income"]),
            "Federal Tax": float(row["Federal Tax"]),
            "ACA Cost": float(row["ACA Cost"]),
            "IRMAA Cost": float(row["IRMAA Cost"]),
        })

    return pd.DataFrame(results)


# -----------------------------
# ACA / IRMAA
# -----------------------------
def calculate_aca_cost(magi: float, year: int, aca_lives: int) -> float:
    magi = max(0.0, float(magi))

    if aca_lives <= 0:
        return 0.0

    if aca_lives == 2:
        table = get_aca_cost_table(year, "2_person")
        return interpolate_cost_from_table(magi, table)

    table = get_aca_cost_table(year, "1_person")
    if table is not None:
        return interpolate_cost_from_table(magi, table)

    two_person_table = get_aca_cost_table(year, "2_person")
    return interpolate_cost_from_table(magi, two_person_table) / 2.0


def calculate_irmaa_cost(magi: float, year: int, medicare_lives: int) -> float:
    magi = max(0.0, float(magi))
    if medicare_lives <= 0:
        return 0.0

    table = get_irmaa_table(year)
    monthly_total_for_household = 0.0

    for start_exclusive, end_inclusive, monthly_total in table:
        if magi > start_exclusive and magi <= end_inclusive:
            monthly_total_for_household = monthly_total
            break

    annual_household = monthly_total_for_household * 12.0
    return annual_household * (medicare_lives / 2.0)


# -----------------------------
# COVERAGE STATUS
# -----------------------------
def get_coverage_status(year: int, primary_aca_end_year: int, spouse_aca_end_year: int) -> dict:
    primary_on_aca = year <= int(primary_aca_end_year)
    spouse_on_aca = year <= int(spouse_aca_end_year)
    aca_lives = int(primary_on_aca) + int(spouse_on_aca)
    medicare_lives = 2 - aca_lives

    return {
        "primary_on_aca": primary_on_aca,
        "spouse_on_aca": spouse_on_aca,
        "aca_lives": aca_lives,
        "medicare_lives": medicare_lives,
    }


# -----------------------------
# SOCIAL SECURITY
# -----------------------------
def annual_ss_benefit(base_benefit_at_67: float, claim_age: int) -> float:
    base_benefit_at_67 = float(base_benefit_at_67)
    claim_age = int(claim_age)

    delta = claim_age - 67
    if delta < 0:
        benefit = base_benefit_at_67 * (1 - 0.06 * abs(delta))
    elif delta > 0:
        benefit = base_benefit_at_67 * (1 + 0.08 * delta)
    else:
        benefit = base_benefit_at_67

    return max(0.0, benefit)


def ss_start_year_from_current_age(start_year: int, current_age: int, claim_age: int) -> int:
    return int(start_year + (int(claim_age) - int(current_age)))


# -----------------------------
# WITHDRAWAL ENGINE
# -----------------------------
def withdraw_by_policy(amount_needed: float, trad: float, roth: float, brokerage: float, cash: float, policy_name: str, brokerage_basis: float | None = None) -> dict:
    amount_needed = max(0.0, float(amount_needed))
    trad = float(trad)
    roth = float(roth)
    brokerage = float(brokerage)
    cash = float(cash)
    brokerage_basis = brokerage if brokerage_basis is None else max(0.0, float(brokerage_basis))
    brokerage_basis = min(brokerage_basis, brokerage)

    from_cash = 0.0
    from_brokerage = 0.0
    from_trad = 0.0
    from_roth = 0.0
    realized_ltcg = 0.0

    if policy_name == "Cash then Brokerage then Trad then Roth":
        draw_order = ["cash", "brokerage", "trad", "roth"]
    elif policy_name == "Cash then Brokerage":
        draw_order = ["cash", "brokerage"]
    elif policy_name == "Brokerage only":
        draw_order = ["brokerage"]
    elif policy_name == "Cash only":
        draw_order = ["cash"]
    elif policy_name == "Trad then Roth":
        draw_order = ["trad", "roth"]
    else:
        raise ValueError(f"Unknown withdrawal policy: {policy_name}")

    start_need = amount_needed

    for source in draw_order:
        if amount_needed <= 0:
            break

        if source == "cash":
            take = min(cash, amount_needed)
            cash -= take
            amount_needed -= take
            from_cash += take
        elif source == "brokerage":
            take = min(brokerage, amount_needed)
            if brokerage > 0 and take > 0:
                gain_ratio = max(0.0, (brokerage - brokerage_basis) / brokerage)
                realized_gain = take * gain_ratio
                basis_used = take - realized_gain
                brokerage_basis = max(0.0, brokerage_basis - basis_used)
                realized_ltcg += realized_gain
            brokerage -= take
            amount_needed -= take
            from_brokerage += take
        elif source == "trad":
            take = min(trad, amount_needed)
            trad -= take
            amount_needed -= take
            from_trad += take
        elif source == "roth":
            take = min(roth, amount_needed)
            roth -= take
            amount_needed -= take
            from_roth += take

    funded = from_cash + from_brokerage + from_trad + from_roth
    shortfall = amount_needed

    if abs((start_need - shortfall) - funded) > 0.01:
        raise AssertionError("Withdrawal accounting mismatch")

    return {
        "trad": trad,
        "roth": roth,
        "brokerage": brokerage,
        "brokerage_basis": brokerage_basis,
        "cash": cash,
        "from_cash": from_cash,
        "from_brokerage": from_brokerage,
        "from_trad": from_trad,
        "from_roth": from_roth,
        "taxable_trad_withdrawal": from_trad,
        "realized_ltcg": realized_ltcg,
        "shortfall": shortfall,
    }


def withdraw_for_spending(amount_needed: float, trad: float, roth: float, brokerage: float, cash: float, brokerage_basis: float) -> dict:
    return withdraw_by_policy(amount_needed, trad, roth, brokerage, cash, "Cash then Brokerage then Trad then Roth", brokerage_basis)


def withdraw_for_tax_with_fallback(amount_needed: float, trad: float, roth: float, brokerage: float, cash: float, preferred_policy: str, brokerage_basis: float) -> dict:
    preferred = withdraw_by_policy(amount_needed, trad, roth, brokerage, cash, preferred_policy, brokerage_basis)
    remaining = preferred["shortfall"]

    fallback_from_trad = 0.0
    fallback_from_roth = 0.0

    if remaining > 0:
        fallback = withdraw_by_policy(
            remaining,
            preferred["trad"],
            preferred["roth"],
            preferred["brokerage"],
            preferred["cash"],
            "Trad then Roth",
            preferred["brokerage_basis"],
        )

        trad = fallback["trad"]
        roth = fallback["roth"]
        brokerage = fallback["brokerage"]
        brokerage_basis = fallback["brokerage_basis"]
        cash = fallback["cash"]
        true_shortfall = fallback["shortfall"]
        fallback_from_trad = fallback["from_trad"]
        fallback_from_roth = fallback["from_roth"]
    else:
        trad = preferred["trad"]
        roth = preferred["roth"]
        brokerage = preferred["brokerage"]
        brokerage_basis = preferred["brokerage_basis"]
        cash = preferred["cash"]
        true_shortfall = 0.0

    return {
        "trad": trad,
        "roth": roth,
        "brokerage": brokerage,
        "brokerage_basis": brokerage_basis,
        "cash": cash,
        "preferred_from_cash": preferred["from_cash"],
        "preferred_from_brokerage": preferred["from_brokerage"],
        "preferred_from_trad": preferred["from_trad"],
        "preferred_from_roth": preferred["from_roth"],
        "fallback_from_trad": fallback_from_trad,
        "fallback_from_roth": fallback_from_roth,
        "realized_ltcg": preferred["realized_ltcg"] + (fallback["realized_ltcg"] if remaining > 0 else 0.0),
        "true_tax_shortfall": true_shortfall,
    }




TAX_SOURCE_PENALTY = {
    "cash": 0.0,
    "brokerage": 0.02,
    "trad": 0.08,
    "roth": 0.25,
}


def determine_tax_source_mix_from_row(row: dict) -> tuple[list[str], float]:
    sources = []
    if float(row.get("Tax Paid Preferred Cash", 0.0)) > 1e-9:
        sources.append("cash")
    if float(row.get("Tax Paid Preferred Brokerage", 0.0)) > 1e-9:
        sources.append("brokerage")
    if float(row.get("Tax Paid Preferred Trad", 0.0)) > 1e-9 or float(row.get("Tax Paid Fallback Trad", 0.0)) > 1e-9:
        sources.append("trad")
    if float(row.get("Tax Paid Preferred Roth", 0.0)) > 1e-9 or float(row.get("Tax Paid Fallback Roth", 0.0)) > 1e-9:
        sources.append("roth")
    penalty = max((TAX_SOURCE_PENALTY[s] for s in sources), default=0.0)
    return sources, penalty


def normalize_balances(trad: float, roth: float, brokerage: float, cash: float, brokerage_basis: float) -> tuple:
    brokerage = max(0.0, float(brokerage))
    brokerage_basis = min(max(0.0, float(brokerage_basis)), brokerage)
    return (
        max(0.0, float(trad)),
        max(0.0, float(roth)),
        brokerage,
        max(0.0, float(cash)),
        brokerage_basis,
    )


# -----------------------------
# PARAMS / HELPERS
# -----------------------------

def rmd_start_age_from_current_age(current_age: int) -> int:
    """
    Approximate SECURE 2.0 cohort logic from the modeled start-year age.
    If the person would have been born in 1950 or earlier -> 72
    1951-1959 -> 73
    1960+ -> 75
    """
    birth_year = START_YEAR - int(current_age)
    if birth_year <= 1950:
        return 72
    if birth_year <= 1959:
        return 73
    return 75


def uniform_lifetime_divisor(age: int) -> float:
    divisor_map = {
        72: 27.4, 73: 26.5, 74: 25.5, 75: 24.6, 76: 23.7, 77: 22.9, 78: 22.0, 79: 21.1,
        80: 20.2, 81: 19.4, 82: 18.5, 83: 17.7, 84: 16.8, 85: 16.0, 86: 15.2,
        87: 14.4, 88: 13.7, 89: 12.9, 90: 12.2, 91: 11.5, 92: 10.8, 93: 10.1,
        94: 9.5, 95: 8.9, 96: 8.4, 97: 7.8, 98: 7.3, 99: 6.8, 100: 6.4
    }
    if age in divisor_map:
        return float(divisor_map[age])
    if age < 72:
        return 27.4
    return 6.4


def estimate_household_rmd_for_year(year: int, trad_balance: float, params: dict) -> float:
    """
    Household simplification: once the first spouse reaches RMD age, apply a Uniform Lifetime
    divisor based on the older spouse's age to the combined traditional balance.
    """
    owner_age = int(params.get("owner_current_age", 0)) + (year - START_YEAR)
    spouse_age = int(params.get("spouse_current_age", 0)) + (year - START_YEAR)
    owner_start_age = int(params.get("owner_rmd_start_age", 73))
    spouse_start_age = int(params.get("spouse_rmd_start_age", 73))
    owner_rmd_start = int(params.get("owner_rmd_start", START_YEAR + max(0, owner_start_age - int(params.get("owner_current_age", 0)))))
    spouse_rmd_start = int(params.get("spouse_rmd_start", START_YEAR + max(0, spouse_start_age - int(params.get("spouse_current_age", 0)))))

    if year < min(owner_rmd_start, spouse_rmd_start) and year < max(owner_rmd_start, spouse_rmd_start):
        # Neither spouse yet at RMD age
        if year < owner_rmd_start and year < spouse_rmd_start:
            return 0.0

    if trad_balance <= 0:
        return 0.0

    oldest_age = max(owner_age, spouse_age)
    divisor = uniform_lifetime_divisor(oldest_age)
    return max(0.0, float(trad_balance) / divisor)

def build_common_params(inputs: dict) -> dict:
    owner_ss_start = ss_start_year_from_current_age(START_YEAR, int(inputs["owner_current_age"]), int(inputs["owner_claim_age"]))
    spouse_ss_start = ss_start_year_from_current_age(START_YEAR, int(inputs["spouse_current_age"]), int(inputs["spouse_claim_age"]))

    owner_ss_annual = annual_ss_benefit(float(inputs["owner_ss_base"]), int(inputs["owner_claim_age"]))
    spouse_ss_annual = annual_ss_benefit(float(inputs["spouse_ss_base"]), int(inputs["spouse_claim_age"]))

    owner_rmd_start_age = rmd_start_age_from_current_age(int(inputs["owner_current_age"]))
    spouse_rmd_start_age = rmd_start_age_from_current_age(int(inputs["spouse_current_age"]))
    owner_rmd_start = START_YEAR + max(0, owner_rmd_start_age - int(inputs["owner_current_age"]))
    spouse_rmd_start = START_YEAR + max(0, spouse_rmd_start_age - int(inputs["spouse_current_age"]))
    household_rmd_start = min(owner_rmd_start, spouse_rmd_start)

    return {
        "growth": float(inputs["growth"]),
        "annual_spending": float(inputs["annual_spending"]),
        "spending_inflation_rate": float(inputs.get("spending_inflation_rate", 0.0)),
        "retirement_smile_enabled": bool(inputs.get("retirement_smile_enabled", False)),
        "go_go_end_age": int(inputs.get("go_go_end_age", 70)),
        "slow_go_end_age": int(inputs.get("slow_go_end_age", 80)),
        "go_go_multiplier": float(inputs.get("go_go_multiplier", 1.00)),
        "slow_go_multiplier": float(inputs.get("slow_go_multiplier", 0.85)),
        "no_go_multiplier": float(inputs.get("no_go_multiplier", 1.20)),
        "conversion_tax_funding_policy": inputs["conversion_tax_funding_policy"],
        "owner_ss_start": owner_ss_start,
        "spouse_ss_start": spouse_ss_start,
        "owner_ss_annual": owner_ss_annual,
        "spouse_ss_annual": spouse_ss_annual,
        "earned_income_annual": float(inputs.get("earned_income_annual", 0.0)),
        "earned_income_start_year": int(inputs.get("earned_income_start_year", START_YEAR)),
        "earned_income_end_year": int(inputs.get("earned_income_end_year", START_YEAR - 1)),
        "primary_aca_end_year": int(inputs["primary_aca_end_year"]),
        "spouse_aca_end_year": int(inputs["spouse_aca_end_year"]),
        "household_rmd_start": household_rmd_start,
        "post_aca_target_bracket": str(inputs.get("post_aca_target_bracket", "22%")),
        "rmd_era_target_bracket": str(inputs.get("rmd_era_target_bracket", "22%")),
        "owner_current_age": int(inputs["owner_current_age"]),
        "spouse_current_age": int(inputs["spouse_current_age"]),
        "owner_rmd_start_age": int(owner_rmd_start_age),
        "spouse_rmd_start_age": int(spouse_rmd_start_age),
        "owner_rmd_start": int(owner_rmd_start),
        "spouse_rmd_start": int(spouse_rmd_start),
        "cash_sweep_threshold": float(inputs.get("cash_sweep_threshold", 50000.0)),
        "state_tax_rate": float(inputs.get("state_tax_rate", 0.0399)),
        "target_trad_balance_enabled": bool(inputs.get("target_trad_balance_enabled", False)),
        "target_trad_balance": float(inputs.get("target_trad_balance", 300000.0)),
        "target_trad_override_enabled": bool(inputs.get("target_trad_override_enabled", False)),
        "target_trad_override_max_rate": float(inputs.get("target_trad_override_max_rate", 0.22)),
    }


def summarize_run(df: pd.DataFrame, params: dict) -> dict:
    total_state_taxes = float(df["State Tax"].sum()) if "State Tax" in df.columns else 0.0
    total_government_drag = float(
        df["Federal Tax"].sum()
        + total_state_taxes
        + df["ACA Cost"].sum()
        + df["IRMAA Cost"].sum()
    )
    return {
        "df": df,
        "final_net_worth": float(df.iloc[-1]["Net Worth"]),
        "total_federal_taxes": float(df["Federal Tax"].sum()),
        "total_state_taxes": total_state_taxes,
        "total_aca_cost": float(df["ACA Cost"].sum()),
        "total_irmaa_cost": float(df["IRMAA Cost"].sum()),
        "total_government_drag": total_government_drag,
        "total_shortfall": float(df["Year Shortfall"].sum()),
        "max_magi": float(df["MAGI"].max()),
        "aca_hit_years": int((df["ACA Cost"] > 0).sum()),
        "irmaa_hit_years": int((df["IRMAA Cost"] > 0).sum()),
        "first_irmaa_year": int(df.loc[df["IRMAA Cost"] > 0, "Year"].iloc[0]) if (df["IRMAA Cost"] > 0).any() else None,
        "owner_ss_start": int(params["owner_ss_start"]),
        "spouse_ss_start": int(params["spouse_ss_start"]),
        "household_rmd_start": int(params["household_rmd_start"]),
        "owner_claim_age": int(params["owner_ss_start"] - START_YEAR + int(params["owner_current_age"])),
        "spouse_claim_age": int(params["spouse_ss_start"] - START_YEAR + int(params["spouse_current_age"])),
        "total_conversions": float(df["Chosen Conversion"].sum()),
        "ending_trad_balance": float(df.iloc[-1]["EOY Trad"]),
    }


def calc_total_drag(row: dict) -> float:
    return float(row.get("Federal Tax", 0.0) + row.get("State Tax", 0.0) + row.get("ACA Cost", 0.0) + row.get("IRMAA Cost", 0.0))

def sanitize_effective_rate(raw_rate: float, current_marginal_rate: float) -> float:
    """
    Clamp pathological effective-rate outputs caused by stale/zero deltas.
    Effective rate can exceed marginal rate due to SS interaction, but it should not
    be materially negative and should not sit far below marginal rate when taxes are positive.
    """
    r = max(0.0, float(raw_rate))
    cm = max(0.0, float(current_marginal_rate))
    if cm > 0 and r > 0 and r < cm * 0.8:
        r = cm
    return r



# -----------------------------
# DISPLAY COLUMN ORGANIZATION
# -----------------------------
def _ordered_subset(existing_cols, preferred_order):
    seen = set()
    ordered = []
    for c in preferred_order:
        if c in existing_cols and c not in seen:
            ordered.append(c)
            seen.add(c)
    for c in existing_cols:
        if c not in seen:
            ordered.append(c)
            seen.add(c)
    return ordered


def organize_yearly_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df

    preferred = [
        # Core timing / decision
        "Year",
        "Chosen Conversion",

        # Starting balances
        "SOY Trad", "SOY Roth", "SOY Brokerage", "SOY Brokerage Basis", "SOY Cash",

        # Cash inflows / income sources
        "Earned Income", "Owner SS", "Spouse SS", "Total SS",
        "Annual Spending Need", "Household Reference Age", "Trad Withdrawal Income Component", "Conversion Income Component", "Brokerage Realized LTCG",

        # Aggregated income
        "Other Ordinary Income", "Taxable SS", "AGI", "MAGI",
        "Taxable Income", "Ordinary Taxable Income", "LTCG Taxable Income",

        # Rates / policy outputs
        "Current Marginal Tax Rate", "Current Marginal Incremental Cost Rate",
        "Estimated Future Marginal Rate", "Projected Future Avoided Rate",
        "Net Benefit Rate", "Effective Current Rate (Adjusted)",
        "ACA Lives", "Medicare Lives", "Primary On ACA", "Spouse On ACA",

        # Costs
        "Federal Tax", "State Tax", "Total Tax", "Ordinary Tax", "LTCG Tax", "ACA Cost", "IRMAA Cost", "Year Shortfall",

        # Tax funding mechanics
        "Tax Funding Source", "Tax Funding Penalty",
        "Tax Paid Preferred Cash", "Tax Paid Preferred Brokerage",
        "Tax Paid Preferred Trad", "Tax Paid Preferred Roth",
        "Tax Paid Fallback Trad", "Tax Paid Fallback Roth",

        # Ending balances
        "EOY Trad", "EOY Roth", "EOY Brokerage", "EOY Brokerage Basis",
        "EOY Brokerage Unrealized Gain", "Cash Swept To Brokerage", "EOY Cash", "Net Worth",

        # Diagnostics / solver details
        "Baseline MAGI Before Conversion",
        "ACA MAGI Limit", "Baseline MAGI (0 Conv)", "ACA Headroom Before Conversion",
        "MAGI Remaining To ACA Limit",
        "Target Bracket", "Target Ordinary Taxable Income",
        "Baseline Ordinary Taxable Income (0 Conv)",
        "Ordinary Income Headroom Before Conversion",
        "Ordinary Income Remaining To Target",
        "Projected Future RMD", "Projected Future Ordinary Income",
        "Future Rate Projection Year", "BETR Stop Trigger Hit",
        "Accounting Status", "Accounting Issues",
    ]
    return df.loc[:, _ordered_subset(list(df.columns), preferred)]


def organize_decision_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df

    preferred = [
        "Year", "Decision Mode", "Step Index",
        "Base Conversion", "Test Conversion", "Step Amount", "Applied Conversion", "Selected Conversion After Test",
        "Baseline MAGI (0 Conv)", "ACA MAGI Limit", "MAGI Headroom Before Conversion",
        "Base MAGI", "Test MAGI", "MAGI Remaining To Limit", "Within ACA Limit",
        "Target Bracket", "Target Ordinary Taxable Income", "Baseline Ordinary Taxable Income (0 Conv)",
        "Test Ordinary Taxable Income", "Ordinary Income Remaining To Target", "Within Target Bracket",
        "Current Marginal Incremental Cost Rate", "Projected Future Avoided Rate", "Net Benefit Rate",
        "Current Marginal Tax Rate", "Estimated Future Marginal Rate", "BETR Stop Trigger Hit",
        "Tax Funding Source", "Tax Funding Penalty", "Effective Current Rate (Adjusted)",
        "Current Year Federal Tax Delta", "Current Year ACA Delta", "Current Year IRMAA Delta", "Current Marginal Cost",
        "Future Avoided Federal Tax", "Future Avoided ACA Cost", "Future Avoided IRMAA Cost", "Future Avoided Cost",
        "Federal Tax", "ACA Cost", "IRMAA Cost",
        "EOY Trad", "EOY Brokerage", "EOY Cash", "Final Net Worth (Zero Later Conv)",
        "Selected MAGI", "Selected Ordinary Taxable Income",
        "ACA Solver Note", "Bracket Solver Note",
    ]
    return df.loc[:, _ordered_subset(list(df.columns), preferred)]


def enrich_year_row_for_display(
    year: int,
    state_before: dict,
    params: dict,
    row: dict,
    baseline_row: dict | None = None,
    projection_cache: dict | None = None,
) -> dict:
    """
    Add the same readability/diagnostic columns to both the flat-strategy table
    and the governor table so the schemas stay aligned.
    """
    coverage = get_coverage_status(
        year,
        int(params["primary_aca_end_year"]),
        int(params["spouse_aca_end_year"]),
    )
    aca_limit = get_aca_magi_limit(year, coverage["aca_lives"])
    if baseline_row is None:
        baseline_path = run_projection_from_state(
            year,
            dict(state_before),
            params,
            first_year_conversion=0.0,
            later_year_conversion=0.0,
            projection_cache=projection_cache,
        )
        baseline_row = baseline_path["df"].iloc[0].to_dict()
    target_label = params["post_aca_target_bracket"] if year < int(params["household_rmd_start"]) else params["rmd_era_target_bracket"]
    target_top = get_target_bracket_top(year, target_label)
    baseline_ord = float(baseline_row.get("Ordinary Taxable Income", 0.0))
    row["ACA MAGI Limit"] = float(aca_limit) if coverage["aca_lives"] > 0 else float("inf")
    row["Baseline MAGI (0 Conv)"] = float(baseline_row.get("MAGI", 0.0))
    row["ACA Headroom Before Conversion"] = float(max(0.0, aca_limit - baseline_row.get("MAGI", 0.0))) if coverage["aca_lives"] > 0 else 0.0
    row["MAGI Remaining To ACA Limit"] = float(aca_limit - float(row.get("MAGI", 0.0))) if coverage["aca_lives"] > 0 else float("inf")
    row["Target Bracket"] = "ACA" if coverage["aca_lives"] > 0 else str(target_label)
    row["Target Ordinary Taxable Income"] = float(target_top) if coverage["aca_lives"] == 0 else 0.0
    row["Baseline Ordinary Taxable Income (0 Conv)"] = baseline_ord
    row["Ordinary Income Headroom Before Conversion"] = float(max(0.0, target_top - baseline_ord)) if coverage["aca_lives"] == 0 else 0.0
    row["Ordinary Income Remaining To Target"] = float(target_top - float(row.get("Ordinary Taxable Income", 0.0))) if coverage["aca_lives"] == 0 else 0.0
    if "Future Rate Projection Year" in row and pd.notna(row["Future Rate Projection Year"]):
        try:
            row["Future Rate Projection Year"] = str(int(float(row["Future Rate Projection Year"])))
        except Exception:
            row["Future Rate Projection Year"] = str(row["Future Rate Projection Year"])
    return row



# -----------------------------
# YEAR SIMULATION
# -----------------------------

def simulate_one_year(year: int, state: dict, params: dict, annual_conversion: float) -> tuple:
    trad = float(state["trad"])
    roth = float(state["roth"])
    brokerage = float(state["brokerage"])
    brokerage_basis = min(float(state.get("brokerage_basis", state["brokerage"])), brokerage)
    cash = float(state["cash"])

    growth = float(params["growth"])
    annual_spending = get_annual_spending_for_year(year, params)
    conversion_tax_funding_policy = params["conversion_tax_funding_policy"]

    owner_ss_start = int(params["owner_ss_start"])
    spouse_ss_start = int(params["spouse_ss_start"])
    owner_ss_annual = float(params["owner_ss_annual"])
    spouse_ss_annual = float(params["spouse_ss_annual"])
    primary_aca_end_year = int(params["primary_aca_end_year"])
    spouse_aca_end_year = int(params["spouse_aca_end_year"])

    soy_trad = trad
    soy_roth = roth
    soy_brokerage = brokerage
    soy_brokerage_basis = brokerage_basis
    soy_cash = cash
    cash_swept_to_brokerage = 0.0

    # Grow investable assets
    trad *= (1 + growth)
    roth *= (1 + growth)
    brokerage *= (1 + growth)

    # Cash-like inflows arrive during the year
    # Actual RMD enforcement (household simplification on combined Trad balance)
    rmd_for_year = estimate_household_rmd_for_year(year, trad, params)
    if rmd_for_year > 0:
        trad -= rmd_for_year
        cash += rmd_for_year

    owner_ss = owner_ss_annual if year >= owner_ss_start else 0.0
    spouse_ss = spouse_ss_annual if year >= spouse_ss_start else 0.0
    total_ss = owner_ss + spouse_ss
    earned_income = get_earned_income_for_year(year, params)
    cash += total_ss + earned_income

    # Coverage status is based on the calendar year, not the funding sequence
    coverage = get_coverage_status(year, primary_aca_end_year, spouse_aca_end_year)
    aca_lives = coverage["aca_lives"]
    medicare_lives = coverage["medicare_lives"]

    # 1) Fund mandatory spending first. This creates unavoidable taxable withdrawals/LTCG.
    spend_result = withdraw_for_spending(annual_spending, trad, roth, brokerage, cash, brokerage_basis)
    trad = spend_result["trad"]
    roth = spend_result["roth"]
    brokerage = spend_result["brokerage"]
    brokerage_basis = spend_result["brokerage_basis"]
    cash = spend_result["cash"]

    spending_trad_withdrawal = float(spend_result["taxable_trad_withdrawal"])
    total_trad_withdrawal_income = spending_trad_withdrawal + float(rmd_for_year)
    spending_realized_ltcg = float(spend_result["realized_ltcg"])

    # 2) Baseline income stack before any optional conversion
    baseline_other_ordinary_income = earned_income + total_trad_withdrawal_income
    baseline_tax_info = calculate_federal_tax(
        baseline_other_ordinary_income,
        total_ss,
        year,
        realized_ltcg=spending_realized_ltcg,
    )
    baseline_magi = calculate_magi(baseline_tax_info["agi"], year)

    # 3) Apply optional conversion on top of the mandatory baseline
    conversion = min(float(annual_conversion), trad)
    trad -= conversion
    roth += conversion

    # 4) Final income stack for the year
    other_ordinary_income = earned_income + total_trad_withdrawal_income + conversion
    realized_ltcg = spending_realized_ltcg

    tax_info = calculate_federal_tax(
        other_ordinary_income,
        total_ss,
        year,
        realized_ltcg=realized_ltcg,
    )
    federal_tax = tax_info["federal_tax"]
    estimated_state_tax = max(
        0.0,
        float(tax_info["ordinary_taxable_income"] + tax_info.get("ltcg_taxable_income", 0.0))
    ) * float(params.get("state_tax_rate", 0.0399))

    # 5) Pay current-year tax estimate (federal + state). Brokerage sales here can realize additional LTCG.
    tax_result = withdraw_for_tax_with_fallback(
        federal_tax + estimated_state_tax, trad, roth, brokerage, cash, conversion_tax_funding_policy, brokerage_basis
    )
    trad = tax_result["trad"]
    roth = tax_result["roth"]
    brokerage = tax_result["brokerage"]
    brokerage_basis = tax_result["brokerage_basis"]
    cash = tax_result["cash"]
    realized_ltcg += tax_result["realized_ltcg"]

    # Recompute tax after tax-funding LTCG effects
    tax_info = calculate_federal_tax(
        other_ordinary_income,
        total_ss,
        year,
        realized_ltcg=realized_ltcg,
    )
    federal_tax = tax_info["federal_tax"]
    state_tax = max(0.0, float(tax_info["ordinary_taxable_income"] + tax_info.get("ltcg_taxable_income", 0.0))) * float(params.get("state_tax_rate", 0.0399))
    magi = calculate_magi(tax_info["agi"], year)

    # 6) ACA / IRMAA are determined off final MAGI
    aca_cost = calculate_aca_cost(magi, year, aca_lives)
    irmaa_cost = calculate_irmaa_cost(magi, year, medicare_lives)

    # 7) Pay ACA / IRMAA; brokerage sales here can also realize additional LTCG
    aca_result = withdraw_for_spending(aca_cost, trad, roth, brokerage, cash, brokerage_basis)
    trad = aca_result["trad"]
    roth = aca_result["roth"]
    brokerage = aca_result["brokerage"]
    brokerage_basis = aca_result["brokerage_basis"]
    cash = aca_result["cash"]
    realized_ltcg += aca_result["realized_ltcg"]

    irmaa_result = withdraw_for_spending(irmaa_cost, trad, roth, brokerage, cash, brokerage_basis)
    trad = irmaa_result["trad"]
    roth = irmaa_result["roth"]
    brokerage = irmaa_result["brokerage"]
    brokerage_basis = irmaa_result["brokerage_basis"]
    cash = irmaa_result["cash"]
    realized_ltcg += irmaa_result["realized_ltcg"]

    # Final recompute after all realized LTCG for the year
    tax_info = calculate_federal_tax(
        other_ordinary_income,
        total_ss,
        year,
        realized_ltcg=realized_ltcg,
    )
    federal_tax = tax_info["federal_tax"]
    state_tax = max(
        0.0,
        float(tax_info["ordinary_taxable_income"] + tax_info.get("ltcg_taxable_income", 0.0))
    ) * float(params.get("state_tax_rate", 0.0399))
    magi = calculate_magi(tax_info["agi"], year)

    trad, roth, brokerage, cash, brokerage_basis = normalize_balances(trad, roth, brokerage, cash, brokerage_basis)

    # End-of-year cash sweep: after all yearly flows are complete, sweep excess cash into brokerage.
    cash_sweep_threshold = float(params.get("cash_sweep_threshold", 50000.0))
    if cash > cash_sweep_threshold:
        cash_swept_to_brokerage = float(cash - cash_sweep_threshold)
        cash = float(cash_sweep_threshold)
        brokerage += cash_swept_to_brokerage
        brokerage_basis += cash_swept_to_brokerage

    trad, roth, brokerage, cash, brokerage_basis = normalize_balances(trad, roth, brokerage, cash, brokerage_basis)

    year_shortfall = (
        spend_result["shortfall"]
        + tax_result["true_tax_shortfall"]
        + aca_result["shortfall"]
        + irmaa_result["shortfall"]
    )

    net_worth = trad + roth + brokerage + cash

    row = {
        "Year": year,
        "SOY Trad": soy_trad,
        "SOY Roth": soy_roth,
        "SOY Brokerage": soy_brokerage,
        "SOY Brokerage Basis": soy_brokerage_basis,
        "SOY Cash": soy_cash,
        "Owner SS": owner_ss,
        "Spouse SS": spouse_ss,
        "Total SS": total_ss,
        "Chosen Conversion": conversion,
        "Earned Income": earned_income,
        "Conversion Income Component": conversion,
        "RMD Income Component": float(rmd_for_year),
        "Trad Withdrawal Income Component": total_trad_withdrawal_income,
        "Spending Trad Withdrawal Component": spending_trad_withdrawal,
        "Spending Funded From Cash": float(spend_result["from_cash"]),
        "Spending Funded From Brokerage": float(spend_result["from_brokerage"]),
        "Spending Brokerage Basis Used": float(max(0.0, spend_result["from_brokerage"] - spending_realized_ltcg)),
        "Spending Funded From Roth": float(spend_result["from_roth"]),
        "Annual Spending Need": annual_spending,
        "Household Reference Age": get_household_reference_age(year, params),
        "Other Ordinary Income": other_ordinary_income,
        "Brokerage Realized LTCG": realized_ltcg,
        "Taxable SS": tax_info["taxable_ss"],
        "AGI": tax_info["agi"],
        "Taxable Income": tax_info["taxable_income"],
        "Ordinary Taxable Income": tax_info["ordinary_taxable_income"],
        "LTCG Taxable Income": tax_info["ltcg_taxable_income"],
        "Current Marginal Tax Rate": tax_info["marginal_rate"],
        "Estimated Future Marginal Rate": 0.0,
        "Baseline MAGI Before Conversion": baseline_magi,
        "MAGI": magi,
        "Primary On ACA": coverage["primary_on_aca"],
        "Spouse On ACA": coverage["spouse_on_aca"],
        "ACA Lives": aca_lives,
        "Medicare Lives": medicare_lives,
        "Federal Tax": federal_tax,
        "State Tax": state_tax,
        "Total Tax": federal_tax + state_tax,
        "Ordinary Tax": tax_info["ordinary_tax"],
        "LTCG Tax": tax_info["ltcg_tax"],
        "Tax Paid Preferred Cash": tax_result["preferred_from_cash"],
        "Tax Paid Preferred Brokerage": tax_result["preferred_from_brokerage"],
        "Tax Paid Preferred Trad": tax_result["preferred_from_trad"],
        "Tax Paid Preferred Roth": tax_result["preferred_from_roth"],
        "Tax Paid Fallback Trad": tax_result["fallback_from_trad"],
        "Tax Paid Fallback Roth": tax_result["fallback_from_roth"],
        "ACA Cost": aca_cost,
        "IRMAA Cost": irmaa_cost,
        "Year Shortfall": year_shortfall,
        "EOY Trad": trad,
        "EOY Roth": roth,
        "EOY Brokerage": brokerage,
        "EOY Brokerage Basis": brokerage_basis,
        "EOY Brokerage Unrealized Gain": max(0.0, brokerage - brokerage_basis),
        "Cash Swept To Brokerage": cash_swept_to_brokerage,
        "EOY Cash": cash,
        "Net Worth": net_worth,
    }

    tax_sources, tax_source_penalty = determine_tax_source_mix_from_row(row)
    row["Tax Funding Source"] = " + ".join(tax_sources) if tax_sources else "none"
    row["Tax Funding Penalty"] = tax_source_penalty
    row["Effective Current Rate (Adjusted)"] = float(row.get("Current Marginal Tax Rate", 0.0)) + tax_source_penalty

    if bool(params.get("integrity_mode", False)):
        accounting_issues = validate_row_accounting(row, year)
        row["Accounting Status"] = "PASS" if not accounting_issues else "FAIL"
        row["Accounting Issues"] = " | ".join(accounting_issues)
    else:
        row["Accounting Status"] = "SKIPPED"
        row["Accounting Issues"] = ""

    next_state = {
        "trad": trad,
        "roth": roth,
        "brokerage": brokerage,
        "brokerage_basis": brokerage_basis,
        "cash": cash,
    }

    return next_state, row


# -----------------------------
# PATH RUNNERS
# -----------------------------
def run_projection_from_state(
    start_year: int,
    starting_state: dict,
    params: dict,
    first_year_conversion: float = 0.0,
    later_year_conversion: float = 0.0,
    projection_cache: dict | None = None,
) -> dict:
    cache_key = None
    if projection_cache is not None:
        cache_key = _make_projection_cache_key(
            start_year,
            starting_state,
            params,
            first_year_conversion,
            later_year_conversion,
        )
        cached = projection_cache.get(cache_key)
        if cached is not None:
            return _clone_projection_result(cached)

    state = {
        "trad": float(starting_state["trad"]),
        "roth": float(starting_state["roth"]),
        "brokerage": float(starting_state["brokerage"]),
        "brokerage_basis": float(starting_state.get("brokerage_basis", starting_state["brokerage"])),
        "cash": float(starting_state["cash"]),
    }
    rows = []

    for year in range(start_year, END_YEAR + 1):
        conversion = float(first_year_conversion) if year == start_year else float(later_year_conversion)
        state, row = simulate_one_year(year, state, params, conversion)
        rows.append(row)

    df = pd.DataFrame(rows)
    result = summarize_run(df, params)
    result["start_year"] = start_year

    if projection_cache is not None and cache_key is not None:
        projection_cache[cache_key] = _clone_projection_result(result)

    return result




def run_projection_summary_from_state(
    start_year: int,
    starting_state: dict,
    params: dict,
    first_year_conversion: float = 0.0,
    later_year_conversion: float = 0.0,
    projection_cache: dict | None = None,
) -> dict:
    cache_key = None
    if projection_cache is not None:
        cache_key = "summary:" + _make_projection_cache_key(
            start_year,
            starting_state,
            params,
            first_year_conversion,
            later_year_conversion,
        )
        cached = projection_cache.get(cache_key)
        if cached is not None:
            return copy.deepcopy(cached)

    state = {
        "trad": float(starting_state["trad"]),
        "roth": float(starting_state["roth"]),
        "brokerage": float(starting_state["brokerage"]),
        "brokerage_basis": float(starting_state.get("brokerage_basis", starting_state["brokerage"])),
        "cash": float(starting_state["cash"]),
    }

    first_row = None
    last_row = None
    total_federal_taxes = 0.0
    total_state_taxes = 0.0
    total_aca_cost = 0.0
    total_irmaa_cost = 0.0
    total_shortfall = 0.0
    total_conversions = 0.0
    max_magi = 0.0

    for year in range(start_year, END_YEAR + 1):
        conversion = float(first_year_conversion) if year == start_year else float(later_year_conversion)
        state, row = simulate_one_year(year, state, params, conversion)
        if first_row is None:
            first_row = dict(row)
        last_row = dict(row)
        total_federal_taxes += float(row.get("Federal Tax", 0.0))
        total_state_taxes += float(row.get("State Tax", 0.0))
        total_aca_cost += float(row.get("ACA Cost", 0.0))
        total_irmaa_cost += float(row.get("IRMAA Cost", 0.0))
        total_shortfall += float(row.get("Year Shortfall", 0.0))
        total_conversions += float(row.get("Chosen Conversion", 0.0))
        max_magi = max(max_magi, float(row.get("MAGI", 0.0)))

    if last_row is None:
        last_row = {}
        first_row = {}

    result = {
        "start_year": int(start_year),
        "first_row": first_row,
        "last_row": last_row,
        "final_net_worth": float(last_row.get("Net Worth", 0.0)),
        "total_federal_taxes": float(total_federal_taxes),
        "total_state_taxes": float(total_state_taxes),
        "total_aca_cost": float(total_aca_cost),
        "total_irmaa_cost": float(total_irmaa_cost),
        "total_government_drag": float(total_federal_taxes + total_state_taxes + total_aca_cost + total_irmaa_cost),
        "total_shortfall": float(total_shortfall),
        "max_magi": float(max_magi),
        "total_conversions": float(total_conversions),
        "ending_trad_balance": float(last_row.get("EOY Trad", 0.0)),
    }

    if projection_cache is not None and cache_key is not None:
        projection_cache[cache_key] = copy.deepcopy(result)

    return result

def run_model_fixed(inputs: dict) -> dict:
    params = build_common_params(inputs)
    state = {
        "trad": float(inputs["trad"]),
        "roth": float(inputs["roth"]),
        "brokerage": float(inputs["brokerage"]),
        "brokerage_basis": float(inputs.get("brokerage_basis", inputs["brokerage"])),
        "cash": float(inputs["cash"]),
    }
    annual_conversion = float(inputs["annual_conversion"])

    rows = []
    for year in range(START_YEAR, END_YEAR + 1):
        state_before = dict(state)
        state, row = simulate_one_year(year, state, params, annual_conversion)
        row = enrich_year_row_for_display(year, state_before, params, row)
        rows.append(row)

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.loc[:, ~df.columns.duplicated()].copy()
        df = df.sort_values("Year").reset_index(drop=True)
        df = organize_yearly_columns(df)
    return summarize_run(df, params)


# -----------------------------
# BREAK-EVEN CONVERSION ENGINE
# -----------------------------
def get_year_conversion_cap(state: dict, params: dict, max_conversion: float) -> float:
    trad_after_growth = max(0.0, float(state["trad"]) * (1.0 + float(params["growth"])))
    return max(0.0, min(float(max_conversion), trad_after_growth))

def estimate_target_trad_pressure_conversion(year: int, state: dict, params: dict, step_size: float, cap: float) -> float:
    """
    Annual conversion needed to work toward the target Traditional IRA balance by the
    first household RMD year. This is intentionally simple but no longer ultra-timid:
    the governor can use it as an explicit pace target for pre-RMD depletion.
    """
    if not bool(params.get("target_trad_balance_enabled", False)):
        return 0.0
    rmd_start = int(params.get("household_rmd_start", year))
    if year >= rmd_start:
        return 0.0
    years_left = max(1, rmd_start - year)
    current_trad = max(0.0, float(state.get("trad", 0.0)))
    target_trad = max(0.0, float(params.get("target_trad_balance", 300000.0)))
    excess = max(0.0, current_trad - target_trad)
    annual_needed = excess / years_left
    rounded = floor_to_step(annual_needed, step_size)
    return max(0.0, min(float(cap), float(rounded)))


def project_trad_balance_at_rmd_start(year: int, state: dict, params: dict, projection_cache: dict | None = None) -> float:
    """
    Project the ending Traditional IRA balance in the year immediately before the
    first household RMD year, assuming no additional optional conversions from the
    current year forward. This is used only as a planning diagnostic / pressure gauge.
    """
    rmd_start = int(params.get("household_rmd_start", year))
    if year >= rmd_start:
        return max(0.0, float(state.get("trad", 0.0)))

    summary = run_projection_summary_from_state(
        start_year=year,
        starting_state=dict(state),
        params=params,
        first_year_conversion=0.0,
        later_year_conversion=0.0,
        projection_cache=projection_cache,
    )
    first_row = summary.get("first_row", {}) or {}
    years_until_rmd = max(0, rmd_start - year - 1)
    projected_trad = float(first_row.get("EOY Trad", state.get("trad", 0.0)))
    growth = float(params.get("growth", 0.0))
    for _ in range(years_until_rmd):
        projected_trad *= (1.0 + growth)
    return max(0.0, projected_trad)



def blended_future_effective_rate(delta_based_rate: float, estimated_future_rate: float, tax_source_penalty: float) -> float:
    """
    Use true delta-based future avoided-cost rate when it exists.
    If the projected path has no modeled future deltas yet (common before real RMD enforcement),
    fall back to the estimated future marginal rate.
    """
    if abs(float(delta_based_rate)) > 1e-12:
        return float(delta_based_rate)
    return max(0.0, float(estimated_future_rate))


def adjusted_current_effective_rate(delta_based_rate: float, tax_source_penalty: float) -> float:
    return max(0.0, float(delta_based_rate) + float(tax_source_penalty))

def stabilized_future_avoided_rate(raw_delta_rate: float, estimated_future_rate: float, current_marginal_rate: float) -> float:
    """
    Use the delta-based future avoided-cost rate when present, but stabilize cliff-driven spikes.
    If the raw delta-based rate is zero/unavailable, fall back to the estimated future marginal rate.
    Then cap the result to a modest premium over the larger of current/future bracket proxies.
    """
    base = float(raw_delta_rate)
    if abs(base) <= 1e-12:
        base = float(estimated_future_rate)
    floor = 0.0
    cap = max(float(current_marginal_rate), float(estimated_future_rate)) + 0.15
    return max(floor, min(base, cap))


def evaluate_conversion_pair(year: int, state: dict, params: dict, current_conversion: float, next_conversion: float) -> dict:
    current_path = run_projection_from_state(year, state, params, first_year_conversion=current_conversion, later_year_conversion=0.0)
    next_path = run_projection_from_state(year, state, params, first_year_conversion=next_conversion, later_year_conversion=0.0)

    current_row = current_path["df"].iloc[0]
    next_row = next_path["df"].iloc[0]

    current_year_drag_now = calc_total_drag(current_row)
    current_year_drag_next = calc_total_drag(next_row)
    current_marginal_cost = current_year_drag_next - current_year_drag_now

    future_drag_now = float(current_path["df"].iloc[1:][["Federal Tax", "State Tax", "ACA Cost", "IRMAA Cost"]].sum().sum())
    future_drag_next = float(next_path["df"].iloc[1:][["Federal Tax", "State Tax", "ACA Cost", "IRMAA Cost"]].sum().sum())
    future_avoided_cost = future_drag_now - future_drag_next

    step_amount = next_conversion - current_conversion
    net_benefit = future_avoided_cost - current_marginal_cost

    return {
        "Year": year,
        "Base Conversion": float(current_conversion),
        "Test Conversion": float(next_conversion),
        "Step Amount": float(step_amount),
        "Base MAGI": float(current_row["MAGI"]),
        "Test MAGI": float(next_row["MAGI"]),
        "Base Taxable Income": float(current_row["Taxable Income"]),
        "Test Taxable Income": float(next_row["Taxable Income"]),
        "Current Year Federal Tax Delta": float(next_row["Federal Tax"] - current_row["Federal Tax"]),
        "Current Year ACA Delta": float(next_row["ACA Cost"] - current_row["ACA Cost"]),
        "Current Year IRMAA Delta": float(next_row["IRMAA Cost"] - current_row["IRMAA Cost"]),
        "Current Marginal Cost": float(current_marginal_cost),
        "Future Avoided Federal Tax": float(current_path["df"].iloc[1:]["Federal Tax"].sum() - next_path["df"].iloc[1:]["Federal Tax"].sum()),
        "Future Avoided State Tax": float(current_path["df"].iloc[1:]["State Tax"].sum() - next_path["df"].iloc[1:]["State Tax"].sum()),
        "Future Avoided ACA Cost": float(current_path["df"].iloc[1:]["ACA Cost"].sum() - next_path["df"].iloc[1:]["ACA Cost"].sum()),
        "Future Avoided IRMAA Cost": float(current_path["df"].iloc[1:]["IRMAA Cost"].sum() - next_path["df"].iloc[1:]["IRMAA Cost"].sum()),
        "Future Avoided Cost": float(future_avoided_cost),
        "Net Benefit": float(net_benefit),
        "Break-Even Reached": bool(current_marginal_cost >= future_avoided_cost),
        "Base EOY Trad": float(current_row["EOY Trad"]),
        "Test EOY Trad": float(next_row["EOY Trad"]),
        "Base Final Net Worth": float(current_path["final_net_worth"]),
        "Test Final Net Worth": float(next_path["final_net_worth"]),
    }



def floor_to_step(value: float, step_size: float) -> float:
    step = max(1.0, float(step_size))
    if value <= 0:
        return 0.0
    return max(0.0, float(int(value // step) * step))


def get_target_bracket_top(year: int, label: str) -> float:
    """
    Returns the top of the requested MFJ ordinary-income bracket for the model year.
    Uses a simple inflation-adjusted anchor from 2025-style thresholds.
    """
    bracket_label = str(label).strip().replace("%", "")
    year_offset = max(0, int(year) - 2025)
    inflation = 1.03 ** year_offset

    tops_2025 = {
        "10": 23850.0,
        "12": 96950.0,
        "22": 206700.0,
        "24": 394600.0,
        "32": 501050.0,
        "35": 751600.0,
    }

    if bracket_label not in tops_2025:
        return float("inf")
    return float(tops_2025[bracket_label] * inflation)



def estimate_future_marginal_rate(year: int, state: dict, params: dict) -> dict:
    """
    Estimate future marginal ordinary-income tax rate based on the first RMD-era year.
    Uses projected traditional balance, projected Social Security, and current earned-income schedule.
    """
    growth = float(params["growth"])
    future_year = max(int(params["household_rmd_start"]), year + 1)

    years_forward = max(0, future_year - year)
    projected_trad = float(state.get("trad", 0.0)) * ((1.0 + growth) ** years_forward)

    owner_ss = float(params["owner_ss_annual"]) if future_year >= int(params["owner_ss_start"]) else 0.0
    spouse_ss = float(params["spouse_ss_annual"]) if future_year >= int(params["spouse_ss_start"]) else 0.0
    total_ss = owner_ss + spouse_ss

    earned_income = 0.0
    if int(params.get("earned_income_start_year", START_YEAR)) <= future_year <= int(params.get("earned_income_end_year", START_YEAR - 1)):
        earned_income = float(params.get("earned_income_annual", 0.0))

    projected_rmd = estimate_household_rmd_for_year(future_year, projected_trad, params)

    tax_info = calculate_federal_tax(
        other_ordinary_income=earned_income + projected_rmd,
        total_ss=total_ss,
        year=future_year,
        realized_ltcg=0.0,
    )

    return {
        "future_year": future_year,
        "projected_trad_balance": projected_trad,
        "projected_future_rmd": projected_rmd,
        "projected_future_total_ss": total_ss,
        "projected_future_taxable_ss": float(tax_info["taxable_ss"]),
        "projected_future_ordinary_income": float(earned_income + projected_rmd + tax_info["taxable_ss"]),
        "estimated_future_marginal_rate": float(tax_info["marginal_rate"]),
        "estimated_future_ordinary_taxable_income": float(tax_info["ordinary_taxable_income"]),
    }


def find_optimal_conversion_for_year(year: int, state: dict, params: dict, max_conversion: float, step_size: float, projection_cache: dict | None = None) -> tuple:
    cap = get_year_conversion_cap(state, params, max_conversion)
    step_size = sanitize_governor_step_size(step_size)
    coverage = get_coverage_status(year, int(params["primary_aca_end_year"]), int(params["spouse_aca_end_year"]))

    if cap <= 0.0:
        zero_path = run_projection_summary_from_state(year, state, params, first_year_conversion=0.0, later_year_conversion=0.0, projection_cache=projection_cache)
        zero_row = dict(zero_path["first_row"])
        diag = pd.DataFrame([{
            "Year": year,
            "Base Conversion": 0.0,
            "Test Conversion": 0.0,
            "Step Amount": 0.0,
            "Current Marginal Incremental Cost Rate": 0.0,
            "Projected Future Avoided Rate": 0.0,
            "Net Benefit Rate": 0.0,
            "Break-Even Reached": True,
            "Decision Mode": "No Conversion Available",
            "Reason": "No available traditional balance to convert",
        }])
        return 0.0, zero_row, diag

    def _future_drag(path: dict, first_row: dict) -> dict:
        return {
            "federal": float(path["total_federal_taxes"]) - float(first_row.get("Federal Tax", 0.0)),
            "state": float(path.get("total_state_taxes", 0.0)) - float(first_row.get("State Tax", 0.0)),
            "aca": float(path["total_aca_cost"]) - float(first_row.get("ACA Cost", 0.0)),
            "irmaa": float(path["total_irmaa_cost"]) - float(first_row.get("IRMAA Cost", 0.0)),
        }

    # ACA years: maximize conversion under ACA limit, but do it efficiently.
    if coverage["aca_lives"] > 0:
        aca_limit = get_aca_magi_limit(year, coverage["aca_lives"])
        tested_rows = []
        baseline_path = run_projection_summary_from_state(
            year,
            state,
            params,
            first_year_conversion=0.0,
            later_year_conversion=0.0,
            projection_cache=projection_cache,
        )
        baseline_row = dict(baseline_path["first_row"])
        baseline_magi = float(baseline_row["MAGI"])
        baseline_total_tax = float(
            baseline_row.get("Federal Tax", 0.0)
            + baseline_row.get("State Tax", 0.0)
            + baseline_row.get("ACA Cost", 0.0)
            + baseline_row.get("IRMAA Cost", 0.0)
        )
        selected_conversion = 0.0
        selected_row = baseline_row

        aca_buffer = max(float(params.get("aca_headroom_buffer", ACA_HEADROOM_BUFFER)), 1.0)
        aca_headroom = max(0.0, float(aca_limit) - baseline_magi - aca_buffer)
        max_test = min(cap, floor_to_step(aca_headroom, step_size))

        eval_cache: dict[float, dict] = {}

        def _evaluate_aca_conversion(current_conversion: float) -> dict:
            current_conversion = float(round(current_conversion, 10))
            cached_eval = eval_cache.get(current_conversion)
            if cached_eval is not None:
                return cached_eval

            path = run_projection_summary_from_state(
                year,
                state,
                params,
                first_year_conversion=current_conversion,
                later_year_conversion=0.0,
                projection_cache=projection_cache,
            )
            row = dict(path["first_row"])
            tax_sources, tax_source_penalty = determine_tax_source_mix_from_row(row)
            roth_tax_used = "roth" in tax_sources
            within_limit = bool(float(row["MAGI"]) <= aca_limit + 0.01)

            test_total_tax = float(
                row["Federal Tax"]
                + row.get("State Tax", 0.0)
                + row["ACA Cost"]
                + row["IRMAA Cost"]
            )
            if current_conversion <= 1e-9:
                delta_total_tax = 0.0
                current_effective = 0.0
                whole_effective_rate = 0.0
            else:
                delta_total_tax = float(test_total_tax - baseline_total_tax)
                current_effective = sanitize_effective_rate(
                    delta_total_tax / float(current_conversion),
                    float(row.get("Current Marginal Tax Rate", 0.0)),
                )
                whole_effective_rate = max(0.0, delta_total_tax / float(current_conversion))

            payload = {
                "conversion": float(current_conversion),
                "path": path,
                "row": row,
                "tax_sources": tax_sources,
                "tax_source_penalty": float(tax_source_penalty),
                "roth_tax_used": bool(roth_tax_used),
                "within_limit": bool(within_limit),
                "test_total_tax": float(test_total_tax),
                "delta_total_tax": float(delta_total_tax),
                "current_effective": float(current_effective),
                "whole_effective_rate": float(whole_effective_rate),
            }
            eval_cache[current_conversion] = payload
            return payload

        if max_test <= 0.0:
            eval_steps = [0.0]
        else:
            max_step_index = int(round(max_test / step_size))
            lo = 0
            hi = max_step_index
            best_ok = 0
            visited_indices = {0, max_step_index}

            while lo <= hi:
                mid = (lo + hi) // 2
                visited_indices.add(mid)
                mid_conversion = float(mid * step_size)
                info = _evaluate_aca_conversion(mid_conversion)
                if info["within_limit"] and not info["roth_tax_used"]:
                    best_ok = mid
                    lo = mid + 1
                else:
                    hi = mid - 1

            eval_steps = sorted(float(idx * step_size) for idx in visited_indices if idx >= 0)
            selected_conversion = float(best_ok * step_size)
            selected_row = _evaluate_aca_conversion(selected_conversion)["row"]

        prev_info = None
        for step_index, current_conversion in enumerate(eval_steps):
            info = _evaluate_aca_conversion(current_conversion)
            row = info["row"]
            current_fed_delta = 0.0
            current_aca_delta = 0.0
            current_irmaa_delta = 0.0
            future_avoided_fed = 0.0
            future_avoided_state = 0.0
            future_avoided_aca = 0.0
            future_avoided_irmaa = 0.0
            future_effective = 0.0
            net_benefit_rate = 0.0

            if prev_info is not None and current_conversion > prev_info["conversion"] + 1e-9:
                delta_conv = float(current_conversion - prev_info["conversion"])
                current_fed_delta = float(row["Federal Tax"]) - float(prev_info["row"]["Federal Tax"])
                current_aca_delta = float(row["ACA Cost"]) - float(prev_info["row"]["ACA Cost"])
                current_irmaa_delta = float(row["IRMAA Cost"]) - float(prev_info["row"]["IRMAA Cost"])

                curr_future = _future_drag(info["path"], row)
                prev_future = _future_drag(prev_info["path"], prev_info["row"])
                future_avoided_fed = prev_future["federal"] - curr_future["federal"]
                future_avoided_state = prev_future.get("state", 0.0) - curr_future.get("state", 0.0)
                future_avoided_aca = prev_future["aca"] - curr_future["aca"]
                future_avoided_irmaa = prev_future["irmaa"] - curr_future["irmaa"]
                future_effective = (
                    future_avoided_fed + future_avoided_state + future_avoided_aca + future_avoided_irmaa
                ) / delta_conv
                net_benefit_rate = future_effective - info["current_effective"]

            tested_rows.append({
                "Year": year,
                "Decision Mode": "ACA Headroom",
                "Step Index": int(step_index),
                "Base Conversion": float(prev_info["conversion"]) if prev_info is not None else 0.0,
                "Test Conversion": float(current_conversion),
                "Step Amount": float(current_conversion - (prev_info["conversion"] if prev_info is not None else 0.0)),
                "Baseline MAGI (0 Conv)": baseline_magi,
                "ACA MAGI Limit": float(aca_limit),
                "MAGI Headroom Before Conversion": float(max(0.0, aca_limit - baseline_magi)),
                "Buffered ACA Headroom": float(aca_headroom),
                "Test MAGI": float(row["MAGI"]),
                "MAGI Remaining To Limit": float(aca_limit - float(row["MAGI"])),
                "Within ACA Limit": bool(info["within_limit"] and not info["roth_tax_used"]),
                "Current Marginal Incremental Cost Rate": float(info["current_effective"]),
                "Projected Future Avoided Rate": float(future_effective),
                "Net Benefit Rate": float(net_benefit_rate),
                "Tax Funding Source": " + ".join(info["tax_sources"]) if info["tax_sources"] else "none",
                "Tax Funding Penalty": float(info["tax_source_penalty"]),
                "Current Marginal Tax Rate": float(row.get("Current Marginal Tax Rate", 0.0)),
                "Estimated Future Marginal Rate": float("nan"),
                "Effective Current Rate (Adjusted)": float(adjusted_current_effective_rate(info["current_effective"], info["tax_source_penalty"])),
                "Roth Used For Tax Payment": bool(info["roth_tax_used"]),
                "Current Year Federal Tax Delta": float(current_fed_delta),
                "Current Year ACA Delta": float(current_aca_delta),
                "Current Year IRMAA Delta": float(current_irmaa_delta),
                "Current Marginal Cost": float(current_fed_delta + current_aca_delta + current_irmaa_delta),
                "Baseline Total Tax": float(baseline_total_tax),
                "Test Total Tax": float(info["test_total_tax"]),
                "Delta Total Tax": float(info["delta_total_tax"]),
                "Whole Conversion Effective Cost Rate": float(0.0 if abs(info["whole_effective_rate"]) > 1.0 else info["whole_effective_rate"]),
                "Future Avoided Federal Tax": float(future_avoided_fed),
                "Future Avoided State Tax": float(future_avoided_state),
                "Future Avoided ACA Cost": float(future_avoided_aca),
                "Future Avoided IRMAA Cost": float(future_avoided_irmaa),
                "Future Avoided Cost": float(future_avoided_fed + future_avoided_state + future_avoided_aca + future_avoided_irmaa),
                "Federal Tax": float(row["Federal Tax"]),
                "ACA Cost": float(row["ACA Cost"]),
                "IRMAA Cost": float(row["IRMAA Cost"]),
                "EOY Trad": float(row["EOY Trad"]),
                "EOY Brokerage": float(row["EOY Brokerage"]),
                "EOY Cash": float(row["EOY Cash"]),
                "Final Net Worth (Zero Later Conv)": float(info["path"]["final_net_worth"]),
            })
            prev_info = info

        diag_df = pd.DataFrame(tested_rows)
        if not diag_df.empty:
            diag_df["Selected Conversion After Test"] = selected_conversion
            diag_df["Selected MAGI"] = float(selected_row["MAGI"])
            diag_df["ACA Solver Note"] = (
                "ACA years use buffered MAGI headroom, cached projections, and binary search "
                "to select the highest tested conversion that stays within the ACA limit."
            )
        return round(selected_conversion, 2), selected_row, diag_df

    # Non-ACA years: use true incremental BETR math, subject to target bracket and tax-funding guardrails.
    target_label = params["post_aca_target_bracket"] if year < int(params["household_rmd_start"]) else params["rmd_era_target_bracket"]
    target_top = get_target_bracket_top(year, target_label)
    baseline_path = run_projection_summary_from_state(year, state, params, first_year_conversion=0.0, later_year_conversion=0.0, projection_cache=projection_cache)
    baseline_row = dict(baseline_path["first_row"])
    baseline_ordinary_taxable = float(baseline_row.get("Ordinary Taxable Income", 0.0))
    target_headroom = max(0.0, float(target_top) - baseline_ordinary_taxable)
    future_rate_info = estimate_future_marginal_rate(year, state, params)
    future_rate = float(future_rate_info["estimated_future_marginal_rate"])

    max_test = min(cap, floor_to_step(target_headroom, step_size))
    target_pressure_conversion = estimate_target_trad_pressure_conversion(year, state, params, step_size, max_test)
    projected_trad_at_rmd_start = project_trad_balance_at_rmd_start(year, state, params, projection_cache=projection_cache)
    target_gap_at_rmd_start = max(0.0, projected_trad_at_rmd_start - float(params.get("target_trad_balance", 0.0)))
    target_lane_active = bool(
        params.get("target_trad_balance_enabled", False)
        and year < int(params.get("household_rmd_start", year))
        and target_pressure_conversion > 0.0
    )
    tested_rows = []
    selected_conversion = 0.0
    selected_row = baseline_row
    selected_net_benefit = float("-inf")
    highest_guardrail_conversion = 0.0
    highest_guardrail_row = baseline_row
    highest_bracket_fill_conversion = 0.0
    highest_bracket_fill_row = baseline_row
    highest_override_conversion = 0.0
    highest_override_row = baseline_row
    prev = None

    step_index = 0
    while True:
        current_conversion = min(max_test, step_index * step_size)
        if current_conversion > max_test + 0.01:
            break

        path = run_projection_summary_from_state(year, state, params, first_year_conversion=current_conversion, later_year_conversion=0.0, projection_cache=projection_cache)
        row = dict(path["first_row"])
        ordinary_taxable = float(row.get("Ordinary Taxable Income", 0.0))
        current_rate = float(row.get("Current Marginal Tax Rate", 0.0))
        within_target = bool(ordinary_taxable <= float(target_top) + 0.01)
        tax_sources, tax_source_penalty = determine_tax_source_mix_from_row(row)
        roth_tax_used = "roth" in tax_sources

        current_effective = 0.0
        future_effective = 0.0
        net_benefit_rate = 0.0
        current_fed_delta = 0.0
        current_aca_delta = 0.0
        current_irmaa_delta = 0.0
        future_avoided_fed = 0.0
        future_avoided_state = 0.0
        future_avoided_aca = 0.0
        future_avoided_irmaa = 0.0
        baseline_total_tax = float(baseline_row.get("Federal Tax", 0.0) + baseline_row.get("State Tax", 0.0) + baseline_row.get("ACA Cost", 0.0) + baseline_row.get("IRMAA Cost", 0.0))
        test_total_tax = float(row["Federal Tax"] + row.get("State Tax", 0.0) + row["ACA Cost"] + row["IRMAA Cost"])
        if current_conversion <= 1e-9:
            delta_total_tax = 0.0
            current_effective = 0.0
            whole_effective_rate = 0.0
        else:
            delta_total_tax = float(test_total_tax - baseline_total_tax)
            current_effective = sanitize_effective_rate(delta_total_tax / float(current_conversion), float(row.get("Current Marginal Tax Rate", 0.0)))
            whole_effective_rate = max(0.0, delta_total_tax / float(current_conversion))
        if prev is not None and current_conversion > prev["conversion"] + 1e-9:
            delta_conv = float(current_conversion - prev["conversion"])
            current_fed_delta = float(row["Federal Tax"]) - float(prev["row"]["Federal Tax"])
            current_aca_delta = float(row["ACA Cost"]) - float(prev["row"]["ACA Cost"])
            current_irmaa_delta = float(row["IRMAA Cost"]) - float(prev["row"]["IRMAA Cost"])

            curr_future = _future_drag(path, row)
            prev_future = _future_drag(prev["path"], prev["row"])
            future_avoided_fed = prev_future["federal"] - curr_future["federal"]
            future_avoided_state = prev_future.get("state", 0.0) - curr_future.get("state", 0.0)
            future_avoided_aca = prev_future["aca"] - curr_future["aca"]
            future_avoided_irmaa = prev_future["irmaa"] - curr_future["irmaa"]
            future_effective = (future_avoided_fed + future_avoided_state + future_avoided_aca + future_avoided_irmaa) / delta_conv
            net_benefit_rate = future_effective - current_effective

        if current_conversion <= 1e-9:
            effective_current_adjusted = 0.0
            future_effective_blended = float(future_rate)
            net_benefit_rate = 0.0
        else:
            effective_current_adjusted = adjusted_current_effective_rate(current_effective, tax_source_penalty)

            # Clean future-rate logic:
            # Use the projected future marginal rate as the avoided-rate anchor instead of
            # lifetime-delta percentages, which can be distorted by multi-year threshold effects.
            future_effective_blended = float(future_rate)
            net_benefit_rate = future_effective_blended - effective_current_adjusted
        # Policy layer: after household RMD start, require a stronger BETR margin before allowing extra conversion.
        post_rmd_hurdle = 0.05 if year >= int(params["household_rmd_start"]) else 0.0
        within_limit = bool(
            within_target
            and (not roth_tax_used)
            and (
                current_conversion == 0
                or net_benefit_rate > post_rmd_hurdle
            )
        )

        tested_rows.append({
            "Year": year,
            "Decision Mode": "BETR Full-Range Search",
            "Step Index": int(step_index),
            "Base Conversion": float(prev["conversion"]) if prev is not None else 0.0,
            "Test Conversion": float(current_conversion),
            "Step Amount": float(current_conversion - (prev["conversion"] if prev is not None else 0.0)),
            "Target Bracket": str(target_label),
            "Target Ordinary Taxable Income": float(target_top),
            "Baseline Ordinary Taxable Income (0 Conv)": baseline_ordinary_taxable,
            "Test Ordinary Taxable Income": ordinary_taxable,
            "Ordinary Income Headroom Before Conversion": float(max(0.0, target_top - baseline_ordinary_taxable)),
            "Ordinary Income Remaining To Target": float(target_top - ordinary_taxable),
            "Within Target Bracket": within_target,
            "Post-RMD Hurdle": float(post_rmd_hurdle),
            "Current Marginal Incremental Cost Rate": float(current_effective),
            "Projected Future Avoided Rate": float(future_effective_blended),
            "Net Benefit Rate": float(net_benefit_rate),
            "Current Marginal Tax Rate": current_rate,
            "Estimated Future Marginal Rate": future_rate,
            "Tax Funding Source": " + ".join(tax_sources) if tax_sources else "none",
            "Tax Funding Penalty": float(tax_source_penalty),
            "Effective Current Rate (Adjusted)": float(effective_current_adjusted),
            "Roth Used For Tax Payment": bool(roth_tax_used),
            "Current Year Federal Tax Delta": float(current_fed_delta),
            "Current Year ACA Delta": float(current_aca_delta),
            "Current Year IRMAA Delta": float(current_irmaa_delta),
            "Current Marginal Cost": float(current_fed_delta + current_aca_delta + current_irmaa_delta),
            "Baseline Total Tax": float(baseline_total_tax),
            "Test Total Tax": float(test_total_tax),
            "Delta Total Tax": float(delta_total_tax),
            "Whole Conversion Effective Cost Rate": float(0.0 if abs(whole_effective_rate) > 1.0 else whole_effective_rate),
            "Future Avoided Federal Tax": float(future_avoided_fed),
            "Future Avoided State Tax": float(future_avoided_state),
            "Future Avoided ACA Cost": float(future_avoided_aca),
            "Future Avoided IRMAA Cost": float(future_avoided_irmaa),
            "Future Avoided Cost": float(future_avoided_fed + future_avoided_state + future_avoided_aca + future_avoided_irmaa),
            "Federal Tax": float(row["Federal Tax"]),
            "ACA Cost": float(row["ACA Cost"]),
            "IRMAA Cost": float(row["IRMAA Cost"]),
            "EOY Trad": float(row["EOY Trad"]),
            "EOY Brokerage": float(row["EOY Brokerage"]),
            "EOY Cash": float(row["EOY Cash"]),
            "Final Net Worth (Zero Later Conv)": float(path["final_net_worth"]),
            "BETR Stop Trigger Hit": bool(current_conversion > 0 and net_benefit_rate <= 0.0),
            "Within Full Guardrails": within_limit,
            "Within Planner Override Cap": bool(
                (not roth_tax_used)
                and float(current_conversion) > 0.0
                and bool(params.get("target_trad_override_enabled", False))
                and float(effective_current_adjusted) <= float(params.get("target_trad_override_max_rate", 0.22)) + 1e-12
            ),
            "Post-RMD Policy Active": bool(year >= int(params["household_rmd_start"])),
            "Target Lane Active": bool(target_lane_active),
            "Required Annual Conversion To Target": float(target_pressure_conversion),
            "Projected Trad At RMD Start (No Extra Conv)": float(projected_trad_at_rmd_start),
            "Target Trad Gap At RMD Start": float(target_gap_at_rmd_start),
        })

        # Winner selection:
        # - highest_guardrail_conversion tracks pure BETR-valid rows
        # - highest_bracket_fill_conversion tracks the highest conversion that still fits
        #   the chosen bracket without using Roth to pay tax
        # - highest_override_conversion tracks rows that stay under the planner override cap,
        #   even if pure BETR already says stop
        base_override_eligible = bool((not roth_tax_used))

        if base_override_eligible and within_target:
            if float(current_conversion) >= float(highest_bracket_fill_conversion) - 1e-12:
                highest_bracket_fill_conversion = float(current_conversion)
                highest_bracket_fill_row = row

        if within_limit:
            if float(current_conversion) >= float(highest_guardrail_conversion) - 1e-12:
                highest_guardrail_conversion = float(current_conversion)
                highest_guardrail_row = row

            if (float(current_conversion) > float(selected_conversion) + 1e-12) or (
                abs(float(current_conversion) - float(selected_conversion)) <= 1e-12
                and float(net_benefit_rate) >= float(selected_net_benefit) - 1e-12
            ):
                selected_conversion = float(current_conversion)
                selected_row = row
                selected_net_benefit = float(net_benefit_rate)

        override_cap = float(params.get("target_trad_override_max_rate", 0.22))
        override_enabled = bool(params.get("target_trad_override_enabled", False))
        if (
            override_enabled
            and base_override_eligible
            and float(current_conversion) > 0.0
            and float(effective_current_adjusted) <= override_cap + 1e-12
        ):
            if float(current_conversion) >= float(highest_override_conversion) - 1e-12:
                highest_override_conversion = float(current_conversion)
                highest_override_row = row

        prev = {"conversion": current_conversion, "path": path, "row": row}
        if current_conversion >= max_test - 0.01:
            break
        step_index += 1

    # Hard target-depletion overlay: if the plan is still projected to miss the target
    # by household RMD start, target mode becomes authoritative in pre-RMD non-ACA years.
    # In that case, the governor should not be allowed to sit at tiny conversions or zero.
    selection_mode = "BETR"
    actual_required_conversion = float(target_pressure_conversion)
    hard_target_floor = 0.0
    if target_lane_active and float(target_gap_at_rmd_start) > 1e-9:
        hard_target_floor = float(target_pressure_conversion)

        # When behind target, use the full bracket runway first. This is the exact behavior
        # the user expects in years like 2036-2040: post-ACA, pre-RMD, MFJ, wide 24% bracket.
        if float(highest_bracket_fill_conversion) > hard_target_floor + 1e-9:
            hard_target_floor = float(highest_bracket_fill_conversion)
            selection_mode = "HARD_TARGET_BRACKET_FILL"
        else:
            selection_mode = "TRAD_TARGET_PRESSURE"

        # If the planner override cap allows even more than the bracket-fill winner, take it.
        if bool(params.get("target_trad_override_enabled", False)) and float(highest_override_conversion) > hard_target_floor + 1e-9:
            hard_target_floor = float(highest_override_conversion)
            selection_mode = "HARD_TARGET_OVERRIDE"

        if float(hard_target_floor) > float(selected_conversion) + 1e-9:
            selected_conversion = float(hard_target_floor)
            selected_row = dict(run_projection_summary_from_state(
                year,
                state,
                params,
                first_year_conversion=selected_conversion,
                later_year_conversion=0.0,
                projection_cache=projection_cache,
            )["first_row"])

    actual_required_conversion = float(hard_target_floor if hard_target_floor > 0 else target_pressure_conversion)
    target_status = "OFF"
    if target_lane_active:
        if float(target_gap_at_rmd_start) <= 1e-9:
            target_status = "ON TRACK"
        elif float(selected_conversion) + 1e-9 >= float(actual_required_conversion):
            target_status = "ON TRACK"
        else:
            target_status = "BEHIND"

    diag_df = pd.DataFrame(tested_rows)
    if not diag_df.empty:
        diag_df["Selected Conversion After Test"] = selected_conversion
        diag_df["Selected Ordinary Taxable Income"] = float(selected_row.get("Ordinary Taxable Income", 0.0))
        diag_df["Target Trad Pressure Conversion"] = float(target_pressure_conversion)
        diag_df["Required Annual Conversion To Target"] = float(actual_required_conversion)
        diag_df["Target Trad Highest Bracket Fill Conversion"] = float(highest_bracket_fill_conversion)
        diag_df["Target Trad Highest Override Conversion"] = float(highest_override_conversion)
        diag_df["Projected Trad At RMD Start (No Extra Conv)"] = float(projected_trad_at_rmd_start)
        diag_df["Target Trad Gap At RMD Start"] = float(target_gap_at_rmd_start)
        diag_df["Target Path Status"] = target_status
        diag_df["Selection Mode Detail"] = selection_mode
        diag_df["Bracket Solver Note"] = "Non-ACA years use BETR diagnostics, but when the target Traditional IRA path is behind schedule before RMD start, the target lane becomes authoritative: fill the bracket runway first, then use the planner override cap if available."
    return round(selected_conversion, 2), selected_row, diag_df



def run_model_break_even_governor(inputs: dict, max_conversion: float, step_size: float) -> dict:
    params = build_common_params(inputs)
    max_conversion = sanitize_governor_max_conversion(max_conversion)
    step_size = sanitize_governor_step_size(step_size)
    projection_cache: dict = {}
    state = {
        "trad": float(inputs["trad"]),
        "roth": float(inputs["roth"]),
        "brokerage": float(inputs["brokerage"]),
        "brokerage_basis": float(inputs.get("brokerage_basis", inputs["brokerage"])),
        "cash": float(inputs["cash"]),
    }

    chosen_rows = []
    decision_frames = []

    for year in range(START_YEAR, END_YEAR + 1):
        state_before = dict(state)
        optimal_conversion, _, diag_df = find_optimal_conversion_for_year(
            year=year,
            state=dict(state_before),
            params=params,
            max_conversion=max_conversion,
            step_size=step_size,
            projection_cache=projection_cache,
        )

        state, chosen_row = simulate_one_year(year, dict(state_before), params, optimal_conversion)
        coverage = get_coverage_status(year, int(params["primary_aca_end_year"]), int(params["spouse_aca_end_year"]))
        aca_limit = get_aca_magi_limit(year, coverage["aca_lives"])
        baseline_state = {
            "trad": chosen_row["SOY Trad"],
            "roth": chosen_row["SOY Roth"],
            "brokerage": chosen_row["SOY Brokerage"],
            "brokerage_basis": chosen_row["SOY Brokerage Basis"],
            "cash": chosen_row["SOY Cash"],
        }
        baseline_row = dict(run_projection_summary_from_state(
            year,
            dict(baseline_state),
            params,
            first_year_conversion=0.0,
            later_year_conversion=0.0,
            projection_cache=projection_cache,
        )["first_row"])
        future_rate_info = estimate_future_marginal_rate(year, dict({
            "trad": chosen_row["SOY Trad"],
            "roth": chosen_row["SOY Roth"],
            "brokerage": chosen_row["SOY Brokerage"],
            "brokerage_basis": chosen_row["SOY Brokerage Basis"],
            "cash": chosen_row["SOY Cash"],
        }), params)
        chosen_row["Estimated Future Marginal Rate"] = float(future_rate_info["estimated_future_marginal_rate"])
        tax_sources, tax_source_penalty = determine_tax_source_mix_from_row(chosen_row)
        chosen_row["Tax Funding Source"] = " + ".join(tax_sources) if tax_sources else "none"
        chosen_row["Tax Funding Penalty"] = float(tax_source_penalty)
        chosen_row["Effective Current Rate (Adjusted)"] = float(chosen_row.get("Current Marginal Tax Rate", 0.0)) + float(tax_source_penalty)
        chosen_row["Projected Future RMD"] = float(future_rate_info["projected_future_rmd"])
        chosen_row["Projected Future Ordinary Income"] = float(future_rate_info["projected_future_ordinary_income"])
        chosen_row["Future Rate Projection Year"] = str(int(future_rate_info["future_year"]))
        chosen_row["BETR Stop Trigger Hit"] = bool(float(chosen_row.get("Current Marginal Tax Rate", 0.0)) >= float(chosen_row["Estimated Future Marginal Rate"]) and coverage["aca_lives"] == 0 and float(chosen_row.get("Chosen Conversion", 0.0)) > 0)
        chosen_row["ACA MAGI Limit"] = float(aca_limit) if coverage["aca_lives"] > 0 else float('inf')
        chosen_row["Baseline MAGI (0 Conv)"] = float(baseline_row["MAGI"])
        chosen_row["ACA Headroom Before Conversion"] = float(max(0.0, aca_limit - baseline_row["MAGI"])) if coverage["aca_lives"] > 0 else 0.0
        chosen_row["MAGI Remaining To ACA Limit"] = float(aca_limit - chosen_row["MAGI"]) if coverage["aca_lives"] > 0 else float('inf')
        target_label = params["post_aca_target_bracket"] if year < int(params["household_rmd_start"]) else params["rmd_era_target_bracket"]
        target_top = get_target_bracket_top(year, target_label)
        baseline_ord = float(baseline_row.get("Ordinary Taxable Income", 0.0))
        chosen_row["Target Bracket"] = "ACA" if coverage["aca_lives"] > 0 else str(target_label)
        chosen_row["Target Ordinary Taxable Income"] = float(target_top) if coverage["aca_lives"] == 0 else 0.0
        chosen_row["Baseline Ordinary Taxable Income (0 Conv)"] = baseline_ord
        chosen_row["Ordinary Income Headroom Before Conversion"] = float(max(0.0, target_top - baseline_ord)) if coverage["aca_lives"] == 0 else 0.0
        chosen_row["Ordinary Income Remaining To Target"] = float(target_top - float(chosen_row.get("Ordinary Taxable Income", 0.0))) if coverage["aca_lives"] == 0 else 0.0
        chosen_row = enrich_year_row_for_display(year, dict(state_before), params, chosen_row, baseline_row=baseline_row, projection_cache=projection_cache)
        # Pull selected BETR metrics from the matching decision row when available
        if diag_df is not None and not diag_df.empty:
            match = diag_df[diag_df["Test Conversion"].round(2) == round(float(optimal_conversion), 2)]
            if not match.empty:
                sel = match.iloc[-1].to_dict()
                for src_key, dst_key in [
                    ("Current Marginal Incremental Cost Rate", "Current Marginal Incremental Cost Rate"),
                    ("Projected Future Avoided Rate", "Projected Future Avoided Rate"),
                    ("Net Benefit Rate", "Net Benefit Rate"),
                    ("Tax Funding Source", "Tax Funding Source"),
                    ("Tax Funding Penalty", "Tax Funding Penalty"),
                    ("Effective Current Rate (Adjusted)", "Effective Current Rate (Adjusted)"),
                    ("Estimated Future Marginal Rate", "Estimated Future Marginal Rate"),
                    ("Baseline Total Tax", "Baseline Total Tax"),
                    ("Test Total Tax", "Test Total Tax"),
                    ("Delta Total Tax", "Delta Total Tax"),
                    ("Post-RMD Hurdle", "Post-RMD Hurdle"),
                    ("Post-RMD Policy Active", "Post-RMD Policy Active"),
                ]:
                    chosen_row[dst_key] = sel.get(src_key, chosen_row.get(dst_key))
                # legacy-to-current fallback aliases
                if chosen_row.get("Current Marginal Incremental Cost Rate") in (None, "", 0):
                    chosen_row["Current Marginal Incremental Cost Rate"] = sel.get("Current Marginal Incremental Cost Rate", chosen_row.get("Current Marginal Incremental Cost Rate", 0))
                if chosen_row.get("Projected Future Avoided Rate") in (None, "", 0):
                    chosen_row["Projected Future Avoided Rate"] = sel.get("Projected Future Avoided Rate", chosen_row.get("Projected Future Avoided Rate", 0))
                if chosen_row.get("Baseline Total Tax") in (None, ""):
                    chosen_row["Baseline Total Tax"] = sel.get("Baseline Total Tax", 0.0)
                if chosen_row.get("Test Total Tax") in (None, ""):
                    chosen_row["Test Total Tax"] = sel.get("Test Total Tax", 0.0)
                if chosen_row.get("Delta Total Tax") in (None, ""):
                    chosen_row["Delta Total Tax"] = sel.get("Delta Total Tax", 0.0)
                if chosen_row.get("Whole Conversion Effective Cost Rate") in (None, ""):
                    chosen_row["Whole Conversion Effective Cost Rate"] = sel.get("Whole Conversion Effective Cost Rate", 0.0)
        # Compute selected-row receipts directly from a true 0-conversion baseline so they always print.
        baseline_path = run_projection_summary_from_state(year, dict(state_before), params, first_year_conversion=0.0, later_year_conversion=0.0, projection_cache=projection_cache)
        baseline_first_row = dict(baseline_path["first_row"])
        baseline_total_tax = float(baseline_first_row.get("Federal Tax", 0.0) + baseline_first_row.get("State Tax", 0.0) + baseline_first_row.get("ACA Cost", 0.0) + baseline_first_row.get("IRMAA Cost", 0.0))
        test_total_tax = float(chosen_row.get("Federal Tax", 0.0) + chosen_row.get("State Tax", 0.0) + chosen_row.get("ACA Cost", 0.0) + chosen_row.get("IRMAA Cost", 0.0))
        if float(optimal_conversion) <= 1e-9:
            delta_total_tax = 0.0
            whole_effective_rate = 0.0
        else:
            delta_total_tax = float(test_total_tax - baseline_total_tax)
            whole_effective_rate = max(0.0, delta_total_tax / float(optimal_conversion))
        chosen_row["Baseline Total Tax"] = baseline_total_tax
        chosen_row["Test Total Tax"] = test_total_tax
        chosen_row["Delta Total Tax"] = delta_total_tax
        chosen_row["Whole Conversion Effective Cost Rate"] = 0.0 if abs(whole_effective_rate) > 1.0 else whole_effective_rate

        if not diag_df.empty:
            diag_df["Applied Conversion"] = float(optimal_conversion)
            try:
                selected_diag = diag_df.loc[(diag_df["Test Conversion"].astype(float) - float(optimal_conversion)).abs() < 0.01].iloc[-1].to_dict()
                for k in [
                    "Current Marginal Incremental Cost Rate",
                    "Projected Future Avoided Rate",
                    "Net Benefit Rate",
                    "Tax Funding Source",
                    "Tax Funding Penalty",
                    "Effective Current Rate (Adjusted)",
                    "BETR Stop Trigger Hit",
                    "Baseline Total Tax",
                    "Test Total Tax",
                    "Delta Total Tax",
                    "Whole Conversion Effective Cost Rate",
                    "Target Trad Pressure Conversion",
                    "Target Trad Highest Bracket Fill Conversion",
                    "Target Trad Highest Override Conversion",
                    "Within Planner Override Cap",
                    "Selection Mode Detail",
                    "Required Annual Conversion To Target",
                    "Projected Trad At RMD Start (No Extra Conv)",
                    "Target Trad Gap At RMD Start",
                    "Target Lane Active",
                ]:
                    if k in selected_diag:
                        chosen_row[k] = selected_diag[k]
            except Exception:
                pass
            decision_frames.append(diag_df)

        chosen_row["Override Active"] = bool(chosen_row.get("Selection Mode Detail", "") == "TRAD_TARGET_OVERRIDE")
        chosen_row["Override Cost"] = float(chosen_row.get("Delta Total Tax", 0.0)) if chosen_row["Override Active"] else 0.0
        chosen_row["Future Benefit"] = (
            max(
                0.0,
                float(chosen_row.get("Chosen Conversion", 0.0))
                * max(
                    0.0,
                    float(chosen_row.get("Projected Future Avoided Rate", 0.0))
                    - float(chosen_row.get("Effective Current Rate (Adjusted)", 0.0))
                )
            )
            if chosen_row["Override Active"] else 0.0
        )
        chosen_row["Net Lifetime Value"] = float(chosen_row["Future Benefit"] - chosen_row["Override Cost"]) if chosen_row["Override Active"] else 0.0

        chosen_rows.append(chosen_row)

    chosen_df = pd.DataFrame(chosen_rows)
    if not chosen_df.empty:
        chosen_df = chosen_df.loc[:, ~chosen_df.columns.duplicated()].copy()
        chosen_df = chosen_df.sort_values("Year").reset_index(drop=True)
        expected_years = list(range(START_YEAR, START_YEAR + len(chosen_df)))
        chosen_df["Year"] = expected_years
        chosen_df = chosen_df.drop_duplicates(subset=["Year"], keep="last").reset_index(drop=True)
        chosen_df = organize_yearly_columns(chosen_df)

    decision_df = pd.concat(decision_frames, ignore_index=True) if decision_frames else pd.DataFrame()
    if not decision_df.empty:
        decision_df = decision_df.loc[:, ~decision_df.columns.duplicated()].copy()
        decision_df = organize_decision_columns(decision_df)

    result = summarize_run(chosen_df, params)
    result["decision_df"] = decision_df
    return result


def run_ss_optimizer(
    inputs: dict,
    max_conversion: float,
    step_size: float,
    trad_balance_penalty_lambda: float = 1.00,
    integrity_mode: bool = False,
    validation_tolerance: float = 0.01,
    start_index: int = 0,
    existing_results: list | None = None,
    profile_name: str = "Balanced",
) -> dict:
    combos = get_ss_scan_candidate_combos("full")
    total_combos = len(combos)
    base_snapshot = prepare_ss_scan_base_inputs(inputs)
    results = list(existing_results or [])
    st.session_state["ss_optimizer_running"] = True
    st.session_state["ss_optimizer_interrupted"] = False
    st.session_state["ss_optimizer_error"] = None
    st.session_state["ss_optimizer_partial_results"] = results

    def _progress_callback(combo_index: int, owner_age: int, spouse_age: int, current_results: list[dict]) -> None:
        st.session_state["ss_optimizer_partial_results"] = current_results
        st.session_state["ss_optimizer_progress_index"] = combo_index + 1
        st.session_state["ss_optimizer_last_completed"] = (owner_age, spouse_age)

    def _error_callback(combo_index: int, owner_age: int, spouse_age: int, exc: Exception, current_results: list[dict]):
        st.session_state["ss_optimizer_progress_index"] = combo_index
        st.session_state["ss_optimizer_partial_results"] = current_results
        st.session_state["ss_optimizer_last_completed"] = combos[combo_index - 1] if combo_index > 0 else None
        st.session_state["ss_optimizer_interrupted"] = True
        st.session_state["ss_optimizer_error"] = f"SS optimizer failed for owner age {owner_age} / spouse age {spouse_age}: {exc}"
        interrupted_df = pd.DataFrame(current_results) if current_results else pd.DataFrame()
        return tag_result_payload({
            "all_results_df": interrupted_df.copy(),
            "all_results_export_df": interrupted_df.copy(),
            "top_10_df": interrupted_df.head(10).copy(),
            "top_10_export_df": interrupted_df.head(10).copy(),
            "comparison_df": pd.DataFrame(),
            "comparison_display_df": pd.DataFrame(),
            "best_result": None,
            "best_validation": None,
            "best_rerun_summary": None,
            "trad_balance_penalty_lambda": float(trad_balance_penalty_lambda),
            "profile_name": None,
            "optimizer_is_profile_neutral": True,
            "interrupted_partial_df": interrupted_df.copy(),
            "completed": False,
            "progress_index": combo_index,
            "total_combos": total_combos,
            "error_message": st.session_state["ss_optimizer_error"],
        }, engine="ss_optimizer")

    evaluated, errors = evaluate_ss_scan_candidates(
        base_snapshot,
        combos,
        max_conversion,
        step_size,
        trad_balance_penalty_lambda=trad_balance_penalty_lambda,
        progress_prefix="Running Social Security optimizer",
        initial_results=results,
        start_index=start_index,
        progress_callback=_progress_callback,
        error_callback=_error_callback,
    )
    if isinstance(evaluated, dict):
        return evaluated
    results = evaluated

    scoring_preferences = extract_scoring_preferences(inputs)
    shared_outputs = score_rank_ss_scan_rows(
        results,
        inputs=base_snapshot,
        selected_profile_name=profile_name,
        preferences=scoring_preferences,
        trad_balance_penalty_lambda=trad_balance_penalty_lambda,
        shortlist_top_n=5,
    )
    scoring_context = shared_outputs["scoring_context"]
    results_df = shared_outputs["ranked_df"]
    profile_shortlists = shared_outputs["profile_shortlists"]

    top_10_df = results_df.head(10).copy()
    top_3 = results_df.head(3).copy()

    compare_metrics = [
        ("SS Ages", lambda r: f"{int(r['Owner SS Age'])}/{int(r['Spouse SS Age'])}"),
        ("Final Net Worth", lambda r: format_dollars(r["Final Net Worth"])),
        ("Ending Traditional IRA Balance", lambda r: format_dollars(r["Ending Traditional IRA Balance"])),
        ("Total Government Drag", lambda r: format_dollars(r["Total Government Drag"])),
        ("Total Conversions", lambda r: format_dollars(r["Total Conversions"])),
        ("Total Federal Tax", lambda r: format_dollars(r["Total Federal Tax"])),
        ("Total State Tax", lambda r: format_dollars(r["Total State Tax"])),
        ("Total ACA Cost", lambda r: format_dollars(r["Total ACA Cost"])),
        ("Total IRMAA Cost", lambda r: format_dollars(r["Total IRMAA Cost"])),
        ("Max MAGI", lambda r: format_dollars(r["Max MAGI"])),
        ("ACA Hit Years", lambda r: int(r["ACA Hit Years"])),
        ("IRMAA Hit Years", lambda r: int(r["IRMAA Hit Years"])),
        ("First IRMAA Year", lambda r: "None" if pd.isna(r["First IRMAA Year"]) else int(r["First IRMAA Year"])),
        ("Traditional IRA Penalty Applied", lambda r: format_dollars(r["Traditional IRA Penalty Applied"])),
        ("Score", lambda r: format_dollars(r["Score"])),
    ]

    compare_rows = []
    for metric_name, getter in compare_metrics:
        row = {"Metric": metric_name}
        for idx in range(3):
            col_name = f"#{idx + 1}"
            row[col_name] = getter(top_3.iloc[idx]) if idx < len(top_3) else ""
        compare_rows.append(row)
    comparison_df = pd.DataFrame(compare_rows)

    best_result = results_df.iloc[0].to_dict() if not results_df.empty else None
    best_validation = None
    best_rerun_summary = None
    if integrity_mode and best_result is not None:
        best_inputs = dict(inputs)
        best_inputs["owner_claim_age"] = int(best_result["Owner SS Age"])
        best_inputs["spouse_claim_age"] = int(best_result["Spouse SS Age"])
        best_rerun = run_model_break_even_governor(best_inputs, max_conversion, step_size)
        best_validation = make_consistency_payload(
            {
                "final_net_worth": float(best_result["Final Net Worth"]),
                "ending_trad_balance": float(best_result["Ending Traditional IRA Balance"]),
                "total_conversions": float(best_result["Total Conversions"]),
                "total_federal_taxes": float(best_result["Total Federal Tax"]),
                "total_state_taxes": float(best_result["Total State Tax"]),
                "total_aca_cost": float(best_result["Total ACA Cost"]),
                "total_irmaa_cost": float(best_result["Total IRMAA Cost"]),
                "total_government_drag": float(best_result["Total Government Drag"]),
                "total_shortfall": 0.0,
                "max_magi": float(best_result["Max MAGI"]),
            },
            best_rerun,
            tol=validation_tolerance,
        )
        best_rerun_summary = {k: best_rerun.get(k) for k in CONSISTENCY_KEYS}

    result_payload = tag_result_payload({
        "all_results_df": results_df.copy(),
        "all_results_export_df": results_df.copy(),
        "top_10_df": top_10_df.copy(),
        "top_10_export_df": top_10_df.copy(),
        "comparison_df": comparison_df.copy(),
        "comparison_display_df": comparison_df.copy(),
        "best_result": best_result,
        "best_validation": best_validation,
        "best_rerun_summary": best_rerun_summary,
        "trad_balance_penalty_lambda": float(trad_balance_penalty_lambda),
        "profile_name": profile_name,
        "optimizer_is_profile_neutral": False,
        "profile_shortlists": profile_shortlists,
        "scoring_context": scoring_context,
        "scoring_preferences_snapshot": copy.deepcopy(scoring_preferences),
        "planning_profile_snapshot": str(profile_name),
        "completed": True,
        "progress_index": total_combos,
        "total_combos": total_combos,
        "error_message": None,
    }, engine="ss_optimizer")
    mark_result_state("ss_optimizer", base_snapshot)
    st.session_state["ss_optimizer_running"] = False
    st.session_state["ss_optimizer_interrupted"] = False
    st.session_state["ss_optimizer_error"] = None
    st.session_state["ss_optimizer_progress_index"] = total_combos
    st.session_state["ss_optimizer_partial_results"] = results
    st.session_state["ss_optimizer_last_completed"] = combos[-1] if combos else None
    return result_payload

def render_snapshot_viewer_page() -> None:
    ensure_default_state()
    st.title("Snapshot Viewer")
    render_top_nav("snapshot")
    notice = st.session_state.pop("snapshot_open_notice", None)
    if notice:
        st.success(str(notice))
    payload = st.session_state.get("snapshot_viewer_payload")
    if not payload:
        st.info("No snapshot is currently open. Use Open Snapshot above to view a saved recommendation snapshot.")
        return
    render_snapshot_summary_card(payload, heading="Opened Snapshot")

def main() -> None:
    apply_app_state_version_guard()
    ensure_default_state()
    preserve_session_state_across_pages()
    current_page = get_app_page()
    if current_page == "home":
        render_home_page()
    elif current_page == "annual":
        render_annual_page()
    elif current_page == "snapshot":
        render_snapshot_viewer_page()
    else:
        render_conversion_page()


if __name__ == "__main__":
    main()

def get_shared_household_inputs_from_state() -> dict:
    """Read the same household/planning inputs from session state without rendering the full UI."""
    return {
        "trad": float(st.session_state.get("trad", DEFAULT_APP_STATE["trad"])),
        "roth": float(st.session_state.get("roth", DEFAULT_APP_STATE["roth"])),
        "brokerage": float(st.session_state.get("brokerage", DEFAULT_APP_STATE["brokerage"])),
        "brokerage_basis": min(
            float(st.session_state.get("brokerage_basis", DEFAULT_APP_STATE["brokerage_basis"])),
            float(st.session_state.get("brokerage", DEFAULT_APP_STATE["brokerage"])),
        ),
        "cash": float(st.session_state.get("cash", DEFAULT_APP_STATE["cash"])),
        "growth": float(st.session_state.get("growth_pct", DEFAULT_APP_STATE["growth_pct"])) / 100.0,
        "annual_spending": float(st.session_state.get("annual_spending", DEFAULT_APP_STATE["annual_spending"])),
        "spending_inflation_rate": float(st.session_state.get("spending_inflation_rate_pct", DEFAULT_APP_STATE["spending_inflation_rate_pct"])) / 100.0,
        "retirement_smile_enabled": bool(st.session_state.get("retirement_smile_enabled", DEFAULT_APP_STATE["retirement_smile_enabled"])),
        "go_go_end_age": int(st.session_state.get("go_go_end_age", DEFAULT_APP_STATE["go_go_end_age"])),
        "slow_go_end_age": int(st.session_state.get("slow_go_end_age", DEFAULT_APP_STATE["slow_go_end_age"])),
        "go_go_multiplier": float(st.session_state.get("go_go_multiplier", DEFAULT_APP_STATE["go_go_multiplier"])),
        "slow_go_multiplier": float(st.session_state.get("slow_go_multiplier", DEFAULT_APP_STATE["slow_go_multiplier"])),
        "no_go_multiplier": float(st.session_state.get("no_go_multiplier", DEFAULT_APP_STATE["no_go_multiplier"])),
        "annual_conversion": float(st.session_state.get("annual_conversion", DEFAULT_APP_STATE["annual_conversion"])),
        "conversion_tax_funding_policy": st.session_state.get("conversion_tax_funding_policy", DEFAULT_APP_STATE["conversion_tax_funding_policy"]),
        "owner_current_age": int(st.session_state.get("owner_current_age", DEFAULT_APP_STATE["owner_current_age"])),
        "spouse_current_age": int(st.session_state.get("spouse_current_age", DEFAULT_APP_STATE["spouse_current_age"])),
        "owner_claim_age": int(st.session_state.get("owner_claim_age", DEFAULT_APP_STATE["owner_claim_age"])),
        "spouse_claim_age": int(st.session_state.get("spouse_claim_age", DEFAULT_APP_STATE["spouse_claim_age"])),
        "owner_ss_base": float(st.session_state.get("owner_ss_base", DEFAULT_APP_STATE["owner_ss_base"])),
        "spouse_ss_base": float(st.session_state.get("spouse_ss_base", DEFAULT_APP_STATE["spouse_ss_base"])),
        "earned_income_annual": float(st.session_state.get("earned_income_annual", DEFAULT_APP_STATE["earned_income_annual"])),
        "earned_income_start_year": int(st.session_state.get("earned_income_start_year", DEFAULT_APP_STATE["earned_income_start_year"])),
        "earned_income_end_year": int(st.session_state.get("earned_income_end_year", DEFAULT_APP_STATE["earned_income_end_year"])),
        "primary_aca_end_year": int(st.session_state.get("primary_aca_end_year", DEFAULT_APP_STATE["primary_aca_end_year"])),
        "spouse_aca_end_year": int(st.session_state.get("spouse_aca_end_year", DEFAULT_APP_STATE["spouse_aca_end_year"])),
        "preference_maximize_social_security": bool(st.session_state.get("preference_maximize_social_security", DEFAULT_APP_STATE["preference_maximize_social_security"])),
        "preference_minimize_trad_ira_for_heirs": bool(st.session_state.get("preference_minimize_trad_ira_for_heirs", DEFAULT_APP_STATE["preference_minimize_trad_ira_for_heirs"])),
        "preference_income_stability_focus": bool(st.session_state.get("preference_income_stability_focus", DEFAULT_APP_STATE["preference_income_stability_focus"])),
        "state_tax_rate": float(st.session_state.get("state_tax_rate", DEFAULT_APP_STATE["state_tax_rate"])),
        "planning_profile": st.session_state.get("planning_profile", DEFAULT_APP_STATE["planning_profile"]),
        "post_aca_target_bracket": st.session_state.get("post_aca_target_bracket", DEFAULT_APP_STATE["post_aca_target_bracket"]),
        "rmd_era_target_bracket": st.session_state.get("rmd_era_target_bracket", DEFAULT_APP_STATE["rmd_era_target_bracket"]),
        "target_trad_balance_enabled": bool(st.session_state.get("target_trad_balance_enabled", DEFAULT_APP_STATE["target_trad_balance_enabled"])),
        "target_trad_balance": float(st.session_state.get("target_trad_balance", DEFAULT_APP_STATE["target_trad_balance"])),
        "target_trad_override_enabled": bool(st.session_state.get("target_trad_override_enabled", DEFAULT_APP_STATE["target_trad_override_enabled"])),
        "target_trad_override_max_rate": float(st.session_state.get("target_trad_override_max_rate", DEFAULT_APP_STATE["target_trad_override_max_rate"])),
    }

