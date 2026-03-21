import streamlit as st
import pandas as pd

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
# TAX
# -----------------------------
def calculate_progressive_tax(income):
    income = max(0.0, income)
    tax = 0.0
    for i, (lb, rate) in enumerate(FEDERAL_BRACKETS_MFJ):
        ub = FEDERAL_BRACKETS_MFJ[i + 1][0] if i + 1 < len(FEDERAL_BRACKETS_MFJ) else float("inf")
        if income > lb:
            tax += (min(income, ub) - lb) * rate
    return max(0.0, tax)


def calculate_tax(other_income, ss):
    provisional = other_income + 0.5 * ss

    if provisional <= 32000:
        taxable_ss = 0
    elif provisional <= 44000:
        taxable_ss = 0.5 * (provisional - 32000)
    else:
        taxable_ss = 6000 + 0.85 * (provisional - 44000)

    taxable_ss = min(taxable_ss, 0.85 * ss)
    agi = other_income + taxable_ss
    taxable_income = max(0, agi - STANDARD_DEDUCTION_MFJ)
    tax = calculate_progressive_tax(taxable_income)

    return agi, tax


# -----------------------------
# ACA / IRMAA
# -----------------------------
def aca_cost(magi):
    if magi <= 30000:
        return 1103.76
    elif magi <= 84000:
        slope = (8364 - 1103.76) / (84000 - 30000)
        return 1103.76 + slope * (magi - 30000)
    elif magi <= 85000:
        return 8364
    else:
        return 27996


def irmaa_cost(magi):
    if magi <= 218000:
        m = 0
    elif magi <= 274000:
        m = 96
    elif magi <= 342000:
        m = 240
    elif magi <= 410000:
        m = 385
    elif magi <= 750000:
        m = 530
    else:
        m = 578
    return m * 12


# -----------------------------
# MODEL
# -----------------------------
def run_model(inputs):
    trad = inputs["trad"]
    roth = inputs["roth"]
    brokerage = inputs["brokerage"]
    cash = inputs["cash"]

    growth = inputs["growth"]
    spend = inputs["spend"]
    conversion = inputs["conversion"]
    policy = inputs["policy"]

    p_end = inputs["p_aca_end"]
    s_end = inputs["s_aca_end"]

    total_tax = 0
    total_aca = 0
    total_irmaa = 0
    total_shortfall = 0
    max_magi = 0

    for year in range(START_YEAR, END_YEAR + 1):

        trad *= (1 + growth)
        roth *= (1 + growth)
        brokerage *= (1 + growth)

        trad_conv = min(conversion, trad)
        trad -= trad_conv
        roth += trad_conv

        cash_needed = spend
        from_cash = min(cash, cash_needed)
        cash -= from_cash
        cash_needed -= from_cash

        from_brokerage = min(brokerage, cash_needed)
        brokerage -= from_brokerage
        cash_needed -= from_brokerage

        from_trad = min(trad, cash_needed)
        trad -= from_trad
        cash_needed -= from_trad

        shortfall = cash_needed

        income = trad_conv + from_trad
        agi, tax = calculate_tax(income, 0)

        tax_needed = tax

        if policy == "Cash then Brokerage":
            take = min(cash, tax_needed)
            cash -= take
            tax_needed -= take

            take = min(brokerage, tax_needed)
            brokerage -= take
            tax_needed -= take

        shortfall += tax_needed

        aca_lives = int(year <= p_end) + int(year <= s_end)
        medicare_lives = 2 - aca_lives

        aca = aca_cost(agi) * (aca_lives / 2)
        irmaa = irmaa_cost(agi) * (medicare_lives / 2)

        cash -= aca
        cash -= irmaa

        if cash < 0:
            shortfall += abs(cash)
            cash = 0

        total_tax += tax
        total_aca += aca
        total_irmaa += irmaa
        total_shortfall += shortfall
        max_magi = max(max_magi, agi)

    net = trad + roth + brokerage + cash

    return {
        "net": net,
        "tax": total_tax,
        "aca": total_aca,
        "irmaa": total_irmaa,
        "shortfall": total_shortfall,
        "magi": max_magi,
    }


# -----------------------------
# OPTIMIZER (DUAL OUTPUT)
# -----------------------------
def run_optimizer(inputs, max_conv, step):
    rows = []
    details = {}

    for c in range(0, int(max_conv) + 1, int(step)):
        i = dict(inputs)
        i["conversion"] = c

        r = run_model(i)

        row = {
            "Conversion": c,
            "Net": r["net"],
            "Shortfall": r["shortfall"],
            "Drag": r["tax"] + r["aca"] + r["irmaa"],
            "OK": r["shortfall"] < 1
        }

        rows.append(row)
        details[c] = r

    df = pd.DataFrame(rows)

    # Best raw (ignore shortfall)
    raw = df.sort_values("Net", ascending=False).iloc[0]

    # Best feasible
    feasible = df[df["OK"]]
    if not feasible.empty:
        feas = feasible.sort_values(["Net", "Drag"], ascending=[False, True]).iloc[0]
    else:
        feas = df.sort_values(["Shortfall", "Net"], ascending=[True, False]).iloc[0]

    return df, raw, feas


# -----------------------------
# UI
# -----------------------------
st.title("Retirement Model — Phase 4.5")

trad = st.number_input("Trad", value=500000.0)
roth = st.number_input("Roth", value=200000.0)
brokerage = st.number_input("Brokerage", value=300000.0)
cash = st.number_input("Cash", value=50000.0)

growth = st.number_input("Growth %", value=5.0) / 100
spend = st.number_input("Spending", value=80000.0)

p_aca_end = st.number_input("Primary ACA End", value=2030)
s_aca_end = st.number_input("Spouse ACA End", value=2034)

policy = st.selectbox("Tax Policy", ["Cash then Brokerage"])

max_conv = st.number_input("Max Conversion", value=100000)
step = st.number_input("Step", value=10000)

inputs = {
    "trad": trad,
    "roth": roth,
    "brokerage": brokerage,
    "cash": cash,
    "growth": growth,
    "spend": spend,
    "conversion": 0,
    "policy": policy,
    "p_aca_end": p_aca_end,
    "s_aca_end": s_aca_end,
}

if st.button("Run Governor"):
    df, raw, feas = run_optimizer(inputs, max_conv, step)

    st.subheader("Best RAW (Max Net Worth)")
    st.write(raw)

    st.subheader("Best FEASIBLE (No Shortfall)")
    st.write(feas)

    if raw["Conversion"] != feas["Conversion"]:
        st.warning("Feasible strategy differs from raw max — constraints are binding")

    st.dataframe(df.sort_values("Net", ascending=False))
