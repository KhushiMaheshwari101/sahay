"""Auditable deterministic eligibility and offer rules."""
from __future__ import annotations
from .config import RULES
from .store import log_event
from .finance import calculate_emi

def check_eligibility(session: dict, profile: dict) -> dict:
    amount=float(session.get("requested_amount") or 0); age=int(profile["age"]); income=float(profile["monthly_inflow"]); active=int(profile["active_months"]); existing=float(profile["existing_emi"]); variability=float(profile["inflow_variability"])
    reasons=[]; hard_fails=[]
    if not RULES["minimum_age"] <= age <= RULES["maximum_age"]: hard_fails.append("R02_AGE_OUT_OF_RANGE")
    if active < RULES["minimum_active_months"]: hard_fails.append("R07_SHORT_HISTORY")
    if not amount or amount < 5000: hard_fails.append("R08_AMOUNT_BELOW_MINIMUM")
    if hard_fails:
        decision={"decision":"REVIEW_REQUIRED" if "R07_SHORT_HISTORY" in hard_fails else "NOT_ELIGIBLE","reason_codes":hard_fails,"reason_text":reason_text(hard_fails),"requested_amount":amount,"monthly_inflow":income,"active_months":active,"rules_version":RULES["version"],"profile_id":session.get("profile_id")}
        log_event(session["session_id"],"ELIGIBILITY","step_failed","failure",",".join(hard_fails)); return decision
    rate_tier="stable" if variability <= RULES["stable_variability_max"] else "borderline" if variability >= RULES["borderline_variability_max"] else "standard"
    rate=float(RULES["interest_rate_tiers"][rate_tier]); max_emi=max(0,income*RULES["foir_cap"]-existing); max_principal=max_emi*12/(rate/1200) if rate else max_emi*12
    # Approximate principal supported by an EMI budget over 12 months.
    monthly_factor=(rate/1200)*(1+rate/1200)**12/((1+rate/1200)**12-1) if rate else 1/12
    max_principal=max_emi/monthly_factor if monthly_factor else 0
    offer_amount=min(amount,max_principal,income*RULES["max_income_multiple"])
    if existing/income >= RULES["foir_cap"]: reasons.append("R04_HIGH_EXISTING_EMI")
    if variability <= RULES["stable_variability_max"]: reasons.append("R01_STABLE_INFLOW")
    elif variability >= RULES["borderline_variability_max"]: reasons.append("R05_VARIABLE_INFLOW")
    if active < 12: reasons.append("R06_LIMITED_HISTORY")
    if offer_amount < amount: reasons.append("R09_AMOUNT_CAPPED_BY_AFFORDABILITY")
    if max_emi <= 0 or offer_amount < 5000:
        decision={"decision":"NOT_ELIGIBLE","reason_codes":reasons+["R04_HIGH_EXISTING_EMI"],"reason_text":reason_text(reasons+["R04_HIGH_EXISTING_EMI"]),"requested_amount":amount,"monthly_inflow":income,"active_months":active,"rules_version":RULES["version"],"profile_id":session.get("profile_id")}
        log_event(session["session_id"],"ELIGIBILITY","step_failed","failure",",".join(decision["reason_codes"])); return decision
    decision={"decision":"PRELIMINARY_OFFER","reason_codes":reasons,"reason_text":reason_text(reasons),"requested_amount":amount,"eligible_amount":round(offer_amount,2),"annual_rate_percent":rate,"rate_tier":rate_tier,"monthly_inflow":income,"existing_emi":existing,"active_months":active,"rules_version":RULES["version"],"profile_id":session.get("profile_id"),"is_final_approval":False}
    log_event(session["session_id"],"ELIGIBILITY","step_completed","success",",".join(reasons) or "R00_WITHIN_SAMPLE_RULES"); return decision

def reason_text(codes: list[str]) -> str:
    words={"R02_AGE_OUT_OF_RANGE":"This demo profile is outside the sample age range.","R07_SHORT_HISTORY":"The sample transaction history is shorter than six active months, so this flow needs a human review.","R08_AMOUNT_BELOW_MINIMUM":"The requested amount is below the demo minimum.","R04_HIGH_EXISTING_EMI":"Existing monthly payments use a large share of the sample monthly inflow.","R01_STABLE_INFLOW":"The sample profile shows relatively stable monthly inflows.","R05_VARIABLE_INFLOW":"Monthly inflows vary, so the sample offer uses a higher rate tier.","R06_LIMITED_HISTORY":"The profile has fewer than twelve months of activity, so the amount may be limited.","R09_AMOUNT_CAPPED_BY_AFFORDABILITY":"The requested amount is above the sample affordability cap, so the offer amount was reduced.","R00_WITHIN_SAMPLE_RULES":"The sample profile meets the demo rules for a preliminary offer."}
    return " ".join(words.get(code,code) for code in codes) or words["R00_WITHIN_SAMPLE_RULES"]
