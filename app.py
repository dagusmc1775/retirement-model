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

    # sanity
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


def withdraw_for_tax(amount_needed: float, trad: float, roth: float, brokerage: float, cash: float, policy_name: str) -> dict:
    return withdraw_by_policy(amount_needed, trad, roth, brokerage, cash, policy_name)


def normalize_balances(trad: float, roth: float, brokerage: float, cash: float) -> tuple:
    return (
        max(0.0, float(trad)),
        max(0.0, float(roth)),
        max(0.0, float(brokerage)),
        max(0.0, float(cash)),
    )


# -----------------------------
# CORE MODEL
# -----------------------------
def run_model(inputs: dict) -> dict:
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

    rows = []

    total_federal_taxes = 0.0
    total_aca_cost = 0.0
    total_irmaa_cost = 0.0
    total_shortfall = 0.0
    max_magi = 0.0

    aca_hit_years = 0
    irmaa_hit_years = 0
    first_irmaa_year = None

    for year in range(START_YEAR, END_YEAR + 1):
        soy_trad = trad
        soy_roth = roth
        soy_brokerage = brokerage
        soy_cash = cash

        # Growth
        trad *= (1 + growth)
        roth *= (1 + growth)
        brokerage *= (1 + growth)

        # SS cash inflow
        owner_ss = owner_ss_annual if year >= owner_ss_start else 0.0
        spouse_ss = spouse_ss_annual if year >= spouse_ss_start else 0.0
        total_ss = owner_ss + spouse_ss
        cash += total_ss

        # Conversion
        conversion = min(annual_conversion, trad)
        trad -= conversion
        roth += conversion

        # Spend
        spend_result = withdraw_for_spending(annual_spending, trad, roth, brokerage, cash)
        trad = spend_result["trad"]
        roth = spend_result["roth"]
        brokerage = spend_result["brokerage"]
        cash = spend_result["cash"]

        # Tax
        other_ordinary_income = conversion + spend_result["taxable_trad_withdrawal"]
        tax_info = calculate_federal_tax(other_ordinary_income, total_ss)
        federal_tax = tax_info["federal_tax"]
        magi = tax_info["agi"]

        tax_result = withdraw_for_tax(federal_tax, trad, roth, brokerage, cash, conversion_tax_funding_policy)
        trad = tax_result["trad"]
        roth = tax_result["roth"]
        brokerage = tax_result["brokerage"]
        cash = tax_result["cash"]

        # Coverage / ACA / IRMAA
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
        tax_shortfall = tax_result["shortfall"]
        aca_shortfall = aca_result["shortfall"]
        irmaa_shortfall = irmaa_result["shortfall"]
        year_shortfall = spend_shortfall + tax_shortfall + aca_shortfall + irmaa_shortfall

        total_federal_taxes += federal_tax
        total_aca_cost += aca_cost
        total_irmaa_cost += irmaa_cost
        total_shortfall += year_shortfall
        max_magi = max(max_magi, magi)

        if aca_cost > 0:
            aca_hit_years += 1
        if irmaa_cost > 0:
            irmaa_hit_years += 1
            if first_irmaa_year is None:
                first_irmaa_year = year

        net_worth = trad + roth + brokerage + cash

        rows.append({
            "Year": year,
            "SOY Trad": soy_trad,
            "SOY Roth": soy_roth,
            "SOY Brokerage": soy_brokerage,
            "SOY Cash": soy_cash,
            "Owner SS": owner_ss,
            "Spouse SS": spouse_ss,
            "Total SS": total_ss,
            "Conversion": conversion,
            "Other Ordinary Income": other_ordinary_income,
            "Taxable SS": tax_info["taxable_ss"],
            "AGI": tax_info["agi"],
            "MAGI": magi,
            "Primary On ACA": coverage["primary_on_aca"],
            "Spouse On ACA": coverage["spouse_on_aca"],
            "ACA Lives": aca_lives,
            "Medicare Lives": medicare_lives,
            "Federal Tax": federal_tax,
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
        })

    df = pd.DataFrame(rows)

    return {
        "df": df,
        "final_net_worth": float(df.iloc[-1]["Net Worth"]),
        "total_federal_taxes": float(total_federal_taxes),
        "total_aca_cost": float(total_aca_cost),
        "total_irmaa_cost": float(total_irmaa_cost),
        "total_shortfall": float(total_shortfall),
        "max_magi": float(max_magi),
        "aca_hit_years": int(aca_hit_years),
        "irmaa_hit_years": int(irmaa_hit_years),
        "first_irmaa_year": first_irmaa_year,
        "owner_ss_start": owner_ss_start,
        "spouse_ss_start": spouse_ss_start,
    }


# -----------------------------
# GOVERNOR DEBUG
# -----------------------------
def build_candidate_strategies(max_conversion: float, step: float) -> list:
    max_conversion = int(max(0, max_conversion))
    step = int(max(1, step))
    vals = list(range(0, max_conversion + 1, step))
    if vals[-1] != max_conversion:
        vals.append(max_conversion)
    return sorted(set(vals))


def run_governor_debug(base_inputs: dict, max_conversion: float, step: float):
    candidates = build_candidate_strategies(max_conversion, step)

    rows = []
    details = {}

    for conv in candidates:
        test_inputs = dict(base_inputs)
        test_inputs["annual_conversion"] = float(conv)

        result = run_model(test_inputs)
        drag = result["total_federal_taxes"] + result["total_aca_cost"] + result["total_irmaa_cost"]

        row = {
            "Conversion": float(conv),
            "Final Net Worth": result["final_net_worth"],
            "Total Shortfall": result["total_shortfall"],
            "Shortfall OK": result["total_shortfall"] <= 0.01,
            "Total Federal Taxes": result["total_federal_taxes"],
            "Total ACA Cost": result["total_aca_cost"],
            "Total IRMAA Cost": result["total_irmaa_cost"],
            "Total Government Drag": drag,
            "Max MAGI": result["max_magi"],
            "ACA Hit Years": result["aca_hit_years"],
            "IRMAA Hit Years": result["irmaa_hit_years"],
            "First IRMAA Year": result["first_irmaa_year"] if result["first_irmaa_year"] is not None else "",
        }

        rows.append(row)
        details[float(conv)] = result

    df = pd.DataFrame(rows)

    best_raw = df.sort_values(
        by=["Final Net Worth", "Total Government Drag", "Conversion"],
        ascending=[False, True, True]
    ).reset_index(drop=True)

    feasible = df[df["Shortfall OK"]].copy()
    if not feasible.empty:
        best_feasible = feasible.sort_values(
            by=["Final Net Worth", "Total Government Drag", "Conversion"],
            ascending=[False, True, True]
        ).reset_index(drop=True)
    else:
        best_feasible = df.sort_values(
            by=["Total Shortfall", "Final Net Worth", "Total Government Drag", "Conversion"],
            ascending=[True, False, True, True]
        ).reset_index(drop=True)

    return {
        "candidate_df": df.sort_values(by="Conversion").reset_index(drop=True),
        "best_raw_df": best_raw,
        "best_feasible_df": best_feasible,
        "details": details,
    }


# -----------------------------
# UI
# -----------------------------
st.title("Retirement Model — Debug Build")

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
    "How to fund federal tax",
    [
        "Cash then Brokerage",
        "Brokerage only",
        "Cash only",
        "Cash then Brokerage then Trad then Roth",
    ],
    index=0,
)

st.header("Governor Debug Inputs")
max_conversion = st.number_input("Max Annual Conversion To Test", min_value=0.0, value=100000.0, step=5000.0)
conversion_step = st.number_input("Conversion Step Size", min_value=1000.0, value=10000.0, step=1000.0)
inspect_conversion = st.number_input("Inspect Conversion Amount", min_value=0.0, value=0.0, step=10000.0)

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

if st.button("Run Governor Debug"):
    output = run_governor_debug(base_inputs, max_conversion, conversion_step)

    st.subheader("Best RAW Strategy")
    st.dataframe(output["best_raw_df"].head(5), use_container_width=True)

    st.subheader("Best FEASIBLE Strategy")
    st.dataframe(output["best_feasible_df"].head(5), use_container_width=True)

    st.subheader("Governor Candidate Table")
    st.dataframe(output["candidate_df"], use_container_width=True)

    chosen = float(inspect_conversion)
    if chosen not in output["details"]:
        st.warning("Inspect Conversion Amount was not one of the tested candidates.")
    else:
        result = output["details"][chosen]
        st.subheader(f"Yearly Debug for Chosen Conversion = ${chosen:,.0f}")
        st.write(f"Final Net Worth: ${result['final_net_worth']:,.0f}")
        st.write(f"Total Shortfall: ${result['total_shortfall']:,.0f}")
        st.write(f"Total Federal Taxes: ${result['total_federal_taxes']:,.0f}")
        st.write(f"Total ACA Cost: ${result['total_aca_cost']:,.0f}")
        st.write(f"Total IRMAA Cost: ${result['total_irmaa_cost']:,.0f}")
        st.write(f"Max MAGI: ${result['max_magi']:,.0f}")
        st.write(f"ACA Hit Years: {result['aca_hit_years']}")
        st.write(f"IRMAA Hit Years: {result['irmaa_hit_years']}")
        st.write(f"First IRMAA Year: {result['first_irmaa_year'] if result['first_irmaa_year'] is not None else 'None'}")
        st.dataframe(result["df"], use_container_width=True)
