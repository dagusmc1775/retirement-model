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
IRMAA_FIRST_CLIFF_MFJ = 218000.0

# -----------------------------
# YEARLY TABLES
# Update these over time.
# If a specific year is missing, the model uses the latest prior year available.
# -----------------------------

STANDARD_DEDUCTION_BY_YEAR = {
    2026: 30000.0,
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


def run_governor_with_validation(inputs: dict, max_conversion: float, step_size: float, strict_repeatability_check: bool = False, tol: float = 0.01) -> dict:
    result = run_model_break_even_governor(inputs, max_conversion, step_size)
    result["scenario_fingerprint"] = build_scenario_fingerprint(inputs, max_conversion=max_conversion, step_size=step_size)

    if strict_repeatability_check:
        rerun = run_model_break_even_governor(copy.deepcopy(inputs), max_conversion, step_size)
        rerun["scenario_fingerprint"] = build_scenario_fingerprint(inputs, max_conversion=max_conversion, step_size=step_size)
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


def enrich_year_row_for_display(year: int, state_before: dict, params: dict, row: dict) -> dict:
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
    baseline_path = run_projection_from_state(
        year,
        dict(state_before),
        params,
        first_year_conversion=0.0,
        later_year_conversion=0.0,
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

    accounting_issues = validate_row_accounting(row, year)
    row["Accounting Status"] = "PASS" if not accounting_issues else "FAIL"
    row["Accounting Issues"] = " | ".join(accounting_issues)

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
def run_projection_from_state(start_year: int, starting_state: dict, params: dict, first_year_conversion: float = 0.0, later_year_conversion: float = 0.0) -> dict:
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
    Soft planning overlay: estimate the annual pre-RMD conversion needed to work toward a target
    Traditional IRA balance by household RMD start. This is intentionally simple and conservative:
    it spreads the current excess Trad balance over the remaining pre-RMD years.
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
    # Round to model step size and cap.
    rounded = floor_to_step(annual_needed, step_size)
    return max(0.0, min(float(cap), float(rounded)))



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


def find_optimal_conversion_for_year(year: int, state: dict, params: dict, max_conversion: float, step_size: float) -> tuple:
    cap = get_year_conversion_cap(state, params, max_conversion)
    step_size = max(1.0, float(step_size))
    coverage = get_coverage_status(year, int(params["primary_aca_end_year"]), int(params["spouse_aca_end_year"]))

    if cap <= 0.0:
        zero_path = run_projection_from_state(year, state, params, first_year_conversion=0.0, later_year_conversion=0.0)
        zero_row = zero_path["df"].iloc[0].to_dict()
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
        total_state_taxes = float(path["df"]["State Tax"].sum()) if "State Tax" in path["df"].columns else 0.0
        return {
            "federal": float(path["total_federal_taxes"]) - float(first_row.get("Federal Tax", 0.0)),
            "state": total_state_taxes - float(first_row.get("State Tax", 0.0)),
            "aca": float(path["total_aca_cost"]) - float(first_row.get("ACA Cost", 0.0)),
            "irmaa": float(path["total_irmaa_cost"]) - float(first_row.get("IRMAA Cost", 0.0)),
        }

    # ACA years: maximize conversion under ACA limit, but also expose true incremental economics
    if coverage["aca_lives"] > 0:
        aca_limit = get_aca_magi_limit(year, coverage["aca_lives"])
        tested_rows = []
        baseline_path = run_projection_from_state(year, state, params, first_year_conversion=0.0, later_year_conversion=0.0)
        baseline_row = baseline_path["df"].iloc[0].to_dict()
        baseline_magi = float(baseline_row["MAGI"])
        selected_conversion = 0.0
        selected_row = baseline_row

        prev = None
        step_index = 0
        while True:
            current_conversion = min(cap, step_index * step_size)
            if current_conversion > cap + 0.01:
                break

            path = run_projection_from_state(year, state, params, first_year_conversion=current_conversion, later_year_conversion=0.0)
            row = path["df"].iloc[0].to_dict()
            within_limit = bool(float(row["MAGI"]) <= aca_limit + 0.01)
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

            tested_rows.append({
                "Year": year,
                "Decision Mode": "ACA Headroom",
                "Step Index": int(step_index),
                "Base Conversion": float(prev["conversion"]) if prev is not None else 0.0,
                "Test Conversion": float(current_conversion),
                "Step Amount": float(current_conversion - (prev["conversion"] if prev is not None else 0.0)),
                "Baseline MAGI (0 Conv)": baseline_magi,
                "ACA MAGI Limit": float(aca_limit),
                "MAGI Headroom Before Conversion": float(max(0.0, aca_limit - baseline_magi)),
                "Test MAGI": float(row["MAGI"]),
                "MAGI Remaining To Limit": float(aca_limit - float(row["MAGI"])),
                "Within ACA Limit": bool(within_limit and not roth_tax_used),
                "Current Marginal Incremental Cost Rate": float(current_effective),
                "Projected Future Avoided Rate": float(future_effective),
                "Net Benefit Rate": float(net_benefit_rate),
                "Tax Funding Source": " + ".join(tax_sources) if tax_sources else "none",
                "Tax Funding Penalty": float(tax_source_penalty),
                "Current Marginal Tax Rate": float(row.get("Current Marginal Tax Rate", 0.0)),
                "Estimated Future Marginal Rate": float("nan"),
                "Effective Current Rate (Adjusted)": float(adjusted_current_effective_rate(current_effective, tax_source_penalty)),
                "Roth Used For Tax Payment": bool(roth_tax_used),
                "Current Year Federal Tax Delta": float(current_fed_delta),
                "Current Year ACA Delta": float(current_aca_delta),
                "Current Year IRMAA Delta": float(current_irmaa_delta),
                "Current Marginal Cost": float(current_fed_delta + current_aca_delta + current_irmaa_delta),
            "Baseline Total Tax": float(baseline_total_tax),
            "Test Total Tax": float(test_total_tax),
            "Delta Total Tax": float(delta_total_tax),
                "Baseline Total Tax": float(baseline_total_tax),
                "Test Total Tax": float(test_total_tax),
                "Delta Total Tax": float(delta_total_tax),
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
            })
            if within_limit and not roth_tax_used:
                selected_conversion = float(current_conversion)
                selected_row = row
            else:
                break

            prev = {"conversion": current_conversion, "path": path, "row": row}
            if current_conversion >= cap - 0.01:
                break
            step_index += 1

        diag_df = pd.DataFrame(tested_rows)
        if not diag_df.empty:
            diag_df["Selected Conversion After Test"] = selected_conversion
            diag_df["Selected MAGI"] = float(selected_row["MAGI"])
            diag_df["ACA Solver Note"] = "ACA years use highest tested conversion that stays within ACA MAGI limit"
        return round(selected_conversion, 2), selected_row, diag_df

    # Non-ACA years: use true incremental BETR math, subject to target bracket and tax-funding guardrails.
    target_label = params["post_aca_target_bracket"] if year < int(params["household_rmd_start"]) else params["rmd_era_target_bracket"]
    target_top = get_target_bracket_top(year, target_label)
    baseline_path = run_projection_from_state(year, state, params, first_year_conversion=0.0, later_year_conversion=0.0)
    baseline_row = baseline_path["df"].iloc[0].to_dict()
    baseline_ordinary_taxable = float(baseline_row.get("Ordinary Taxable Income", 0.0))
    target_headroom = max(0.0, float(target_top) - baseline_ordinary_taxable)
    future_rate_info = estimate_future_marginal_rate(year, state, params)
    future_rate = float(future_rate_info["estimated_future_marginal_rate"])

    max_test = min(cap, floor_to_step(target_headroom, step_size))
    target_pressure_conversion = estimate_target_trad_pressure_conversion(year, state, params, step_size, max_test)
    tested_rows = []
    selected_conversion = 0.0
    selected_row = baseline_row
    selected_net_benefit = float("-inf")
    highest_guardrail_conversion = 0.0
    highest_guardrail_row = baseline_row
    highest_override_conversion = 0.0
    highest_override_row = baseline_row
    prev = None

    step_index = 0
    while True:
        current_conversion = min(max_test, step_index * step_size)
        if current_conversion > max_test + 0.01:
            break

        path = run_projection_from_state(year, state, params, first_year_conversion=current_conversion, later_year_conversion=0.0)
        row = path["df"].iloc[0].to_dict()
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
        })

        # Winner selection:
        # - highest_guardrail_conversion tracks pure BETR-valid rows
        # - highest_override_conversion tracks rows that stay under the planner override cap,
        #   even if pure BETR already says stop
        base_override_eligible = bool((not roth_tax_used))

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

    # Optional planning overlay: if the Trad target goal implies a higher pre-RMD conversion,
    # allow a larger conversion under either pure BETR guardrails or planner override cap.
    selection_mode = "BETR"
    if float(target_pressure_conversion) > float(selected_conversion) + 1e-9:
        chosen_override_conversion = None
        if float(highest_guardrail_conversion) >= float(target_pressure_conversion) - 1e-9:
            chosen_override_conversion = float(target_pressure_conversion)
            selection_mode = "TRAD_TARGET_PRESSURE"
        elif bool(params.get("target_trad_override_enabled", False)) and float(highest_override_conversion) > float(selected_conversion) + 1e-9:
            chosen_override_conversion = float(min(target_pressure_conversion, highest_override_conversion))
            selection_mode = "TRAD_TARGET_OVERRIDE"

        if chosen_override_conversion is not None and float(chosen_override_conversion) > float(selected_conversion) + 1e-9:
            selected_conversion = float(chosen_override_conversion)
            selected_row = run_projection_from_state(year, state, params, first_year_conversion=selected_conversion, later_year_conversion=0.0)["df"].iloc[0].to_dict()

    diag_df = pd.DataFrame(tested_rows)
    if not diag_df.empty:
        diag_df["Selected Conversion After Test"] = selected_conversion
        diag_df["Selected Ordinary Taxable Income"] = float(selected_row.get("Ordinary Taxable Income", 0.0))
        diag_df["Target Trad Pressure Conversion"] = float(target_pressure_conversion)
        diag_df["Target Trad Highest Override Conversion"] = float(highest_override_conversion)
        diag_df["Selection Mode Detail"] = selection_mode
        diag_df["Bracket Solver Note"] = "Non-ACA years use full-range BETR search with delta-based current cost, projected future marginal-rate comparison, highest-valid-conversion winner selection, a stricter post-RMD hurdle, and optional target-Trad pressure."
    return round(selected_conversion, 2), selected_row, diag_df



def run_model_break_even_governor(inputs: dict, max_conversion: float, step_size: float) -> dict:
    params = build_common_params(inputs)
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
        )

        state, chosen_row = simulate_one_year(year, dict(state_before), params, optimal_conversion)
        coverage = get_coverage_status(year, int(params["primary_aca_end_year"]), int(params["spouse_aca_end_year"]))
        aca_limit = get_aca_magi_limit(year, coverage["aca_lives"])
        baseline_row = run_projection_from_state(year, dict({
            "trad": chosen_row["SOY Trad"],
            "roth": chosen_row["SOY Roth"],
            "brokerage": chosen_row["SOY Brokerage"],
            "brokerage_basis": chosen_row["SOY Brokerage Basis"],
            "cash": chosen_row["SOY Cash"],
        }), params, first_year_conversion=0.0, later_year_conversion=0.0)["df"].iloc[0].to_dict()
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
        chosen_row = enrich_year_row_for_display(year, dict(state_before), params, chosen_row)
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
        baseline_path = run_projection_from_state(year, dict(state_before), params, first_year_conversion=0.0, later_year_conversion=0.0)
        baseline_first_row = baseline_path["df"].iloc[0].to_dict()
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
                    "Target Trad Highest Override Conversion",
                    "Within Planner Override Cap",
                    "Selection Mode Detail",
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
    trad_balance_penalty_lambda: float = 0.25,
    rerun_best_validation: bool = True,
    validation_tolerance: float = 0.01,
) -> dict:
    results = []
    progress_bar = st.progress(0.0, text="Running Social Security optimizer...")
    total_runs = 9 * 9 + (1 if rerun_best_validation else 0)
    run_idx = 0

    for owner_age in range(62, 71):
        for spouse_age in range(62, 71):
            scenario_inputs = dict(inputs)
            scenario_inputs["owner_claim_age"] = int(owner_age)
            scenario_inputs["spouse_claim_age"] = int(spouse_age)

            try:
                run_result = run_model_break_even_governor(scenario_inputs, max_conversion, step_size)
            except Exception as exc:
                raise RuntimeError(
                    f"SS optimizer failed for owner age {owner_age} / spouse age {spouse_age}: {exc}"
                ) from exc

            scenario_fingerprint = build_scenario_fingerprint(
                scenario_inputs,
                max_conversion=max_conversion,
                step_size=step_size,
            )

            results.append({
                "Owner SS Age": int(owner_age),
                "Spouse SS Age": int(spouse_age),
                "Final Net Worth": float(run_result["final_net_worth"]),
                "Total Federal Tax": float(run_result["total_federal_taxes"]),
                "Total State Tax": float(run_result.get("total_state_taxes", 0.0)),
                "Total ACA Cost": float(run_result["total_aca_cost"]),
                "Total IRMAA Cost": float(run_result["total_irmaa_cost"]),
                "Total Government Drag": float(run_result.get("total_government_drag", 0.0)),
                "Total Conversions": float(run_result["total_conversions"]),
                "Ending Trad Balance": float(run_result["ending_trad_balance"]),
                "First IRMAA Year": run_result["first_irmaa_year"],
                "Max MAGI": float(run_result["max_magi"]),
                "ACA Hit Years": int(run_result["aca_hit_years"]),
                "IRMAA Hit Years": int(run_result["irmaa_hit_years"]),
                "Scenario Fingerprint": scenario_fingerprint,
                "Score": float(run_result["final_net_worth"]) - float(trad_balance_penalty_lambda) * float(run_result["ending_trad_balance"]),
            })

            run_idx += 1
            progress_bar.progress(run_idx / total_runs, text=f"Running Social Security optimizer... {run_idx}/{total_runs}")

    results_df = pd.DataFrame(results).sort_values(
        by=["Score", "Final Net Worth"],
        ascending=[False, False],
    ).reset_index(drop=True)
    results_df.insert(0, "Rank", range(1, len(results_df) + 1))

    top_10_df = results_df.head(10).copy()
    top_3 = results_df.head(3).copy()

    compare_metrics = [
        ("SS Ages", lambda r: f"{int(r['Owner SS Age'])}/{int(r['Spouse SS Age'])}"),
        ("Final Net Worth", lambda r: float(r["Final Net Worth"])),
        ("Ending Trad Balance", lambda r: float(r["Ending Trad Balance"])),
        ("Total Government Drag", lambda r: float(r["Total Government Drag"])),
        ("Total Conversions", lambda r: float(r["Total Conversions"])),
        ("Total Federal Tax", lambda r: float(r["Total Federal Tax"])),
        ("Total State Tax", lambda r: float(r["Total State Tax"])),
        ("Total ACA Cost", lambda r: float(r["Total ACA Cost"])),
        ("Total IRMAA Cost", lambda r: float(r["Total IRMAA Cost"])),
        ("Max MAGI", lambda r: float(r["Max MAGI"])),
        ("ACA Hit Years", lambda r: int(r["ACA Hit Years"])),
        ("IRMAA Hit Years", lambda r: int(r["IRMAA Hit Years"])),
        ("First IRMAA Year", lambda r: "None" if pd.isna(r["First IRMAA Year"]) else int(r["First IRMAA Year"])),
        ("Score", lambda r: float(r["Score"])),
    ]

    compare_rows = []
    for metric_name, getter in compare_metrics:
        row = {"Metric": metric_name}
        for idx in range(3):
            col_name = f"#{idx + 1}"
            if idx < len(top_3):
                row[col_name] = getter(top_3.iloc[idx])
            else:
                row[col_name] = ""
        compare_rows.append(row)

    comparison_df = pd.DataFrame(compare_rows)

    best_result = results_df.iloc[0].to_dict() if not results_df.empty else None
    best_validation = None
    best_rerun_summary = None

    if rerun_best_validation and best_result is not None:
        best_inputs = dict(inputs)
        best_inputs["owner_claim_age"] = int(best_result["Owner SS Age"])
        best_inputs["spouse_claim_age"] = int(best_result["Spouse SS Age"])
        best_rerun = run_model_break_even_governor(best_inputs, max_conversion, step_size)
        best_validation = make_consistency_payload(
            {
                "final_net_worth": float(best_result["Final Net Worth"]),
                "ending_trad_balance": float(best_result["Ending Trad Balance"]),
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
        run_idx += 1
        progress_bar.progress(run_idx / total_runs, text=f"Running Social Security optimizer... {run_idx}/{total_runs}")

    progress_bar.empty()

    return {
        "all_results_df": results_df,
        "top_10_df": top_10_df,
        "comparison_df": comparison_df,
        "best_result": best_result,
        "best_validation": best_validation,
        "best_rerun_summary": best_rerun_summary,
        "trad_balance_penalty_lambda": float(trad_balance_penalty_lambda),
    }


def render_ss_optimizer_results(result: dict):
    st.subheader("Social Security Optimizer Summary")
    st.write(f"Scoring lambda (Trad balance penalty): {result['trad_balance_penalty_lambda']:.2f}")
    if result["best_result"] is not None:
        best = result["best_result"]
        st.write(f"Best SS Ages: {int(best['Owner SS Age'])}/{int(best['Spouse SS Age'])}")
        st.write(f"Best Score: ${float(best['Score']):,.0f}")
        st.write(f"Best Final Net Worth: ${float(best['Final Net Worth']):,.0f}")
        st.write(f"Best Ending Trad Balance: ${float(best['Ending Trad Balance']):,.0f}")
        st.write(f"Best Scenario Fingerprint: `{best['Scenario Fingerprint']}`")

    if result.get("best_validation") is not None:
        validation = result["best_validation"]
        if validation["passed"]:
            st.success("Best-strategy rerun validation passed. Optimizer winner matched a fresh rerun within tolerance.")
        else:
            st.error("Best-strategy rerun validation failed. The optimizer winner did not match a fresh rerun.")
            st.dataframe(validation["mismatch_df"], use_container_width=True)

    st.subheader("Top 10 SS Strategies")
    st.dataframe(result["top_10_df"], use_container_width=True)

    st.subheader("Top 3 Side-by-Side Comparison")
    st.dataframe(result["comparison_df"], use_container_width=True)

    with st.expander("All 81 SS combinations"):
        st.dataframe(result["all_results_df"], use_container_width=True)


# -----------------------------
# DISPLAY
# -----------------------------
def render_summary(title: str, result: dict):
    st.subheader(title)
    if result.get("scenario_fingerprint"):
        st.write(f"Scenario Fingerprint: `{result['scenario_fingerprint']}`")
    st.write(f"Owner SS Start Year: {result['owner_ss_start']}")
    st.write(f"Spouse SS Start Year: {result['spouse_ss_start']}")
    st.write(f"Household RMD Start Year (approx): {result['household_rmd_start']}")
    st.write(f"Final Net Worth: ${result['final_net_worth']:,.0f}")
    st.write(f"Ending Trad Balance: ${result['ending_trad_balance']:,.0f}")
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
st.title("Retirement Model — Break-Even Roth Conversion Engine")

st.header("Household Inputs")

owner_claim_age = st.slider("Owner SS Claim Age", 62, 70, 67)
spouse_claim_age = st.slider("Spouse SS Claim Age", 62, 70, 67)

owner_current_age = st.number_input("Owner Current Age", min_value=0, value=60, step=1)
spouse_current_age = st.number_input("Spouse Current Age", min_value=0, value=57, step=1)

col1, col2 = st.columns(2)

with col1:
    trad = st.number_input("Traditional Balance", min_value=0.0, value=1100000.0, step=1000.0)
    roth = st.number_input("Roth Balance", min_value=0.0, value=1700000.0, step=1000.0)
    brokerage = st.number_input("Brokerage Balance", min_value=0.0, value=300000.0, step=1000.0)
    brokerage_basis = st.number_input(
        "Brokerage Cost Basis",
        min_value=0.0,
        value=300000.0,
        step=1000.0,
        help="Tax basis of the current brokerage balance. Realized gains on withdrawals are based on this.",
    )
    cash = st.number_input("Cash", min_value=0.0, value=10000.0, step=1000.0)

with col2:
    growth = st.number_input("Growth Rate (%)", min_value=0.0, value=8.0, step=0.1) / 100
    annual_spending = st.number_input("Base Annual Spending Need", min_value=0.0, value=80000.0, step=1000.0)
    spending_inflation_rate = st.number_input(
        "Spending Inflation Rate (%)",
        min_value=0.0,
        value=2.5,
        step=0.1,
        help="Applied to spending each year before any retirement-smile multiplier.",
    ) / 100
    owner_ss_base = st.number_input("Owner Annual SS at Age 67", min_value=0.0, value=43000.0, step=1000.0)
    spouse_ss_base = st.number_input("Spouse Annual SS at Age 67", min_value=0.0, value=15000.0, step=1000.0)

st.header("Retirement Smile Spending")
sm1, sm2 = st.columns(2)
with sm1:
    retirement_smile_enabled = st.checkbox(
        "Enable Retirement Smile Spending",
        value=True,
        help="Uses higher spending in go-go years, lower spending in slow-go years, and higher spending again in no-go years.",
    )
    go_go_end_age = st.number_input(
        "Go-Go Ends At Age",
        min_value=0,
        value=70,
        step=1,
        help="Applies to the older household member's age for the modeled year.",
    )
    slow_go_end_age = st.number_input(
        "Slow-Go Ends At Age",
        min_value=0,
        value=80,
        step=1,
        help="No-go spending starts at this age and later.",
    )
with sm2:
    go_go_multiplier = st.number_input("Go-Go Spending Multiplier", min_value=0.0, value=1.00, step=0.05, format="%.2f")
    slow_go_multiplier = st.number_input("Slow-Go Spending Multiplier", min_value=0.0, value=0.85, step=0.05, format="%.2f")
    no_go_multiplier = st.number_input("No-Go Spending Multiplier", min_value=0.0, value=1.20, step=0.05, format="%.2f")

st.caption("Modeled spending = base spending × annual spending inflation × phase multiplier.")

st.header("Coverage Timing")
cov1, cov2 = st.columns(2)
with cov1:
    primary_aca_end_year = st.number_input("Primary ACA End Year", min_value=START_YEAR, value=2031, step=1)
with cov2:
    spouse_aca_end_year = st.number_input("Spouse ACA End Year", min_value=START_YEAR, value=2034, step=1)

st.header("Earned Income")
earn1, earn2, earn3 = st.columns(3)
with earn1:
    earned_income_annual = st.number_input("Annual Wage Income", min_value=0.0, value=15000.0, step=1000.0)
with earn2:
    earned_income_start_year = st.number_input("Wage Income Start Year", min_value=START_YEAR, value=2026, step=1)
with earn3:
    earned_income_end_year = st.number_input("Wage Income End Year", min_value=START_YEAR, value=2031, step=1)

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
)
st.caption("This build falls back to Trad then Roth if the preferred source is insufficient.")

st.header("Flat Strategy Test")
annual_conversion = st.number_input("Flat Annual Conversion", min_value=0.0, value=0.0, step=5000.0)

st.header("Break-Even Governor Inputs")
max_conversion = st.number_input("Max Annual Conversion To Test", min_value=0.0, value=300000.0, step=5000.0)
step_size = st.number_input(
    "Break-Even Step Size",
    min_value=1000.0,
    value=5000.0,
    step=1000.0,
    help="Smaller steps improve accuracy but run slower.",
)

pol1, pol2 = st.columns(2)
with pol1:
    cash_sweep_threshold = st.number_input(
        "Cash Sweep Threshold",
        min_value=0.0,
        value=50000.0,
        step=5000.0,
        help="End-of-year cash above this amount is swept into brokerage."
    )
with pol2:
    state_tax_rate = st.number_input(
        "State Tax Rate",
        min_value=0.0,
        max_value=0.20,
        value=0.0399,
        step=0.0001,
        format="%.4f"
    )

tg1, tg2 = st.columns(2)
with tg1:
    target_trad_balance_enabled = st.checkbox(
        "Use Target Trad Balance Goal",
        value=False,
        help="When enabled, pre-RMD non-ACA years can push conversions above pure BETR minimums to work toward a target Traditional IRA balance by household RMD start."
    )
with tg2:
    target_trad_balance = st.number_input(
        "Target Trad Balance By RMD Start",
        min_value=0.0,
        value=300000.0,
        step=25000.0,
        help="Planner goal for remaining Traditional IRA balance by household RMD start."
    )

ov1, ov2 = st.columns(2)
with ov1:
    target_trad_override_enabled = st.checkbox(
        "Allow Target Trad Planner Override",
        value=False,
        help="When enabled, pre-RMD non-ACA years may exceed pure BETR stopping as long as current adjusted cost stays under the planner cap."
    )
with ov2:
    target_trad_override_max_rate = st.number_input(
        "Target Trad Override Max All-In Rate",
        min_value=0.0,
        max_value=1.0,
        value=0.22,
        step=0.01,
        format="%.2f",
        help="Maximum adjusted current cost rate allowed for target-Trad override."
    )

br1, br2 = st.columns(2)
with br1:
    post_aca_target_bracket = st.selectbox(
        "Post-ACA Target Bracket",
        ["12%", "22%", "24%"],
        index=1,
        help="Used in non-ACA years before household RMDs begin.",
    )
with br2:
    rmd_era_target_bracket = st.selectbox(
        "RMD-Era Target Bracket",
        ["12%", "22%", "24%"],
        index=1,
        help="Used once the household reaches the first RMD year.",
    )


st.header("Reliability / Validation")
rv1, rv2 = st.columns(2)
with rv1:
    strict_repeatability_check = st.checkbox(
        "Run Repeatability Check On Break-Even Governor",
        value=True,
        help="Runs the exact same break-even scenario twice back-to-back and compares the key outputs.",
    )
with rv2:
    optimizer_rerun_best_validation = st.checkbox(
        "Rerun Optimizer Winner To Validate",
        value=True,
        help="After all 81 SS combinations finish, reruns the winner once more and compares the key outputs.",
    )

validation_tolerance = st.number_input(
    "Validation Tolerance ($)",
    min_value=0.0,
    value=0.01,
    step=0.01,
    format="%.2f",
    help="Absolute tolerance used when comparing repeated runs.",
)

ss_opt1, ss_opt2 = st.columns(2)
with ss_opt1:
    run_ss_optimizer_toggle = st.checkbox(
        "Run SS Optimizer",
        value=False,
        help="Runs all 81 Social Security claim-age combinations through the existing break-even governor and ranks them.",
    )
with ss_opt2:
    trad_balance_penalty_lambda = st.number_input(
        "SS Optimizer Trad Penalty Lambda",
        min_value=0.0,
        value=0.25,
        step=0.05,
        format="%.2f",
        help="Score = Final Net Worth - lambda × Ending Trad Balance",
    )

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
    "annual_conversion": annual_conversion,
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
    "cash_sweep_threshold": cash_sweep_threshold,
    "state_tax_rate": state_tax_rate,
    "target_trad_balance_enabled": target_trad_balance_enabled,
    "target_trad_balance": target_trad_balance,
    "target_trad_override_enabled": target_trad_override_enabled,
    "target_trad_override_max_rate": target_trad_override_max_rate,
    "post_aca_target_bracket": post_aca_target_bracket,
    "rmd_era_target_bracket": rmd_era_target_bracket,
}

if run_ss_optimizer_toggle:
    if st.button("Run All SS Strategies"):
        optimizer_result = run_ss_optimizer(
            inputs=inputs,
            max_conversion=max_conversion,
            step_size=step_size,
            trad_balance_penalty_lambda=trad_balance_penalty_lambda,
            rerun_best_validation=optimizer_rerun_best_validation,
            validation_tolerance=validation_tolerance,
        )
        render_ss_optimizer_results(optimizer_result)
else:
    btn1, btn2 = st.columns(2)

    with btn1:
        if st.button("Run Flat Strategy Test"):
            result = run_model_fixed(inputs)
            result["scenario_fingerprint"] = build_scenario_fingerprint(inputs)
            render_summary("Flat Strategy Summary", result)
            st.subheader("Flat Strategy Yearly Results")
            st.dataframe(result["df"], use_container_width=True)

    with btn2:
        if st.button("Run Break-Even Governor"):
            result = run_governor_with_validation(
                inputs=inputs,
                max_conversion=max_conversion,
                step_size=step_size,
                strict_repeatability_check=strict_repeatability_check,
                tol=validation_tolerance,
            )
            render_summary("Break-Even Governor Summary", result)
            st.subheader("Chosen Year-by-Year Path")
            st.dataframe(result["df"], use_container_width=True)
            st.subheader("Per-Step Break-Even Testing")
            st.dataframe(result["decision_df"], use_container_width=True)
