# version: hard-target-depletion-v22
# version: override-valuation-columns
# version: target-trad-override-v3-relaxed-cap
# version: target-trad-override-handoff-fix
# version: target-trad-balance-override-cap
# version: target-trad-balance-goal
# version: nc-state-tax-clean-base-v2
# version: nc-state-tax-clean-base
import copy
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

ACA_CLIFF_MFJ = 85000.0
ACA_HEADROOM_BUFFER = 1.0

GOVERNOR_MIN_STEP_SIZE = 1000.0
APP_VERSION = "v33-ss-preference-checkbox"

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
    "trad": 0.0,
    "trad_balance_penalty_lambda": 1.00,
    "validation_tolerance": 0.01,
}

SCENARIO_STATE_KEYS = [k for k in DEFAULT_APP_STATE.keys() if k != "app_page"]
PAGE_STATE_KEY_PREFIXES = {
    "annual": ["annual_calc_"],
    "conversion": [
        "annual_", "brokerage", "cash", "conversion_tax_funding_policy", "earned_income_annual",
        "earned_income_end_year", "earned_income_start_year", "go_go_", "growth_pct", "max_conversion",
        "no_go_", "optimizer_", "owner_", "post_aca_", "primary_aca_end_year", "retirement_smile_",
        "rmd_era_", "roth", "run_ss_optimizer_toggle", "slow_go_", "spending_inflation_rate_pct",
        "spouse_", "state_tax_rate", "step_size", "integrity_mode", "strict_repeatability_check", "target_trad_",
        "trad", "validation_tolerance", "preference_"
    ],
}


def format_dollars(value: float) -> str:
    try:
        return f"${float(value):,.0f}"
    except Exception:
        return str(value)


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
        "Current Marginal Tax Rate": "Current Marginal Rate (%)",
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
        "weights": {"nw": 0.02, "legacy": 0.74, "trad": 0.18, "stability": 0.04, "risk": 0.02, "drag": 0.06, "trad_share": 0.22},
        "description": "You are prioritizing what heirs are likely to keep after taxes, not just raw estate size.",
        "bullets": [
            "favor more tax-efficient assets at death",
            "penalize large remaining Traditional IRA balances",
            "accept some reduction in projected net worth if legacy quality improves",
        ],
        "tradeoff": "This approach may reduce maximum projected wealth somewhat, but it can improve after-tax inheritance value.",
    },
    "Spend With Confidence": {
        "weights": {"nw": 0.10, "legacy": 0.08, "trad": 0.14, "stability": 0.34, "risk": 0.20, "drag": 0.06, "trad_share": 0.06},
        "description": "You are prioritizing confidence, flexibility, and the ability to enjoy retirement spending safely.",
        "bullets": [
            "place more value on reliable income and stability",
            "reduce emphasis on maximizing wealth at death",
            "favor strategies that support spending without excessive fear of future shortfall",
        ],
        "tradeoff": "This approach may leave less money at death than a growth-focused strategy, but it is designed to support a more confident retirement lifestyle.",
    },
}

QUICK_STRATEGY_COMBOS = [(62, 62), (67, 67), (70, 70), (70, 67), (67, 70)]

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
        stale_reason = build_optimizer_stale_reason(last_result, planning_profile, current_preferences)
        msg = "Your ranking lens changed since the last optimizer scoring snapshot. Use 'Re-rank Existing 81 Results' to refresh the Top 5 profile shortlists without rerunning the 81-combination engine."
        if stale_reason:
            msg += f" {stale_reason}"
        st.warning(msg)
    else:
        st.success("Scenario facts and ranking preferences match the last 81-combination result. You can review the current rankings without rerunning anything.")

    cols = st.columns(3)
    cols[0].metric("Scenario facts", "Changed" if facts_changed else "Up to date")
    cols[1].metric("Ranking lens", "Changed" if ranking_changed else "Up to date")
    cols[2].metric("Last scoring profile", str(prior_profile or planning_profile))




def get_conversion_workflow_stage() -> int:
    ensure_default_state()
    return int(st.session_state.get("conversion_workflow_stage", 1))


def set_conversion_workflow_stage(stage: int) -> None:
    st.session_state["conversion_workflow_stage"] = max(1, min(6, int(stage)))


def household_inputs_complete(inputs: dict) -> bool:
    try:
        total_assets = float(inputs.get("trad", 0.0)) + float(inputs.get("roth", 0.0)) + float(inputs.get("brokerage", 0.0)) + float(inputs.get("cash", 0.0))
        return int(inputs.get("owner_current_age", 0)) > 0 and int(inputs.get("spouse_current_age", 0)) > 0 and total_assets > 0 and float(inputs.get("owner_ss_base", 0.0)) >= 0.0 and float(inputs.get("spouse_ss_base", 0.0)) >= 0.0
    except Exception:
        return False


def governor_inputs_complete(max_conversion: float, step_size: float) -> bool:
    try:
        return float(max_conversion) > 0.0 and float(step_size) >= 1000.0
    except Exception:
        return False


def render_conversion_workflow_nav(active_stage: int, unlocked_stage: int) -> None:
    labels = [
        "1. Scenario",
        "2. Household",
        "3. Governor",
        "4. Preferences",
        "5. Optimizer",
        "6. Results",
    ]
    cols = st.columns(len(labels))
    for idx, label in enumerate(labels, start=1):
        with cols[idx-1]:
            st.button(label, key=f"workflow_nav_{idx}", use_container_width=True, disabled=idx > unlocked_stage or idx == active_stage, on_click=set_conversion_workflow_stage, args=(idx,))
    st.caption("Use Next / Back to move through the workflow. You can jump to any unlocked step above. Results stay visible even when a rerun is required.")


def render_next_back(stage: int, unlocked_stage: int) -> None:
    c1, c2, c3 = st.columns([1,1,4])
    with c1:
        st.button("Back", key=f"back_stage_{stage}", use_container_width=True, disabled=stage <= 1, on_click=set_conversion_workflow_stage, args=(stage - 1,))
    with c2:
        st.button("Next", key=f"next_stage_{stage}", use_container_width=True, disabled=stage >= 6 or stage >= unlocked_stage, on_click=set_conversion_workflow_stage, args=(min(6, max(stage + 1, unlocked_stage)),))


def build_optimizer_stale_reason(last_result: dict | None, current_profile: str, current_preferences: dict) -> str:
    if not last_result:
        return ""
    prior_profile = last_result.get("planning_profile_snapshot")
    prior_preferences = last_result.get("scoring_preferences_snapshot", {})
    if scoring_preferences_match(current_profile, current_preferences, prior_profile, prior_preferences):
        return ""
    return (
        f"Based on: {prior_profile or current_profile} + {describe_active_scoring_preferences(prior_preferences)}. "
        f"Current: {current_profile} + {describe_active_scoring_preferences(current_preferences)}."
    )

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


def build_profile_shortlists_from_optimizer_rows(results_rows: list[dict], top_n: int = 5, preferences: dict | None = None) -> dict[str, pd.DataFrame]:
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
        ranked = score_strategy_metrics(metric_rows, profile_name, preferences=preferences)
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


def score_strategy_metrics(metrics_list: list[dict], profile_name: str, preferences: dict | None = None) -> list[dict]:
    weights = get_profile_summary(profile_name)["weights"]
    preferences = preferences or {}

    nw_norm = normalize_series([m["final_net_worth"] for m in metrics_list])
    base_legacy_norm = normalize_series([m["after_tax_legacy"] for m in metrics_list])
    effective_legacy_norm = normalize_series([float(m.get("effective_legacy_value", m["after_tax_legacy"])) for m in metrics_list])
    heir_tax_drag_norm = normalize_series([float(m.get("heir_tax_drag", 0.0)) for m in metrics_list])
    trad_norm = normalize_series([m["ending_traditional_ira_balance"] for m in metrics_list])
    stability_norm = normalize_series([m["stability_value"] for m in metrics_list])
    ss_income_norm = normalize_series([m["final_household_ss_income"] for m in metrics_list])
    survivor_ss_norm = normalize_series([m["survivor_ss_income"] for m in metrics_list])
    ss_present_value_norm = normalize_series([float(m.get("social_security_present_value", estimate_social_security_present_value(m["final_household_ss_income"], m["survivor_ss_income"]))) for m in metrics_list])
    risk_norm = normalize_series([m["risk_value"] for m in metrics_list])
    drag_norm = normalize_series([float(m.get("Total Government Drag", 0.0)) for m in metrics_list])
    trad_share_values = []
    for m in metrics_list:
        end_total = max(
            1.0,
            float(m.get("ending_traditional_ira_balance", 0.0))
            + float(m.get("ending_roth_balance", 0.0))
            + float(m.get("ending_brokerage_balance", 0.0))
            + float(m.get("ending_cash_balance", 0.0)),
        )
        trad_share_values.append(float(m.get("ending_traditional_ira_balance", 0.0)) / end_total)
    trad_share_norm = normalize_series(trad_share_values)

    scored = []
    for i, metrics in enumerate(metrics_list):
        nw_adjusted = nw_norm[i] ** 0.72
        if profile_name == "Legacy Focused":
            legacy_signal = 0.80 * effective_legacy_norm[i] + 0.20 * base_legacy_norm[i]
            legacy_adjusted = legacy_signal ** 1.30
            trad_penalty = trad_norm[i] ** 1.60
            drag_penalty = drag_norm[i] ** 1.05
            trad_share_penalty = trad_share_norm[i] ** 2.10
            heir_tax_penalty = heir_tax_drag_norm[i] ** 1.45
            stability_adjusted = 0.55 * (stability_norm[i] ** 1.20) + 0.30 * (ss_income_norm[i] ** 1.20) + 0.15 * (survivor_ss_norm[i] ** 1.10)
            risk_penalty = risk_norm[i] ** 1.05
            positive_score = (0.10 * weights["nw"] * nw_adjusted) + (weights["legacy"] * legacy_adjusted) + (weights["stability"] * stability_adjusted)
            negative_score = (weights["trad"] * trad_penalty) + (weights.get("trad_share", 0.0) * trad_share_penalty) + (0.60 * weights.get("drag", 0.0) * drag_penalty) + (0.90 * weights["trad"] * heir_tax_penalty) + (weights["risk"] * risk_penalty)
        else:
            legacy_adjusted = base_legacy_norm[i] ** 1.05
            trad_penalty = trad_norm[i] ** 1.85
            drag_penalty = drag_norm[i] ** 1.20
            trad_share_penalty = trad_share_norm[i] ** 1.65
            heir_tax_penalty = 0.0
            stability_adjusted = 0.50 * (stability_norm[i] ** 1.35) + 0.35 * (ss_income_norm[i] ** 1.35) + 0.15 * (survivor_ss_norm[i] ** 1.20)
            risk_penalty = risk_norm[i] ** 1.10
            positive_score = (weights["nw"] * nw_adjusted) + (weights["legacy"] * legacy_adjusted) + (weights["stability"] * stability_adjusted)
            negative_score = (weights["trad"] * trad_penalty) + (weights["risk"] * risk_penalty) + (weights.get("drag", 0.0) * drag_penalty) + (weights.get("trad_share", 0.0) * trad_share_penalty)

        preference_bonus = 0.0
        preference_penalty = 0.0
        if preferences.get("maximize_social_security"):
            ss_bonus = 0.14 * (ss_present_value_norm[i] ** 1.15)
            if profile_name in ("Spend With Confidence", "Tax-Efficient Stability"):
                ss_bonus *= 1.15
            preference_bonus += ss_bonus
        if preferences.get("income_stability_focus"):
            stability_bonus = 0.10 * ((0.65 * stability_norm[i]) + (0.35 * ss_income_norm[i]))
            preference_bonus += stability_bonus
        if preferences.get("minimize_trad_ira_for_heirs"):
            heir_structure_penalty = 0.12 * (trad_share_norm[i] ** 1.80) + 0.12 * (heir_tax_drag_norm[i] ** 1.20)
            preference_penalty += heir_structure_penalty

        positive_score += preference_bonus
        negative_score += preference_penalty
        score = positive_score - negative_score

        scored.append({
            **metrics,
            "score": float(score),
            "score_100": float(score * 100.0),
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
            "ss_value_component": float((0.14 * (ss_present_value_norm[i] ** 1.15) * (1.15 if (preferences.get("maximize_social_security") and profile_name in ("Spend With Confidence", "Tax-Efficient Stability")) else 1.0)) if preferences.get("maximize_social_security") else 0.0),
            "preference_bonus_component": float(preference_bonus),
            "preference_penalty_component": float(preference_penalty),
            "social_security_present_value": float(metrics.get("social_security_present_value", estimate_social_security_present_value(metrics["final_household_ss_income"], metrics["survivor_ss_income"]))),
            "positive_score": float(positive_score),
            "negative_score": float(negative_score),
            "ending_traditional_ira_share": float(trad_share_values[i]),
        })
    return sorted(scored, key=lambda x: x["score"], reverse=True)


def generate_advisor_interpretation(profile_name: str, ranked_rows: list[dict]) -> str:
    if not ranked_rows:
        return "No recommendation is available yet."
    winner = ranked_rows[0]
    baseline = next((r for r in ranked_rows if r["Strategy"] == "62/62"), ranked_rows[0])
    nw_delta = float(baseline["Final Net Worth"] - winner["Final Net Worth"])
    trad_delta = float(baseline["Ending Traditional IRA Balance"] - winner["Ending Traditional IRA Balance"])
    legacy_delta = float(winner["After-Tax Legacy"] - baseline["After-Tax Legacy"])
    pieces = [
        f"Based on your selected priorities ({profile_name}), the model recommends {winner['Strategy']} as the strongest overall quick strategy.",
    ]
    if winner["Strategy"] != baseline["Strategy"]:
        pieces.append(
            f"Compared with 62/62, the recommended strategy changes ending Traditional IRA by {format_dollars(trad_delta)} and changes after-tax legacy value by {format_dollars(legacy_delta)}."
        )
        if nw_delta > 0:
            pieces.append(
                f"It also changes projected final net worth by {format_dollars(-nw_delta)} versus 62/62. This is not a strictly better outcome. It is a tradeoff between maximizing total wealth and improving tax efficiency, guaranteed income later in life, or both."
            )
        else:
            pieces.append(
                "It also matches or improves projected final net worth versus 62/62 while better aligning with your stated planning priorities."
            )
    else:
        pieces.append(
            "In this quick comparison, the same strategy that wins on net worth also best fits your selected planning profile."
        )
    pieces.append(
        "Use this as a fast recommendation layer. If you want a tighter check, test a few nearby claim-age combinations around the winner before deciding whether a full 81-strategy run is worth it."
    )
    return " ".join(pieces)


def is_close_quick_result(ranked_rows: list[dict], tolerance_pct: float = 0.02) -> bool:
    if len(ranked_rows) < 2:
        return False
    top_score = float(ranked_rows[0].get("score", 0.0))
    second_score = float(ranked_rows[1].get("score", 0.0))
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
    step_size = sanitize_governor_step_size(step_size)
    preferences = extract_scoring_preferences(inputs)
    base_inputs, preset = build_profile_adjusted_inputs(profile_name, inputs)
    metric_rows = []
    errors = []
    for owner_age, spouse_age in QUICK_STRATEGY_COMBOS:
        try:
            scenario_inputs = copy.deepcopy(base_inputs)
            scenario_inputs["owner_claim_age"] = int(owner_age)
            scenario_inputs["spouse_claim_age"] = int(spouse_age)
            run_result = run_model_break_even_governor(scenario_inputs, sanitize_governor_max_conversion(max_conversion), step_size)
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
                "Effective Legacy Value": float(metrics.get("effective_legacy_value", metrics["after_tax_legacy"])),
                "Heir Tax Drag": float(metrics.get("heir_tax_drag", 0.0)),
                "Ending Traditional IRA Balance": float(metrics["ending_traditional_ira_balance"]),
                "Roth @ End": float(metrics["ending_roth_balance"]),
                "Brokerage @ End": float(metrics["ending_brokerage_balance"]),
                "Stability Value": float(metrics["stability_value"]),
                "Risk Value": float(metrics["risk_value"]),
                "Final Household SS Income": float(metrics["final_household_ss_income"]),
                "Survivor SS Income": float(metrics["survivor_ss_income"]),
            })
        except Exception as exc:
            errors.append(f"{owner_age}/{spouse_age}: {exc}")
    if not metric_rows:
        raise RuntimeError("Quick strategy recommendation could not produce any valid strategy results.")
    ranked = score_strategy_metrics(metric_rows, profile_name, preferences=preferences)
    summary_rows = []
    for row in ranked:
        summary_rows.append({
            "Strategy": row["Strategy"],
            "Score": row["score_100"],
            "Net Worth": row["Final Net Worth"],
            "After-Tax Legacy": row["After-Tax Legacy"],
            "Trad IRA @ End": row["Ending Traditional IRA Balance"],
            "Roth @ End": row["Roth @ End"],
            "Brokerage @ End": row["Brokerage @ End"],
            "Stability": row["stability_label"],
            "Risk": row["risk_label"],
            "Final Household SS Income": row["Final Household SS Income"],
            "Survivor SS Income": row["Survivor SS Income"],
        })
    summary_df = pd.DataFrame(summary_rows)
    explanation = generate_advisor_interpretation(profile_name, ranked)
    return {
        "profile_name": profile_name,
        "summary_df": summary_df,
        "ranked_rows": ranked,
        "advisor_text": explanation,
        "close_result": is_close_quick_result(ranked),
        "next_step_guidance": generate_next_step_guidance(profile_name, ranked),
        "errors": errors,
        "data_source": "break_even_governor",
        "applied_preset_note": preset.get("preset_note", ""),
        "active_preferences_text": describe_active_scoring_preferences(preferences),
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


def get_page_specific_state_keys(page: str) -> list[str]:
    prefixes = PAGE_STATE_KEY_PREFIXES.get(page, [])
    keys = []
    for key in SCENARIO_STATE_KEYS:
        if any(key.startswith(prefix) for prefix in prefixes):
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


def collect_scenario_state() -> dict:
    ensure_default_state()
    return {key: copy.deepcopy(st.session_state.get(key, DEFAULT_APP_STATE[key])) for key in SCENARIO_STATE_KEYS}


def sync_widget_state_from_canonical_state() -> None:
    """Keep UI-only widget keys aligned with the canonical saved scenario values."""
    if "target_trad_override_max_rate" in st.session_state:
        pct = float(st.session_state.get("target_trad_override_max_rate", 0.0)) * 100.0
        st.session_state["target_trad_override_max_rate_pct_display"] = f"{pct:.0f}%"


def apply_scenario_state(state: dict) -> None:
    ensure_default_state()
    for key in SCENARIO_STATE_KEYS:
        st.session_state[key] = copy.deepcopy(state.get(key, DEFAULT_APP_STATE[key]))
    sync_widget_state_from_canonical_state()


def reset_scenario_state() -> None:
    current_page = st.session_state.get("app_page", "home")
    apply_scenario_state({})
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


def build_scenario_export_payload(scope: str = "full") -> str:
    state = collect_scenario_state() if scope == "full" else collect_page_state(scope)
    payload = {
        "meta": {
            "app": "retirement_model",
            "version": "two_page_tools_save_load_guardrails_rates_v2",
            "scope": scope,
        },
        "state": state,
    }
    return json.dumps(payload, indent=2)


def render_scenario_manager(current_page: str) -> None:
    with st.expander("Scenario Save / Load / Reset", expanded=False):
        st.caption("Download a full scenario or a page-specific snapshot, upload a saved scenario into a new session, or reset all inputs back to zero-style defaults.")
        d1, d2 = st.columns(2)
        with d1:
            st.download_button(
                "Download Full Scenario",
                data=build_scenario_export_payload("full"),
                file_name=f"retirement_model_full_{current_page}.json",
                mime="application/json",
                use_container_width=True,
            )
        with d2:
            st.download_button(
                f"Download {current_page.title()} Page Inputs",
                data=build_scenario_export_payload(current_page),
                file_name=f"retirement_model_{current_page}_inputs.json",
                mime="application/json",
                use_container_width=True,
                disabled=current_page == "home",
            )
        upload_key = f"scenario_upload_{current_page}"
        uploaded_file = st.file_uploader("Upload Saved Scenario", type=["json"], key=upload_key)
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Load Uploaded Scenario", use_container_width=True, disabled=uploaded_file is None, key=f"load_scenario_{current_page}"):
                try:
                    payload = json.load(uploaded_file)
                    state = payload.get("state", payload)
                    if not isinstance(state, dict):
                        raise ValueError("Uploaded JSON does not contain a valid scenario state.")
                    meta = payload.get("meta", {}) if isinstance(payload, dict) else {}
                    scope = str(meta.get("scope", "full"))
                    if scope == "full":
                        apply_scenario_state(state)
                    else:
                        ensure_default_state()
                        for key in get_page_specific_state_keys(scope):
                            if key in state:
                                st.session_state[key] = copy.deepcopy(state[key])
                    st.session_state["app_page"] = current_page
                    st.success(f"Scenario loaded ({scope}).")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not load scenario: {exc}")
        with c2:
            if st.button("Reset Inputs To Defaults", use_container_width=True, key=f"reset_scenario_{current_page}"):
                reset_scenario_state()
                st.success("Inputs reset to defaults.")
                st.rerun()



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
    combos = [(owner_age, spouse_age) for owner_age in range(62, 71) for spouse_age in range(62, 71)]
    total_combos = len(combos)

    # Freeze a clean snapshot for optimizer runs and force the optimizer path into fast mode.
    # This 81-combination scan is intentionally profile-neutral at the engine level so the
    # resulting raw combo table can be rescored independently for every planning profile.
    base_snapshot = copy.deepcopy(inputs)
    base_snapshot["integrity_mode"] = False
    base_snapshot["strict_repeatability_check"] = False

    results = list(existing_results or [])
    st.session_state["ss_optimizer_running"] = True
    st.session_state["ss_optimizer_interrupted"] = False
    st.session_state["ss_optimizer_error"] = None
    st.session_state["ss_optimizer_partial_results"] = results
    progress_bar = st.progress(start_index / total_combos if total_combos else 0.0, text="Running Social Security optimizer...")

    try:
        for combo_index in range(start_index, total_combos):
            owner_age, spouse_age = combos[combo_index]
            scenario_inputs = dict(base_snapshot)
            scenario_inputs["owner_claim_age"] = int(owner_age)
            scenario_inputs["spouse_claim_age"] = int(spouse_age)

            try:
                run_result = run_model_break_even_governor(scenario_inputs, sanitize_governor_max_conversion(max_conversion), step_size)
            except Exception as exc:
                st.session_state["ss_optimizer_progress_index"] = combo_index
                st.session_state["ss_optimizer_partial_results"] = results
                st.session_state["ss_optimizer_last_completed"] = combos[combo_index - 1] if combo_index > 0 else None
                st.session_state["ss_optimizer_interrupted"] = True
                st.session_state["ss_optimizer_error"] = f"SS optimizer failed for owner age {owner_age} / spouse age {spouse_age}: {exc}"
                interrupted_df = pd.DataFrame(results) if results else pd.DataFrame()
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

            trad_penalty_applied = float(trad_balance_penalty_lambda) * float(run_result["ending_trad_balance"])
            metrics = build_strategy_metrics(run_result)
            row = {
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
            results.append(row)
            st.session_state["ss_optimizer_partial_results"] = results
            st.session_state["ss_optimizer_progress_index"] = combo_index + 1
            st.session_state["ss_optimizer_last_completed"] = (owner_age, spouse_age)
            progress_bar.progress((combo_index + 1) / total_combos, text=f"Running Social Security optimizer... {combo_index + 1}/{total_combos}")

        results_df = pd.DataFrame(results).sort_values(
            by=["Score", "Final Net Worth"],
            ascending=[False, False],
        ).reset_index(drop=True)
        results_df.insert(0, "Rank", range(1, len(results_df) + 1))

        top_10_df = results_df.head(10).copy()
        top_3 = results_df.head(3).copy()
        profile_shortlists = build_profile_shortlists_from_optimizer_rows(results, preferences=extract_scoring_preferences(inputs))

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

        progress_bar.empty()
        clear_ss_optimizer_state(clear_last_result=False)
        st.session_state["ss_optimizer_last_result"] = tag_result_payload({
            "all_results_df": results_df,
            "top_10_df": top_10_df,
            "comparison_df": comparison_df,
            "profile_shortlists": profile_shortlists,
            "best_result": best_result,
            "best_validation": best_validation,
            "best_rerun_summary": best_rerun_summary,
            "trad_balance_penalty_lambda": float(trad_balance_penalty_lambda),
            "profile_name": None,
            "optimizer_is_profile_neutral": True,
            "completed": True,
            "progress_index": total_combos,
            "total_combos": total_combos,
            "interrupted_partial_df": None,
            "error_message": None,
        }, engine="ss_optimizer")
        return tag_result_payload({
            "all_results_df": results_df,
            "all_results_export_df": results_df.copy(),
            "top_10_df": top_10_df,
            "top_10_export_df": top_10_df.copy(),
            "comparison_df": comparison_df,
            "comparison_display_df": comparison_df,
            "profile_shortlists": profile_shortlists,
            "best_result": best_result,
            "best_validation": best_validation,
            "best_rerun_summary": best_rerun_summary,
            "trad_balance_penalty_lambda": float(trad_balance_penalty_lambda),
            "profile_name": profile_name,
            "applied_preset_note": "",
            "interrupted_partial_df": None,
            "completed": True,
            "progress_index": total_combos,
            "total_combos": total_combos,
            "error_message": None,
        }, engine="ss_optimizer")
    finally:
        st.session_state["ss_optimizer_running"] = False
        try:
            progress_bar.empty()
        except Exception:
            pass


def render_ss_optimizer_results(result: dict):
    st.subheader("Social Security Optimizer Summary")
    st.write(f"Scoring lambda (Trad IRA penalty): {result['trad_balance_penalty_lambda']:.2f}")
    if result.get("optimizer_is_profile_neutral", False):
        st.caption("The full 81-combination optimizer run is profile-neutral at the engine level. The Top 5 profile tabs below rescore that same 81-row result set independently for each planning profile using the same scoring logic as Quick Strategy Recommendation.")
    if result["best_result"] is not None:
        best = result["best_result"]
        st.write(f"Best SS Ages: {int(best['Owner SS Age'])}/{int(best['Spouse SS Age'])}")
        st.write(f"Best Score: {format_dollars(best['Score'])}")
        st.write(f"Best Final Net Worth: {format_dollars(best['Final Net Worth'])}")
        st.write(f"Best Ending Traditional IRA Balance: {format_dollars(best['Ending Traditional IRA Balance'])}")

    if result.get("best_validation") is not None:
        validation = result["best_validation"]
        if validation["passed"]:
            st.success("Best-strategy rerun validation passed. Optimizer winner matched a fresh rerun within tolerance.")
        else:
            st.error("Best-strategy rerun validation failed. The optimizer winner did not match a fresh rerun.")
            st.dataframe(validation["mismatch_df"], use_container_width=True)

    if result.get("interrupted_partial_df") is not None or not result.get("completed", True):
        msg = result.get("error_message") or "A prior optimizer run was interrupted before all 81 combinations finished."
        st.warning(f"{msg} Resume it to complete the full result set.")

    if not result.get("completed", True):
        return

    st.subheader("Top 10 SS Strategies")
    st.caption("This table is the raw optimizer ranking sorted by Final Net Worth minus the Traditional IRA penalty lambda. It is not a planning-profile ranking.")
    st.dataframe(
        result["top_10_df"].style.format({
            "Final Net Worth": "${:,.0f}",
            "Total Federal Tax": "${:,.0f}",
            "Total State Tax": "${:,.0f}",
            "Total ACA Cost": "${:,.0f}",
            "Total IRMAA Cost": "${:,.0f}",
            "Total Government Drag": "${:,.0f}",
            "Total Conversions": "${:,.0f}",
            "Ending Traditional IRA Balance": "${:,.0f}",
            "Traditional IRA Penalty Applied": "${:,.0f}",
            "Max MAGI": "${:,.0f}",
            "Score": "${:,.0f}",
        }),
        use_container_width=True,
    )
    st.download_button(
        "Download Top 10 SS Strategies (CSV)",
        data=result["top_10_export_df"].to_csv(index=False),
        file_name="top_10_ss_strategies.csv",
        mime="text/csv",
        use_container_width=True,
    )

    st.subheader("Top 3 Side-by-Side Comparison")
    st.dataframe(result["comparison_display_df"], use_container_width=True)
    st.download_button(
        "Download Top 3 Comparison (CSV)",
        data=result["comparison_display_df"].to_csv(index=False),
        file_name="top_3_ss_comparison.csv",
        mime="text/csv",
        use_container_width=True,
    )

    profile_shortlists = result.get("profile_shortlists", {}) or {}
    if profile_shortlists:
        st.subheader("Top 5 combinations by planning profile")
        st.caption("These shortlists are derived from the full 81-combination optimizer run, then rescored for each planning profile using the same underlying results. If you change ranking preferences later, use the rerank button below instead of rerunning the full 81-combination engine.")
        tabs = st.tabs(list(profile_shortlists.keys()))
        for tab, profile_name in zip(tabs, profile_shortlists.keys()):
            with tab:
                shortlist_df = profile_shortlists.get(profile_name, pd.DataFrame())
                if shortlist_df.empty:
                    st.info("No shortlist available for this profile yet.")
                    continue
                st.caption("Score breakdown columns show which forces are helping or hurting each strategy for this profile. Positive columns help the score. Penalty columns subtract from it.")
                st.dataframe(
                    shortlist_df.style.format({
                        "Score": "{:.2f}",
                        "Net Worth": "${:,.0f}",
                        "After-Tax Legacy": "${:,.0f}",
                        "Effective Legacy Value": "${:,.0f}",
                        "Heir Tax Drag": "${:,.0f}",
                        "Trad IRA @ End": "${:,.0f}",
                        "Traditional IRA Share @ End": "{:.1%}",
                        "Roth @ End": "${:,.0f}",
                        "Brokerage @ End": "${:,.0f}",
                        "Final Household SS Income": "${:,.0f}",
                        "Survivor SS Income": "${:,.0f}",
                        "Total Government Drag": "${:,.0f}",
                        "Total Conversions": "${:,.0f}",
                        "NW Score +": "{:.2f}",
                        "Legacy Score +": "{:.2f}",
                        "Stability Score +": "{:.2f}",
                        "Trad Penalty -": "{:.2f}",
                        "Trad Share Penalty -": "{:.2f}",
                        "Gov Drag Penalty -": "{:.2f}",
                        "Heir Tax Penalty -": "{:.2f}",
                        "Risk Penalty -": "{:.2f}",
                    }),
                    use_container_width=True,
                )
                top_row = shortlist_df.iloc[0]
                st.caption("This button loads the selected optimizer shortlist winner into the Governor below. It does not rerun the optimizer.")
                st.button(
                    f"Apply {profile_name} winner {top_row['Strategy']} to Governor",
                    key=f"profile_shortlist_open_{profile_name}_{top_row['Strategy']}",
                    on_click=launch_conversion_optimizer_from_strategy,
                    args=(int(top_row["Owner SS Age"]), int(top_row["Spouse SS Age"]), "optimizer_profile_shortlist", profile_name),
                    use_container_width=True,
                )
                st.download_button(
                    f"Download {profile_name} Top 5 (CSV)",
                    data=shortlist_df.to_csv(index=False),
                    file_name=f"ss_optimizer_top5_{profile_name.lower().replace(' ', '_').replace('-', '_')}.csv",
                    mime="text/csv",
                    use_container_width=True,
                    key=f"download_profile_shortlist_{profile_name}",
                )

    with st.expander("All 81 SS combinations"):
        st.dataframe(
            result["all_results_df"].style.format({
                "Final Net Worth": "${:,.0f}",
                "Total Federal Tax": "${:,.0f}",
                "Total State Tax": "${:,.0f}",
                "Total ACA Cost": "${:,.0f}",
                "Total IRMAA Cost": "${:,.0f}",
                "Total Government Drag": "${:,.0f}",
                "Total Conversions": "${:,.0f}",
                "Ending Traditional IRA Balance": "${:,.0f}",
                "Traditional IRA Penalty Applied": "${:,.0f}",
                "Max MAGI": "${:,.0f}",
                "Score": "${:,.0f}",
            }),
            use_container_width=True,
        )
        st.download_button(
            "Download All SS Results (CSV)",
            data=result["all_results_export_df"].to_csv(index=False),
            file_name="all_ss_results.csv",
            mime="text/csv",
            use_container_width=True,
        )



def get_first_irmaa_cliff_threshold(year: int) -> float | None:
    table = get_irmaa_table(year)
    positive_rows = [row for row in table if float(row[2]) > 0.0]
    if not positive_rows:
        return None
    return float(positive_rows[0][0])


def evaluate_annual_conversion_candidate(
    year: int,
    other_ordinary_income: float,
    total_ss: float,
    realized_ltcg: float,
    conversion: float,
    state_tax_rate: float,
    aca_lives: int,
    medicare_lives: int,
) -> dict:
    conversion = max(0.0, float(conversion))
    other_ordinary_income = max(0.0, float(other_ordinary_income))
    total_ss = max(0.0, float(total_ss))
    realized_ltcg = max(0.0, float(realized_ltcg))

    tax_info = calculate_federal_tax(
        other_ordinary_income + conversion,
        total_ss,
        year,
        realized_ltcg=realized_ltcg,
    )
    magi = calculate_magi(tax_info['agi'], year)
    state_tax = max(
        0.0,
        float(tax_info['ordinary_taxable_income'] + tax_info.get('ltcg_taxable_income', 0.0))
    ) * float(state_tax_rate)
    aca_cost = calculate_aca_cost(magi, year, aca_lives)
    irmaa_cost = calculate_irmaa_cost(magi, year, medicare_lives)
    total_drag = float(tax_info['federal_tax'] + state_tax + aca_cost + irmaa_cost)

    return {
        'Conversion': conversion,
        'Other Ordinary Income': float(other_ordinary_income + conversion),
        'Total SS': total_ss,
        'Realized LTCG': realized_ltcg,
        'Taxable SS': float(tax_info['taxable_ss']),
        'AGI': float(tax_info['agi']),
        'MAGI': float(magi),
        'Ordinary Taxable Income': float(tax_info['ordinary_taxable_income']),
        'LTCG Taxable Income': float(tax_info['ltcg_taxable_income']),
        'Taxable Income': float(tax_info['taxable_income']),
        'Federal Tax': float(tax_info['federal_tax']),
        'State Tax': float(state_tax),
        'ACA Cost': float(aca_cost),
        'IRMAA Cost': float(irmaa_cost),
        'Total Government Drag': total_drag,
        'Marginal Rate': float(tax_info['marginal_rate']),
    }


def round_down_to_step(value: float, step_size: float) -> float:
    value = max(0.0, float(value))
    step_size = max(1.0, float(step_size))
    return math.floor(value / step_size) * step_size


def find_max_conversion_under_rule(
    year: int,
    base_other_ordinary_income: float,
    total_ss: float,
    realized_ltcg: float,
    state_tax_rate: float,
    aca_lives: int,
    medicare_lives: int,
    max_conversion: float,
    step_size: float,
    predicate,
) -> tuple[float, dict]:
    best_conversion = 0.0
    best_candidate = evaluate_annual_conversion_candidate(
        year,
        base_other_ordinary_income,
        total_ss,
        realized_ltcg,
        0.0,
        state_tax_rate,
        aca_lives,
        medicare_lives,
    )

    steps = int(max(0, math.floor(float(max_conversion) / float(step_size))))
    for step_idx in range(steps + 1):
        conversion = float(step_idx) * float(step_size)
        candidate = evaluate_annual_conversion_candidate(
            year,
            base_other_ordinary_income,
            total_ss,
            realized_ltcg,
            conversion,
            state_tax_rate,
            aca_lives,
            medicare_lives,
        )
        if predicate(candidate):
            best_conversion = conversion
            best_candidate = candidate
        else:
            break

    return best_conversion, best_candidate


def run_annual_conversion_calculator(
    inputs: dict,
    calc_year: int,
    external_other_ordinary_income: float,
    realized_ltcg_so_far: float,
    total_ss_for_year: float,
    target_bracket: str,
    income_safety_buffer: float,
    max_conversion: float,
    step_size: float,
    apply_bracket_guardrail: bool,
    apply_aca_guardrail: bool,
    apply_irmaa_guardrail: bool,
) -> dict:
    coverage = get_coverage_status(calc_year, int(inputs['primary_aca_end_year']), int(inputs['spouse_aca_end_year']))
    aca_lives = int(coverage['aca_lives'])
    medicare_lives = int(coverage['medicare_lives'])
    state_tax_rate = float(inputs.get('state_tax_rate', 0.0399))
    safety_buffer = max(0.0, float(income_safety_buffer))

    baseline = evaluate_annual_conversion_candidate(
        calc_year,
        external_other_ordinary_income,
        total_ss_for_year,
        realized_ltcg_so_far,
        0.0,
        state_tax_rate,
        aca_lives,
        medicare_lives,
    )

    bracket_tops = get_bracket_tops(calc_year)
    target_bracket_top = float(bracket_tops[target_bracket])
    bracket_limit = max(0.0, target_bracket_top - safety_buffer)

    aca_limit = get_aca_magi_limit(calc_year, aca_lives) if aca_lives > 0 else None
    aca_limit_buffered = max(0.0, float(aca_limit) - safety_buffer) if aca_limit is not None else None

    first_irmaa_cliff = get_first_irmaa_cliff_threshold(calc_year) if medicare_lives > 0 else None
    first_irmaa_cliff_buffered = max(0.0, float(first_irmaa_cliff) - safety_buffer) if first_irmaa_cliff is not None else None

    threshold_rows = []
    active_caps = []

    bracket_max_conversion, bracket_candidate = find_max_conversion_under_rule(
        calc_year,
        external_other_ordinary_income,
        total_ss_for_year,
        realized_ltcg_so_far,
        state_tax_rate,
        aca_lives,
        medicare_lives,
        max_conversion,
        step_size,
        lambda c: float(c['Ordinary Taxable Income']) <= bracket_limit,
    )
    threshold_rows.append({
        'Rule': f'Top of {target_bracket} bracket',
        'Enabled For Recommendation': bool(apply_bracket_guardrail),
        'Threshold Value': target_bracket_top,
        'Buffered Threshold': bracket_limit,
        'Max Conversion': bracket_max_conversion,
        'MAGI At Max': float(bracket_candidate['MAGI']),
        'Ordinary Taxable Income At Max': float(bracket_candidate['Ordinary Taxable Income']),
        'Federal Tax At Max': float(bracket_candidate['Federal Tax']),
        'State Tax At Max': float(bracket_candidate['State Tax']),
        'ACA Cost At Max': float(bracket_candidate['ACA Cost']),
        'IRMAA Cost At Max': float(bracket_candidate['IRMAA Cost']),
        'Total Government Drag At Max': float(bracket_candidate['Total Government Drag']),
        'Note': 'Primary federal bracket guardrail for the current year.',
    })
    if apply_bracket_guardrail:
        active_caps.append(bracket_max_conversion)

    if aca_lives > 0:
        aca_max_conversion, aca_candidate = find_max_conversion_under_rule(
            calc_year,
            external_other_ordinary_income,
            total_ss_for_year,
            realized_ltcg_so_far,
            state_tax_rate,
            aca_lives,
            medicare_lives,
            max_conversion,
            step_size,
            lambda c: float(c['MAGI']) <= float(aca_limit_buffered),
        )
        threshold_rows.append({
            'Rule': 'ACA MAGI limit',
            'Enabled For Recommendation': bool(apply_aca_guardrail),
            'Threshold Value': float(aca_limit),
            'Buffered Threshold': float(aca_limit_buffered),
            'Max Conversion': aca_max_conversion,
            'MAGI At Max': float(aca_candidate['MAGI']),
            'Ordinary Taxable Income At Max': float(aca_candidate['Ordinary Taxable Income']),
            'Federal Tax At Max': float(aca_candidate['Federal Tax']),
            'State Tax At Max': float(aca_candidate['State Tax']),
            'ACA Cost At Max': float(aca_candidate['ACA Cost']),
            'IRMAA Cost At Max': float(aca_candidate['IRMAA Cost']),
            'Total Government Drag At Max': float(aca_candidate['Total Government Drag']),
            'Note': 'Keeps MAGI under the ACA cliff/headroom line for the selected year.',
        })
        if apply_aca_guardrail:
            active_caps.append(aca_max_conversion)
    else:
        threshold_rows.append({
            'Rule': 'ACA MAGI limit',
            'Enabled For Recommendation': False,
            'Threshold Value': '',
            'Buffered Threshold': '',
            'Max Conversion': '',
            'MAGI At Max': '',
            'Ordinary Taxable Income At Max': '',
            'Federal Tax At Max': '',
            'State Tax At Max': '',
            'ACA Cost At Max': '',
            'IRMAA Cost At Max': '',
            'Total Government Drag At Max': '',
            'Note': 'No ACA-covered lives in the selected year, so ACA guardrail is not applicable.',
        })

    if medicare_lives > 0 and first_irmaa_cliff is not None:
        irmaa_max_conversion, irmaa_candidate = find_max_conversion_under_rule(
            calc_year,
            external_other_ordinary_income,
            total_ss_for_year,
            realized_ltcg_so_far,
            state_tax_rate,
            aca_lives,
            medicare_lives,
            max_conversion,
            step_size,
            lambda c: float(c['MAGI']) <= float(first_irmaa_cliff_buffered),
        )
        threshold_rows.append({
            'Rule': 'First IRMAA cliff',
            'Enabled For Recommendation': bool(apply_irmaa_guardrail),
            'Threshold Value': float(first_irmaa_cliff),
            'Buffered Threshold': float(first_irmaa_cliff_buffered),
            'Max Conversion': irmaa_max_conversion,
            'MAGI At Max': float(irmaa_candidate['MAGI']),
            'Ordinary Taxable Income At Max': float(irmaa_candidate['Ordinary Taxable Income']),
            'Federal Tax At Max': float(irmaa_candidate['Federal Tax']),
            'State Tax At Max': float(irmaa_candidate['State Tax']),
            'ACA Cost At Max': float(irmaa_candidate['ACA Cost']),
            'IRMAA Cost At Max': float(irmaa_candidate['IRMAA Cost']),
            'Total Government Drag At Max': float(irmaa_candidate['Total Government Drag']),
            'Note': 'Current-year MAGI warning line for the first IRMAA tier.',
        })
        if apply_irmaa_guardrail:
            active_caps.append(irmaa_max_conversion)
    else:
        threshold_rows.append({
            'Rule': 'First IRMAA cliff',
            'Enabled For Recommendation': False,
            'Threshold Value': '',
            'Buffered Threshold': '',
            'Max Conversion': '',
            'MAGI At Max': '',
            'Ordinary Taxable Income At Max': '',
            'Federal Tax At Max': '',
            'State Tax At Max': '',
            'ACA Cost At Max': '',
            'IRMAA Cost At Max': '',
            'Total Government Drag At Max': '',
            'Note': 'No Medicare-covered lives in the selected year, so IRMAA guardrail is not applicable.',
        })

    recommended_conversion = min(active_caps) if active_caps else max_conversion
    recommended_conversion = round_down_to_step(recommended_conversion, step_size)
    recommended = evaluate_annual_conversion_candidate(
        calc_year,
        external_other_ordinary_income,
        total_ss_for_year,
        realized_ltcg_so_far,
        recommended_conversion,
        state_tax_rate,
        aca_lives,
        medicare_lives,
    )

    baseline_vs_recommended = pd.DataFrame([
        {
            'Scenario': 'No conversion',
            'Conversion': float(baseline['Conversion']),
            'MAGI': float(baseline['MAGI']),
            'Ordinary Taxable Income': float(baseline['Ordinary Taxable Income']),
            'Federal Tax': float(baseline['Federal Tax']),
            'State Tax': float(baseline['State Tax']),
            'ACA Cost': float(baseline['ACA Cost']),
            'IRMAA Cost': float(baseline['IRMAA Cost']),
            'Total Government Drag': float(baseline['Total Government Drag']),
        },
        {
            'Scenario': 'Recommended conversion',
            'Conversion': float(recommended['Conversion']),
            'MAGI': float(recommended['MAGI']),
            'Ordinary Taxable Income': float(recommended['Ordinary Taxable Income']),
            'Federal Tax': float(recommended['Federal Tax']),
            'State Tax': float(recommended['State Tax']),
            'ACA Cost': float(recommended['ACA Cost']),
            'IRMAA Cost': float(recommended['IRMAA Cost']),
            'Total Government Drag': float(recommended['Total Government Drag']),
        },
    ])
    baseline_vs_recommended['Incremental vs No Conversion'] = baseline_vs_recommended['Total Government Drag'] - float(baseline['Total Government Drag'])

    calc_assumptions = {
        'calc_year': int(calc_year),
        'external_other_ordinary_income': float(external_other_ordinary_income),
        'realized_ltcg_so_far': float(realized_ltcg_so_far),
        'total_ss_for_year': float(total_ss_for_year),
        'target_bracket': str(target_bracket),
        'income_safety_buffer': float(income_safety_buffer),
        'max_conversion': float(max_conversion),
        'step_size': float(step_size),
        'apply_bracket_guardrail': bool(apply_bracket_guardrail),
        'apply_aca_guardrail': bool(apply_aca_guardrail),
        'apply_irmaa_guardrail': bool(apply_irmaa_guardrail),
        'aca_lives': int(aca_lives),
        'medicare_lives': int(medicare_lives),
        'state_tax_rate': float(state_tax_rate),
    }
    calc_fingerprint = hashlib.md5(json.dumps(_json_safe(calc_assumptions), sort_keys=True).encode()).hexdigest()[:10]

    summary = {
        'Year': int(calc_year),
        'Recommended Conversion': float(recommended_conversion),
        'Current-Year SS Used': float(total_ss_for_year),
        'Current-Year Other Ordinary Income Used': float(external_other_ordinary_income),
        'Current-Year LTCG Used': float(realized_ltcg_so_far),
        'Target Bracket': str(target_bracket),
        'ACA Lives': int(aca_lives),
        'Medicare Lives': int(medicare_lives),
        'ACA Limit': aca_limit,
        'First IRMAA Cliff': first_irmaa_cliff,
        'Scenario Fingerprint': calc_fingerprint,
    }

    return {
        'summary': summary,
        'threshold_df': pd.DataFrame(threshold_rows),
        'compare_df': baseline_vs_recommended,
        'recommended_candidate': recommended,
        'baseline_candidate': baseline,
    }


def render_annual_conversion_calculator_results(result: dict):
    summary = result['summary']
    recommended = result['recommended_candidate']
    baseline = result['baseline_candidate']

    st.subheader('Annual Conversion Calculator Summary')
    st.write(f"Scenario Fingerprint: `{summary['Scenario Fingerprint']}`")
    st.write(f"Year analyzed: {summary['Year']}")
    st.write(f"Recommended conversion: ${float(summary['Recommended Conversion']):,.0f}")
    st.write(f"Target bracket: {summary['Target Bracket']}")
    st.write(f"ACA lives / Medicare lives: {summary['ACA Lives']} / {summary['Medicare Lives']}")
    st.write(f"Current-year SS used: ${float(summary['Current-Year SS Used']):,.0f}")
    st.write(f"Current-year other ordinary income used: ${float(summary['Current-Year Other Ordinary Income Used']):,.0f}")
    st.write(f"Current-year realized LTCG used: ${float(summary['Current-Year LTCG Used']):,.0f}")
    if summary['ACA Limit'] is not None:
        st.write(f"ACA MAGI limit used: ${float(summary['ACA Limit']):,.0f}")
    if summary['First IRMAA Cliff'] is not None:
        st.write(f"First IRMAA cliff used: ${float(summary['First IRMAA Cliff']):,.0f}")

    metric_cols = st.columns(4)
    metric_cols[0].metric('Recommended Conversion', f"${float(summary['Recommended Conversion']):,.0f}")
    metric_cols[1].metric('Recommended MAGI', f"${float(recommended['MAGI']):,.0f}")
    metric_cols[2].metric('Incremental Total Drag', f"${float(recommended['Total Government Drag'] - baseline['Total Government Drag']):,.0f}")
    metric_cols[3].metric('Recommended Total Drag', f"${float(recommended['Total Government Drag']):,.0f}")

    st.subheader('Guardrail Thresholds')
    st.dataframe(result['threshold_df'], use_container_width=True)

    st.subheader('No Conversion vs Recommended Conversion')
    st.dataframe(result['compare_df'], use_container_width=True)


# -----------------------------
# DISPLAY
# -----------------------------
def render_summary(title: str, result: dict):
    st.subheader(title)
    st.write(f"Owner SS Start Year: {result['owner_ss_start']}")
    st.write(f"Spouse SS Start Year: {result['spouse_ss_start']}")
    st.write(f"Household RMD Start Year (approx): {result['household_rmd_start']}")
    st.write(f"Final Net Worth: ${result['final_net_worth']:,.0f}")
    st.write(f"Ending Traditional IRA Balance: ${result['ending_trad_balance']:,.0f}")
    st.write(f"Total Federal Taxes: ${result['total_federal_taxes']:,.0f}")
    st.write(f"Total State Taxes: ${result.get('total_state_taxes', 0.0):,.0f}")
    st.write(f"Total ACA Cost: ${result['total_aca_cost']:,.0f}")
    st.write(f"Total IRMAA Cost: ${result['total_irmaa_cost']:,.0f}")
    st.write(f"Total Government Drag: ${result.get('total_government_drag', 0.0):,.0f}")
    st.write(f"Total Shortfall: ${result['total_shortfall']:,.0f}")
    st.write(f"Max MAGI: ${result['max_magi']:,.0f}")
    st.write(f"Total Conversions: ${result['total_conversions']:,.0f}")
    st.write(f"ACA Hit Years: {result['aca_hit_years']}")
    st.write(f"IRMAA Hit Years: {result['irmaa_hit_years']}")
    st.write(f"First IRMAA Year: {result['first_irmaa_year'] if result['first_irmaa_year'] is not None else 'None'}")
    validation = result.get("validation")
    if validation is not None:
        if validation["passed"]:
            st.success("Repeatability check passed. Back-to-back rerun matched within tolerance.")
        else:
            st.error("Repeatability check failed. Back-to-back rerun did not match.")
            st.dataframe(validation["mismatch_df"], use_container_width=True)


# -----------------------------
# UI
# -----------------------------



ANNUAL_FILING_STATUS_OPTIONS = ["MFJ", "Single"]
ANNUAL_TARGET_BRACKET_OPTIONS = ["12%", "22%", "24%"]


def annual_status_factor(filing_status: str) -> float:
    return 1.0 if filing_status == "MFJ" else 0.5


def get_annual_standard_deduction_default(year: int, filing_status: str) -> float:
    return float(get_standard_deduction(year)) * annual_status_factor(filing_status)


def get_annual_federal_brackets(year: int, filing_status: str) -> list[tuple[float, float]]:
    factor = annual_status_factor(filing_status)
    return [(float(start) * factor, float(rate)) for start, rate in get_federal_brackets(year)]


def get_annual_ltcg_brackets(year: int, filing_status: str) -> list[tuple[float, float]]:
    factor = annual_status_factor(filing_status)
    return [(float(start) * factor, float(rate)) for start, rate in get_ltcg_brackets(year)]


def get_annual_bracket_tops(year: int, filing_status: str) -> dict:
    factor = annual_status_factor(filing_status)
    base = get_bracket_tops(year)
    return {k: float(v) * factor for k, v in base.items()}


def calculate_taxable_ss_annual(total_ss: float, non_ss_income: float, filing_status: str) -> tuple[float, float]:
    total_ss = max(0.0, float(total_ss))
    non_ss_income = max(0.0, float(non_ss_income))
    if filing_status == "MFJ":
        base1, base2, base_taxable = 32000.0, 44000.0, 6000.0
    else:
        base1, base2, base_taxable = 25000.0, 34000.0, 4500.0

    provisional_income = non_ss_income + 0.5 * total_ss
    if provisional_income <= base1:
        taxable_ss = 0.0
    elif provisional_income <= base2:
        taxable_ss = 0.5 * (provisional_income - base1)
    else:
        taxable_ss = base_taxable + 0.85 * (provisional_income - base2)

    taxable_ss = max(0.0, min(taxable_ss, 0.85 * total_ss))
    return float(taxable_ss), float(provisional_income)


def calculate_progressive_tax_from_brackets(taxable_income: float, brackets: list[tuple[float, float]]) -> float:
    taxable_income = max(0.0, float(taxable_income))
    if taxable_income <= 0:
        return 0.0

    tax = 0.0
    for i, (start, rate) in enumerate(brackets):
        end = brackets[i + 1][0] if i + 1 < len(brackets) else None
        if taxable_income <= start:
            break
        upper = taxable_income if end is None else min(taxable_income, end)
        amount = max(0.0, upper - start)
        tax += amount * rate
        if end is not None and taxable_income <= end:
            break
    return float(tax)


def calculate_ltcg_tax_from_brackets(ordinary_taxable_income: float, preferential_taxable_income: float, brackets: list[tuple[float, float]]) -> float:
    ordinary_taxable_income = max(0.0, float(ordinary_taxable_income))
    preferential_taxable_income = max(0.0, float(preferential_taxable_income))
    if preferential_taxable_income <= 0:
        return 0.0

    tax = 0.0
    remaining = preferential_taxable_income
    current = ordinary_taxable_income

    for i, (start, rate) in enumerate(brackets):
        end = brackets[i + 1][0] if i + 1 < len(brackets) else None
        band_start = max(current, start)
        if end is None:
            tax += remaining * rate
            remaining = 0.0
            break
        band_room = max(0.0, end - band_start)
        if band_room <= 0:
            continue
        amount = min(remaining, band_room)
        tax += amount * rate
        remaining -= amount
        current += amount
        if remaining <= 1e-9:
            break
    return float(tax)


def get_annual_marginal_rate(ordinary_taxable_income: float, year: int, filing_status: str) -> float:
    ordinary_taxable_income = max(0.0, float(ordinary_taxable_income))
    brackets = get_annual_federal_brackets(year, filing_status)
    rate = brackets[0][1]
    for start, bracket_rate in brackets:
        if ordinary_taxable_income >= start:
            rate = bracket_rate
        else:
            break
    return float(rate)


def get_annual_aca_limit(calc_year: int, filing_status: str, aca_covered_lives: int) -> float | None:
    if aca_covered_lives <= 0:
        return None
    household_key = "2_person" if filing_status == "MFJ" and aca_covered_lives >= 2 else "1_person"
    return float(get_aca_magi_limit(calc_year, 2 if household_key == "2_person" else 1))


def get_annual_irmaa_first_cliff(calc_year: int, filing_status: str) -> float | None:
    if filing_status == "MFJ":
        return float(get_first_irmaa_cliff_threshold(calc_year))
    return float(get_first_irmaa_cliff_threshold(calc_year)) * 0.5


def evaluate_annual_tax_scenario(
    year: int,
    filing_status: str,
    earned_income: float,
    other_ordinary_income: float,
    ira_withdrawals: float,
    conversions_done: float,
    additional_conversion: float,
    social_security_received: float,
    realized_ltcg: float,
    qualified_dividends: float,
    standard_deduction: float,
    state_tax_rate: float,
    aca_covered_lives: int,
    medicare_covered_lives: int,
) -> dict:
    earned_income = max(0.0, float(earned_income))
    other_ordinary_income = max(0.0, float(other_ordinary_income))
    ira_withdrawals = max(0.0, float(ira_withdrawals))
    conversions_done = max(0.0, float(conversions_done))
    additional_conversion = max(0.0, float(additional_conversion))
    social_security_received = max(0.0, float(social_security_received))
    realized_ltcg = max(0.0, float(realized_ltcg))
    qualified_dividends = max(0.0, float(qualified_dividends))
    standard_deduction = max(0.0, float(standard_deduction))
    preferential_income = realized_ltcg + qualified_dividends

    ordinary_income_pre_ss = (
        earned_income
        + other_ordinary_income
        + ira_withdrawals
        + conversions_done
        + additional_conversion
    )
    taxable_ss, provisional_income = calculate_taxable_ss_annual(
        social_security_received,
        ordinary_income_pre_ss + preferential_income,
        filing_status,
    )
    ordinary_income_total = ordinary_income_pre_ss + taxable_ss
    agi = ordinary_income_total + preferential_income
    magi = agi

    ordinary_taxable_income = max(0.0, ordinary_income_total - standard_deduction)
    deduction_remaining_for_preferential = max(0.0, standard_deduction - ordinary_income_total)
    preferential_taxable_income = max(0.0, preferential_income - deduction_remaining_for_preferential)
    taxable_income = ordinary_taxable_income + preferential_taxable_income

    federal_brackets = get_annual_federal_brackets(year, filing_status)
    ltcg_brackets = get_annual_ltcg_brackets(year, filing_status)
    ordinary_tax = calculate_progressive_tax_from_brackets(ordinary_taxable_income, federal_brackets)
    ltcg_qd_tax = calculate_ltcg_tax_from_brackets(ordinary_taxable_income, preferential_taxable_income, ltcg_brackets)
    federal_tax = ordinary_tax + ltcg_qd_tax

    nc_taxable_income = taxable_income
    state_tax = max(0.0, nc_taxable_income) * float(state_tax_rate)

    aca_cost = 0.0
    aca_limit = None
    if aca_covered_lives > 0:
        aca_limit = get_annual_aca_limit(year, filing_status, aca_covered_lives)
        household_key = "2_person" if filing_status == "MFJ" and aca_covered_lives >= 2 else "1_person"
        aca_cost = calculate_aca_cost(magi, year, 2 if household_key == "2_person" else 1)

    irmaa_first_cliff = None
    irmaa_cost = 0.0
    if medicare_covered_lives > 0:
        if filing_status == "MFJ":
            irmaa_cost = calculate_irmaa_cost(magi, year, medicare_covered_lives)
            irmaa_first_cliff = get_first_irmaa_cliff_threshold(year)
        else:
            irmaa_first_cliff = get_annual_irmaa_first_cliff(year, filing_status)
            single_irmaa_table = [(start * 0.5, end * 0.5, surcharge) for start, end, surcharge in get_latest_year_value(IRMAA_TABLE_BY_YEAR, year)]
            annual_surcharge = 0.0
            for start, end, surcharge in single_irmaa_table:
                if magi >= start and magi < end:
                    annual_surcharge = surcharge * 12.0
                    break
            irmaa_cost = annual_surcharge * max(1, medicare_covered_lives)

    total_tax = federal_tax + state_tax
    total_government_drag = total_tax + aca_cost + irmaa_cost
    effective_tax_rate = total_tax / agi if agi > 0 else 0.0
    all_in_effective_rate = total_government_drag / agi if agi > 0 else 0.0
    marginal_rate = get_annual_marginal_rate(ordinary_taxable_income, year, filing_status)

    return {
        "Year": int(year),
        "Filing Status": filing_status,
        "Earned Income": earned_income,
        "Other Ordinary Income": other_ordinary_income,
        "IRA Withdrawals": ira_withdrawals,
        "Conversions Already Done": conversions_done,
        "Additional Conversion": additional_conversion,
        "Total Conversion For Year": conversions_done + additional_conversion,
        "Social Security Received": social_security_received,
        "Realized LTCG": realized_ltcg,
        "Qualified Dividends": qualified_dividends,
        "Preferential Income": preferential_income,
        "Provisional Income": provisional_income,
        "Taxable SS": taxable_ss,
        "Ordinary Income Before SS": ordinary_income_pre_ss,
        "Ordinary Income Total": ordinary_income_total,
        "AGI": agi,
        "MAGI": magi,
        "Standard Deduction": standard_deduction,
        "Ordinary Taxable Income": ordinary_taxable_income,
        "Preferential Taxable Income": preferential_taxable_income,
        "Taxable Income": taxable_income,
        "Federal Ordinary Tax": ordinary_tax,
        "Federal LTCG/QD Tax": ltcg_qd_tax,
        "Federal Tax": federal_tax,
        "NC State Tax": state_tax,
        "Total Tax": total_tax,
        "ACA Cost": aca_cost,
        "IRMAA Cost": irmaa_cost,
        "Total Government Drag": total_government_drag,
        "Effective Tax Rate": effective_tax_rate,
        "All-In Effective Rate": all_in_effective_rate,
        "Marginal Federal Rate": marginal_rate,
        "ACA Limit": aca_limit,
        "First IRMAA Cliff": irmaa_first_cliff,
        "ACA Covered Lives": int(aca_covered_lives),
        "Medicare Covered Lives": int(medicare_covered_lives),
    }


def find_max_additional_conversion_for_rule(
    year: int,
    filing_status: str,
    earned_income: float,
    other_ordinary_income: float,
    ira_withdrawals: float,
    conversions_done: float,
    social_security_received: float,
    realized_ltcg: float,
    qualified_dividends: float,
    standard_deduction: float,
    state_tax_rate: float,
    aca_covered_lives: int,
    medicare_covered_lives: int,
    max_additional_conversion: float,
    step_size: float,
    rule_fn,
) -> tuple[float, dict]:
    best_conversion = 0.0
    best_candidate = evaluate_annual_tax_scenario(
        year, filing_status, earned_income, other_ordinary_income, ira_withdrawals,
        conversions_done, 0.0, social_security_received, realized_ltcg, qualified_dividends,
        standard_deduction, state_tax_rate, aca_covered_lives, medicare_covered_lives,
    )
    step_index = 0
    while True:
        additional_conversion = min(max_additional_conversion, step_index * step_size)
        candidate = evaluate_annual_tax_scenario(
            year, filing_status, earned_income, other_ordinary_income, ira_withdrawals,
            conversions_done, additional_conversion, social_security_received, realized_ltcg, qualified_dividends,
            standard_deduction, state_tax_rate, aca_covered_lives, medicare_covered_lives,
        )
        if rule_fn(candidate):
            best_conversion = additional_conversion
            best_candidate = candidate
        else:
            break
        if additional_conversion >= max_additional_conversion - 1e-9:
            break
        step_index += 1
    return float(best_conversion), best_candidate



def run_standalone_annual_tax_engine(
    year: int,
    filing_status: str,
    earned_income: float,
    other_ordinary_income: float,
    ira_withdrawals: float,
    conversions_done: float,
    social_security_received: float,
    realized_ltcg: float,
    qualified_dividends: float,
    standard_deduction: float,
    state_tax_rate: float,
    aca_covered_lives: int,
    medicare_covered_lives: int,
    target_bracket: str,
    safety_buffer: float,
    max_additional_conversion: float,
    step_size: float,
    use_bracket_guardrail: bool,
    use_aca_guardrail: bool,
    use_irmaa_guardrail: bool,
) -> dict:
    baseline = evaluate_annual_tax_scenario(
        year, filing_status, earned_income, other_ordinary_income, ira_withdrawals,
        conversions_done, 0.0, social_security_received, realized_ltcg, qualified_dividends,
        standard_deduction, state_tax_rate, aca_covered_lives, medicare_covered_lives,
    )

    bracket_tops = get_annual_bracket_tops(year, filing_status)
    target_bracket_top = float(bracket_tops[target_bracket])
    bracket_limit = max(0.0, target_bracket_top - float(safety_buffer))

    threshold_rows = []
    active_caps = []

    bracket_max, bracket_candidate = find_max_additional_conversion_for_rule(
        year, filing_status, earned_income, other_ordinary_income, ira_withdrawals, conversions_done,
        social_security_received, realized_ltcg, qualified_dividends, standard_deduction, state_tax_rate,
        aca_covered_lives, medicare_covered_lives, max_additional_conversion, step_size,
        lambda c: float(c["Ordinary Taxable Income"]) <= bracket_limit,
    )
    bracket_current_value = float(baseline["Ordinary Taxable Income"])
    threshold_rows.append({
        "Rule": f"Top of {target_bracket} bracket",
        "Enabled": bool(use_bracket_guardrail),
        "Threshold": target_bracket_top,
        "Buffered Threshold": bracket_limit,
        "Max Additional Conversion": bracket_max,
        "MAGI At Max": float(bracket_candidate["MAGI"]),
        "Taxable Income At Max": float(bracket_candidate["Taxable Income"]),
        "Federal Tax At Max": float(bracket_candidate["Federal Tax"]),
        "NC Tax At Max": float(bracket_candidate["NC State Tax"]),
        "ACA Cost At Max": float(bracket_candidate["ACA Cost"]),
        "Total Drag At Max": float(bracket_candidate["Total Government Drag"]),
        "Effective Tax Rate At Max": float(bracket_candidate["Effective Tax Rate"]),
        "All-In Effective Rate At Max": float(bracket_candidate["All-In Effective Rate"]),
        "Binding Metric": "Ordinary Taxable Income",
    })
    if use_bracket_guardrail:
        active_caps.append(bracket_max)

    aca_limit = get_annual_aca_limit(year, filing_status, aca_covered_lives)
    if aca_limit is not None:
        aca_limit_buffered = max(0.0, float(aca_limit) - float(safety_buffer))
        aca_max, aca_candidate = find_max_additional_conversion_for_rule(
            year, filing_status, earned_income, other_ordinary_income, ira_withdrawals, conversions_done,
            social_security_received, realized_ltcg, qualified_dividends, standard_deduction, state_tax_rate,
            aca_covered_lives, medicare_covered_lives, max_additional_conversion, step_size,
            lambda c: float(c["MAGI"]) <= aca_limit_buffered,
        )
        threshold_rows.append({
            "Rule": "ACA MAGI limit",
            "Enabled": bool(use_aca_guardrail),
            "Threshold": float(aca_limit),
            "Buffered Threshold": float(aca_limit_buffered),
            "Max Additional Conversion": aca_max,
            "MAGI At Max": float(aca_candidate["MAGI"]),
            "Taxable Income At Max": float(aca_candidate["Taxable Income"]),
            "Federal Tax At Max": float(aca_candidate["Federal Tax"]),
            "NC Tax At Max": float(aca_candidate["NC State Tax"]),
            "ACA Cost At Max": float(aca_candidate["ACA Cost"]),
            "Total Drag At Max": float(aca_candidate["Total Government Drag"]),
            "Effective Tax Rate At Max": float(aca_candidate["Effective Tax Rate"]),
            "All-In Effective Rate At Max": float(aca_candidate["All-In Effective Rate"]),
            "Binding Metric": "MAGI",
        })
        if use_aca_guardrail:
            active_caps.append(aca_max)

    irmaa_first_cliff = None
    if medicare_covered_lives > 0:
        irmaa_first_cliff = get_annual_irmaa_first_cliff(year, filing_status)
        irmaa_limit_buffered = max(0.0, float(irmaa_first_cliff) - float(safety_buffer))
        irmaa_max, irmaa_candidate = find_max_additional_conversion_for_rule(
            year, filing_status, earned_income, other_ordinary_income, ira_withdrawals, conversions_done,
            social_security_received, realized_ltcg, qualified_dividends, standard_deduction, state_tax_rate,
            aca_covered_lives, medicare_covered_lives, max_additional_conversion, step_size,
            lambda c: float(c["MAGI"]) <= irmaa_limit_buffered,
        )
        threshold_rows.append({
            "Rule": "First IRMAA cliff",
            "Enabled": bool(use_irmaa_guardrail),
            "Threshold": float(irmaa_first_cliff),
            "Buffered Threshold": float(irmaa_limit_buffered),
            "Max Additional Conversion": irmaa_max,
            "MAGI At Max": float(irmaa_candidate["MAGI"]),
            "Taxable Income At Max": float(irmaa_candidate["Taxable Income"]),
            "Federal Tax At Max": float(irmaa_candidate["Federal Tax"]),
            "NC Tax At Max": float(irmaa_candidate["NC State Tax"]),
            "ACA Cost At Max": float(irmaa_candidate["ACA Cost"]),
            "Total Drag At Max": float(irmaa_candidate["Total Government Drag"]),
            "Effective Tax Rate At Max": float(irmaa_candidate["Effective Tax Rate"]),
            "All-In Effective Rate At Max": float(irmaa_candidate["All-In Effective Rate"]),
            "Binding Metric": "MAGI",
        })
        if use_irmaa_guardrail:
            active_caps.append(irmaa_max)

    recommended_additional_conversion = min(active_caps) if active_caps else max_additional_conversion
    recommended_additional_conversion = floor_to_step(recommended_additional_conversion, step_size)
    recommended = evaluate_annual_tax_scenario(
        year, filing_status, earned_income, other_ordinary_income, ira_withdrawals,
        conversions_done, recommended_additional_conversion, social_security_received, realized_ltcg, qualified_dividends,
        standard_deduction, state_tax_rate, aca_covered_lives, medicare_covered_lives,
    )

    summary_inputs = {
        "year": int(year),
        "filing_status": filing_status,
        "earned_income": earned_income,
        "other_ordinary_income": other_ordinary_income,
        "ira_withdrawals": ira_withdrawals,
        "conversions_done": conversions_done,
        "social_security_received": social_security_received,
        "realized_ltcg": realized_ltcg,
        "qualified_dividends": qualified_dividends,
        "standard_deduction": standard_deduction,
        "state_tax_rate": state_tax_rate,
        "aca_covered_lives": int(aca_covered_lives),
        "medicare_covered_lives": int(medicare_covered_lives),
        "target_bracket": target_bracket,
        "safety_buffer": safety_buffer,
        "max_additional_conversion": max_additional_conversion,
        "step_size": step_size,
        "use_bracket_guardrail": bool(use_bracket_guardrail),
        "use_aca_guardrail": bool(use_aca_guardrail),
        "use_irmaa_guardrail": bool(use_irmaa_guardrail),
    }
    scenario_fingerprint = build_scenario_fingerprint(summary_inputs, max_additional_conversion, step_size)

    compare_rows = []
    for label, candidate in [("Current", baseline), ("After Recommended Additional Conversion", recommended)]:
        compare_rows.append({
            "Scenario": label,
            "Additional Conversion": float(candidate["Additional Conversion"]),
            "Total Conversion For Year": float(candidate["Total Conversion For Year"]),
            "Taxable SS": float(candidate["Taxable SS"]),
            "AGI": float(candidate["AGI"]),
            "MAGI": float(candidate["MAGI"]),
            "Taxable Income": float(candidate["Taxable Income"]),
            "Federal Tax": float(candidate["Federal Tax"]),
            "NC State Tax": float(candidate["NC State Tax"]),
            "Federal LTCG/QD Tax": float(candidate["Federal LTCG/QD Tax"]),
            "ACA Cost": float(candidate["ACA Cost"]),
            "IRMAA Cost": float(candidate["IRMAA Cost"]),
            "Total Government Drag": float(candidate["Total Government Drag"]),
            "Effective Tax Rate": float(candidate["Effective Tax Rate"]),
            "All-In Effective Rate": float(candidate["All-In Effective Rate"]),
        })

    summary = {
        "Year": int(year),
        "Filing Status": filing_status,
        "Recommended Additional Conversion": float(recommended_additional_conversion),
        "Target Bracket": target_bracket,
        "Safety Buffer": float(safety_buffer),
        "Standard Deduction": float(standard_deduction),
        "ACA Covered Lives": int(aca_covered_lives),
        "Medicare Covered Lives": int(medicare_covered_lives),
    }

    return {
        "summary": summary,
        "baseline_candidate": baseline,
        "recommended_candidate": recommended,
        "threshold_df": pd.DataFrame(threshold_rows),
        "compare_df": pd.DataFrame(compare_rows),
    }


def render_standalone_annual_tax_results(result: dict) -> None:
    summary = result["summary"]
    current = result["baseline_candidate"]
    recommended = result["recommended_candidate"]

    st.subheader("Annual Tax Engine Summary")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Recommended Additional Conversion", f"${float(summary['Recommended Additional Conversion']):,.0f}")
    m2.metric("Current MAGI", f"${float(current['MAGI']):,.0f}")
    m3.metric("Recommended MAGI", f"${float(recommended['MAGI']):,.0f}")
    m4.metric("Incremental Total Drag", f"${float(recommended['Total Government Drag'] - current['Total Government Drag']):,.0f}")

    st.subheader("Current-Year Snapshot")
    snapshot_rows = [
        ("Filing Status", current["Filing Status"]),
        ("Provisional Income", current["Provisional Income"]),
        ("Taxable Social Security", current["Taxable SS"]),
        ("AGI", current["AGI"]),
        ("MAGI", current["MAGI"]),
        ("Taxable Income", current["Taxable Income"]),
        ("Federal Ordinary Tax", current["Federal Ordinary Tax"]),
        ("Federal LTCG/QD Tax", current["Federal LTCG/QD Tax"]),
        ("Projected Federal Tax", current["Federal Tax"]),
        ("Projected NC State Tax", current["NC State Tax"]),
        ("Total Projected Tax", current["Total Tax"]),
        ("Tax-Only Effective Rate", current["Effective Tax Rate"]),
        ("All-In Effective Rate", current["All-In Effective Rate"]),
        ("Marginal Federal Rate", current["Marginal Federal Rate"]),
    ]
    snapshot_df = pd.DataFrame(snapshot_rows, columns=["Metric", "Current"])
    currency_metrics = {
        "Provisional Income", "Taxable Social Security", "AGI", "MAGI", "Taxable Income",
        "Federal Ordinary Tax", "Federal LTCG/QD Tax", "Projected Federal Tax",
        "Projected NC State Tax", "Total Projected Tax"
    }
    percent_metrics = {"Tax-Only Effective Rate", "All-In Effective Rate", "Marginal Federal Rate"}
    snapshot_df["Display"] = snapshot_df.apply(
        lambda row: f"${float(row['Current']):,.0f}" if row["Metric"] in currency_metrics else (
            f"{float(row['Current']):.2%}" if row["Metric"] in percent_metrics else str(row["Current"])
        ),
        axis=1,
    )
    st.dataframe(snapshot_df[["Metric", "Display"]], use_container_width=True)

    st.subheader("Current Snapshot")
    snapshot_cols = st.columns(3)
    snapshot_cols[0].metric("Current AGI", format_dollars(current["AGI"]))
    snapshot_cols[1].metric("Current MAGI", format_dollars(current["MAGI"]))
    snapshot_cols[2].metric("Current Ordinary Taxable Income", format_dollars(current["Ordinary Taxable Income"]))

    st.subheader("Additional Conversion Guardrails")
    st.dataframe(
        result["threshold_df"].style.format({
            "Threshold": "${:,.0f}",
            "Buffered Threshold": "${:,.0f}",
            "Max Additional Conversion": "${:,.0f}",
            "MAGI At Max": "${:,.0f}",
            "Taxable Income At Max": "${:,.0f}",
            "Federal Tax At Max": "${:,.0f}",
            "NC Tax At Max": "${:,.0f}",
            "ACA Cost At Max": "${:,.0f}",
            "Total Drag At Max": "${:,.0f}",
            "Effective Tax Rate At Max": "{:.2%}",
            "All-In Effective Rate At Max": "{:.2%}",
        }),
        use_container_width=True,
    )

    st.subheader("Current vs Recommended Additional Conversion")
    st.dataframe(
        result["compare_df"].style.format({
            "Additional Conversion": "${:,.0f}",
            "Total Conversion For Year": "${:,.0f}",
            "Taxable SS": "${:,.0f}",
            "AGI": "${:,.0f}",
            "MAGI": "${:,.0f}",
            "Taxable Income": "${:,.0f}",
            "Federal Tax": "${:,.0f}",
            "NC State Tax": "${:,.0f}",
            "Federal LTCG/QD Tax": "${:,.0f}",
            "ACA Cost": "${:,.0f}",
            "IRMAA Cost": "${:,.0f}",
            "Total Government Drag": "${:,.0f}",
            "Effective Tax Rate": "{:.2%}",
            "All-In Effective Rate": "{:.2%}",
        }),
        use_container_width=True,
    )

def go_to_page(page_name: str) -> None:
    st.session_state["app_page"] = page_name


def launch_conversion_optimizer_from_strategy(owner_age: int, spouse_age: int, source_label: str = "quick_recommendation", profile_name: str | None = None) -> None:
    st.session_state["owner_claim_age"] = int(owner_age)
    st.session_state["spouse_claim_age"] = int(spouse_age)
    st.session_state["selected_recommendation_strategy"] = f"{int(owner_age)}/{int(spouse_age)}"
    st.session_state["selected_recommendation_source"] = source_label
    st.session_state["suppress_quick_recommendation_stale_once"] = True
    if profile_name:
        st.session_state["planning_profile"] = profile_name
        apply_break_even_governor_profile_presets(profile_name, st.session_state.get("trad", 0.0), force=True)
    st.session_state["app_page"] = "conversion"


def get_app_page() -> str:
    if "app_page" not in st.session_state:
        st.session_state["app_page"] = "home"
    return st.session_state["app_page"]


def render_top_nav(current_page: str) -> None:
    ensure_default_state()
    nav1, nav2, nav3 = st.columns([1, 1, 1])
    with nav1:
        st.button("Home", on_click=go_to_page, args=("home",), disabled=current_page == "home", use_container_width=True)
    with nav2:
        st.button(
            "Annual Calculator",
            on_click=go_to_page,
            args=("annual",),
            disabled=current_page == "annual",
            use_container_width=True,
        )
    with nav3:
        st.button(
            "Conversion Optimizer",
            on_click=go_to_page,
            args=("conversion",),
            disabled=current_page == "conversion",
            use_container_width=True,
        )
    st.divider()
    render_scenario_manager(current_page)


def render_home_page() -> None:
    ensure_default_state()
    st.title("Retirement Model")
    st.subheader("Choose a tool")
    st.write(
        "Use the Annual Conversion Calculator for a clean current-year tax cockpit, or open the Break-Even Governor for the full lifetime governor and SS optimizer."
    )
    render_top_nav("home")
    st.info(
        "State is kept across pages. Annual current-year inputs stay in session and remain available when you switch tools."
    )


def render_shared_household_inputs() -> dict:
    st.header("Household Inputs")

    owner_claim_age = st.slider("Owner SS Claim Age", 62, 70, int(st.session_state.get("owner_claim_age", DEFAULT_APP_STATE["owner_claim_age"])), key="owner_claim_age")
    spouse_claim_age = st.slider("Spouse SS Claim Age", 62, 70, int(st.session_state.get("spouse_claim_age", DEFAULT_APP_STATE["spouse_claim_age"])), key="spouse_claim_age")

    owner_current_age = st.number_input("Owner Current Age", min_value=0, value=int(st.session_state.get("owner_current_age", DEFAULT_APP_STATE["owner_current_age"])), step=1, key="owner_current_age")
    spouse_current_age = st.number_input("Spouse Current Age", min_value=0, value=int(st.session_state.get("spouse_current_age", DEFAULT_APP_STATE["spouse_current_age"])), step=1, key="spouse_current_age")

    col1, col2 = st.columns(2)

    with col1:
        trad = st.number_input("Traditional IRA Balance", min_value=0.0, value=float(st.session_state.get("trad", DEFAULT_APP_STATE["trad"])), step=1000.0, key="trad")
        roth = st.number_input("Roth Balance", min_value=0.0, value=float(st.session_state.get("roth", DEFAULT_APP_STATE["roth"])), step=1000.0, key="roth")
        brokerage = st.number_input("Brokerage Balance", min_value=0.0, value=float(st.session_state.get("brokerage", DEFAULT_APP_STATE["brokerage"])), step=1000.0, key="brokerage")
        brokerage_basis = st.number_input(
            "Brokerage Cost Basis",
            min_value=0.0,
            value=float(st.session_state.get("brokerage_basis", DEFAULT_APP_STATE["brokerage_basis"])),
            step=1000.0,
            help="Tax basis of the current brokerage balance. Realized gains on withdrawals are based on this.",
            key="brokerage_basis",
        )
        cash = st.number_input("Cash", min_value=0.0, value=float(st.session_state.get("cash", DEFAULT_APP_STATE["cash"])), step=1000.0, key="cash")

    with col2:
        growth = st.number_input("Growth Rate (%)", min_value=0.0, value=float(st.session_state.get("growth_pct", DEFAULT_APP_STATE["growth_pct"])), step=0.1, key="growth_pct") / 100
        annual_spending = st.number_input("Base Annual Spending Need", min_value=0.0, value=float(st.session_state.get("annual_spending", DEFAULT_APP_STATE["annual_spending"])), step=1000.0, key="annual_spending")
        spending_inflation_rate = st.number_input(
            "Spending Inflation Rate (%)",
            min_value=0.0,
            value=float(st.session_state.get("spending_inflation_rate_pct", DEFAULT_APP_STATE["spending_inflation_rate_pct"])),
            step=0.1,
            help="Applied to spending each year before any retirement-smile multiplier.",
            key="spending_inflation_rate_pct",
        ) / 100
        owner_ss_base = st.number_input("Owner Annual SS at Age 67", min_value=0.0, value=float(st.session_state.get("owner_ss_base", DEFAULT_APP_STATE["owner_ss_base"])), step=1000.0, key="owner_ss_base")
        spouse_ss_base = st.number_input("Spouse Annual SS at Age 67", min_value=0.0, value=float(st.session_state.get("spouse_ss_base", DEFAULT_APP_STATE["spouse_ss_base"])), step=1000.0, key="spouse_ss_base")

    st.header("Retirement Smile Spending")
    sm1, sm2 = st.columns(2)
    with sm1:
        retirement_smile_enabled = st.checkbox(
            "Enable Retirement Smile Spending",
            value=bool(st.session_state.get("retirement_smile_enabled", DEFAULT_APP_STATE["retirement_smile_enabled"])),
            help="Uses higher spending in go-go years, lower spending in slow-go years, and higher spending again in no-go years.",
            key="retirement_smile_enabled",
        )
        go_go_end_age = st.number_input(
            "Go-Go Ends At Age",
            min_value=0,
            value=int(st.session_state.get("go_go_end_age", DEFAULT_APP_STATE["go_go_end_age"])),
            step=1,
            help="Applies to the older household member's age for the modeled year.",
            key="go_go_end_age",
        )
        slow_go_end_age = st.number_input(
            "Slow-Go Ends At Age",
            min_value=0,
            value=int(st.session_state.get("slow_go_end_age", DEFAULT_APP_STATE["slow_go_end_age"])),
            step=1,
            help="No-go spending starts at this age and later.",
            key="slow_go_end_age",
        )
    with sm2:
        go_go_multiplier = st.number_input("Go-Go Spending Multiplier", min_value=0.0, value=float(st.session_state.get("go_go_multiplier", DEFAULT_APP_STATE["go_go_multiplier"])), step=0.05, format="%.2f", key="go_go_multiplier")
        slow_go_multiplier = st.number_input("Slow-Go Spending Multiplier", min_value=0.0, value=float(st.session_state.get("slow_go_multiplier", DEFAULT_APP_STATE["slow_go_multiplier"])), step=0.05, format="%.2f", key="slow_go_multiplier")
        no_go_multiplier = st.number_input("No-Go Spending Multiplier", min_value=0.0, value=float(st.session_state.get("no_go_multiplier", DEFAULT_APP_STATE["no_go_multiplier"])), step=0.05, format="%.2f", key="no_go_multiplier")

    st.caption("Modeled spending = base spending x annual spending inflation x phase multiplier.")

    st.header("Coverage Timing")
    cov1, cov2 = st.columns(2)
    with cov1:
        primary_aca_end_year = st.number_input("Primary ACA End Year", min_value=START_YEAR, value=int(st.session_state.get("primary_aca_end_year", DEFAULT_APP_STATE["primary_aca_end_year"])), step=1, key="primary_aca_end_year")
    with cov2:
        spouse_aca_end_year = st.number_input("Spouse ACA End Year", min_value=START_YEAR, value=int(st.session_state.get("spouse_aca_end_year", DEFAULT_APP_STATE["spouse_aca_end_year"])), step=1, key="spouse_aca_end_year")

    st.header("Earned Income")
    earn1, earn2, earn3 = st.columns(3)
    with earn1:
        earned_income_annual = st.number_input("Annual Wage Income", min_value=0.0, value=float(st.session_state.get("earned_income_annual", DEFAULT_APP_STATE["earned_income_annual"])), step=1000.0, key="earned_income_annual")
    with earn2:
        earned_income_start_year = st.number_input("Wage Income Start Year", min_value=START_YEAR, value=int(st.session_state.get("earned_income_start_year", DEFAULT_APP_STATE["earned_income_start_year"])), step=1, key="earned_income_start_year")
    with earn3:
        earned_income_end_year = st.number_input("Wage Income End Year", min_value=START_YEAR, value=int(st.session_state.get("earned_income_end_year", DEFAULT_APP_STATE["earned_income_end_year"])), step=1, key="earned_income_end_year")

    st.header("Tax Funding Policy")
    conversion_tax_funding_policy = st.selectbox(
        "Preferred tax funding source",
        [
            "Cash then Brokerage",
            "Brokerage only",
            "Cash only",
            "Cash then Brokerage then Trad then Roth",
        ],
        index=0,
        key="conversion_tax_funding_policy",
    )
    st.caption("This build falls back to Trad then Roth if the preferred source is insufficient.")

    inputs = {
        "trad": trad,
        "roth": roth,
        "brokerage": brokerage,
        "brokerage_basis": min(brokerage_basis, brokerage),
        "cash": cash,
        "growth": growth,
        "annual_spending": annual_spending,
        "spending_inflation_rate": spending_inflation_rate,
        "retirement_smile_enabled": retirement_smile_enabled,
        "go_go_end_age": go_go_end_age,
        "slow_go_end_age": slow_go_end_age,
        "go_go_multiplier": go_go_multiplier,
        "slow_go_multiplier": slow_go_multiplier,
        "no_go_multiplier": no_go_multiplier,
        "annual_conversion": float(st.session_state.get("annual_conversion", DEFAULT_APP_STATE["annual_conversion"])),
        "conversion_tax_funding_policy": conversion_tax_funding_policy,
        "owner_current_age": owner_current_age,
        "spouse_current_age": spouse_current_age,
        "owner_claim_age": owner_claim_age,
        "spouse_claim_age": spouse_claim_age,
        "owner_ss_base": owner_ss_base,
        "spouse_ss_base": spouse_ss_base,
        "earned_income_annual": earned_income_annual,
        "earned_income_start_year": earned_income_start_year,
        "earned_income_end_year": earned_income_end_year,
        "primary_aca_end_year": primary_aca_end_year,
        "spouse_aca_end_year": spouse_aca_end_year,
        "preference_maximize_social_security": bool(st.session_state.get("preference_maximize_social_security", DEFAULT_APP_STATE["preference_maximize_social_security"])),
        "preference_minimize_trad_ira_for_heirs": bool(st.session_state.get("preference_minimize_trad_ira_for_heirs", DEFAULT_APP_STATE["preference_minimize_trad_ira_for_heirs"])),
        "preference_income_stability_focus": bool(st.session_state.get("preference_income_stability_focus", DEFAULT_APP_STATE["preference_income_stability_focus"])),
    }
    return inputs


def render_conversion_page() -> None:
    ensure_default_state()
    st.title("Break-Even Governor")
    active_stage = get_conversion_workflow_stage()
    selected_strategy = st.session_state.get("selected_recommendation_strategy")
    selected_source = st.session_state.get("selected_recommendation_source")
    selected_profile = st.session_state.get("selected_recommendation_profile")
    preset_note = st.session_state.get("break_even_governor_preset_note")
    if selected_strategy:
        source_text = "" if not selected_source else f" from {str(selected_source).replace('_', ' ')}"
        profile_text = "" if not selected_profile else f" under the {selected_profile} planning profile"
        st.info(f"Using Social Security claim ages {selected_strategy}{source_text}{profile_text}. You can adjust them below before running the Break-Even Governor.")
    if preset_note:
        st.caption(preset_note)
    with st.container(border=True):
        st.subheader("Step 1: Scenario")
        st.caption("Start here to load, save, or reset the scenario before changing planning assumptions.")
        render_top_nav("conversion")
        render_next_back(1, 2)

    with st.container(border=True):
        st.subheader("Step 2: Household Setup")
        st.caption("Enter household facts, retirement smile assumptions, ACA timing, earned income, and tax funding policy.")
        inputs = render_shared_household_inputs()
        household_ready = household_inputs_complete(inputs)
        if not household_ready:
            st.warning("Complete basic ages, starting balances, and Social Security inputs to unlock the next step.")
        render_next_back(2, 3 if household_ready else 2)

    unlocked_stage = 2
    if household_ready:
        unlocked_stage = 3
    render_conversion_workflow_nav(active_stage, unlocked_stage if unlocked_stage >= active_stage else active_stage)

    with st.container(border=True):
        st.subheader("Step 3: Break-Even Governor Inputs")
        st.caption("These controls affect the conversion engine and therefore the underlying scenario facts. Changing them requires a rerun of the optimizer.")

    current_max_conversion_value = sanitize_governor_max_conversion(st.session_state.get("max_conversion", DEFAULT_APP_STATE["max_conversion"]))
    if float(current_max_conversion_value) != float(st.session_state.get("max_conversion", current_max_conversion_value)):
        st.session_state["max_conversion"] = float(current_max_conversion_value)
    max_conversion = st.number_input("Max Annual Conversion To Test", min_value=0.0, value=float(current_max_conversion_value), step=5000.0, key="max_conversion")
    current_step_size_value = sanitize_governor_step_size(st.session_state.get("step_size", DEFAULT_APP_STATE["step_size"]))
    if float(current_step_size_value) != float(st.session_state.get("step_size", current_step_size_value)):
        st.session_state["step_size"] = float(current_step_size_value)
    step_size = st.number_input(
        "Break-Even Step Size",
        min_value=1000.0,
        value=float(current_step_size_value),
        step=1000.0,
        help="Smaller steps improve accuracy but run slower. The governor will not use a step size below $1,000.",
        key="step_size",
    )

    pol1, pol2 = st.columns(2)
    with pol1:
        cash_sweep_threshold = st.number_input(
            "Cash Sweep Threshold",
            min_value=0.0,
            value=float(st.session_state.get("cash_sweep_threshold", DEFAULT_APP_STATE["cash_sweep_threshold"])),
            step=5000.0,
            help="End-of-year cash above this amount is swept into brokerage.",
            key="cash_sweep_threshold",
        )
    with pol2:
        current_state_tax_pct = float(st.session_state.get("state_tax_rate", DEFAULT_APP_STATE["state_tax_rate"])) * 100.0
        existing_state_tax_display = st.session_state.get("state_tax_rate_pct_display", f"{current_state_tax_pct:.2f}%")
        if not isinstance(existing_state_tax_display, str):
            existing_state_tax_display = f"{float(existing_state_tax_display):.2f}%"
            st.session_state["state_tax_rate_pct_display"] = existing_state_tax_display
        state_tax_display_value = st.text_input(
            "State Tax Rate",
            value=f"{current_state_tax_pct:.2f}%",
            help="Enter the state tax rate as a percent, for example 4.75%.",
            key="state_tax_rate_pct_display",
        )
        cleaned_state_tax_display_value = str(state_tax_display_value).strip().replace("%", "")
        try:
            state_tax_rate_pct = max(0.0, min(20.0, float(cleaned_state_tax_display_value)))
        except Exception:
            state_tax_rate_pct = current_state_tax_pct
        state_tax_rate = state_tax_rate_pct / 100.0
        st.session_state["state_tax_rate"] = state_tax_rate
        normalized_state_tax_display = f"{state_tax_rate_pct:.2f}%"
        if st.session_state.get("state_tax_rate_pct_display") != normalized_state_tax_display:
            st.session_state["state_tax_rate_pct_display"] = normalized_state_tax_display

    tg1, tg2 = st.columns(2)
    with tg1:
        target_trad_balance_enabled = st.checkbox(
            "Use Target Traditional IRA Balance Goal",
            value=bool(st.session_state.get("target_trad_balance_enabled", DEFAULT_APP_STATE["target_trad_balance_enabled"])),
            help="When enabled, pre-RMD non-ACA years can push conversions above pure BETR minimums to work toward a target Traditional IRA balance by household RMD start.",
            key="target_trad_balance_enabled",
        )
    with tg2:
        target_trad_balance = st.number_input(
            "Target Traditional IRA Balance By RMD Start",
            min_value=0.0,
            value=float(st.session_state.get("target_trad_balance", DEFAULT_APP_STATE["target_trad_balance"])),
            step=25000.0,
            help="Planner goal for remaining Traditional IRA balance by household RMD start.",
            key="target_trad_balance",
        )

    ov1, ov2 = st.columns(2)
    with ov1:
        target_trad_override_enabled = st.checkbox(
            "Allow Target Traditional IRA Planner Override",
            value=bool(st.session_state.get("target_trad_override_enabled", DEFAULT_APP_STATE["target_trad_override_enabled"])),
            help="When enabled, pre-RMD non-ACA years may exceed pure BETR stopping as long as current adjusted cost stays under the planner cap.",
            key="target_trad_override_enabled",
        )
    with ov2:
        current_override_pct = float(st.session_state.get("target_trad_override_max_rate", DEFAULT_APP_STATE["target_trad_override_max_rate"])) * 100.0
        existing_override_display = st.session_state.get("target_trad_override_max_rate_pct_display", f"{current_override_pct:.0f}%")
        if not isinstance(existing_override_display, str):
            existing_override_display = f"{float(existing_override_display):.0f}%"
            st.session_state["target_trad_override_max_rate_pct_display"] = existing_override_display
        display_value = st.text_input(
            "Target Traditional IRA Override Max All-In Rate",
            value=f"{current_override_pct:.0f}%",
            help="Maximum adjusted current cost rate allowed for target-Traditional-IRA override. Enter a whole-number percent like 32%.",
            key="target_trad_override_max_rate_pct_display",
        )
        cleaned_display_value = str(display_value).strip().replace("%", "")
        try:
            target_trad_override_max_rate_pct = max(0.0, min(100.0, float(cleaned_display_value)))
        except Exception:
            target_trad_override_max_rate_pct = current_override_pct
        target_trad_override_max_rate = target_trad_override_max_rate_pct / 100.0
        st.session_state["target_trad_override_max_rate"] = target_trad_override_max_rate
        normalized_display = f"{target_trad_override_max_rate_pct:.0f}%"
        if st.session_state.get("target_trad_override_max_rate_pct_display") != normalized_display:
            st.session_state["target_trad_override_max_rate_pct_display"] = normalized_display

    br1, br2 = st.columns(2)
    with br1:
        post_aca_target_bracket = st.selectbox(
            "Post-ACA Target Bracket",
            ["12%", "22%", "24%"],
            index=["12%", "22%", "24%"].index(st.session_state.get("post_aca_target_bracket", DEFAULT_APP_STATE["post_aca_target_bracket"])),
            help="Used in non-ACA years before household RMDs begin.",
            key="post_aca_target_bracket",
        )
    with br2:
        rmd_era_target_bracket = st.selectbox(
            "RMD-Era Target Bracket",
            ["12%", "22%", "24%"],
            index=["12%", "22%", "24%"].index(st.session_state.get("rmd_era_target_bracket", DEFAULT_APP_STATE["rmd_era_target_bracket"])),
            help="Used once the household reaches the first RMD year.",
            key="rmd_era_target_bracket",
        )
    governor_ready = governor_inputs_complete(max_conversion, step_size)
    if not governor_ready:
        st.warning("Set a positive max conversion and a valid step size to unlock planning preferences and optimizer actions.")
    render_next_back(3, 4 if governor_ready else 3)

    with st.container(border=True):
        st.subheader("Step 4: Planning Preferences")
        st.caption("Profiles set the base ranking philosophy. Modifiers nudge that philosophy without changing the underlying 81-combination fact set.")

    st.header("Recommendation Engine v1")
    planning_profile = st.selectbox(
        "Optimize For",
        list(PROFILE_PRESETS.keys()),
        index=list(PROFILE_PRESETS.keys()).index(st.session_state.get("planning_profile", DEFAULT_APP_STATE.get("planning_profile", "Balanced"))),
        help="Choose the planning lens the recommendation engine should use when ranking quick Social Security strategies.",
        key="planning_profile",
    )
    profile_summary = get_profile_summary(planning_profile)
    st.info(
        f"{profile_summary['description']}\n\n"
        f"This means the model will: \n- {profile_summary['bullets'][0]}\n- {profile_summary['bullets'][1]}\n- {profile_summary['bullets'][2]}\n\n"
        f"Tradeoff to expect: {profile_summary['tradeoff']}"
    )

    st.caption("Optional preference modifiers let you tilt any base profile without changing the underlying profile definitions.")
    pref1, pref2, pref3 = st.columns(3)
    with pref1:
        st.checkbox("Maximize Social Security", key="preference_maximize_social_security", help="Adds extra scoring credit for higher present-value Social Security income while keeping your base profile intact.")
    with pref2:
        st.checkbox("Minimize Traditional IRA for heirs", key="preference_minimize_trad_ira_for_heirs", help="Adds extra scoring penalty for larger Traditional IRA balances, Trad share, and heir tax drag.")
    with pref3:
        st.checkbox("Income stability focus", key="preference_income_stability_focus", help="Adds extra credit for higher guaranteed income and steadier late-life funding support.")
    current_preferences = extract_scoring_preferences(st.session_state)
    st.caption(f"Active preference modifiers: {describe_active_scoring_preferences(current_preferences)}")
    selection_summary = build_strategy_selection_summary(planning_profile, current_preferences)
    with st.container(border=True):
        st.markdown(f"**{selection_summary['title']}**")
        st.caption("Base profile tendencies")
        for item in selection_summary["defaults"]:
            st.write(f"- {item}")
        st.caption("Active modifier nudges")
        for item in selection_summary["modifiers"]:
            st.write(f"- {item}")
        st.caption("How this combination behaves")
        for item in selection_summary["notes"]:
            st.write(f"- {item}")
    render_next_back(4, 5 if governor_ready else 4)

    rec_col1, rec_col2 = st.columns([1, 2])
    with rec_col1:
        if st.button("Run Quick Strategy Recommendation", use_container_width=True):
            with st.spinner("Running quick strategy recommendation..."):
                recommendation_result = run_quick_strategy_recommendation(
                    inputs=inputs,
                    max_conversion=max_conversion,
                    step_size=step_size,
                    profile_name=planning_profile,
                )
            quick_hash_inputs, _ = build_profile_adjusted_inputs(planning_profile, inputs)
            quick_hash_inputs.update({"max_conversion": max_conversion, "step_size": step_size, "planning_profile": planning_profile})
            st.session_state["quick_strategy_recommendation_result"] = tag_result_payload(recommendation_result, engine="quick_strategy_recommendation", inputs=quick_hash_inputs)
            mark_result_state("quick_strategy_recommendation", quick_hash_inputs)
    with rec_col2:
        st.caption("Quick Strategy Mode compares 62/62, 67/67, 70/70, 70/67, and 67/70. Use it for a fast advisor-style recommendation, then open the Break-Even Governor around the winner with a small nearby set if needed.")

    quick_result = get_current_result_payload("quick_strategy_recommendation_result")
    if quick_result is not None:
        quick_inputs_snapshot, _ = build_profile_adjusted_inputs(planning_profile, inputs)
        quick_inputs_snapshot.update({"max_conversion": max_conversion, "step_size": step_size, "planning_profile": planning_profile})
        if should_suppress_quick_recommendation_stale_warning(quick_inputs_snapshot):
            st.caption("Showing the previously generated quick recommendation snapshot while you review the selected Break-Even Governor setup.")
            st.session_state["suppress_quick_recommendation_stale_once"] = False
        else:
            render_stale_warning("quick_strategy_recommendation", quick_inputs_snapshot, "Quick recommendation results")
        st.subheader("Strategy Summary")
        st.caption("These strategy rows are produced by the same Break-Even Governor engine used below, with the selected planning-profile presets applied before the quick run. If this app version changes, cached strategy summaries are automatically discarded and must be rerun.")
        if quick_result.get("applied_preset_note"):
            st.caption(quick_result["applied_preset_note"])
        st.caption(f"Preference modifiers used: {quick_result.get('active_preferences_text', 'None')}")
        st.dataframe(
            quick_result["summary_df"].style.format({
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
        if quick_result.get("close_result"):
            st.info(
                "Top strategies produce very similar outcomes here. This is less about a single mathematically obvious winner and more about preference: earlier income now versus stronger long-term guarantees and balance-sheet structure later."
            )
        st.download_button(
            "Download Strategy Summary (CSV)",
            data=quick_result["summary_df"].to_csv(index=False),
            file_name="quick_strategy_summary.csv",
            mime="text/csv",
            use_container_width=True,
        )
        st.subheader("Advisor Interpretation")
        st.write(quick_result["advisor_text"])
        guidance = quick_result.get("next_step_guidance", [])
        if guidance:
            st.subheader("Recommended Next Steps")
            for item in guidance:
                st.write(f"- {item}")
        top_ranked_rows = quick_result.get("ranked_rows", [])
        if top_ranked_rows:
            top_strategy = quick_result.get("top_ranked_rows", quick_result.get("ranked_rows", []))[0]
            st.caption("Quick Strategy and Optimizer picks can differ because they come from different ranking layers. Use the button below to load this quick recommendation into the Governor.")
            st.button(
                f"Apply Quick Strategy Winner {top_strategy['Strategy']} to Governor",
                on_click=launch_conversion_optimizer_from_strategy,
                args=(int(top_strategy["Owner SS Age"]), int(top_strategy["Spouse SS Age"]), "quick_recommendation", planning_profile),
                use_container_width=True,
            )

    st.header("Integrity / Speed")
    integrity_mode = st.checkbox(
        "Enable Integrity Mode",
        value=bool(st.session_state.get("integrity_mode", DEFAULT_APP_STATE["integrity_mode"])),
        help="When enabled, the app runs slower but adds repeatability and accounting checks. Leave this off for faster day-to-day use.",
        key="integrity_mode",
    )
    validation_tolerance = st.number_input(
        "Validation Tolerance ($)",
        min_value=0.0,
        value=float(st.session_state.get("validation_tolerance", DEFAULT_APP_STATE["validation_tolerance"])),
        step=0.01,
        format="%.2f",
        help="Used only when Integrity Mode is enabled.",
        key="validation_tolerance",
        disabled=not integrity_mode,
    )

    st.header("Social Security Optimizer Workflow")
    st.info(
        "Step 1: Set your ranking preferences above (profile + optional modifiers) if you want the profile shortlists to reflect them.\n\n"
        "Step 2: Run the SS Optimizer to generate the 81 raw SS combinations. The engine itself is profile-neutral and computes facts only.\n\n"
        "Step 3: Review results. Top 10 shows the raw optimizer ranking. Top 5 by planning profile shows those same 81 rows rescored by profile and modifiers.\n\n"
        "If you later change only profile/modifier preferences, use 'Re-rank Existing 81 Results' instead of rerunning the full engine."
    )
    with st.container(border=True):
        st.subheader("Step 5: Social Security Optimizer")
        st.caption("Run the 81-combination strategy universe once, then rerank it as needed when only the profile or modifiers change.")

    ss_opt1, ss_opt2 = st.columns(2)
    with ss_opt1:
        st.caption("The optimizer engine is always profile-neutral. Profiles and modifiers only affect ranking after the 81 combinations are generated.")
    with ss_opt2:
        trad_balance_penalty_lambda = st.number_input(
            "SS Optimizer Traditional IRA Penalty Lambda",
            min_value=0.0,
            value=float(st.session_state.get("trad_balance_penalty_lambda", DEFAULT_APP_STATE["trad_balance_penalty_lambda"])),
            step=0.05,
            format="%.2f",
            help="Used only for the raw Top 10 ranking: Score = Final Net Worth - lambda x Ending Traditional IRA Balance. Example: lambda 1.00 applies a $1,000,000 score penalty for each $1,000,000 of ending Traditional IRA.",
            key="trad_balance_penalty_lambda",
        )

    inputs.update(
        {
            "cash_sweep_threshold": cash_sweep_threshold,
            "state_tax_rate": state_tax_rate,
            "target_trad_balance_enabled": target_trad_balance_enabled,
            "target_trad_balance": target_trad_balance,
            "target_trad_override_enabled": target_trad_override_enabled,
            "target_trad_override_max_rate": target_trad_override_max_rate,
            "post_aca_target_bracket": post_aca_target_bracket,
            "rmd_era_target_bracket": rmd_era_target_bracket,
        }
    )

    if "annual_calc_year" in st.session_state:
        st.info(
            f"Current annual calculator snapshot in session: year {int(st.session_state['annual_calc_year'])}, filing status {st.session_state.get('annual_calc_filing_status', 'MFJ')}, earned income ${float(st.session_state.get('annual_calc_earned_income', 0.0)):,.0f}, other ordinary income ${float(st.session_state.get('annual_calc_other_income', 0.0)):,.0f}, LTCG ${float(st.session_state.get('annual_calc_ltcg', 0.0)):,.0f}, SS ${float(st.session_state.get('annual_calc_total_ss', 0.0)):,.0f}."
        )

    render_optimizer_status_panel(inputs, max_conversion, step_size, trad_balance_penalty_lambda, planning_profile, current_preferences)

    total_combos = get_ss_optimizer_combo_count()
    if st.session_state.get("ss_optimizer_running"):
        st.session_state["ss_optimizer_running"] = False
    partial_results = list(st.session_state.get("ss_optimizer_partial_results", []))
    progress_index = int(st.session_state.get("ss_optimizer_progress_index", 0))
    last_completed = st.session_state.get("ss_optimizer_last_completed")
    partial_available = 0 < progress_index < total_combos and len(partial_results) > 0
    if partial_available:
        last_label = f"{last_completed[0]}/{last_completed[1]}" if isinstance(last_completed, tuple) else "none"
        st.warning(f"Optimizer progress saved: {progress_index}/{total_combos} completed. Last completed SS pair: {last_label}. Resume to finish the full run.")
    optimizer_error = st.session_state.get("ss_optimizer_error")
    if optimizer_error:
        st.error(optimizer_error)
    b1, b2, b3, b4 = st.columns(4)
    with b1:
        if st.button("Run All SS Strategies", disabled=False, use_container_width=True):
            clear_ss_optimizer_state(clear_last_result=True)
            optimizer_result = run_ss_optimizer(
                inputs=inputs,
                max_conversion=max_conversion,
                step_size=step_size,
                trad_balance_penalty_lambda=trad_balance_penalty_lambda,
                integrity_mode=integrity_mode,
                validation_tolerance=validation_tolerance,
                start_index=0,
                existing_results=[],
            )
            optimizer_result["scoring_preferences_snapshot"] = copy.deepcopy(extract_scoring_preferences(st.session_state))
            optimizer_result["planning_profile_snapshot"] = planning_profile
            optimizer_hash_inputs = {**copy.deepcopy(inputs), "max_conversion": max_conversion, "step_size": step_size, "trad_balance_penalty_lambda": trad_balance_penalty_lambda, "optimizer_is_profile_neutral": True}
            st.session_state["ss_optimizer_last_result"] = tag_result_payload(optimizer_result, engine="ss_optimizer", inputs=optimizer_hash_inputs)
            if optimizer_result.get("completed", False):
                mark_result_state("ss_optimizer", optimizer_hash_inputs)
            st.rerun()
    with b2:
        resume_disabled = not partial_available
        if st.button("Resume SS Optimizer", disabled=resume_disabled, use_container_width=True):
            optimizer_result = run_ss_optimizer(
                inputs=inputs,
                max_conversion=max_conversion,
                step_size=step_size,
                trad_balance_penalty_lambda=trad_balance_penalty_lambda,
                integrity_mode=integrity_mode,
                validation_tolerance=validation_tolerance,
                start_index=progress_index,
                existing_results=partial_results,
                profile_name=planning_profile,
            )
            optimizer_result["scoring_preferences_snapshot"] = copy.deepcopy(extract_scoring_preferences(st.session_state))
            optimizer_result["planning_profile_snapshot"] = planning_profile
            optimizer_hash_inputs = {**copy.deepcopy(inputs), "max_conversion": max_conversion, "step_size": step_size, "trad_balance_penalty_lambda": trad_balance_penalty_lambda, "optimizer_is_profile_neutral": True}
            st.session_state["ss_optimizer_last_result"] = tag_result_payload(optimizer_result, engine="ss_optimizer", inputs=optimizer_hash_inputs)
            if optimizer_result.get("completed", False):
                mark_result_state("ss_optimizer", optimizer_hash_inputs)
            st.rerun()
    with b3:
        last_result_for_rerank = get_current_result_payload("ss_optimizer_last_result")
        rerank_disabled = last_result_for_rerank is None or not last_result_for_rerank.get("completed", False)
        if st.button("Re-rank Existing 81 Results", disabled=rerank_disabled, use_container_width=True):
            reranked = rerank_existing_optimizer_result(
                last_result_for_rerank,
                preferences=extract_scoring_preferences(st.session_state),
            )
            reranked["planning_profile_snapshot"] = planning_profile
            optimizer_hash_inputs = {**copy.deepcopy(inputs), "max_conversion": max_conversion, "step_size": step_size, "trad_balance_penalty_lambda": trad_balance_penalty_lambda, "optimizer_is_profile_neutral": True}
            st.session_state["ss_optimizer_last_result"] = tag_result_payload(reranked, engine="ss_optimizer", inputs=optimizer_hash_inputs)
            st.rerun()
    with b4:
        reset_disabled = not partial_available and st.session_state.get("ss_optimizer_last_result") is None
        if st.button("Reset SS Optimizer Progress", disabled=reset_disabled, use_container_width=True):
            clear_ss_optimizer_state(clear_last_result=True)
            st.rerun()

    last_result = get_current_result_payload("ss_optimizer_last_result")
    if last_result is not None:
        if last_result.get("completed", False):
            st.caption(
                f"Current shortlist profile: {last_result.get('planning_profile_snapshot', planning_profile)} | "
                f"Current modifiers at last scoring snapshot: {describe_active_scoring_preferences(last_result.get('scoring_preferences_snapshot', {}))}"
            )
        render_ss_optimizer_results(last_result)
    render_next_back(5, 6 if get_current_result_payload("ss_optimizer_last_result") is not None else 5)

    with st.container(border=True):
        st.subheader("Step 6: Strategy Execution and Results")
        st.caption("Use the Break-Even Governor or Flat Strategy below to inspect any chosen strategy in detail. These sections remain visible even when results are stale.")

    st.header("Strategy Execution")
    st.caption("These sections stay visible so you can always run or review the Break-Even Governor and Flat Strategy without hiding the optimizer.")

    flat_annual_conversion = st.number_input(
        "Flat Annual Conversion",
        min_value=0.0,
        value=float(st.session_state.get("annual_conversion", DEFAULT_APP_STATE["annual_conversion"])),
        step=5000.0,
        key="annual_conversion",
        help="Used only by the flat strategy runner below. The Break-Even Governor ignores this field.",
    )

    btn1, btn2 = st.columns(2)

    with btn1:
        st.subheader("Flat Strategy")
        if st.button("Run Flat Strategy Test"):
            flat_inputs = dict(inputs)
            flat_inputs["annual_conversion"] = float(flat_annual_conversion)
            result = run_model_fixed(flat_inputs)
            result["scenario_fingerprint"] = build_scenario_fingerprint(flat_inputs)
            st.session_state["flat_strategy_last_result"] = tag_result_payload(result, engine="flat_strategy", inputs=flat_inputs)
            mark_result_state("flat_strategy", flat_inputs)
        flat_result = get_current_result_payload("flat_strategy_last_result")
        if flat_result is not None:
            flat_inputs = dict(inputs)
            flat_inputs["annual_conversion"] = float(flat_annual_conversion)
            render_stale_warning("flat_strategy", flat_inputs, "Flat strategy results")
            render_summary("Flat Strategy Summary", flat_result)
            st.subheader("Flat Strategy Yearly Results")
            st.dataframe(flat_result["df"], use_container_width=True)

    with btn2:
        st.subheader("Break-Even Governor")
        st.caption("Use any 'Apply ... to Governor' button above to load Social Security claim ages here, then run the Governor.")
        if st.button("Run Break-Even Governor"):
            result = run_governor_with_validation(
                inputs=inputs,
                max_conversion=max_conversion,
                step_size=step_size,
                integrity_mode=integrity_mode,
                tol=validation_tolerance,
            )
            st.session_state["break_even_last_result"] = tag_result_payload(result, engine="break_even_governor", inputs={**inputs, "max_conversion": max_conversion, "step_size": step_size})
            mark_result_state("break_even", {**inputs, "max_conversion": max_conversion, "step_size": step_size})
        result = get_current_result_payload("break_even_last_result")
        if result is not None:
            render_stale_warning("break_even", {**inputs, "max_conversion": max_conversion, "step_size": step_size}, "Break-even governor results")
            render_summary("Break-Even Governor Summary", result)
            st.subheader("Chosen Year-by-Year Path")
            path_display_df = build_chosen_path_display_df(result["df"])
            st.caption("Chosen Conversion ($) is intended to be the actual dollar conversion for that year. Binding Constraint shows what limited the decision (for example ACA). Target Bracket (%) shows the bracket target the governor was aiming under when applicable.")
            with st.expander("Debug: First Year Raw Data", expanded=False):
                try:
                    if result.get("df") is not None and not result["df"].empty:
                        first_row_raw = result["df"].iloc[0].to_dict()
                        st.json(first_row_raw)
                except Exception as debug_exc:
                    st.write(f"Debug panel unavailable: {debug_exc}")
            if path_display_df is not None and not path_display_df.empty:
                fmt = {}
                non_currency_cols = {"Year", "Binding Constraint", "Target Bracket (%)"}
                for col in path_display_df.columns:
                    if col in non_currency_cols:
                        continue
                    series = path_display_df[col]
                    if not pd.api.types.is_numeric_dtype(series):
                        continue
                    if "Rate" in col:
                        fmt[col] = "{:.2%}"
                    else:
                        fmt[col] = "${:,.0f}"
                st.dataframe(path_display_df.style.format(fmt), use_container_width=True)
            st.download_button(
                "Download Chosen Path (CSV)",
                data=result["df"].to_csv(index=False),
                file_name="break_even_governor_chosen_path.csv",
                mime="text/csv",
                use_container_width=True,
            )
            st.subheader("Year-by-Year Decision Diagnostics")
            st.dataframe(result["decision_df"], use_container_width=True)
            st.download_button(
                "Download Decision Diagnostics (CSV)",
                data=result["decision_df"].to_csv(index=False),
                file_name="break_even_governor_decisions.csv",
                mime="text/csv",
                use_container_width=True,
            )


def main() -> None:
    ensure_default_state()
    current_page = get_app_page()
    if current_page == "home":
        render_home_page()
    elif current_page == "annual":
        render_annual_page()
    else:
        render_conversion_page()


if __name__ == "__main__":
    main()
