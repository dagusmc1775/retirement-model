import streamlit as st
import pandas as pd
from bisect import bisect_left

# -----------------------------
# CONSTANTS
# -----------------------------
START_YEAR = 2025
END_YEAR = 2045
UNIFORM_LIFETIME_DIVISOR_73 = 26.5

ACA_CLIFF_MFJ = 85000.0
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


def calculate_federal_tax(other_ordinary_income: float, total_ss: float, year: int) -> dict:
    taxable_ss = calculate_taxable_ss(total_ss, other_ordinary_income)
    agi = other_ordinary_income + taxable_ss
    taxable_income = max(0.0, agi - get_standard_deduction(year))
    federal_tax = calculate_progressive_tax(taxable_income, year)
    marginal_rate = get_marginal_rate_from_taxable_income(taxable_income, year)

    return {
        "taxable_ss": taxable_ss,
        "agi": agi,
        "taxable_income": taxable_income,
        "federal_tax": federal_tax,
        "marginal_rate": marginal_rate,
    }


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
def withdraw_by_policy(amount_needed: float, trad: float, roth: float, brokerage: float, cash: float, policy_name: str) -> dict:
    amount_needed = max(0.0, float(amount_needed))
    trad = float(trad)
    roth = float(roth)
    brokerage = float(brokerage)
    cash = float(cash)

    from_cash = 0.0
    from_brokerage = 0.0
    from_trad = 0.0
    from_roth = 0.0

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
        "cash": cash,
        "from_cash": from_cash,
        "from_brokerage": from_brokerage,
        "from_trad": from_trad,
        "from_roth": from_roth,
        "taxable_trad_withdrawal": from_trad,
        "shortfall": shortfall,
    }


def withdraw_for_spending(amount_needed: float, trad: float, roth: float, brokerage: float, cash: float) -> dict:
    return withdraw_by_policy(amount_needed, trad, roth, brokerage, cash, "Cash then Brokerage then Trad then Roth")


def withdraw_for_tax_with_fallback(amount_needed: float, trad: float, roth: float, brokerage: float, cash: float, preferred_policy: str) -> dict:
    preferred = withdraw_by_policy(amount_needed, trad, roth, brokerage, cash, preferred_policy)
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
            "Trad then Roth"
        )

        trad = fallback["trad"]
        roth = fallback["roth"]
        brokerage = fallback["brokerage"]
        cash = fallback["cash"]
        true_shortfall = fallback["shortfall"]
        fallback_from_trad = fallback["from_trad"]
        fallback_from_roth = fallback["from_roth"]
    else:
        trad = preferred["trad"]
        roth = preferred["roth"]
        brokerage = preferred["brokerage"]
        cash = preferred["cash"]
        true_shortfall = 0.0

    return {
        "trad": trad,
        "roth": roth,
        "brokerage": brokerage,
        "cash": cash,
        "preferred_from_cash": preferred["from_cash"],
        "preferred_from_brokerage": preferred["from_brokerage"],
        "preferred_from_trad": preferred["from_trad"],
        "preferred_from_roth": preferred["from_roth"],
        "fallback_from_trad": fallback_from_trad,
        "fallback_from_roth": fallback_from_roth,
        "true_tax_shortfall": true_shortfall,
    }


def normalize_balances(trad: float, roth: float, brokerage: float, cash: float) -> tuple:
    return (
        max(0.0, float(trad)),
        max(0.0, float(roth)),
        max(0.0, float(brokerage)),
        max(0.0, float(cash)),
    )


# -----------------------------
# PARAMS / HELPERS
# -----------------------------
def build_common_params(inputs: dict) -> dict:
    owner_ss_start = ss_start_year_from_current_age(START_YEAR, int(inputs["owner_current_age"]), int(inputs["owner_claim_age"]))
    spouse_ss_start = ss_start_year_from_current_age(START_YEAR, int(inputs["spouse_current_age"]), int(inputs["spouse_claim_age"]))

    owner_ss_annual = annual_ss_benefit(float(inputs["owner_ss_base"]), int(inputs["owner_claim_age"]))
    spouse_ss_annual = annual_ss_benefit(float(inputs["spouse_ss_base"]), int(inputs["spouse_claim_age"]))

    owner_rmd_start = START_YEAR + max(0, 73 - int(inputs["owner_current_age"]))
    spouse_rmd_start = START_YEAR + max(0, 73 - int(inputs["spouse_current_age"]))
    household_rmd_start = min(owner_rmd_start, spouse_rmd_start)

    return {
        "growth": float(inputs["growth"]),
        "annual_spending": float(inputs["annual_spending"]),
        "conversion_tax_funding_policy": inputs["conversion_tax_funding_policy"],
        "owner_ss_start": owner_ss_start,
        "spouse_ss_start": spouse_ss_start,
        "owner_ss_annual": owner_ss_annual,
        "spouse_ss_annual": spouse_ss_annual,
        "primary_aca_end_year": int(inputs["primary_aca_end_year"]),
        "spouse_aca_end_year": int(inputs["spouse_aca_end_year"]),
        "household_rmd_start": household_rmd_start,
    }


def summarize_run(df: pd.DataFrame, params: dict) -> dict:
    return {
        "df": df,
        "final_net_worth": float(df.iloc[-1]["Net Worth"]),
        "total_federal_taxes": float(df["Federal Tax"].sum()),
        "total_aca_cost": float(df["ACA Cost"].sum()),
        "total_irmaa_cost": float(df["IRMAA Cost"].sum()),
        "total_shortfall": float(df["Year Shortfall"].sum()),
        "max_magi": float(df["MAGI"].max()),
        "aca_hit_years": int((df["ACA Cost"] > 0).sum()),
        "irmaa_hit_years": int((df["IRMAA Cost"] > 0).sum()),
        "first_irmaa_year": int(df.loc[df["IRMAA Cost"] > 0, "Year"].iloc[0]) if (df["IRMAA Cost"] > 0).any() else None,
        "owner_ss_start": int(params["owner_ss_start"]),
        "spouse_ss_start": int(params["spouse_ss_start"]),
        "household_rmd_start": int(params["household_rmd_start"]),
        "total_conversions": float(df["Chosen Conversion"].sum()),
    }


def calc_total_drag(row: dict) -> float:
    return float(row["Federal Tax"] + row["ACA Cost"] + row["IRMAA Cost"])


# -----------------------------
# YEAR SIMULATION
# -----------------------------
def simulate_one_year(year: int, state: dict, params: dict, annual_conversion: float) -> tuple:
    trad = float(state["trad"])
    roth = float(state["roth"])
    brokerage = float(state["brokerage"])
    cash = float(state["cash"])

    growth = float(params["growth"])
    annual_spending = float(params["annual_spending"])
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
    soy_cash = cash

    trad *= (1 + growth)
    roth *= (1 + growth)
    brokerage *= (1 + growth)

    owner_ss = owner_ss_annual if year >= owner_ss_start else 0.0
    spouse_ss = spouse_ss_annual if year >= spouse_ss_start else 0.0
    total_ss = owner_ss + spouse_ss
    cash += total_ss

    conversion = min(float(annual_conversion), trad)
    trad -= conversion
    roth += conversion

    spend_result = withdraw_for_spending(annual_spending, trad, roth, brokerage, cash)
    trad = spend_result["trad"]
    roth = spend_result["roth"]
    brokerage = spend_result["brokerage"]
    cash = spend_result["cash"]

    other_ordinary_income = conversion + spend_result["taxable_trad_withdrawal"]
    tax_info = calculate_federal_tax(other_ordinary_income, total_ss, year)
    federal_tax = tax_info["federal_tax"]

    tax_result = withdraw_for_tax_with_fallback(
        federal_tax, trad, roth, brokerage, cash, conversion_tax_funding_policy
    )
    trad = tax_result["trad"]
    roth = tax_result["roth"]
    brokerage = tax_result["brokerage"]
    cash = tax_result["cash"]

    magi = tax_info["agi"]
    coverage = get_coverage_status(year, primary_aca_end_year, spouse_aca_end_year)
    aca_lives = coverage["aca_lives"]
    medicare_lives = coverage["medicare_lives"]

    aca_cost = calculate_aca_cost(magi, year, aca_lives)
    irmaa_cost = calculate_irmaa_cost(magi, year, medicare_lives)

    aca_result = withdraw_for_spending(aca_cost, trad, roth, brokerage, cash)
    trad = aca_result["trad"]
    roth = aca_result["roth"]
    brokerage = aca_result["brokerage"]
    cash = aca_result["cash"]

    irmaa_result = withdraw_for_spending(irmaa_cost, trad, roth, brokerage, cash)
    trad = irmaa_result["trad"]
    roth = irmaa_result["roth"]
    brokerage = irmaa_result["brokerage"]
    cash = irmaa_result["cash"]

    trad, roth, brokerage, cash = normalize_balances(trad, roth, brokerage, cash)

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
        "SOY Cash": soy_cash,
        "Owner SS": owner_ss,
        "Spouse SS": spouse_ss,
        "Total SS": total_ss,
        "Chosen Conversion": conversion,
        "Other Ordinary Income": other_ordinary_income,
        "Taxable SS": tax_info["taxable_ss"],
        "AGI": tax_info["agi"],
        "Taxable Income": tax_info["taxable_income"],
        "Current Marginal Tax Rate": tax_info["marginal_rate"],
        "MAGI": magi,
        "Primary On ACA": coverage["primary_on_aca"],
        "Spouse On ACA": coverage["spouse_on_aca"],
        "ACA Lives": aca_lives,
        "Medicare Lives": medicare_lives,
        "Federal Tax": federal_tax,
        "Tax Paid Preferred Cash": tax_result["preferred_from_cash"],
        "Tax Paid Preferred Brokerage": tax_result["preferred_from_brokerage"],
        "Tax Paid Fallback Trad": tax_result["fallback_from_trad"],
        "Tax Paid Fallback Roth": tax_result["fallback_from_roth"],
        "ACA Cost": aca_cost,
        "IRMAA Cost": irmaa_cost,
        "Year Shortfall": year_shortfall,
        "EOY Trad": trad,
        "EOY Roth": roth,
        "EOY Brokerage": brokerage,
        "EOY Cash": cash,
        "Net Worth": net_worth,
    }

    next_state = {
        "trad": trad,
        "roth": roth,
        "brokerage": brokerage,
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
        "cash": float(inputs["cash"]),
    }
    annual_conversion = float(inputs["annual_conversion"])

    rows = []
    for year in range(START_YEAR, END_YEAR + 1):
        state, row = simulate_one_year(year, state, params, annual_conversion)
        rows.append(row)

    df = pd.DataFrame(rows)
    return summarize_run(df, params)


# -----------------------------
# BREAK-EVEN CONVERSION ENGINE
# -----------------------------
def get_year_conversion_cap(state: dict, params: dict, max_conversion: float) -> float:
    trad_after_growth = max(0.0, float(state["trad"]) * (1.0 + float(params["growth"])))
    return max(0.0, min(float(max_conversion), trad_after_growth))


def evaluate_conversion_pair(year: int, state: dict, params: dict, current_conversion: float, next_conversion: float) -> dict:
    current_path = run_projection_from_state(year, state, params, first_year_conversion=current_conversion, later_year_conversion=0.0)
    next_path = run_projection_from_state(year, state, params, first_year_conversion=next_conversion, later_year_conversion=0.0)

    current_row = current_path["df"].iloc[0]
    next_row = next_path["df"].iloc[0]

    current_year_drag_now = calc_total_drag(current_row)
    current_year_drag_next = calc_total_drag(next_row)
    current_marginal_cost = current_year_drag_next - current_year_drag_now

    future_drag_now = float(current_path["df"].iloc[1:][["Federal Tax", "ACA Cost", "IRMAA Cost"]].sum().sum())
    future_drag_next = float(next_path["df"].iloc[1:][["Federal Tax", "ACA Cost", "IRMAA Cost"]].sum().sum())
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


def find_optimal_conversion_for_year(year: int, state: dict, params: dict, max_conversion: float, step_size: float) -> tuple:
    cap = get_year_conversion_cap(state, params, max_conversion)
    step_size = max(1.0, float(step_size))

    if cap <= 0.0:
        zero_path = run_projection_from_state(year, state, params, first_year_conversion=0.0, later_year_conversion=0.0)
        zero_row = zero_path["df"].iloc[0].to_dict()
        diag = pd.DataFrame([{
            "Year": year,
            "Base Conversion": 0.0,
            "Test Conversion": 0.0,
            "Step Amount": 0.0,
            "Current Marginal Cost": 0.0,
            "Future Avoided Cost": 0.0,
            "Net Benefit": 0.0,
            "Break-Even Reached": True,
            "Reason": "No available traditional balance to convert",
        }])
        return 0.0, zero_row, diag

    tested_rows = []
    current_conversion = 0.0

    while current_conversion < cap - 0.01:
        next_conversion = min(cap, current_conversion + step_size)
        eval_row = evaluate_conversion_pair(year, state, params, current_conversion, next_conversion)
        tested_rows.append(eval_row)

        # Keep stepping while the next increment still has positive or neutral economic value.
        if eval_row["Current Marginal Cost"] <= eval_row["Future Avoided Cost"] + 0.01:
            current_conversion = next_conversion
            if next_conversion >= cap - 0.01:
                break
        else:
            break

    optimal_conversion = round(current_conversion, 2)
    optimal_path = run_projection_from_state(year, state, params, first_year_conversion=optimal_conversion, later_year_conversion=0.0)
    optimal_row = optimal_path["df"].iloc[0].to_dict()

    diag_df = pd.DataFrame(tested_rows)
    if not diag_df.empty:
        diag_df["Selected Conversion After Test"] = optimal_conversion

    return optimal_conversion, optimal_row, diag_df


def run_model_break_even_governor(inputs: dict, max_conversion: float, step_size: float) -> dict:
    params = build_common_params(inputs)
    state = {
        "trad": float(inputs["trad"]),
        "roth": float(inputs["roth"]),
        "brokerage": float(inputs["brokerage"]),
        "cash": float(inputs["cash"]),
    }

    chosen_rows = []
    decision_frames = []

    for year in range(START_YEAR, END_YEAR + 1):
        optimal_conversion, _, diag_df = find_optimal_conversion_for_year(
            year=year,
            state=dict(state),
            params=params,
            max_conversion=max_conversion,
            step_size=step_size,
        )

        state, chosen_row = simulate_one_year(year, dict(state), params, optimal_conversion)
        chosen_rows.append(chosen_row)

        if not diag_df.empty:
            diag_df["Applied Conversion"] = float(optimal_conversion)
            decision_frames.append(diag_df)

    chosen_df = pd.DataFrame(chosen_rows)
    decision_df = pd.concat(decision_frames, ignore_index=True) if decision_frames else pd.DataFrame()

    result = summarize_run(chosen_df, params)
    result["decision_df"] = decision_df
    return result


# -----------------------------
# DISPLAY
# -----------------------------
def render_summary(title: str, result: dict):
    st.subheader(title)
    st.write(f"Owner SS Start Year: {result['owner_ss_start']}")
    st.write(f"Spouse SS Start Year: {result['spouse_ss_start']}")
    st.write(f"Household RMD Start Year (approx): {result['household_rmd_start']}")
    st.write(f"Final Net Worth: ${result['final_net_worth']:,.0f}")
    st.write(f"Total Federal Taxes: ${result['total_federal_taxes']:,.0f}")
    st.write(f"Total ACA Cost: ${result['total_aca_cost']:,.0f}")
    st.write(f"Total IRMAA Cost: ${result['total_irmaa_cost']:,.0f}")
    st.write(f"Total Government Drag: ${result['total_federal_taxes'] + result['total_aca_cost'] + result['total_irmaa_cost']:,.0f}")
    st.write(f"Total Shortfall: ${result['total_shortfall']:,.0f}")
    st.write(f"Max MAGI: ${result['max_magi']:,.0f}")
    st.write(f"Total Conversions: ${result['total_conversions']:,.0f}")
    st.write(f"ACA Hit Years: {result['aca_hit_years']}")
    st.write(f"IRMAA Hit Years: {result['irmaa_hit_years']}")
    st.write(f"First IRMAA Year: {result['first_irmaa_year'] if result['first_irmaa_year'] is not None else 'None'}")


# -----------------------------
# UI
# -----------------------------
st.title("Retirement Model — Break-Even Roth Conversion Engine")

st.header("Household Inputs")

owner_claim_age = st.slider("Owner SS Claim Age", 62, 70, 67)
spouse_claim_age = st.slider("Spouse SS Claim Age", 62, 70, 67)

owner_current_age = st.number_input("Owner Current Age", min_value=0, value=60, step=1)
spouse_current_age = st.number_input("Spouse Current Age", min_value=0, value=56, step=1)

col1, col2 = st.columns(2)

with col1:
    trad = st.number_input("Traditional Balance", min_value=0.0, value=1100000.0, step=1000.0)
    roth = st.number_input("Roth Balance", min_value=0.0, value=1700000.0, step=1000.0)
    brokerage = st.number_input("Brokerage Balance", min_value=0.0, value=300000.0, step=1000.0)
    cash = st.number_input("Cash", min_value=0.0, value=10000.0, step=1000.0)

with col2:
    growth = st.number_input("Growth Rate (%)", min_value=0.0, value=8.0, step=0.1) / 100
    annual_spending = st.number_input("Annual Spending Need", min_value=0.0, value=80000.0, step=1000.0)
    owner_ss_base = st.number_input("Owner Annual SS at Age 67", min_value=0.0, value=43000.0, step=1000.0)
    spouse_ss_base = st.number_input("Spouse Annual SS at Age 67", min_value=0.0, value=15000.0, step=1000.0)

st.header("Coverage Timing")
cov1, cov2 = st.columns(2)
with cov1:
    primary_aca_end_year = st.number_input("Primary ACA End Year", min_value=START_YEAR, value=2031, step=1)
with cov2:
    spouse_aca_end_year = st.number_input("Spouse ACA End Year", min_value=START_YEAR, value=2034, step=1)

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

inputs = {
    "trad": trad,
    "roth": roth,
    "brokerage": brokerage,
    "cash": cash,
    "growth": growth,
    "annual_spending": annual_spending,
    "annual_conversion": annual_conversion,
    "conversion_tax_funding_policy": conversion_tax_funding_policy,
    "owner_current_age": owner_current_age,
    "spouse_current_age": spouse_current_age,
    "owner_claim_age": owner_claim_age,
    "spouse_claim_age": spouse_claim_age,
    "owner_ss_base": owner_ss_base,
    "spouse_ss_base": spouse_ss_base,
    "primary_aca_end_year": primary_aca_end_year,
    "spouse_aca_end_year": spouse_aca_end_year,
}

btn1, btn2 = st.columns(2)

with btn1:
    if st.button("Run Flat Strategy Test"):
        result = run_model_fixed(inputs)
        render_summary("Flat Strategy Summary", result)
        st.subheader("Flat Strategy Yearly Results")
        st.dataframe(result["df"], use_container_width=True)

with btn2:
    if st.button("Run Break-Even Governor"):
        result = run_model_break_even_governor(inputs, max_conversion, step_size)
        render_summary("Break-Even Governor Summary", result)
        st.subheader("Chosen Year-by-Year Path")
        st.dataframe(result["df"], use_container_width=True)
        st.subheader("Per-Step Break-Even Testing")
        st.dataframe(result["decision_df"], use_container_width=True)
