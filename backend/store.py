"""SQLite persistence for local demo sessions, consent, events, docs and handoffs."""
from __future__ import annotations
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from .config import ROOT

DB_PATH = ROOT / "data/sahay_demo.sqlite3"

def now() -> str:
    return datetime.now(timezone.utc).isoformat()

@contextmanager
def db_session():
    db=sqlite3.connect(DB_PATH,timeout=10)
    db.row_factory=sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def init_db():
    with db_session() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS sessions(session_id TEXT PRIMARY KEY, profile_id TEXT, state TEXT NOT NULL, language TEXT NOT NULL, consent INTEGER NOT NULL DEFAULT 0, requested_amount REAL, tenure_months INTEGER, decision_json TEXT, offer_json TEXT, retry_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(event_id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, step TEXT NOT NULL, event_type TEXT NOT NULL, status TEXT NOT NULL, detail TEXT, latency_ms INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, FOREIGN KEY(session_id) REFERENCES sessions(session_id));
        CREATE TABLE IF NOT EXISTS documents(document_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, doc_type TEXT NOT NULL, quality_score REAL, extracted_name_masked TEXT, name_match_score REAL, status TEXT NOT NULL, issues_json TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(session_id) REFERENCES sessions(session_id));
        CREATE TABLE IF NOT EXISTS handoffs(ticket_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, reason TEXT NOT NULL, context_summary TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(session_id) REFERENCES sessions(session_id));
        CREATE TABLE IF NOT EXISTS consent_records(consent_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, purpose TEXT NOT NULL, granted INTEGER NOT NULL, disclosure_version TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(session_id) REFERENCES sessions(session_id));
        """)

def create_session(profile_id: str, language: str) -> dict:
    sid=uuid.uuid4().hex[:12]
    timestamp=now()
    with db_session() as db:
        db.execute("INSERT INTO sessions(session_id,profile_id,state,language,created_at,updated_at) VALUES(?,?,?,?,?,?)",(sid,profile_id,"INTENT",language,timestamp,timestamp))
    log_event(sid,"INTENT","step_started","success",f"profile={profile_id}")
    return get_session(sid)

def get_session(sid: str) -> dict | None:
    with db_session() as db:
        row=db.execute("SELECT * FROM sessions WHERE session_id=?",(sid,)).fetchone()
    if not row: return None
    result=dict(row)
    for key in ("decision_json","offer_json"):
        raw=result.pop(key)
        result[key.removesuffix("_json")]=json.loads(raw) if raw else None
    return result

def update_session(sid: str, **fields):
    allowed={"state","language","consent","requested_amount","tenure_months","decision_json","offer_json","retry_count"}
    values={}
    for key,value in fields.items():
        if key not in allowed: continue
        values[key]=json.dumps(value,ensure_ascii=False) if key.endswith("_json") and not isinstance(value,str) else value
    if not values: return
    values["updated_at"]=now()
    clause=", ".join(f"{key}=?" for key in values)
    with db_session() as db:
        db.execute(f"UPDATE sessions SET {clause} WHERE session_id=?",(*values.values(),sid))

def log_event(sid: str, step: str, event_type: str, status: str, detail: str="", latency_ms: int=0):
    with db_session() as db:
        db.execute("INSERT INTO events(session_id,step,event_type,status,detail,latency_ms,created_at) VALUES(?,?,?,?,?,?,?)",(sid,step,event_type,status,detail[:500],latency_ms,now()))

def save_consent(sid: str, purpose: str, granted: bool):
    with db_session() as db:
        db.execute("INSERT INTO consent_records VALUES(?,?,?,?,?,?)",(uuid.uuid4().hex,sid,purpose,int(granted),"consent_v1",now()))

def get_consent_records(sid: str) -> list[dict]:
    with db_session() as db:
        rows=db.execute("SELECT purpose,granted,disclosure_version,created_at FROM consent_records WHERE session_id=? ORDER BY created_at",(sid,)).fetchall()
    return [dict(row) for row in rows]

def save_decision(sid: str, decision: dict): update_session(sid,decision_json=decision)
def save_offer(sid: str, offer: dict): update_session(sid,offer_json=offer)

def save_document(sid: str, doc_type: str, quality: float, name_masked: str, match: float, status: str, issues: list[str]) -> str:
    did=uuid.uuid4().hex[:12]
    with db_session() as db:
        db.execute("INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?)",(did,sid,doc_type,quality,name_masked,match,status,json.dumps(issues),now()))
    return did

def create_handoff(sid: str, reason: str, context: str) -> str:
    ticket="SH-"+uuid.uuid4().hex[:8].upper()
    with db_session() as db:
        db.execute("INSERT INTO handoffs VALUES(?,?,?,?,?,?)",(ticket,sid,reason,context,"OPEN",now()))
    log_event(sid,"HANDOFF","handed_off","success",reason)
    return ticket

def get_events(sid: str | None=None) -> list[dict]:
    with db_session() as db:
        rows=db.execute("SELECT * FROM events WHERE (? IS NULL OR session_id=?) ORDER BY event_id",(sid,sid)).fetchall()
    return [dict(row) for row in rows]

def list_handoffs() -> list[dict]:
    with db_session() as db: rows=db.execute("SELECT * FROM handoffs ORDER BY created_at DESC").fetchall()
    return [dict(row) for row in rows]

def get_open_handoff(sid: str) -> dict | None:
    with db_session() as db: row=db.execute("SELECT * FROM handoffs WHERE session_id=? AND status='OPEN' ORDER BY created_at DESC LIMIT 1",(sid,)).fetchone()
    return dict(row) if row else None

def update_handoff(ticket_id: str,status: str) -> bool:
    if status not in ("OPEN","IN_PROGRESS","RESOLVED"): raise ValueError("Unsupported handoff status.")
    with db_session() as db:
        result=db.execute("UPDATE handoffs SET status=? WHERE ticket_id=?",(status,ticket_id))
        return result.rowcount>0

def reset_db():
    with db_session() as db:
        for table in ("events","documents","handoffs","consent_records","sessions"):
            db.execute(f"DELETE FROM {table}")
