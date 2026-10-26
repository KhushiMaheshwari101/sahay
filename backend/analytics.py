"""Synthetic funnel metrics and clearly labelled demo data."""
from __future__ import annotations
from datetime import datetime
import re
from .store import db_session, get_events, list_handoffs
STEPS=["INTENT","CONSENT","ELIGIBILITY","KYC","OFFER","MANDATE","DONE"]
def dashboard() -> dict:
    events=get_events(); started={}; completed={}; failures={}; failed_sessions=set(); recovered_sessions=set(); first_failure={}
    session_events={}
    for event in events:
        step=event["step"]; sid=event["session_id"]
        session_events.setdefault(sid,[]).append(event)
        if event["event_type"]=="step_started": started[step]=started.get(step,0)+1
        if event["event_type"]=="step_completed": completed[step]=completed.get(step,0)+1
        if event["status"] in ("failure","declined"):
            key=(sid,step); failed_sessions.add(key); first_failure.setdefault(key,event["event_id"])
            detail=event.get("detail") or ""
            code=re.search(r"failure_codes=([^;]+)",detail)
            reason=code.group(1) if code else detail.split(":",1)[-1].strip() or event["event_type"]
            label=f"{step}: {reason[:80]}"; failures[label]=failures.get(label,0)+1
        elif event["event_type"]=="step_completed" and (sid,step) in first_failure and event["event_id"]>first_failure[(sid,step)]:
            recovered_sessions.add((sid,step))
    with db_session() as db:
        total=db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        done=db.execute("SELECT COUNT(*) FROM sessions WHERE state='DONE'").fetchone()[0]
        sessions=db.execute("SELECT session_id,created_at FROM sessions").fetchall()
    durations=[]
    for session in sessions:
        rows=session_events.get(session["session_id"],[])
        finished=[e for e in rows if e["step"]=="DONE" and e["event_type"]=="step_completed"]
        if finished:
            try: durations.append(max(0,(datetime.fromisoformat(finished[-1]["created_at"])-datetime.fromisoformat(session["created_at"])).total_seconds()))
            except (TypeError,ValueError): pass
    funnel=[{"step":step,"started":started.get(step,0),"completed":completed.get(step,0),"dropoff":max(0,started.get(step,0)-completed.get(step,0))} for step in STEPS]
    latency=[event["latency_ms"] for event in events if event["latency_ms"]>0]
    recovery_rate=round(len(recovered_sessions)/max(1,len(failed_sessions))*100,1)
    return {"funnel":funnel,"failures":[{"reason":k,"count":v} for k,v in failures.items()],"handoff_count":len(list_handoffs()),"event_count":len(events),"session_count":total,"completed_sessions":done,"completion_rate_percent":round(done/max(1,total)*100,1),"average_latency_ms":round(sum(latency)/max(1,len(latency))),"recovery_rate_percent":recovery_rate,"recovered_steps":len(recovered_sessions),"failed_steps":len(failed_sessions),"average_time_to_complete_seconds":round(sum(durations)/max(1,len(durations)),1),"completed_duration_sample_count":len(durations),"illustrative_baseline":{"label":"Illustrative form baseline (assumption, not measured)","completion_rate_percent":35,"time_to_complete_seconds":720},"label":"Live local demo events. Seeded sessions are synthetic and must not be presented as real customer results."}

def seed_synthetic(count: int=30) -> dict:
    """Insert deterministic synthetic event sequences. Safe to rerun after first seed."""
    import random
    from .store import now
    if count<1 or count>50: raise ValueError("Seed 1 to 50 synthetic demo sessions.")
    random.seed(42); ts=now(); profiles=["stable_salaried","irregular_gig","short_history","high_emi","borderline"]
    with db_session() as db:
        existing=db.execute("SELECT COUNT(*) FROM sessions WHERE session_id LIKE 'SYN-%'").fetchone()[0]
        if existing: return {"inserted":0,"already_seeded":existing,"label":"SIMULATED DATA"}
        for i in range(count):
            sid=f"SYN-{i+1:03d}"; profile=profiles[i%len(profiles)]; state="DONE" if i%4 else "HANDOFF"; db.execute("INSERT INTO sessions(session_id,profile_id,state,language,consent,requested_amount,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(sid,profile,state,"Hinglish",1,50000,ts,ts))
            path=STEPS[:(7 if state=="DONE" else 5)]
            for step in path:
                db.execute("INSERT INTO events(session_id,step,event_type,status,detail,latency_ms,created_at) VALUES(?,?,?,?,?,?,?)",(sid,step,"step_started","success","SYNTHETIC_SEED",random.randint(120,1900),ts))
                if step=="KYC" and i%7==0:
                    db.execute("INSERT INTO events(session_id,step,event_type,status,detail,latency_ms,created_at) VALUES(?,?,?,?,?,?,?)",(sid,step,"step_failed","failure","SYNTHETIC: blurry_document",random.randint(120,500),ts))
                    db.execute("INSERT INTO events(session_id,step,event_type,status,detail,latency_ms,created_at) VALUES(?,?,?,?,?,?,?)",(sid,step,"step_completed","success","SYNTHETIC: recovered_after_retake",random.randint(120,900),ts))
                else: db.execute("INSERT INTO events(session_id,step,event_type,status,detail,latency_ms,created_at) VALUES(?,?,?,?,?,?,?)",(sid,step,"step_completed","success","SYNTHETIC_SEED",random.randint(120,1900),ts))
            if state=="HANDOFF": db.execute("INSERT INTO events(session_id,step,event_type,status,detail,latency_ms,created_at) VALUES(?,?,?,?,?,?,?)",(sid,"HANDOFF","handed_off","success","SYNTHETIC_SEED",random.randint(120,500),ts))
    return {"inserted":count,"label":"SIMULATED DATA"}

