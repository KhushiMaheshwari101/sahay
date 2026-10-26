"""Typed local API for the Sahay demo. It is loopback-only and uses synthetic data."""
from __future__ import annotations
import json, logging, math, os, re, time, uuid
from threading import RLock
from collections import defaultdict, deque
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt
from dotenv import load_dotenv
from . import store
from .agent import consent, handoff, mock_mandate
from .analytics import dashboard, seed_synthetic
from .config import PROFILES, RULES
from .conversation import reply
from .documents import verify_upload
from .finance import calculate_emi
from .mock_upi import generate_monthly_inflows, generate_transactions
from .llm import configured as llm_configured
from .retrieval import search_text
from .speech import configured as speech_configured, provider_name as speech_provider, transcribe, synthesize

ROOT=Path(__file__).resolve().parents[1]
load_dotenv(ROOT/".env")
store.init_db()
app=FastAPI(title="Sahay demo API",version="1.0.0",docs_url="/api/docs",redoc_url=None)
@app.exception_handler(RequestValidationError)
async def validation_error(request,exc): return JSONResponse(status_code=422,content={"error":"Request fields are invalid.","details":exc.errors()})
@app.exception_handler(HTTPException)
async def http_error(request,exc): return JSONResponse(status_code=exc.status_code,content={"error":str(exc.detail)})
@app.exception_handler(ValueError)
async def value_error(request,exc): return JSONResponse(status_code=400,content={"error":str(exc)})
logging.basicConfig(level=logging.INFO,format="%(message)s")
request_counts=defaultdict(deque); idem_cache={}; idem_lock=RLock(); latency_samples=deque(maxlen=500); status_counts=defaultdict(int); window_seconds=60; max_body=10*1024*1024

class StrictModel(BaseModel): model_config=ConfigDict(extra="forbid")
class SessionCreate(StrictModel): profile_id: str="stable_salaried"; language: Literal["English","Hinglish","Hindi","Punjabi","Tamil"]="English"
class MessageBody(StrictModel): message: str=Field(min_length=1,max_length=4000)
class ConsentBody(StrictModel): granted: StrictBool
class LanguageBody(StrictModel): language: Literal["English","Hinglish","Hindi","Punjabi","Tamil"]
class HandoffBody(StrictModel): reason: str=Field(default="user_requested",max_length=80)
class MandateBody(StrictModel): confirmed: StrictBool
class EmiBody(StrictModel): amount: StrictFloat=Field(ge=5000,le=5_000_000,allow_inf_nan=False); annual_rate_percent: StrictFloat=Field(default=18,ge=0,le=60,allow_inf_nan=False); months: StrictInt=Field(default=12,ge=1,le=36)
class SeedBody(StrictModel): count: StrictInt=Field(default=30,ge=1,le=50)
class HandoffUpdate(StrictModel): status: Literal["OPEN","IN_PROGRESS","RESOLVED"]
class VoiceBody(StrictModel): session_id: str=Field(min_length=1,max_length=32); consent: StrictBool
class SpeechBody(VoiceBody): text: str=Field(min_length=1,max_length=2400); language: Literal["English","Hinglish","Hindi","Punjabi","Tamil"]="Hindi"

@app.middleware("http")
async def request_controls(request:Request,call_next):
    host=request.url.hostname or ""
    if host not in ("127.0.0.1","localhost","testserver"):
        return JSONResponse(status_code=403,content={"error":"The demo API only accepts loopback requests."})
    origin=request.headers.get("origin")
    if origin and (origin_host:=__import__("urllib.parse",fromlist=["urlparse"]).urlparse(origin).hostname) not in ("127.0.0.1","localhost"):
        return JSONResponse(status_code=403,content={"error":"Cross-origin requests are disabled."})
    try:
        length=int(request.headers.get("content-length","0"))
        if length>max_body: return JSONResponse(status_code=413,content={"error":"Request must be under 10 MB."})
    except ValueError: return JSONResponse(status_code=400,content={"error":"Invalid content length."})
    client=request.client.host if request.client else "local"
    bucket=request_counts[client]; now=time.monotonic()
    while bucket and now-bucket[0]>window_seconds: bucket.popleft()
    limit=20 if request.url.path.endswith(("/documents","/voice/transcribe","/voice/speak","/policies/explain")) else 120
    if len(bucket)>=limit: return JSONResponse(status_code=429,content={"error":"Too many requests. Wait a minute and retry."})
    bucket.append(now)
    supplied=request.headers.get("x-request-id","")
    request_id=supplied if re.fullmatch(r"[A-Za-z0-9_-]{1,64}",supplied) else uuid.uuid4().hex
    start=time.perf_counter()
    try: response=await call_next(request)
    except ValueError as exc: response=JSONResponse(status_code=400,content={"error":str(exc)})
    except Exception:
        logging.exception(json.dumps({"request_id":request_id,"path":request.url.path,"event":"request_error"}))
        response=JSONResponse(status_code=500,content={"error":"Local demo service error."})
    elapsed=round((time.perf_counter()-start)*1000,1)
    latency_samples.append(elapsed); status_counts[str(response.status_code)]+=1
    response.headers["X-Request-ID"]=request_id
    response.headers["X-Content-Type-Options"]="nosniff"
    response.headers["Referrer-Policy"]="no-referrer"
    response.headers["Cache-Control"]="no-store"
    response.headers["Content-Security-Policy"]="default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; font-src 'self' data:; media-src 'self' blob:"
    logging.info(json.dumps({"request_id":request_id,"method":request.method,"path":request.url.path,"status":response.status_code,"latency_ms":elapsed}))
    return response

def execute_idempotent(request:Request,action):
    key=request.headers.get("Idempotency-Key")
    if not key: return action()
    if len(key)>128: raise HTTPException(400,"Idempotency-Key must be 128 characters or fewer.")
    identity=(request.url.path,key)
    with idem_lock:
        cached=idem_cache.get(identity)
        if cached and time.monotonic()-cached[0]<3600: return cached[1]
        result=action(); idem_cache[identity]=(time.monotonic(),result)
        cutoff=time.monotonic()-3600
        for old,(created,_) in list(idem_cache.items()):
            if created<cutoff: idem_cache.pop(old,None)
        return result

def public_profiles():
    return [{"id":k,"label":p["label"],"name":p["name"],"type":p["profile_type"],"age":p["age"],"monthly_inflow":p["monthly_inflow"],"variability":p["inflow_variability"],"active_months":p["active_months"],"existing_emi":p["existing_emi"],"monthly_inflows":generate_monthly_inflows(k),"disclosure":"Synthetic demo UPI profile"} for k,p in PROFILES.items()]

@app.get("/")
def home(): return FileResponse(ROOT/"index.html")
@app.get("/index.html")
def index(): return FileResponse(ROOT/"index.html")
@app.get("/src/app.js")
def app_js(): return FileResponse(ROOT/"src/app.js",media_type="text/javascript")
@app.get("/src/styles.css")
def styles(): return FileResponse(ROOT/"src/styles.css",media_type="text/css")
@app.get("/api/health")
def health(): return {"ok":True,"service":"sahay-local-demo","rules_version":RULES["version"],"demo_only":True,"llm_enabled":llm_configured(),"speech_provider":speech_provider(),"speech_external_enabled":speech_configured()}
@app.get("/api/profiles")
def profiles(): return {"profiles":public_profiles(),"synthetic":True}
@app.get("/api/profiles/{profile_id}/transactions")
def transactions(profile_id:str):
    if profile_id not in PROFILES: raise HTTPException(404,"Unknown synthetic profile.")
    return {"transactions":generate_transactions(profile_id),"synthetic":True}
@app.get("/api/products")
def products(): return {"loan":{"name":"Sahay Sample Loan","rules_version":RULES["version"],"interest_rate_tiers":RULES["interest_rate_tiers"],"processing_fee":RULES["processing_fee"]},"demo_only":True}
@app.get("/api/analytics")
def analytics(): return dashboard()
@app.get("/api/handoffs")
def handoffs(): return {"tickets":store.list_handoffs(),"demo_only":True}
@app.patch("/api/handoffs/{ticket_id}")
def handoff_update(ticket_id:str,data:HandoffUpdate):
    if not store.update_handoff(ticket_id,data.status): raise HTTPException(404,"Handoff ticket not found.")
    return {"ticket_id":ticket_id,"status":data.status,"demo_only":True}
@app.get("/api/sessions/{sid}/consent")
def consent_records(sid:str):
    if not store.get_session(sid): raise HTTPException(404,"Session not found.")
    return {"records":store.get_consent_records(sid),"demo_only":True}
@app.get("/api/sessions/{sid}")
def session_get(sid:str):
    value=store.get_session(sid)
    if not value: raise HTTPException(404,"Session not found.")
    return {"session":value}
@app.post("/api/sessions",status_code=201)
def session_create(data:SessionCreate):
    if data.profile_id not in PROFILES: raise HTTPException(400,"Choose one of the available synthetic profiles.")
    return {"session":store.create_session(data.profile_id,data.language),"disclosure":"Synthetic profile only. No real UPI data is accessed.","demo_only":True}
@app.post("/api/emi")
def emi(data:EmiBody): return calculate_emi(data.amount,data.annual_rate_percent,data.months)
@app.post("/api/analytics/seed")
def analytics_seed(data:SeedBody): return seed_synthetic(data.count)
@app.post("/api/reset")
def reset(): store.reset_db(); return {"ok":True,"message":"Demo database reset."}
@app.post("/api/sessions/{sid}/language")
def language(sid:str,data:LanguageBody):
    if not store.get_session(sid): raise HTTPException(404,"Session not found.")
    store.update_session(sid,language=data.language); return {"language":data.language,"demo_only":True}
def process_chat(sid:str,data:MessageBody):
    start=time.perf_counter(); result=reply(sid,data.message); session=store.get_session(sid)
    store.log_event(sid,session["state"],"message_processed","success",result.get("response_mode","template"),round((time.perf_counter()-start)*1000))
    return result
@app.post("/api/sessions/{sid}/message/stream")
def message_stream(sid:str,data:MessageBody,request:Request):
    def events():
        yield "event: status\ndata: "+json.dumps({"message":"Checking the sample rules."})+"\n\n"
        try: result=execute_idempotent(request,lambda:process_chat(sid,data))
        except Exception as exc:
            yield "event: error\ndata: "+json.dumps({"error":str(exc) if isinstance(exc,ValueError) else "The demo could not complete this step."})+"\n\n"
            return
        words=result["reply"].split(" ")
        for i in range(0,len(words),4):
            yield "event: chunk\ndata: "+json.dumps({"text":" ".join(words[i:i+4])+(" " if i+4<len(words) else "")},ensure_ascii=False)+"\n\n"
        yield "event: result\ndata: "+json.dumps(result,ensure_ascii=False)+"\n\n"
    return StreamingResponse(events(),media_type="text/event-stream",headers={"X-Accel-Buffering":"no"})
@app.post("/api/sessions/{sid}/message")
def message(sid:str,data:MessageBody,request:Request):
    return execute_idempotent(request,lambda:process_chat(sid,data))
@app.post("/api/sessions/{sid}/consent")
def consent_post(sid:str,data:ConsentBody,request:Request):
    return execute_idempotent(request,lambda:consent(sid,data.granted))
@app.post("/api/sessions/{sid}/handoff")
def handoff_post(sid:str,data:HandoffBody,request:Request):
    return execute_idempotent(request,lambda:handoff(sid,data.reason))
@app.post("/api/sessions/{sid}/mandate")
def mandate_post(sid:str,data:MandateBody,request:Request):
    return execute_idempotent(request,lambda:mock_mandate(sid,data.confirmed))
@app.post("/api/voice/transcribe")
async def voice_transcribe(session_id:str=Form(...),consent:bool=Form(...),audio:UploadFile=File(...)):
    session=store.get_session(session_id)
    if not session: raise HTTPException(404,"Session not found.")
    store.save_consent(session_id,"external_speech_transcription",consent)
    if not consent: raise HTTPException(403,"External speech processing was not allowed. Use browser speech recognition or type instead.")
    raw=await audio.read(max_body+1)
    if len(raw)>max_body: raise HTTPException(413,"Audio clip must be under 10 MB.")
    try: result=await transcribe(raw,Path(audio.filename or "voice.webm").name,audio.content_type or "audio/webm",session["language"])
    except RuntimeError as exc: raise HTTPException(503,str(exc)) from exc
    except Exception as exc: raise HTTPException(502,"Speech service did not return a transcript. You can retry or type instead.") from exc
    return {**result,"stored":False,"demo_only":True}
@app.post("/api/voice/speak")
async def voice_speak(data:SpeechBody):
    session=store.get_session(data.session_id)
    if not session: raise HTTPException(404,"Session not found.")
    store.save_consent(data.session_id,"external_text_to_speech",data.consent)
    if not data.consent: raise HTTPException(403,"External speech processing was not allowed. Use browser speech synthesis instead.")
    try: audio,provider_request_id=await synthesize(data.text,data.language)
    except RuntimeError as exc: raise HTTPException(503,str(exc)) from exc
    except Exception as exc: raise HTTPException(502,"Speech service did not return audio. Browser speech is available as fallback.") from exc
    return Response(content=audio,media_type="audio/mpeg",headers={"X-Provider-Request-ID":provider_request_id,"Cache-Control":"no-store"})

@app.post("/api/policies/explain")
async def policy_explain(document:UploadFile=File(...),question:str=Form(default="What does this policy say?")):
    if document.content_type!="application/pdf" or not (document.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400,"Upload a text-based PDF policy document.")
    raw=await document.read(max_body+1)
    if len(raw)>max_body: raise HTTPException(413,"Upload must be under 10 MB.")
    from .documents import extract_pdf_text
    from .llm import _numbers
    text,pages=extract_pdf_text(raw)
    if not text.strip(): raise HTTPException(422,"This PDF has no selectable text. Scanned policy OCR is not enabled in this demo.")
    text=__import__("re").sub(r"\b[A-Z]{5}\d{4}[A-Z]\b","[ID MASKED]",text,flags=__import__("re").I)
    text=__import__("re").sub(r"(?<!\d)[2-9]\d{11}(?!\d)","[NUMBER MASKED]",text)
    passages=search_text(question,text,Path(document.filename or "policy.pdf").name,4)
    return {"answer":"Here are the most relevant passages from your uploaded document. Check the complete policy wording before making a decision.","passages":passages,"pages":pages,"stored":False,"demo_only":True}

@app.post("/api/sessions/{sid}/documents")
async def documents(sid:str,document:UploadFile=File(...),doc_type:str=Form("PAN"),ocr_name:str=Form("")):
    data=await document.read(max_body+1)
    if len(data)>max_body: raise HTTPException(413,"Upload must be under 10 MB.")
    result=verify_upload(sid,Path(document.filename or "document").name,document.content_type or "",data,doc_type,ocr_name)
    result["filename"]=Path(document.filename or "document").name; return result
@app.get("/api/metrics")
def metrics():
    ordered=sorted(latency_samples)
    p95=ordered[min(len(ordered)-1,math.ceil(len(ordered)*.95)-1)] if ordered else 0
    return {"requests_in_window":sum(len(v) for v in request_counts.values()),"active_clients":len(request_counts),"window_seconds":window_seconds,"rate_limit_per_minute":120,"status_counts":dict(status_counts),"latency_sample_count":len(ordered),"p95_latency_ms":round(p95,1),"llm_enabled":llm_configured(),"demo_only":True}
