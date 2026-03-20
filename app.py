import streamlit as st
import pandas as pd

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

    results = []
    total_taxes = 0

    prev_year = None

    for year in years:
        # Validation: year progression
        if prev_year is not None:
            assert year > prev_year, "Year sequence error"
        prev_year = year

        # Growth
        trad *= (1 + growth)
        roth *= (1 + growth)
        brokerage *= (1 + growth)

        # Simple conversion rule (placeholder)
        conversion = min(20000, trad)
        trad -= conversion
        roth += conversion

        # Taxes (flat placeholder)
        taxes = conversion * tax_rate
        total_taxes += taxes

        # Validation
        assert trad >= 0, "Traditional balance negative"
        assert roth >= 0, "Roth balance negative"
        assert brokerage >= 0, "Brokerage balance negative"
        assert taxes >= 0, "Taxes negative"

        net_worth = trad + roth + brokerage + cash

        results.append({
            "Year": year,
            "Trad": trad,
            "Roth": roth,
            "Brokerage": brokerage,
            "Cash": cash,
            "Conversion": conversion,
            "Taxes": taxes,
            "Net Worth": net_worth
        })

    df = pd.DataFrame(results)

    return df, total_taxes, net_worth


# -----------------------------
# UI
# -----------------------------
st.title("Retirement Model — Phase 1")

st.header("Inputs")

owner_age = st.slider("Owner SS Claim Age", 62, 70, 67)
spouse_age = st.slider("Spouse SS Claim Age", 62, 70, 67)

trad = st.number_input("Traditional Balance", value=500000.0)
roth = st.number_input("Roth Balance", value=200000.0)
brokerage = st.number_input("Brokerage Balance", value=300000.0)
cash = st.number_input("Cash", value=50000.0)

growth = st.number_input("Growth Rate (%)", value=5.0) / 100
tax_rate = st.number_input("Flat Tax Rate (%)", value=20.0) / 100

inputs = {
    "trad": trad,
    "roth": roth,
    "brokerage": brokerage,
    "cash": cash,
    "growth": growth,
    "tax_rate": tax_rate
}

if st.button("Run Simulation"):
    try:
        df, total_taxes, final_net_worth = run_model(inputs)

        st.subheader("Results Table")
        st.dataframe(df)

        st.subheader("Summary")
        st.write(f"Total Taxes: ${total_taxes:,.0f}")
        st.write(f"Final Net Worth: ${final_net_worth:,.0f}")

        st.success("VALIDATION: PASS")

    except AssertionError as e:
        st.error(f"VALIDATION FAILED: {e}")