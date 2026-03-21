import streamlit as st
import pandas as pd

# -----------------------------
# CONSTANTS
# -----------------------------
START_YEAR = 2025
END_YEAR = 2045

FEDERAL_BRACKETS_MFJ = [
    (0, 0.10),
    (23200, 0.12),
    (94300, 0.22),
    (201050, 0.24),
    (383900, 0.32),
    (487450, 0.35),
    (731200, 0.37),
]

STANDARD_DEDUCTION_MFJ = 30000.0


# -----------------------------
# TAX HELPERS
# -----------------------------
def calculate_progressive_tax(taxable_income: float) -> float:
    taxable_income = max(0.0, float(taxable_income))
    tax = 0.0

    for i, (lower_bound, rate) in enumerate(FEDERAL_BRACKETS_MFJ):
        upper_bound = FEDERAL_BRACKETS_MFJ[i + 1][0] if i + 1 < len(FEDERAL_BRACKETS_MFJ) else float("inf")
        if taxable_income > lower_bound:
            amount_in_bracket = min(taxable_income, upper_bound) - lower_bound
            tax += amount_in_bracket * rate

    return max(0.0, tax)


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


def calculate_federal_tax(other_ordinary_income: float, total_ss: float) -> dict:
    taxable_ss = calculate_taxable_ss(total_ss, other_ordinary_income)
    agi = other_ordinary_income + taxable_ss
    taxable_income = max(0.0, agi - STANDARD_DEDUCTION_MFJ)
    federal_tax = calculate_progressive_tax(taxable_income)

    return {
        "taxable_ss": taxable_ss,
        "agi": agi,
        "taxable_income": taxable_income,
        "federal_tax": federal_tax,
    }


# -----------------------------
# ACA / IRMAA
# -----------------------------
def calculate_household_aca_cost(magi: float) -> float:
    magi = max(0.0, float(magi))

    low_income = 30000.0
    low_cost = 1103.76
    high_income = 84000.0
    high_cost = 8364.0
    cliff_income = 85000.0
    cliff_cost = 27996.0

    if magi <= low_income:
        aca_cost = low_cost
    elif magi <= high_income:
        slope = (high_cost - low_cost) / (high_income - low_income)
        aca_cost = low_cost + slope * (magi - low_income)
    elif magi <= cliff_income:
        aca_cost = high_cost
    else:
        aca_cost = cliff_cost

    return max(0.0, aca_cost)


def calculate_household_irmaa_cost(magi: float) -> float:
    magi = max(0.0, float(magi))

    if magi <= 218000:
        monthly_total = 0.0
    elif magi <= 274000:
        monthly_total = 96.0
    elif magi <= 342000:
        monthly_total = 240.0
    elif magi <= 410000:
        monthly_total = 385.0
    elif magi <= 750000:
        monthly_total = 530.0
    else:
        monthly_total = 578.0

    return max(0.0, monthly_total * 12.0)


def calculate_prorated_aca_cost(magi: float, aca_lives: int) -> float:
    return calculate_household_aca_cost(magi) * (aca_lives / 2.0)


def calculate_prorated_irmaa_cost(magi: float, medicare_lives: int) -> float:
    return calculate_household_irmaa_cost(magi) * (medicare_lives / 2.0)


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
    tax_info = calculate_federal_tax(other_ordinary_income, total_ss)
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

    aca_cost = calculate_prorated_aca_cost(magi, aca_lives) if aca_lives > 0 else 0.0
    irmaa_cost = calculate_prorated_irmaa_cost(magi, medicare_lives) if medicare_lives > 0 else 0.0

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

    spend_shortfall = spend_result["shortfall"]
    tax_shortfall = tax_result["true_tax_shortfall"]
    aca_shortfall = aca_result["shortfall"]
    irmaa_shortfall = irmaa_result["shortfall"]
    year_shortfall = spend_shortfall + tax_shortfall + aca_shortfall + irmaa_shortfall

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
        "Spend Shortfall": spend_shortfall,
        "Tax Shortfall": tax_shortfall,
        "ACA Shortfall": aca_shortfall,
        "IRMAA Shortfall": irmaa_shortfall,
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
# MULTI-YEAR RUNNERS
# -----------------------------
def build_common_params(inputs: dict) -> dict:
    return {
        "growth": float(inputs["growth"]),
        "annual_spending": float(inputs["annual_spending"]),
        "conversion_tax_funding_policy": inputs["conversion_tax_funding_policy"],
        "owner_ss_start": ss_start_year_from_current_age(START_YEAR, int(inputs["owner_current_age"]), int(inputs["owner_claim_age"])),
        "spouse_ss_start": ss_start_year_from_current_age(START_YEAR, int(inputs["spouse_current_age"]), int(inputs["spouse_claim_age"])),
        "owner_ss_annual": annual_ss_benefit(float(inputs["owner_ss_base"]), int(inputs["owner_claim_age"])),
        "spouse_ss_annual": annual_ss_benefit(float(inputs["spouse_ss_base"]), int(inputs["spouse_claim_age"])),
        "primary_aca_end_year": int(inputs["primary_aca_end_year"]),
        "spouse_aca_end_year": int(inputs["spouse_aca_end_year"]),
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
    }


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
# DYNAMIC GOVERNOR
# -----------------------------
def build_year_candidates(state: dict, year: int, params: dict, max_conversion: float, step: float) -> list:
    max_conversion = max(0.0, float(max_conversion))
    step = max(1000.0, float(step))

    coverage = get_coverage_status(year, params["primary_aca_end_year"], params["spouse_aca_end_year"])
    aca_lives = coverage["aca_lives"]
    medicare_lives = coverage["medicare_lives"]

    # Base grid
    candidates = [0.0]
    current = 0.0
    while current <= max_conversion + 0.01:
        candidates.append(round(current, 2))
        current += step

    # Add useful thresholds
    candidates.append(min(max_conversion, STANDARD_DEDUCTION_MFJ))

    if aca_lives > 0:
        candidates.append(min(max_conversion, 84000.0))
        candidates.append(min(max_conversion, 85000.0))

    if medicare_lives > 0:
        candidates.append(min(max_conversion, 218000.0))

    # Can't convert more than available Trad after growth approximation would allow.
    trad_cap = max(0.0, float(state["trad"]) * (1.0 + float(params["growth"])))
    candidates = [min(c, trad_cap, max_conversion) for c in candidates]
    candidates = sorted(set(round(c, 2) for c in candidates if c >= 0))

    return candidates


def score_year_candidate(row: dict, trad_bias: float) -> tuple:
    """
    Higher tuple is better.

    Priority:
    1. No shortfall
    2. Higher end-of-year net worth
    3. Lower current-year drag
    4. Lower end-of-year Traditional balance (weighted by trad_bias)
    5. Lower conversion as final tiebreak
    """
    shortfall_ok = row["Year Shortfall"] <= 0.01
    drag = row["Federal Tax"] + row["ACA Cost"] + row["IRMAA Cost"]
    eoy_trad = row["EOY Trad"]

    adjusted_value = row["Net Worth"] - trad_bias * eoy_trad

    return (
        1 if shortfall_ok else 0,
        adjusted_value,
        -drag,
        -row["Chosen Conversion"],
    )

def run_model_dynamic_greedy(inputs: dict, max_conversion: float, step: float, trad_bias: float) -> dict:
    params = build_common_params(inputs)
    state = {
        "trad": float(inputs["trad"]),
        "roth": float(inputs["roth"]),
        "brokerage": float(inputs["brokerage"]),
        "cash": float(inputs["cash"]),
    }

    chosen_rows = []
    decision_rows = []

    for year in range(START_YEAR, END_YEAR + 1):
        candidates = build_year_candidates(state, year, params, max_conversion, step)

        tested = []
        best_state = None
        best_row = None

        for c in candidates:
            next_state, row = simulate_one_year(year, dict(state), params, c)
            tested.append({
                "Year": year,
                "Candidate Conversion": c,
                "Net Worth": row["Net Worth"],
                "EOY Trad": row["EOY Trad"],
                "Year Shortfall": row["Year Shortfall"],
                "Federal Tax": row["Federal Tax"],
                "ACA Cost": row["ACA Cost"],
                "IRMAA Cost": row["IRMAA Cost"],
                "MAGI": row["MAGI"],
                "Shortfall OK": row["Year Shortfall"] <= 0.01,
            })

            if best_row is None or score_year_candidate(row, trad_bias) > score_year_candidate(best_row, trad_bias):
                best_row = row
                best_state = next_state

        decision_rows.extend(tested)
        chosen_rows.append(best_row)
        state = best_state

    chosen_df = pd.DataFrame(chosen_rows)
    decision_df = pd.DataFrame(decision_rows)

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
    st.write(f"Final Net Worth: ${result['final_net_worth']:,.0f}")
    st.write(f"Total Federal Taxes: ${result['total_federal_taxes']:,.0f}")
    st.write(f"Total ACA Cost: ${result['total_aca_cost']:,.0f}")
    st.write(f"Total IRMAA Cost: ${result['total_irmaa_cost']:,.0f}")
    st.write(f"Total Government Drag: ${result['total_federal_taxes'] + result['total_aca_cost'] + result['total_irmaa_cost']:,.0f}")
    st.write(f"Total Shortfall: ${result['total_shortfall']:,.0f}")
    st.write(f"Max MAGI: ${result['max_magi']:,.0f}")
    st.write(f"ACA Hit Years: {result['aca_hit_years']}")
    st.write(f"IRMAA Hit Years: {result['irmaa_hit_years']}")
    st.write(f"First IRMAA Year: {result['first_irmaa_year'] if result['first_irmaa_year'] is not None else 'None'}")


# -----------------------------
# UI
# -----------------------------
trad_bias = st.number_input(
    "Traditional Balance Reduction Bias",
    min_value=0.0,
    value=0.05,
    step=0.01,
    help="Higher values encourage earlier Roth conversions by penalizing ending Traditional balance."
)

st.title("Retirement Model — Dynamic Governor")

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

st.header("Dynamic Governor Inputs")
max_conversion = st.number_input("Max Annual Conversion To Test", min_value=0.0, value=100000.0, step=5000.0)
conversion_step = st.number_input("Conversion Step Size", min_value=1000.0, value=10000.0, step=1000.0)

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
    if st.button("Run Dynamic Year-by-Year Governor"):
        result = run_model_dynamic_greedy(inputs, max_conversion, conversion_step, trad_bias)
        render_summary("Dynamic Governor Summary", result)
        st.subheader("Chosen Year-by-Year Path")
        st.dataframe(result["df"], use_container_width=True)
        st.subheader("Per-Year Candidate Testing")
        st.dataframe(result["decision_df"], use_container_width=True)
