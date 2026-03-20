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

ACA_MAGI_THRESHOLD_MFJ = 80000.0
ACA_SURCHARGE_RATE = 0.085  # simplified placeholder

IRMAA_THRESHOLD_MFJ = 206000.0
IRMAA_SURCHARGE = 4000.0  # simplified annual household placeholder


# -----------------------------
# TAX HELPERS
# -----------------------------
def calculate_progressive_tax(taxable_income, brackets):
    taxable_income = max(0.0, taxable_income)
    tax = 0.0

    for i, (lower_bound, rate) in enumerate(brackets):
        if i + 1 < len(brackets):
            upper_bound = brackets[i + 1][0]
        else:
            upper_bound = float("inf")

        if taxable_income > lower_bound:
            amount_in_bracket = min(taxable_income, upper_bound) - lower_bound
            tax += amount_in_bracket * rate

    assert tax >= 0, "Progressive tax computed negative"
    return tax


def calculate_taxable_ss(total_ss, other_income):
    total_ss = max(0.0, total_ss)
    other_income = max(0.0, other_income)

    provisional_income = other_income + 0.5 * total_ss

    if provisional_income <= 32000:
        taxable_ss = 0.0
    elif provisional_income <= 44000:
        taxable_ss = 0.5 * (provisional_income - 32000)
    else:
        part1 = 6000.0
        part2 = 0.85 * (provisional_income - 44000)
        taxable_ss = part1 + part2

    taxable_ss = min(taxable_ss, 0.85 * total_ss)
    taxable_ss = max(0.0, taxable_ss)

    assert taxable_ss >= 0, "Taxable SS negative"
    return taxable_ss


def calculate_federal_tax(ordinary_income, total_ss):
    taxable_ss = calculate_taxable_ss(total_ss, ordinary_income)
    agi = ordinary_income + taxable_ss
    taxable_income = max(0.0, agi - STANDARD_DEDUCTION_MFJ)
    federal_tax = calculate_progressive_tax(taxable_income, FEDERAL_BRACKETS_MFJ)

    assert agi >= 0, "AGI negative"
    assert taxable_income >= 0, "Taxable income negative"
    assert federal_tax >= 0, "Federal tax negative"

    return {
        "taxable_ss": taxable_ss,
        "agi": agi,
        "taxable_income": taxable_income,
        "federal_tax": federal_tax,
    }


def calculate_aca_cost(magi):
    magi = max(0.0, magi)
    excess = max(0.0, magi - ACA_MAGI_THRESHOLD_MFJ)
    aca_cost = excess * ACA_SURCHARGE_RATE
    assert aca_cost >= 0, "ACA cost negative"
    return aca_cost


def calculate_irmaa_cost(magi):
    magi = max(0.0, magi)
    irmaa_cost = IRMAA_SURCHARGE if magi > IRMAA_THRESHOLD_MFJ else 0.0
    assert irmaa_cost >= 0, "IRMAA cost negative"
    return irmaa_cost


# -----------------------------
# CASH FLOW HELPERS
# -----------------------------
def withdraw_for_need(amount_needed, trad, roth, brokerage, cash):
    """
    Withdrawal order:
    1. Cash
    2. Brokerage
    3. Traditional
    4. Roth
    """
    starting_need = amount_needed
    assert amount_needed >= 0, "Amount needed cannot be negative"

    from_cash = min(cash, amount_needed)
    cash -= from_cash
    amount_needed -= from_cash

    from_brokerage = min(brokerage, amount_needed)
    brokerage -= from_brokerage
    amount_needed -= from_brokerage

    from_trad = min(trad, amount_needed)
    trad -= from_trad
    amount_needed -= from_trad

    from_roth = min(roth, amount_needed)
    roth -= from_roth
    amount_needed -= from_roth

    shortfall = amount_needed
    total_funded = from_cash + from_brokerage + from_trad + from_roth

    assert abs((starting_need - shortfall) - total_funded) < 0.01, "Withdrawal accounting mismatch"

    return {
        "trad": trad,
        "roth": roth,
        "brokerage": brokerage,
        "cash": cash,
        "from_cash": from_cash,
        "from_brokerage": from_brokerage,
        "from_trad": from_trad,
        "from_roth": from_roth,
        "shortfall": shortfall,
    }


def deposit_cash(amount, cash):
    assert amount >= 0, "Deposit amount cannot be negative"
    cash += amount
    assert cash >= 0, "Cash negative after deposit"
    return cash


# -----------------------------
# SOCIAL SECURITY
# -----------------------------
def annual_ss_benefit(base_benefit_at_67, claim_age):
    """
    Simplified adjustment:
    - 67 = base
    - before 67: -6% per year
    - after 67: +8% per year
    """
    delta = claim_age - 67

    if delta < 0:
        benefit = base_benefit_at_67 * (1 - 0.06 * abs(delta))
    elif delta > 0:
        benefit = base_benefit_at_67 * (1 + 0.08 * delta)
    else:
        benefit = base_benefit_at_67

    return max(0.0, benefit)


def ss_start_year_from_current_age(start_year, current_age, claim_age):
    years_until_claim = claim_age - current_age
    return int(start_year + years_until_claim)


# -----------------------------
# CORE MODEL
# -----------------------------
def run_model(inputs):
    years = list(range(START_YEAR, END_YEAR + 1))

    trad = float(inputs["trad"])
    roth = float(inputs["roth"])
    brokerage = float(inputs["brokerage"])
    cash = float(inputs["cash"])

    growth = float(inputs["growth"])
    annual_spending = float(inputs["annual_spending"])
    annual_conversion = float(inputs["annual_conversion"])

    owner_current_age = int(inputs["owner_current_age"])
    spouse_current_age = int(inputs["spouse_current_age"])
    owner_claim_age = int(inputs["owner_claim_age"])
    spouse_claim_age = int(inputs["spouse_claim_age"])
    owner_ss_base = float(inputs["owner_ss_base"])
    spouse_ss_base = float(inputs["spouse_ss_base"])

    owner_ss_start = ss_start_year_from_current_age(START_YEAR, owner_current_age, owner_claim_age)
    spouse_ss_start = ss_start_year_from_current_age(START_YEAR, spouse_current_age, spouse_claim_age)

    owner_ss_annual = annual_ss_benefit(owner_ss_base, owner_claim_age)
    spouse_ss_annual = annual_ss_benefit(spouse_ss_base, spouse_claim_age)

    results = []
    total_federal_taxes = 0.0
    total_aca_cost = 0.0
    total_irmaa_cost = 0.0
    prev_year = None

    for year in years:
        if prev_year is not None:
            assert year > prev_year, "Year sequence error"
        prev_year = year

        soy_trad = trad
        soy_roth = roth
        soy_brokerage = brokerage
        soy_cash = cash

        # Growth
        trad *= (1 + growth)
        roth *= (1 + growth)
        brokerage *= (1 + growth)

        # SS income to cash
        owner_ss = owner_ss_annual if year >= owner_ss_start else 0.0
        spouse_ss = spouse_ss_annual if year >= spouse_ss_start else 0.0
        total_ss = owner_ss + spouse_ss
        cash = deposit_cash(total_ss, cash)

        # Conversion
        conversion = min(annual_conversion, trad)
        trad -= conversion
        roth += conversion

        # Tax and surcharges
        tax_info = calculate_federal_tax(conversion, total_ss)
        magi = tax_info["agi"]  # simplified placeholder
        federal_tax = tax_info["federal_tax"]
        aca_cost = calculate_aca_cost(magi)
        irmaa_cost = calculate_irmaa_cost(magi)

        total_federal_taxes += federal_tax
        total_aca_cost += aca_cost
        total_irmaa_cost += irmaa_cost

        # Fund spending
        spend_result = withdraw_for_need(
            annual_spending, trad, roth, brokerage, cash
        )
        trad = spend_result["trad"]
        roth = spend_result["roth"]
        brokerage = spend_result["brokerage"]
        cash = spend_result["cash"]

        # Fund federal tax
        tax_result = withdraw_for_need(
            federal_tax, trad, roth, brokerage, cash
        )
        trad = tax_result["trad"]
        roth = tax_result["roth"]
        brokerage = tax_result["brokerage"]
        cash = tax_result["cash"]

        # Fund ACA
        aca_result = withdraw_for_need(
            aca_cost, trad, roth, brokerage, cash
        )
        trad = aca_result["trad"]
        roth = aca_result["roth"]
        brokerage = aca_result["brokerage"]
        cash = aca_result["cash"]

        # Fund IRMAA
        irmaa_result = withdraw_for_need(
            irmaa_cost, trad, roth, brokerage, cash
        )
        trad = irmaa_result["trad"]
        roth = irmaa_result["roth"]
        brokerage = irmaa_result["brokerage"]
        cash = irmaa_result["cash"]

        total_shortfall = (
            spend_result["shortfall"]
            + tax_result["shortfall"]
            + aca_result["shortfall"]
            + irmaa_result["shortfall"]
        )

        # Validation
        assert trad >= -0.01, "Traditional balance negative"
        assert roth >= -0.01, "Roth balance negative"
        assert brokerage >= -0.01, "Brokerage balance negative"
        assert cash >= -0.01, "Cash balance negative"
        assert federal_tax >= 0, "Federal tax negative"
        assert aca_cost >= 0, "ACA cost negative"
        assert irmaa_cost >= 0, "IRMAA cost negative"

        trad = max(0.0, trad)
        roth = max(0.0, roth)
        brokerage = max(0.0, brokerage)
        cash = max(0.0, cash)

        net_worth = trad + roth + brokerage + cash

        results.append({
            "Year": year,
            "SOY Trad": soy_trad,
            "SOY Roth": soy_roth,
            "SOY Brokerage": soy_brokerage,
            "SOY Cash": soy_cash,
            "Owner SS": owner_ss,
            "Spouse SS": spouse_ss,
            "Total SS": total_ss,
            "Conversion": conversion,
            "Taxable SS": tax_info["taxable_ss"],
            "AGI": tax_info["agi"],
            "Taxable Income": tax_info["taxable_income"],
            "Federal Tax": federal_tax,
            "ACA Cost": aca_cost,
            "IRMAA Cost": irmaa_cost,
            "Spend Need": annual_spending,
            "Shortfall": total_shortfall,
            "EOY Trad": trad,
            "EOY Roth": roth,
            "EOY Brokerage": brokerage,
            "EOY Cash": cash,
            "Net Worth": net_worth,
        })

    df = pd.DataFrame(results)

    return {
        "df": df,
        "total_federal_taxes": total_federal_taxes,
        "total_aca_cost": total_aca_cost,
        "total_irmaa_cost": total_irmaa_cost,
        "final_net_worth": net_worth,
        "owner_ss_start": owner_ss_start,
        "spouse_ss_start": spouse_ss_start,
        "total_shortfall": df["Shortfall"].sum(),
    }


# -----------------------------
# GOVERNOR / OPTIMIZER
# -----------------------------
def build_candidate_strategies(max_conversion, step):
    max_conversion = max(0.0, float(max_conversion))
    step = max(1.0, float(step))

    candidates = []
    current = 0.0
    while current <= max_conversion + 0.001:
        candidates.append(round(current, 2))
        current += step

    # Ensure the exact max is included
    if round(max_conversion, 2) not in candidates:
        candidates.append(round(max_conversion, 2))

    candidates = sorted(set(candidates))
    assert len(candidates) > 0, "No candidate strategies generated"
    return candidates


def score_result(result):
    """
    Higher score is better.
    Heavy penalty on shortfall.
    Then prefer higher final net worth.
    Then prefer lower taxes/costs.
    """
    final_net_worth = result["final_net_worth"]
    shortfall = result["total_shortfall"]
    total_drag = (
        result["total_federal_taxes"]
        + result["total_aca_cost"]
        + result["total_irmaa_cost"]
    )

    score = final_net_worth - (shortfall * 1000.0) - total_drag
    return score


def run_optimizer(base_inputs, max_conversion, conversion_step):
    candidates = build_candidate_strategies(max_conversion, conversion_step)

    summary_rows = []
    detailed_results = {}

    for candidate_conversion in candidates:
        candidate_inputs = dict(base_inputs)
        candidate_inputs["annual_conversion"] = candidate_conversion

        result = run_model(candidate_inputs)
        score = score_result(result)

        total_drag = (
            result["total_federal_taxes"]
            + result["total_aca_cost"]
            + result["total_irmaa_cost"]
        )

        row = {
            "Annual Conversion Strategy": candidate_conversion,
            "Final Net Worth": result["final_net_worth"],
            "Total Federal Taxes": result["total_federal_taxes"],
            "Total ACA Cost": result["total_aca_cost"],
            "Total IRMAA Cost": result["total_irmaa_cost"],
            "Total Government Drag": total_drag,
            "Total Shortfall": result["total_shortfall"],
            "Score": score,
        }

        summary_rows.append(row)
        detailed_results[candidate_conversion] = result

    summary_df = pd.DataFrame(summary_rows)

    # Deterministic ranking:
    # 1. Higher score is better
    # 2. Higher final net worth is better
    # 3. Lower shortfall is better
    # 4. Lower government drag is better
    # 5. Lower conversion wins ties
    summary_df = summary_df.sort_values(
        by=[
            "Score",
            "Final Net Worth",
            "Total Shortfall",
            "Total Government Drag",
            "Annual Conversion Strategy",
        ],
        ascending=[False, False, True, True, True]
    ).reset_index(drop=True)

    best_conversion = float(summary_df.iloc[0]["Annual Conversion Strategy"])
    best_result = detailed_results[best_conversion]

    return {
        "summary_df": summary_df,
        "best_conversion": best_conversion,
        "best_result": best_result,
    }
# -----------------------------
# VALIDATION DISPLAY
# -----------------------------
def build_validation_messages(df):
    return [
        ("Years strictly increasing", df["Year"].is_monotonic_increasing),
        ("EOY Trad >= 0", (df["EOY Trad"] >= 0).all()),
        ("EOY Roth >= 0", (df["EOY Roth"] >= 0).all()),
        ("EOY Brokerage >= 0", (df["EOY Brokerage"] >= 0).all()),
        ("EOY Cash >= 0", (df["EOY Cash"] >= 0).all()),
        ("Federal Tax >= 0", (df["Federal Tax"] >= 0).all()),
        ("ACA Cost >= 0", (df["ACA Cost"] >= 0).all()),
        ("IRMAA Cost >= 0", (df["IRMAA Cost"] >= 0).all()),
        ("AGI >= 0", (df["AGI"] >= 0).all()),
        ("Taxable SS >= 0", (df["Taxable SS"] >= 0).all()),
        ("Shortfall >= 0", (df["Shortfall"] >= 0).all()),
    ]


def render_validation(validation_messages):
    all_pass = True
    for label, passed in validation_messages:
        if passed:
            st.success(f"PASS — {label}")
        else:
            st.error(f"FAIL — {label}")
            all_pass = False

    if all_pass:
        st.success("MODEL STATUS: PASS")
    else:
        st.error("MODEL STATUS: FAIL")


# -----------------------------
# UI
# -----------------------------
st.title("Retirement Model — Phase 4")

st.header("Household Inputs")

owner_claim_age = st.slider("Owner SS Claim Age", 62, 70, 67)
spouse_claim_age = st.slider("Spouse SS Claim Age", 62, 70, 67)

owner_current_age = st.number_input("Owner Current Age", min_value=0, value=60, step=1)
spouse_current_age = st.number_input("Spouse Current Age", min_value=0, value=56, step=1)

col1, col2 = st.columns(2)

with col1:
    trad = st.number_input("Traditional Balance", min_value=0.0, value=500000.0, step=1000.0)
    roth = st.number_input("Roth Balance", min_value=0.0, value=200000.0, step=1000.0)
    brokerage = st.number_input("Brokerage Balance", min_value=0.0, value=300000.0, step=1000.0)
    cash = st.number_input("Cash", min_value=0.0, value=50000.0, step=1000.0)

with col2:
    growth = st.number_input("Growth Rate (%)", min_value=0.0, value=5.0, step=0.1) / 100
    annual_spending = st.number_input("Annual Spending Need", min_value=0.0, value=80000.0, step=1000.0)
    owner_ss_base = st.number_input("Owner Annual SS at Age 67", min_value=0.0, value=36000.0, step=1000.0)
    spouse_ss_base = st.number_input("Spouse Annual SS at Age 67", min_value=0.0, value=24000.0, step=1000.0)

st.header("Governor Inputs")

max_conversion = st.number_input("Max Annual Conversion To Test", min_value=0.0, value=100000.0, step=5000.0)
conversion_step = st.number_input("Conversion Step Size", min_value=1000.0, value=10000.0, step=1000.0)

base_inputs = {
    "trad": trad,
    "roth": roth,
    "brokerage": brokerage,
    "cash": cash,
    "growth": growth,
    "annual_spending": annual_spending,
    "annual_conversion": 0.0,  # overridden by optimizer
    "owner_current_age": owner_current_age,
    "spouse_current_age": spouse_current_age,
    "owner_claim_age": owner_claim_age,
    "spouse_claim_age": spouse_claim_age,
    "owner_ss_base": owner_ss_base,
    "spouse_ss_base": spouse_ss_base,
}

if st.button("Run Governor"):
    try:
        optimizer_output = run_optimizer(base_inputs, max_conversion, conversion_step)

        summary_df = optimizer_output["summary_df"]
        best_conversion = optimizer_output["best_conversion"]
        best_result = optimizer_output["best_result"]
        best_df = best_result["df"]

        st.subheader("Winning Strategy")
        st.write(f"Winning Annual Conversion: ${best_conversion:,.0f}")
        st.write(f"Owner SS Start Year: {best_result['owner_ss_start']}")
        st.write(f"Spouse SS Start Year: {best_result['spouse_ss_start']}")
        st.write(f"Final Net Worth: ${best_result['final_net_worth']:,.0f}")
        st.write(f"Total Federal Taxes: ${best_result['total_federal_taxes']:,.0f}")
        st.write(f"Total ACA Cost: ${best_result['total_aca_cost']:,.0f}")
        st.write(f"Total IRMAA Cost: ${best_result['total_irmaa_cost']:,.0f}")
        st.write(
            f"Total Government Drag: "
            f"${best_result['total_federal_taxes'] + best_result['total_aca_cost'] + best_result['total_irmaa_cost']:,.0f}"
        )
        st.write(f"Total Shortfall: ${best_result['total_shortfall']:,.0f}")

        st.subheader("Winning Strategy Validation")
        render_validation(build_validation_messages(best_df))

        st.subheader("Strategy Comparison")
        st.dataframe(summary_df, use_container_width=True)

        st.subheader("Winning Strategy Yearly Results")
        st.dataframe(best_df, use_container_width=True)

    except AssertionError as e:
        st.error(f"VALIDATION FAILED: {e}")
    except Exception as e:
        st.error(f"UNEXPECTED ERROR: {e}")
