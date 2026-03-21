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
def calculate_progressive_tax(taxable_income, brackets):
    taxable_income = max(0.0, taxable_income)
    tax = 0.0

    for i, (lower_bound, rate) in enumerate(brackets):
        upper_bound = brackets[i + 1][0] if i + 1 < len(brackets) else float("inf")
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
        taxable_ss = 6000.0 + 0.85 * (provisional_income - 44000)

    taxable_ss = min(taxable_ss, 0.85 * total_ss)
    taxable_ss = max(0.0, taxable_ss)

    assert taxable_ss >= 0, "Taxable SS negative"
    return taxable_ss


def calculate_federal_tax(other_ordinary_income, total_ss):
    taxable_ss = calculate_taxable_ss(total_ss, other_ordinary_income)
    agi = other_ordinary_income + taxable_ss
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


# -----------------------------
# ACA / IRMAA
# -----------------------------
def calculate_household_aca_cost(magi):
    """
    User-provided simplified ACA schedule:
    - <= 30k: 1,103.76
    - 84k: 8,364
    - > 85k: 27,996 cliff
    Linear interpolation between 30k and 84k.
    """
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

    assert aca_cost >= 0, "ACA cost negative"
    return aca_cost


def calculate_household_irmaa_cost(magi):
    """
    User-provided IRMAA table.
    Assumes 'Total IRMAA surcharge (mo)' is household monthly total.
    """
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

    irmaa_cost = monthly_total * 12.0
    assert irmaa_cost >= 0, "IRMAA cost negative"
    return irmaa_cost


def calculate_prorated_aca_cost(magi, aca_lives):
    household_cost = calculate_household_aca_cost(magi)
    aca_cost = household_cost * (aca_lives / 2.0)
    assert aca_cost >= 0, "Prorated ACA cost negative"
    return aca_cost


def calculate_prorated_irmaa_cost(magi, medicare_lives):
    household_cost = calculate_household_irmaa_cost(magi)
    irmaa_cost = household_cost * (medicare_lives / 2.0)
    assert irmaa_cost >= 0, "Prorated IRMAA cost negative"
    return irmaa_cost


# -----------------------------
# ACCOUNT HELPERS
# -----------------------------
def deposit_cash(amount, cash):
    assert amount >= 0, "Deposit amount cannot be negative"
    cash += amount
    assert cash >= 0, "Cash negative after deposit"
    return cash


def normalize_balances(trad, roth, brokerage, cash):
    trad = max(0.0, trad)
    roth = max(0.0, roth)
    brokerage = max(0.0, brokerage)
    cash = max(0.0, cash)
    return trad, roth, brokerage, cash


def withdraw_by_policy(amount_needed, trad, roth, brokerage, cash, policy_name):
    starting_need = amount_needed
    assert amount_needed >= 0, "Amount needed cannot be negative"

    from_cash = 0.0
    from_brokerage = 0.0
    from_trad = 0.0
    from_roth = 0.0

    if policy_name == "Cash then Brokerage then Trad then Roth":
        draw_order = ["cash", "brokerage", "trad", "roth"]
    elif policy_name == "Cash only":
        draw_order = ["cash"]
    elif policy_name == "Brokerage only":
        draw_order = ["brokerage"]
    elif policy_name == "Cash then Brokerage":
        draw_order = ["cash", "brokerage"]
    else:
        raise ValueError(f"Unknown withdrawal policy: {policy_name}")

    for source in draw_order:
        if amount_needed <= 0:
            break

        if source == "cash":
            amount = min(cash, amount_needed)
            cash -= amount
            amount_needed -= amount
            from_cash += amount
        elif source == "brokerage":
            amount = min(brokerage, amount_needed)
            brokerage -= amount
            amount_needed -= amount
            from_brokerage += amount
        elif source == "trad":
            amount = min(trad, amount_needed)
            trad -= amount
            amount_needed -= amount
            from_trad += amount
        elif source == "roth":
            amount = min(roth, amount_needed)
            roth -= amount
            amount_needed -= amount
            from_roth += amount

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
        "taxable_trad_withdrawal": from_trad,
        "shortfall": shortfall,
    }


def withdraw_for_spending(amount_needed, trad, roth, brokerage, cash):
    return withdraw_by_policy(
        amount_needed, trad, roth, brokerage, cash,
        "Cash then Brokerage then Trad then Roth"
    )


def withdraw_for_conversion_tax(amount_needed, trad, roth, brokerage, cash, conversion_tax_funding_policy):
    return withdraw_by_policy(
        amount_needed, trad, roth, brokerage, cash,
        conversion_tax_funding_policy
    )


# -----------------------------
# SOCIAL SECURITY
# -----------------------------
def annual_ss_benefit(base_benefit_at_67, claim_age):
    delta = claim_age - 67
    if delta < 0:
        benefit = base_benefit_at_67 * (1 - 0.06 * abs(delta))
    elif delta > 0:
        benefit = base_benefit_at_67 * (1 + 0.08 * delta)
    else:
        benefit = base_benefit_at_67
    return max(0.0, benefit)


def ss_start_year_from_current_age(start_year, current_age, claim_age):
    return int(start_year + (claim_age - current_age))


# -----------------------------
# COVERAGE STATUS
# -----------------------------
def get_coverage_status(year, primary_aca_end_year, spouse_aca_end_year):
    primary_on_aca = year <= primary_aca_end_year
    spouse_on_aca = year <= spouse_aca_end_year

    aca_lives = int(primary_on_aca) + int(spouse_on_aca)
    medicare_lives = 2 - aca_lives

    return {
        "primary_on_aca": primary_on_aca,
        "spouse_on_aca": spouse_on_aca,
        "aca_lives": aca_lives,
        "medicare_lives": medicare_lives,
    }


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
    conversion_tax_funding_policy = inputs["conversion_tax_funding_policy"]

    owner_current_age = int(inputs["owner_current_age"])
    spouse_current_age = int(inputs["spouse_current_age"])
    owner_claim_age = int(inputs["owner_claim_age"])
    spouse_claim_age = int(inputs["spouse_claim_age"])
    owner_ss_base = float(inputs["owner_ss_base"])
    spouse_ss_base = float(inputs["spouse_ss_base"])

    primary_aca_end_year = int(inputs["primary_aca_end_year"])
    spouse_aca_end_year = int(inputs["spouse_aca_end_year"])

    owner_ss_start = ss_start_year_from_current_age(START_YEAR, owner_current_age, owner_claim_age)
    spouse_ss_start = ss_start_year_from_current_age(START_YEAR, spouse_current_age, spouse_claim_age)

    owner_ss_annual = annual_ss_benefit(owner_ss_base, owner_claim_age)
    spouse_ss_annual = annual_ss_benefit(spouse_ss_base, spouse_claim_age)

    results = []
    total_federal_taxes = 0.0
    total_aca_cost = 0.0
    total_irmaa_cost = 0.0
    total_shortfall = 0.0
    prev_year = None

    aca_hit_years = 0
    irmaa_hit_years = 0
    first_irmaa_year = None
    max_magi = 0.0

    for year in years:
        if prev_year is not None:
            assert year > prev_year, "Year sequence error"
        prev_year = year

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
        cash = deposit_cash(total_ss, cash)

        conversion = min(annual_conversion, trad)
        trad -= conversion
        roth += conversion

        spend_result = withdraw_for_spending(
            annual_spending, trad, roth, brokerage, cash
        )
        trad = spend_result["trad"]
        roth = spend_result["roth"]
        brokerage = spend_result["brokerage"]
        cash = spend_result["cash"]

        other_ordinary_income = conversion + spend_result["taxable_trad_withdrawal"]

        tax_info = calculate_federal_tax(other_ordinary_income, total_ss)
        federal_tax = tax_info["federal_tax"]

        tax_result = withdraw_for_conversion_tax(
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

        aca_result = withdraw_for_spending(
            aca_cost, trad, roth, brokerage, cash
        )
        trad = aca_result["trad"]
        roth = aca_result["roth"]
        brokerage = aca_result["brokerage"]
        cash = aca_result["cash"]

        irmaa_result = withdraw_for_spending(
            irmaa_cost, trad, roth, brokerage, cash
        )
        trad = irmaa_result["trad"]
        roth = irmaa_result["roth"]
        brokerage = irmaa_result["brokerage"]
        cash = irmaa_result["cash"]

        trad, roth, brokerage, cash = normalize_balances(trad, roth, brokerage, cash)

        year_shortfall = (
            spend_result["shortfall"]
            + tax_result["shortfall"]
            + aca_result["shortfall"]
            + irmaa_result["shortfall"]
        )

        total_shortfall += year_shortfall
        total_federal_taxes += federal_tax
        total_aca_cost += aca_cost
        total_irmaa_cost += irmaa_cost

        if aca_cost > 0:
            aca_hit_years += 1

        if irmaa_cost > 0:
            irmaa_hit_years += 1
            if first_irmaa_year is None:
                first_irmaa_year = year

        max_magi = max(max_magi, magi)

        assert trad >= 0, "Traditional balance negative"
        assert roth >= 0, "Roth balance negative"
        assert brokerage >= 0, "Brokerage balance negative"
        assert cash >= 0, "Cash balance negative"
        assert federal_tax >= 0, "Federal tax negative"
        assert aca_cost >= 0, "ACA cost negative"
        assert irmaa_cost >= 0, "IRMAA cost negative"

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
            "Spend from Cash": spend_result["from_cash"],
            "Spend from Brokerage": spend_result["from_brokerage"],
            "Spend from Trad": spend_result["from_trad"],
            "Spend from Roth": spend_result["from_roth"],
            "Taxable Trad Withdrawal": spend_result["taxable_trad_withdrawal"],
            "Other Ordinary Income": other_ordinary_income,
            "Taxable SS": tax_info["taxable_ss"],
            "AGI": tax_info["agi"],
            "MAGI": magi,
            "Primary On ACA": coverage["primary_on_aca"],
            "Spouse On ACA": coverage["spouse_on_aca"],
            "ACA Lives": aca_lives,
            "Medicare Lives": medicare_lives,
            "Federal Tax": federal_tax,
            "Tax Paid from Cash": tax_result["from_cash"],
            "Tax Paid from Brokerage": tax_result["from_brokerage"],
            "Tax Paid from Trad": tax_result["from_trad"],
            "Tax Paid from Roth": tax_result["from_roth"],
            "Tax Funding Shortfall": tax_result["shortfall"],
            "ACA Cost": aca_cost,
            "IRMAA Cost": irmaa_cost,
            "Spend Need": annual_spending,
            "Shortfall": year_shortfall,
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
        "final_net_worth": float(df.iloc[-1]["Net Worth"]),
        "owner_ss_start": owner_ss_start,
        "spouse_ss_start": spouse_ss_start,
        "total_shortfall": total_shortfall,
        "max_magi": max_magi,
        "first_irmaa_year": first_irmaa_year,
        "aca_hit_years": aca_hit_years,
        "irmaa_hit_years": irmaa_hit_years,
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

    if round(max_conversion, 2) not in candidates:
        candidates.append(round(max_conversion, 2))

    candidates = sorted(set(candidates))
    assert len(candidates) > 0, "No candidate strategies generated"
    return candidates


def score_result(result):
    total_drag = result["total_federal_taxes"] + result["total_aca_cost"] + result["total_irmaa_cost"]
    return result["final_net_worth"] - (result["total_shortfall"] * 1000.0) - total_drag


def run_optimizer(base_inputs, max_conversion, conversion_step):
    candidates = build_candidate_strategies(max_conversion, conversion_step)

    summary_rows = []
    detailed_results = {}

    for candidate_conversion in candidates:
        candidate_inputs = dict(base_inputs)
        candidate_inputs["annual_conversion"] = candidate_conversion

        result = run_model(candidate_inputs)
        total_drag = result["total_federal_taxes"] + result["total_aca_cost"] + result["total_irmaa_cost"]

        row = {
            "Annual Conversion Strategy": candidate_conversion,
            "Final Net Worth": result["final_net_worth"],
            "Total Government Drag": total_drag,
            "Total Federal Taxes": result["total_federal_taxes"],
            "Total ACA Cost": result["total_aca_cost"],
            "Total IRMAA Cost": result["total_irmaa_cost"],
            "Total Shortfall": result["total_shortfall"],
            "Max MAGI": result["max_magi"],
            "ACA Hit Years": result["aca_hit_years"],
            "IRMAA Hit Years": result["irmaa_hit_years"],
            "First IRMAA Year": result["first_irmaa_year"] if result["first_irmaa_year"] is not None else "",
            "Score": score_result(result),
        }

        summary_rows.append(row)
        detailed_results[candidate_conversion] = result

    summary_df = pd.DataFrame(summary_rows).sort_values(
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

    return {
        "summary_df": summary_df,
        "best_conversion": best_conversion,
        "best_result": detailed_results[best_conversion],
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
        ("ACA Lives valid", df["ACA Lives"].isin([0, 1, 2]).all()),
        ("Medicare Lives valid", df["Medicare Lives"].isin([0, 1, 2]).all()),
    ]


def render_validation(df):
    all_pass = True
    for label, passed in build_validation_messages(df):
        if passed:
            st.success(f"PASS — {label}")
        else:
            st.error(f"FAIL — {label}")
            all_pass = False
    if all_pass:
        st.success("MODEL STATUS: PASS")
    else:
        st.error("MODEL STATUS: FAIL")


def render_result_block(title, result):
    df = result["df"]
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
    render_validation(df)
    st.dataframe(df, use_container_width=True)


# -----------------------------
# UI
# -----------------------------
st.title("Retirement Model — Phase 4.4")

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

st.header("Coverage Timing")

coverage_col1, coverage_col2 = st.columns(2)

with coverage_col1:
    primary_aca_end_year = st.number_input("Primary ACA End Year", min_value=START_YEAR, value=2030, step=1)

with coverage_col2:
    spouse_aca_end_year = st.number_input("Spouse ACA End Year", min_value=START_YEAR, value=2034, step=1)

st.header("Tax Funding Policy")

conversion_tax_funding_policy = st.selectbox(
    "How to fund federal tax on conversions/ordinary income",
    [
        "Cash then Brokerage",
        "Brokerage only",
        "Cash only",
        "Cash then Brokerage then Trad then Roth",
    ],
    index=0,
)

st.header("Single Strategy Test")
forced_conversion = st.number_input("Forced Annual Conversion", min_value=0.0, value=50000.0, step=5000.0)

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
    "annual_conversion": 0.0,
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

col_a, col_b = st.columns(2)

with col_a:
    if st.button("Run Single Strategy Test"):
        try:
            test_inputs = dict(base_inputs)
            test_inputs["annual_conversion"] = forced_conversion
            result = run_model(test_inputs)
            render_result_block(f"Single Strategy Test — ${forced_conversion:,.0f}/year", result)
        except AssertionError as e:
            st.error(f"VALIDATION FAILED: {e}")
        except Exception as e:
            st.error(f"UNEXPECTED ERROR: {e}")

with col_b:
    if st.button("Run Governor"):
        try:
            optimizer_output = run_optimizer(base_inputs, max_conversion, conversion_step)
            st.subheader("Winning Strategy")
            st.write(f"Winning Annual Conversion: ${optimizer_output['best_conversion']:,.0f}")
            st.dataframe(optimizer_output["summary_df"], use_container_width=True)
            render_result_block("Winning Strategy Details", optimizer_output["best_result"])
        except AssertionError as e:
            st.error(f"VALIDATION FAILED: {e}")
        except Exception as e:
            st.error(f"UNEXPECTED ERROR: {e}")
