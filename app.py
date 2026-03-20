import streamlit as st
import pandas as pd

# -----------------------------
# CONSTANTS
# -----------------------------
START_YEAR = 2025
END_YEAR = 2045

OWNER_BIRTH_YEAR = 1965
SPOUSE_BIRTH_YEAR = 1965

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
    """
    Simplified MFJ SS taxation:
    - provisional income = other_income + 50% of SS
    - base thresholds: 32k / 44k
    - capped at 85% of SS
    """
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
def ss_start_year(birth_year, claim_age):
    return birth_year + claim_age


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

    benefit = max(0.0, benefit)
    return benefit


# -----------------------------
# MODEL
# -----------------------------
def run_model(inputs):
    years = list(range(START_YEAR, END_YEAR + 1))

    trad = inputs["trad"]
    roth = inputs["roth"]
    brokerage = inputs["brokerage"]
    cash = inputs["cash"]

    growth = inputs["growth"]
    annual_spending = inputs["annual_spending"]
    annual_conversion = inputs["annual_conversion"]

    owner_claim_age = inputs["owner_claim_age"]
    spouse_claim_age = inputs["spouse_claim_age"]
    owner_ss_base = inputs["owner_ss_base"]
    spouse_ss_base = inputs["spouse_ss_base"]

    owner_ss_start = ss_start_year(OWNER_BIRTH_YEAR, owner_claim_age)
    spouse_ss_start = ss_start_year(SPOUSE_BIRTH_YEAR, spouse_claim_age)

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

        # ---- Start of year balances
        soy_trad = trad
        soy_roth = roth
        soy_brokerage = brokerage
        soy_cash = cash

        # ---- Growth on invested accounts
        trad *= (1 + growth)
        roth *= (1 + growth)
        brokerage *= (1 + growth)

        # ---- Social Security income arrives as cash
        owner_ss = owner_ss_annual if year >= owner_ss_start else 0.0
        spouse_ss = spouse_ss_annual if year >= spouse_ss_start else 0.0
        total_ss = owner_ss + spouse_ss

        cash = deposit_cash(total_ss, cash)

        # ---- Roth conversion
        conversion = min(annual_conversion, trad)
        trad -= conversion
        roth += conversion

        # ---- Tax / ACA / IRMAA
        other_ordinary_income = conversion
        tax_info = calculate_federal_tax(other_ordinary_income, total_ss)

        magi = tax_info["agi"]  # simplified MAGI placeholder
        aca_cost = calculate_aca_cost(magi)
        irmaa_cost = calculate_irmaa_cost(magi)

        federal_tax = tax_info["federal_tax"]

        total_federal_taxes += federal_tax
        total_aca_cost += aca_cost
        total_irmaa_cost += irmaa_cost

        # ---- Fund spending need
        spend_result = withdraw_for_need(
            annual_spending,
            trad,
            roth,
            brokerage,
            cash
        )
        trad = spend_result["trad"]
        roth = spend_result["roth"]
        brokerage = spend_result["brokerage"]
        cash = spend_result["cash"]

        # ---- Fund federal tax
        tax_result = withdraw_for_need(
            federal_tax,
            trad,
            roth,
            brokerage,
            cash
        )
        trad = tax_result["trad"]
        roth = tax_result["roth"]
        brokerage = tax_result["brokerage"]
        cash = tax_result["cash"]

        # ---- Fund ACA cost
        aca_result = withdraw_for_need(
            aca_cost,
            trad,
            roth,
            brokerage,
            cash
        )
        trad = aca_result["trad"]
        roth = aca_result["roth"]
        brokerage = aca_result["brokerage"]
        cash = aca_result["cash"]

        # ---- Fund IRMAA cost
        irmaa_result = withdraw_for_need(
            irmaa_cost,
            trad,
            roth,
            brokerage,
            cash
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

        # ---- Validation
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
            "Spent from Cash": spend_result["from_cash"],
            "Spent from Brokerage": spend_result["from_brokerage"],
            "Spent from Trad": spend_result["from_trad"],
            "Spent from Roth": spend_result["from_roth"],
            "Tax from Cash": tax_result["from_cash"],
            "Tax from Brokerage": tax_result["from_brokerage"],
            "Tax from Trad": tax_result["from_trad"],
            "Tax from Roth": tax_result["from_roth"],
            "ACA from Cash": aca_result["from_cash"],
            "ACA from Brokerage": aca_result["from_brokerage"],
            "ACA from Trad": aca_result["from_trad"],
            "ACA from Roth": aca_result["from_roth"],
            "IRMAA from Cash": irmaa_result["from_cash"],
            "IRMAA from Brokerage": irmaa_result["from_brokerage"],
            "IRMAA from Trad": irmaa_result["from_trad"],
            "IRMAA from Roth": irmaa_result["from_roth"],
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
    }


# -----------------------------
# UI
# -----------------------------
st.title("Retirement Model — Phase 3")

st.header("Inputs")

owner_claim_age = st.slider("Owner SS Claim Age", 62, 70, 67)
spouse_claim_age = st.slider("Spouse SS Claim Age", 62, 70, 67)

col1, col2 = st.columns(2)

with col1:
    trad = st.number_input("Traditional Balance", min_value=0.0, value=500000.0, step=1000.0)
    roth = st.number_input("Roth Balance", min_value=0.0, value=200000.0, step=1000.0)
    brokerage = st.number_input("Brokerage Balance", min_value=0.0, value=300000.0, step=1000.0)
    cash = st.number_input("Cash", min_value=0.0, value=50000.0, step=1000.0)

with col2:
    growth = st.number_input("Growth Rate (%)", min_value=0.0, value=5.0, step=0.1) / 100
    annual_spending = st.number_input("Annual Spending Need", min_value=0.0, value=80000.0, step=1000.0)
    annual_conversion = st.number_input("Annual Roth Conversion", min_value=0.0, value=20000.0, step=1000.0)
    owner_ss_base = st.number_input("Owner Annual SS at Age 67", min_value=0.0, value=36000.0, step=1000.0)
    spouse_ss_base = st.number_input("Spouse Annual SS at Age 67", min_value=0.0, value=24000.0, step=1000.0)

inputs = {
    "trad": trad,
    "roth": roth,
    "brokerage": brokerage,
    "cash": cash,
    "growth": growth,
    "annual_spending": annual_spending,
    "annual_conversion": annual_conversion,
    "owner_claim_age": owner_claim_age,
    "spouse_claim_age": spouse_claim_age,
    "owner_ss_base": owner_ss_base,
    "spouse_ss_base": spouse_ss_base,
}

if st.button("Run Simulation"):
    try:
        result = run_model(inputs)
        df = result["df"]

        validation_messages = []
        validation_messages.append(("Years strictly increasing", df["Year"].is_monotonic_increasing))
        validation_messages.append(("EOY Trad >= 0", (df["EOY Trad"] >= 0).all()))
        validation_messages.append(("EOY Roth >= 0", (df["EOY Roth"] >= 0).all()))
        validation_messages.append(("EOY Brokerage >= 0", (df["EOY Brokerage"] >= 0).all()))
        validation_messages.append(("EOY Cash >= 0", (df["EOY Cash"] >= 0).all()))
        validation_messages.append(("Federal Tax >= 0", (df["Federal Tax"] >= 0).all()))
        validation_messages.append(("ACA Cost >= 0", (df["ACA Cost"] >= 0).all()))
        validation_messages.append(("IRMAA Cost >= 0", (df["IRMAA Cost"] >= 0).all()))
        validation_messages.append(("AGI >= 0", (df["AGI"] >= 0).all()))
        validation_messages.append(("Taxable SS >= 0", (df["Taxable SS"] >= 0).all()))

        st.subheader("Summary")
        st.write(f"Owner SS Start Year: {result['owner_ss_start']}")
        st.write(f"Spouse SS Start Year: {result['spouse_ss_start']}")
        st.write(f"Total Federal Taxes: ${result['total_federal_taxes']:,.0f}")
        st.write(f"Total ACA Cost: ${result['total_aca_cost']:,.0f}")
        st.write(f"Total IRMAA Cost: ${result['total_irmaa_cost']:,.0f}")
        st.write(f"Final Net Worth: ${result['final_net_worth']:,.0f}")
        st.write(f"Total Shortfall: ${df['Shortfall'].sum():,.0f}")

        st.subheader("Validation")
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

        st.subheader("Yearly Results")
        st.dataframe(df, use_container_width=True)

    except AssertionError as e:
        st.error(f"VALIDATION FAILED: {e}")
