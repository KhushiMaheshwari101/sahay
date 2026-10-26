"""Loopback API and static server for the Sahay hackathon MVP."""
from __future__ import annotations
import json
import math
import os
import time
from email import policy
from email.parser import BytesParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse
from dotenv import load_dotenv
from backend import store
from backend.agent import consent, handoff, mock_mandate
from backend.analytics import dashboard, seed_synthetic
from backend.config import PROFILES, RULES
from backend.conversation import reply
from backend.documents import verify_upload
from backend.finance import calculate_emi
from backend.mock_upi import generate_monthly_inflows, generate_transactions
from backend.llm import configured as llm_configured

ROOT=Path(__file__).resolve().parent
MAX_BODY=10*1024*1024
load_dotenv(ROOT / ".env")
store.init_db()

def public_profiles():
    return [{"id":key,"label":p["label"],"name":p["name"],"type":p["profile_type"],"age":p["age"],"monthly_inflow":p["monthly_inflow"],"variability":p["inflow_variability"],"active_months":p["active_months"],"existing_emi":p["existing_emi"],"monthly_inflows":generate_monthly_inflows(key),"disclosure":"Synthetic demo UPI profile"} for key,p in PROFILES.items()]

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(ROOT),**kwargs)
    def end_headers(self):
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Referrer-Policy","no-referrer")
        self.send_header("Content-Security-Policy","default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; font-src 'self' data:; media-src 'self' blob:")
        super().end_headers()
    def translate_path(self,path):
        route=unquote(urlparse(path).path)
        if route in ("/","/index.html"): return str(ROOT/"index.html")
        if route=="/src/app.js": return str(ROOT/"src/app.js")
        if route=="/src/styles.css": return str(ROOT/"src/styles.css")
        return str(ROOT/"__not_found__")
    def local_request(self):
        host=self.headers.get("Host","").split(":",1)[0].strip("[]").lower()
        origin=self.headers.get("Origin")
        if host not in ("127.0.0.1","localhost"): return False
        if origin:
            origin_host=urlparse(origin).hostname
            if origin_host not in ("127.0.0.1","localhost"): return False
        return True
    def send_json(self,status,data):
        payload=json.dumps(data,ensure_ascii=False).encode("utf-8")
        self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Content-Length",str(len(payload))); self.end_headers(); self.wfile.write(payload)
    def body(self):
        try: length=int(self.headers.get("Content-Length","0"))
        except ValueError: raise ValueError("Invalid content length.")
        if length<0 or length>MAX_BODY: raise ValueError("Request must be under 10 MB.")
        return self.rfile.read(length)
    def json_body(self):
        try: value=json.loads(self.body() or b"{}")
        except json.JSONDecodeError as exc: raise ValueError("Send a valid JSON request.") from exc
        if not isinstance(value,dict): raise ValueError("JSON request must be an object.")
        return value
    @staticmethod
    def boolean_field(data,key):
        value=data.get(key,False)
        if not isinstance(value,bool): raise ValueError(f"'{key}' must be true or false.")
        return value
    @staticmethod
    def number_field(data,key,default):
        value=data.get(key,default)
        if isinstance(value,bool) or not isinstance(value,(int,float)):
            raise ValueError(f"'{key}' must be a number.")
        try: value=float(value)
        except (ValueError,OverflowError) as exc: raise ValueError(f"'{key}' must be a finite number.") from exc
        if not math.isfinite(value): raise ValueError(f"'{key}' must be a finite number.")
        return value
    @staticmethod
    def integer_field(data,key,default):
        value=data.get(key,default)
        if isinstance(value,bool) or not isinstance(value,int): raise ValueError(f"'{key}' must be a whole number.")
        return value
    def do_GET(self):
        if not self.local_request(): return self.send_json(403,{"error":"The local demo only accepts loopback requests."})
        path=urlparse(self.path).path
        if path=="/api/health": return self.send_json(200,{"ok":True,"service":"sahay-local-demo","rules_version":RULES["version"],"demo_only":True,"llm_enabled":llm_configured()})
        if path=="/api/profiles": return self.send_json(200,{"profiles":public_profiles(),"synthetic":True})
        if path.startswith("/api/profiles/") and path.endswith("/transactions"):
            profile_id=path.split("/")[3]
            if profile_id not in PROFILES: return self.send_json(404,{"error":"Unknown synthetic profile."})
            return self.send_json(200,{"transactions":generate_transactions(profile_id),"synthetic":True})
        if path=="/api/analytics": return self.send_json(200,dashboard())
        if path=="/api/handoffs": return self.send_json(200,{"tickets":store.list_handoffs(),"demo_only":True})
        if path.startswith("/api/sessions/") and path.endswith("/consent"):
            sid=path.split("/")[3]
            if not store.get_session(sid): return self.send_json(404,{"error":"Session not found."})
            return self.send_json(200,{"records":store.get_consent_records(sid),"demo_only":True})
        if path.startswith("/api/sessions/"):
            sid=path.rsplit("/",1)[1]; session=store.get_session(sid)
            if not session: return self.send_json(404,{"error":"Session not found."})
            return self.send_json(200,{"session":session})
        if path=="/api/products": return self.send_json(200,{"loan":{"name":"Sahay Sample Loan","rules_version":RULES["version"],"interest_rate_tiers":RULES["interest_rate_tiers"],"processing_fee":RULES["processing_fee"]},"demo_only":True})
        return super().do_GET()
    def do_POST(self):
        if not self.local_request(): return self.send_json(403,{"error":"The local demo only accepts loopback requests."})
        path=urlparse(self.path).path
        try:
            if path=="/api/sessions":
                data=self.json_body(); profile=data.get("profile_id","stable_salaried"); language=data.get("language","English")
                if not isinstance(profile,str) or profile not in PROFILES: raise ValueError("Choose one of the available synthetic profiles.")
                if language not in ("English","Hinglish","Hindi","Punjabi","Tamil"): raise ValueError("Unsupported language setting.")
                return self.send_json(201,{"session":store.create_session(profile,language),"disclosure":"Synthetic profile only. No real UPI data is accessed.","demo_only":True})
            if path=="/api/emi":
                data=self.json_body(); return self.send_json(200,calculate_emi(self.number_field(data,"amount",0),self.number_field(data,"annual_rate_percent",18),self.integer_field(data,"months",12)))
            if path=="/api/analytics/seed": return self.send_json(200,seed_synthetic(self.integer_field(self.json_body(),"count",30)))
            if path=="/api/reset": store.reset_db(); return self.send_json(200,{"ok":True,"message":"Demo database reset."})
            if path.startswith("/api/sessions/") and path.endswith("/language"):
                sid=path.split("/")[3]; data=self.json_body(); language=data.get("language","English")
                if language not in ("English","Hinglish","Hindi","Punjabi","Tamil"): raise ValueError("Unsupported language setting.")
                if not store.get_session(sid): return self.send_json(404,{"error":"Session not found."})
                store.update_session(sid,language=language)
                return self.send_json(200,{"language":language,"demo_only":True})
            if path.startswith("/api/sessions/") and path.endswith("/message"):
                sid=path.split("/")[3]; data=self.json_body(); started=time.perf_counter(); result=reply(sid,str(data.get("message","")))
                state=store.get_session(sid)["state"]; store.log_event(sid,state,"message_processed","success","redacted",round((time.perf_counter()-started)*1000))
                return self.send_json(200,result)
            if path.startswith("/api/sessions/") and path.endswith("/consent"):
                sid=path.split("/")[3]; data=self.json_body(); return self.send_json(200,consent(sid,self.boolean_field(data,"granted")))
            if path.startswith("/api/sessions/") and path.endswith("/handoff"):
                sid=path.split("/")[3]; data=self.json_body(); return self.send_json(200,handoff(sid,str(data.get("reason","user_requested"))))
            if path.startswith("/api/sessions/") and path.endswith("/mandate"):
                sid=path.split("/")[3]; data=self.json_body(); return self.send_json(200,mock_mandate(sid,self.boolean_field(data,"confirmed")))
            if path.startswith("/api/sessions/") and path.endswith("/documents"):
                sid=path.split("/")[3]; raw=self.body(); ctype=self.headers.get("Content-Type","")
                if "multipart/form-data" not in ctype: raise ValueError("Upload a file using multipart form data.")
                message=BytesParser(policy=policy.default).parsebytes(b"Content-Type: "+ctype.encode()+b"\r\nMIME-Version: 1.0\r\n\r\n"+raw)
                filepart=next((part for part in message.iter_parts() if part.get_filename()),None)
                if filepart is None: raise ValueError("Choose a dummy document image.")
                fields={}
                for part in message.iter_parts():
                    if not part.get_filename(): fields[part.get_param("name",header="content-disposition")]=part.get_content()
                result=verify_upload(sid,Path(filepart.get_filename()).name,filepart.get_content_type(),filepart.get_payload(decode=True) or b"",fields.get("doc_type","PAN"),fields.get("ocr_name",""))
                result["filename"]=Path(filepart.get_filename()).name
                return self.send_json(200,result)
            return self.send_json(404,{"error":"API route not found."})
        except ValueError as exc: return self.send_json(400,{"error":str(exc)})
        except Exception as exc:
            print(f"API error {type(exc).__name__}: {exc}")
            return self.send_json(500,{"error":"Local demo service error. Check the server terminal."})
    def log_message(self,fmt,*args): print("%s - %s"%(self.address_string(),fmt%args))

if __name__=="__main__":
    import uvicorn
    host=os.getenv("SAHAY_HOST","0.0.0.0" if os.getenv("RENDER")=="true" else "127.0.0.1")
    port=int(os.getenv("PORT","4173"))
    print(f"Sahay demo listening on {host}:{port}")
    uvicorn.run("backend.api:app",host=host,port=port,log_level="warning")



