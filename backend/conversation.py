"""Conversation shell: local RAG and state machine first, optional safe LLM wording second."""
from __future__ import annotations
import re
from .store import get_session
from .agent import process_message
from .retrieval import search_faq
from .llm import explain_with_model

def reply(session_id: str, message: str) -> dict:
    session=get_session(session_id)
    if not session: raise ValueError("Session not found.")
    q=message.lower()
    if re.search(r"\b(?:fake|forge|alter|falsify)\b.{0,30}\b(?:pan|aadhaar|identity|document|id)\b",q):
        return {"reply":"I can’t help create or alter identity documents. Use genuine documents for real services; this demo accepts only clearly fictional test samples.","state":session["state"],"intent":"document_safety","demo_only":True}
    if re.search(r"\b(?:scam|fraud|otp|unknown link|suspicious|advance fee|upfront fee)\b",q):
        return {"reply":"Pause before sharing details or paying. Do not share an OTP or pay a release fee. Verify the lender through an official channel. This demo cannot verify links or specific offers.","state":session["state"],"intent":"fraud_safety","demo_only":True}
    if re.search(r"\b(?:fee|fees|charge|charges|interest|rate|prepay|foreclosure|foir|documents?|mandate|processing|emi)\b",q) and not re.search(r"\b(?:i need|want|borrow|loan of|chahiye)\b",q):
        passages=search_faq(message)
        if passages:
            top=passages[0]
            result={"reply":f"From the fictional sample FAQ: {top['text']} This is demo product information, not a lender quote.","state":session["state"],"intent":"faq_retrieval","sources":[{"source":top["source"],"section":top["section"]}],"demo_only":True}
        else:
            result=process_message(session_id,message)
    else:
        result=process_message(session_id,message)
    facts={"state":result.get("state"),"reason_codes":(result.get("decision") or {}).get("reason_codes",[]),"offer":result.get("offer"),"faq_sources":result.get("sources",[])}
    wording,mode=explain_with_model(result["reply"],session["language"],facts)
    result["reply"]=wording
    result["response_mode"]=mode
    result["demo_only"]=True
    if session["language"]=="Hinglish" and mode=="template":
        result["reply"]=result["reply"].replace("What loan amount are you looking for?","Aapko kitne amount ka loan chahiye?").replace("No problem.","Koi baat nahi.")
    return result
