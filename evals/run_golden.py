import json, uuid
from pathlib import Path
from backend import store
from backend.conversation import reply
ROOT=Path(__file__).resolve().parents[1]
CASES=[json.loads(line) for line in (ROOT/"evals/golden_conversations.jsonl").read_text(encoding="utf-8-sig").splitlines() if line.strip()]
def run():
    db_path=ROOT/"evals"/(".eval_"+uuid.uuid4().hex+".sqlite3")
    original=store.DB_PATH; store.DB_PATH=db_path; store.init_db(); passed=[]
    try:
        for case in CASES:
            session=store.create_session(case.get("profile","stable_salaried"),case.get("language","Hinglish"))
            result=reply(session["session_id"],case["message"])
            ok=result.get("state")==case["expected_state"] and case.get("expected_text","").lower() in result["reply"].lower()
            passed.append((ok,case["id"],result.get("state"),case["expected_state"]))
    finally:
        store.DB_PATH=original
        if db_path.exists(): db_path.unlink()
    print(f"Golden conversations: {sum(x[0] for x in passed)}/{len(passed)} passed")
    for ok,cid,state,expected in passed:
        if not ok: print(f"FAIL {cid}: expected {expected}, got {state}")
    return all(x[0] for x in passed)
if __name__=="__main__": raise SystemExit(0 if run() else 1)
