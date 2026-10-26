"""Deterministic loan payment calculator with explicit fee disclosure."""
from __future__ import annotations
import math
from .config import RULES

def calculate_emi(amount: float, annual_rate: float, months: int) -> dict:
    if isinstance(amount, bool) or isinstance(annual_rate, bool): raise ValueError("Amount and rate must be finite numbers.")
    try: amount=float(amount); annual_rate=float(annual_rate)
    except (TypeError, ValueError, OverflowError) as exc: raise ValueError("Amount and rate must be finite numbers.") from exc
    if not math.isfinite(amount) or not math.isfinite(annual_rate): raise ValueError("Amount and rate must be finite numbers.")
    if isinstance(months, bool) or not isinstance(months, int): raise ValueError("Tenure must be a whole number of months.")
    if amount < 5000 or amount > 5_000_000: raise ValueError("Amount must be between ₹5,000 and ₹50,00,000.")
    if months < 1 or months > int(RULES["max_tenure_months"]): raise ValueError("Tenure must be between 1 and 36 months.")
    if annual_rate < 0 or annual_rate > 60: raise ValueError("Rate must be between 0% and 60%.")
    monthly=annual_rate/1200; emi=amount/months if monthly==0 else amount*monthly*(1+monthly)**months/((1+monthly)**months-1); total=emi*months; fee=float(RULES["processing_fee"])
    return {"principal":round(amount,2),"annual_rate_percent":annual_rate,"tenure_months":months,"emi":round(emi,2),"total_repayment":round(total,2),"total_interest":round(total-amount,2),"processing_fee":fee,"taxes_on_fee":None,"total_cost_before_taxes":round(total+fee,2),"demo_only":True}
