import math
from config import BANKS, MONTHLY_INCOME


def monthly_payment(principal: float, annual_rate_pct: float, years: int) -> float:
    """Standard amortization monthly payment."""
    r = annual_rate_pct / 100 / 12
    n = years * 12
    if r == 0:
        return principal / n
    return principal * r * (1 + r) ** n / ((1 + r) ** n - 1)


def max_affordable_loan(monthly_income: float, annual_rate_pct: float, years: int, dbr_limit: float = 0.50) -> float:
    """Maximum loan based on Debt Burden Ratio (UAE standard: 50%)."""
    max_monthly_emi = monthly_income * dbr_limit
    r = annual_rate_pct / 100 / 12
    n = years * 12
    if r == 0:
        return max_monthly_emi * n
    return max_monthly_emi * ((1 + r) ** n - 1) / (r * (1 + r) ** n)


def total_cost(principal: float, annual_rate_pct: float, years: int) -> float:
    return monthly_payment(principal, annual_rate_pct, years) * years * 12


def rank_banks(property_price: float, is_resident: bool = True) -> list[dict]:
    """
    Given a property price, rank all banks by total cost of ownership
    over the fixed period + first 5 variable years.
    """
    results = []
    ltv_key = "max_ltv_resident" if is_resident else "max_ltv_nonresident"

    for bank in BANKS:
        ltv = bank[ltv_key] / 100
        max_loan = property_price * ltv
        down_payment = property_price - max_loan
        down_pct = (1 - ltv) * 100

        # Check eligibility: monthly income * 50% DBR must cover EMI
        emi = monthly_payment(max_loan, bank["fixed_rate"], bank["max_tenure"])
        dbr = emi / MONTHLY_INCOME * 100
        eligible = emi <= MONTHLY_INCOME * 0.50 and MONTHLY_INCOME >= bank["min_salary"]

        processing_fee = max_loan * bank["processing_fee_pct"] / 100

        # Estimate variable rate using current EIBOR (~5.3%) + margin
        eibor_estimate = 5.30
        variable_rate = eibor_estimate + bank["variable_rate_margin"]

        # Total interest cost estimate: fixed period at fixed rate + remaining at variable
        fixed_yrs = bank["fixed_period"]
        remaining_yrs = bank["max_tenure"] - fixed_yrs
        fixed_payments = monthly_payment(max_loan, bank["fixed_rate"], bank["max_tenure"]) * fixed_yrs * 12

        # Balance after fixed period
        r = bank["fixed_rate"] / 100 / 12
        n_total = bank["max_tenure"] * 12
        n_fixed = fixed_yrs * 12
        monthly_emi_fixed = monthly_payment(max_loan, bank["fixed_rate"], bank["max_tenure"])
        if r > 0:
            balance_after_fixed = max_loan * (1 + r) ** n_fixed - monthly_emi_fixed * ((1 + r) ** n_fixed - 1) / r
        else:
            balance_after_fixed = max_loan - monthly_emi_fixed * n_fixed

        variable_total = total_cost(balance_after_fixed, variable_rate, remaining_yrs)
        total_repayment = fixed_payments + variable_total + processing_fee

        results.append({
            "bank": bank["name"],
            "type": bank["type"],
            "fixed_rate": bank["fixed_rate"],
            "fixed_period_years": bank["fixed_period"],
            "variable_rate_estimate": round(variable_rate, 2),
            "max_tenure_years": bank["max_tenure"],
            "max_loan": round(max_loan),
            "down_payment": round(down_payment),
            "down_payment_pct": down_pct,
            "monthly_emi_fixed": round(emi),
            "dbr_pct": round(dbr, 1),
            "eligible": eligible,
            "processing_fee": round(processing_fee),
            "total_repayment_estimate": round(total_repayment),
            "total_interest_estimate": round(total_repayment - max_loan),
            "features": bank["features"],
            "min_salary_required": bank["min_salary"],
        })

    # Sort: eligible first, then by fixed rate asc, then processing fee asc
    results.sort(key=lambda x: (not x["eligible"], x["fixed_rate"], x["processing_fee"]))

    # Add rank
    for i, r in enumerate(results):
        r["rank"] = i + 1

    return results


def affordability_summary(monthly_income: float = MONTHLY_INCOME) -> dict:
    """Quick affordability summary at best available rate."""
    best_rate = min(b["fixed_rate"] for b in BANKS)
    max_tenure = max(b["max_tenure"] for b in BANKS)

    max_loan = max_affordable_loan(monthly_income, best_rate, max_tenure)
    max_property_80ltv = max_loan / 0.80  # 80% LTV for residents
    min_down_payment = max_property_80ltv * 0.20

    max_monthly_emi = monthly_income * 0.50
    estimated_annual_sc = 18 * 1500  # rough estimate: 1500 sqft at AED 18/sqft avg
    total_monthly_cost = max_monthly_emi + estimated_annual_sc / 12

    return {
        "monthly_income": monthly_income,
        "dbr_limit_pct": 50,
        "max_monthly_emi": round(max_monthly_emi),
        "best_rate_used": best_rate,
        "max_loan_amount": round(max_loan),
        "max_property_price_80ltv": round(max_property_80ltv),
        "min_down_payment_required": round(min_down_payment),
        "total_upfront_costs": {
            "down_payment_20pct": round(max_property_80ltv * 0.20),
            "dld_fee_4pct": round(max_property_80ltv * 0.04),
            "agent_commission_2pct": round(max_property_80ltv * 0.02),
            "mortgage_registration": 4300,
            "bank_processing_fee_1pct": round(max_loan * 0.01),
            "valuation_fee": 3500,
            "total_estimate": round(
                max_property_80ltv * 0.20
                + max_property_80ltv * 0.04
                + max_property_80ltv * 0.02
                + 4300
                + max_loan * 0.01
                + 3500
            ),
        },
    }
