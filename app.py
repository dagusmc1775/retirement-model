import streamlit as st
import pandas as pd

# -----------------------------
# TAX
# -----------------------------
def calculate_tax(conversion, tax_rate):
    tax = conversion * tax_rate
    assert tax >= 0, "Taxes negative"
    return tax


# -----------------------------
# SPENDING / TAX PAYMENT
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


# -----------------------------
# MODEL
# -----------------------------
def run_model(inputs):
    years = list(range(2025, 2046))

    trad = inputs["trad"]
    roth = inputs["roth"]
    brokerage = inputs["brokerage"]
    cash = inputs["cash"]

    growth = inputs["growth"]
    tax_rate = inputs["tax_rate"]
    annual_spending = inputs["annual_spending"]
    annual_conversion = inputs["annual_conversion"]

    results = []
    total_taxes = 0
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

        # ---- Roth conversion
        conversion = min(annual_conversion, trad)
        trad -= conversion
        roth += conversion

        # ---- Tax on conversion
        taxes = calculate_tax(conversion, tax_rate)
        total_taxes += taxes

        # ---- Fund spending
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

        # ---- Fund taxes
        tax_result = withdraw_for_need(
            taxes,
            trad,
            roth,
            brokerage,
            cash
        )
        trad = tax_result["trad"]
        roth = tax_result["roth"]
        brokerage = tax_result["brokerage"]
        cash = tax_result["cash"]

        total_shortfall = spend_result["shortfall"] + tax_result["shortfall"]

        # ---- Validation
        assert trad >= -0.01, "Traditional balance negative"
        assert roth >= -0.01, "Roth balance negative"
        assert brokerage >= -0.01, "Brokerage balance negative"
        assert cash >= -0.01, "Cash balance negative"
        assert taxes >= 0, "Taxes negative"

        # Normalize tiny floating point drift
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
            "Conversion": conversion,
            "Taxes": taxes,
            "Spend Need": annual_spending,
            "Spent from Cash": spend_result["from_cash"],
            "Spent from Brokerage": spend_result["from_brokerage"],
            "Spent from Trad": spend_result["from_trad"],
            "Spent from Roth": spend_result["from_roth"],
            "Tax from Cash": tax_result["from_cash"],
            "Tax from Brokerage": tax_result["from_brokerage"],
            "Tax from Trad": tax_result["from_trad"],
            "Tax from Roth": tax_result["from_roth"],
            "Shortfall": total_shortfall,
            "EOY Trad": trad,
            "EOY Roth": roth,
            "EOY Brokerage": brokerage,
            "EOY Cash": cash,
            "Net Worth": net_worth,
        })

    df = pd.DataFrame(results)

    return df, total_taxes, net_worth


# -----------------------------
# UI
# -----------------------------
st.title("Retirement Model — Phase 2")

st.header("Inputs")

owner_age = st.slider("Owner SS Claim Age", 62, 70, 67)
spouse_age = st.slider("Spouse SS Claim Age", 62, 70, 67)

trad = st.number_input("Traditional Balance", min_value=0.0, value=500000.0, step=1000.0)
roth = st.number_input("Roth Balance", min_value=0.0, value=200000.0, step=1000.0)
brokerage = st.number_input("Brokerage Balance", min_value=0.0, value=300000.0, step=1000.0)
cash = st.number_input("Cash", min_value=0.0, value=50000.0, step=1000.0)

growth = st.number_input("Growth Rate (%)", min_value=0.0, value=5.0, step=0.1) / 100
tax_rate = st.number_input("Flat Tax Rate (%)", min_value=0.0, value=20.0, step=0.1) / 100
annual_spending = st.number_input("Annual Spending Need", min_value=0.0, value=80000.0, step=1000.0)
annual_conversion = st.number_input("Annual Roth Conversion", min_value=0.0, value=20000.0, step=1000.0)

inputs = {
    "trad": trad,
    "roth": roth,
    "brokerage": brokerage,
    "cash": cash,
    "growth": growth,
    "tax_rate": tax_rate,
    "annual_spending": annual_spending,
    "annual_conversion": annual_conversion,
}

if st.button("Run Simulation"):
    try:
        df, total_taxes, final_net_worth = run_model(inputs)

        validation_messages = []
        validation_messages.append(("Years strictly increasing", df["Year"].is_monotonic_increasing))
        validation_messages.append(("EOY Trad >= 0", (df["EOY Trad"] >= 0).all()))
        validation_messages.append(("EOY Roth >= 0", (df["EOY Roth"] >= 0).all()))
        validation_messages.append(("EOY Brokerage >= 0", (df["EOY Brokerage"] >= 0).all()))
        validation_messages.append(("EOY Cash >= 0", (df["EOY Cash"] >= 0).all()))
        validation_messages.append(("Taxes >= 0", (df["Taxes"] >= 0).all()))

        st.subheader("Summary")
        st.write(f"Total Taxes: ${total_taxes:,.0f}")
        st.write(f"Final Net Worth: ${final_net_worth:,.0f}")
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
