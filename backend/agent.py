"""Loan application state machine. LLM can be added behind this interface later."""
from __future__ import annotations
import re, uuid
from .config import PROFILES, RULES
from .finance import calculate_emi
from .rules import check_eligibility
from .store import get_session, update_session, log_event, save_consent, save_decision, save_offer, create_handoff, get_open_handoff

def parse_amount(text: str) -> float | None:
    # A number alone is ambiguous (it may be an income or age), so require
    # loan intent words or an explicit loan/currency unit around the amount.
    money=r"(?:₹|rs\.?\s*)?\s*(\d+(?:,\d+)*(?:\.\d+)?)\s*(k|thousand|hazaar|hajar|lakh|lac)?(?![\d,.-])"
    patterns=[rf"(?:loan|borrow|amount|need|want|chahiye)[^\d₹]{{0,18}}{money}",rf"{money}\s*(?:ka\s+loan|for a loan|for the loan|loan|rupees?|rs\.?|chahiye)\b"]
    for pattern in patterns:
        match=re.search(pattern,text,re.I)
        if match:
            groups=match.groups(); number=next((g for g in groups if g and re.fullmatch(r"[\d,]+(?:\.\d+)?",g)),None); suffix=next((g for g in groups if g and g.lower() in ("k","thousand","hazaar","hajar","lakh","lac")),"")
            if number:
                raw=number
                if "," in raw and not (re.fullmatch(r"\d{1,2}(?:,\d{2})*,\d{3}",raw) or re.fullmatch(r"\d{1,3}(?:,\d{3})+",raw)):
                    continue
                try: amount=float(raw.replace(",",""))
                except ValueError: continue
                return amount*(100000 if suffix.lower() in ("lakh","lac") else 1000 if suffix.lower() in ("k","thousand","hazaar","hajar") else 1)
    return None

def explain_offer(session: dict) -> str:
    offer=session.get("offer") or {}; decision=session.get("decision") or {}
    return f"The sample rules show a preliminary offer of ₹{offer.get('amount',0):,.0f}. Reason: {decision.get('reason_text','')} This is not a real approval."

def process_message(session_id: str, message: str) -> dict:
    session=get_session(session_id)
    if not session: raise ValueError("Session not found. Start a new demo journey.")
    text=message.strip(); q=text.lower(); state=session["state"]
    if not text: raise ValueError("Message cannot be empty.")
    if state=="HANDOFF":
        return {"reply":"Your demo support ticket is already open with journey context attached.","state":"HANDOFF"}
    if any(x in q for x in ("frustrated","angry","bakwaas","human","agent","person se")) or re.search(r"\bperson\b",q):
        ticket=create_handoff(session_id,"customer_requested_or_frustrated",f"state={state}; requested_amount={session.get('requested_amount')}; profile={session.get('profile_id')}; last_message_redacted={redact(text)}")
        update_session(session_id,state="HANDOFF")
        return {"reply":f"I’ll hand this to a person with the journey context. Demo ticket: {ticket}.","state":"HANDOFF","ticket_id":ticket}
    if state=="INTENT":
        amount=parse_amount(text)
        if amount is None:
            return {"reply":"What loan amount are you looking for? This demo uses fictional UPI activity, and nothing is checked until you review the data-use notice.","state":state}
        if amount<5000 or amount>500000:
            return {"reply":"For this demo, choose an amount from ₹5,000 to ₹5,00,000.","state":state}
        update_session(session_id,requested_amount=amount,state="CONSENT")
        log_event(session_id,"INTENT","step_completed","success",f"amount={amount}")
        log_event(session_id,"CONSENT","step_started","success")
        return {"reply":f"I heard ₹{amount:,.0f}. Before the sample eligibility check, may I use the selected demo profile’s fictional UPI inflow and existing EMI figures for this purpose? You can review the profile first.","state":"CONSENT","requires_consent":True,"consent_purpose":"Use fictional demo UPI profile for sample loan affordability check"}
    if state=="CONSENT":
        declined=bool(re.search(r"\b(?:no|nope|nah|nahi|decline|disagree|deny|don't|do not|not now)\b",q))
        accepted=bool(re.search(r"\b(?:yes|agree|allow|haan|ha|okay|ok)\b",q))
        if declined:
            save_consent(session_id,"mock_profile_affordability",False); log_event(session_id,"CONSENT","step_failed","declined")
            update_session(session_id,state="END",consent=0)
            return {"reply":"No problem. I stopped the sample eligibility check. You can restart any time.","state":"END"}
        if not accepted:
            return {"reply":"I have not used the sample profile yet. Please say yes to allow this demo check, or no to stop.","state":"CONSENT","requires_consent":True}
        profile=PROFILES[session["profile_id"]]
        save_consent(session_id,"mock_profile_affordability",True); update_session(session_id,consent=1)
        log_event(session_id,"CONSENT","step_completed","success","purpose=mock_profile_affordability")
        decision=check_eligibility(session,profile); save_decision(session_id,decision)
        if decision["decision"]=="REVIEW_REQUIRED":
            update_session(session_id,state="HANDOFF")
            ticket=create_handoff(session_id,"short_history_review",f"Sample profile {session['profile_id']} has short history; no real personal data used.")
            return {"reply":f"The sample profile has only {profile['active_months']} active months, below this demo rule. I will route this to a person for review. Ticket {ticket}.","state":"HANDOFF","decision":decision,"ticket_id":ticket}
        if decision["decision"]=="NOT_ELIGIBLE":
            update_session(session_id,state="REJECT_EXPLAIN")
            log_event(session_id,"REJECT_EXPLAIN","step_started","success")
            return {"reply":f"The sample rules cannot show an offer. {decision['reason_text']} This is not a real rejection or credit decision.","state":"REJECT_EXPLAIN","decision":decision}
        tenure=int(session.get("tenure_months") or 12)
        offer=build_offer(decision,tenure); save_offer(session_id,offer); update_session(session_id,state="KYC")
        log_event(session_id,"KYC","step_started","success")
        return {"reply":f"Sample pre-check complete. {decision['reason_text']} I can show a preliminary offer of ₹{offer['amount']:,.0f} at {offer['annual_rate_percent']}% for {tenure} months. Next, upload a dummy ID image to try quality and name-match checks. Do not use a real Aadhaar or PAN.","state":"KYC","decision":decision,"offer":offer,"show_document_upload":True}
    if state=="KYC_RETRY":
        return {"reply":"Please retake a dummy document in good light, make sure the full page is visible, and upload it again. After three failed attempts I will show the human handoff.","state":state,"show_document_upload":True}
    if state=="KYC":
        return {"reply":"Upload a dummy identity image to continue the simulated document check. This demo never verifies a real identity.","state":state,"show_document_upload":True}
    if state=="OFFER":
        match=re.search(r"(\d{1,2})\s*(?:months?|mos?)",q)
        if match:
            tenure=int(match.group(1))
            session=get_session(session_id); decision=session["decision"]
            offer=build_offer(decision,tenure); save_offer(session_id,offer)
            return {"reply":f"Updated the sample offer to {tenure} months. EMI is ₹{offer['emi']:,.0f}; total repayment is ₹{offer['total_repayment']:,.0f}, plus the disclosed ₹{offer['processing_fee']:,.0f} fee before taxes.","state":"OFFER","offer":offer}
        if any(x in q for x in ("why","kyun","reason")):
            return {"reply":explain_offer(session),"state":"OFFER","decision":session.get("decision"),"offer":session.get("offer")}
        if any(x in q for x in ("accept","continue","proceed","haan","yes")):
            update_session(session_id,state="MANDATE"); log_event(session_id,"OFFER","step_completed","success")
            return {"reply":"Offer accepted for this demo. Next is a mock auto-debit mandate. This will not contact a bank or move money.","state":"MANDATE","show_mandate":True,"offer":session.get("offer")}
        return {"reply":"You can ask “why this amount?”, change the tenure (for example, 24 months), or accept the sample offer to see the mock mandate step.","state":"OFFER","offer":session.get("offer")}
    if state=="MANDATE":
        return {"reply":"Review and confirm the mock mandate button. It does not connect to a bank or create a real e-NACH instruction.","state":"MANDATE","show_mandate":True}
    if state=="DONE": return {"reply":"This sample journey is already complete. No loan was applied for and no money moved.","state":"DONE"}
    if state in ("REJECT_EXPLAIN","END"): return {"reply":"This journey has ended. Start a new demo session to try another profile.","state":state}
    if state=="HANDOFF": return {"reply":"Your demo support ticket is open with journey context attached.","state":state}
    return {"reply":"I lost the current demo step. Start a new journey to continue.","state":state}

def build_offer(decision: dict, tenure: int) -> dict:
    tenure=max(1,min(int(tenure),int(RULES["max_tenure_months"])))
    result=calculate_emi(float(decision["eligible_amount"]),float(decision["annual_rate_percent"]),tenure)
    return {"amount":result["principal"],"annual_rate_percent":result["annual_rate_percent"],"tenure_months":tenure,"emi":result["emi"],"total_repayment":result["total_repayment"],"total_interest":result["total_interest"],"processing_fee":result["processing_fee"],"total_cost_before_taxes":result["total_cost_before_taxes"],"disclosure":"Fictional demo offer. Not an approval or lender quote."}

def consent(session_id: str, granted: bool) -> dict:
    session=get_session(session_id)
    if not session or session["state"]!="CONSENT": raise ValueError("This session is not waiting for consent.")
    return process_message(session_id,"yes" if granted else "no")

def handoff(session_id: str, reason: str="user_requested") -> dict:
    session=get_session(session_id)
    if not session: raise ValueError("Session not found.")
    existing=get_open_handoff(session_id)
    if existing:
        update_session(session_id,state="HANDOFF")
        return {"ticket_id":existing["ticket_id"],"state":"HANDOFF","context_summary":existing["context_summary"],"reply":"Your demo support ticket is already open with the journey context attached. No live agent was contacted."}
    context={"state":session["state"],"profile_id":session["profile_id"],"requested_amount":session.get("requested_amount"),"reason_codes":(session.get("decision") or {}).get("reason_codes",[]),"offer":session.get("offer")}
    ticket=create_handoff(session_id,reason,str(context)); update_session(session_id,state="HANDOFF")
    return {"ticket_id":ticket,"state":"HANDOFF","context_summary":context,"reply":"A demo human-support ticket was created with the current journey context. No live agent was contacted."}

def mock_mandate(session_id: str, confirmed: bool) -> dict:
    session=get_session(session_id)
    if not session or session["state"]!="MANDATE": raise ValueError("This session is not at the mandate step.")
    if not confirmed: return {"state":"MANDATE","reply":"The mock mandate was cancelled. No bank was contacted."}
    reference="DEMO-ENACH-"+uuid.uuid4().hex[:8].upper(); update_session(session_id,state="DONE")
    log_event(session_id,"MANDATE","step_completed","success","mock_reference_created"); log_event(session_id,"DONE","step_completed","success")
    return {"state":"DONE","reference":reference,"reply":f"Mock mandate complete. Reference {reference}. No bank account was linked, no mandate registered, and no money moved."}

def redact(text: str) -> str:
    text=re.sub(r"\b[A-Z]{5}\d{4}[A-Z]\b","[PAN REDACTED]",text,flags=re.I)
    text=re.sub(r"\b\d{12}\b","[ID REDACTED]",text)
    text=re.sub(r"\b[6-9]\d{9}\b","[PHONE REDACTED]",text)
    return text[:200]




