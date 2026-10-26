import os, unittest, uuid
from pathlib import Path
from unittest.mock import patch
from backend import store
from backend.agent import consent, handoff, mock_mandate, parse_amount, process_message
from backend.config import PROFILES
from backend.conversation import reply
from backend.documents import valid_aadhaar_checksum, verify_upload
from backend.finance import calculate_emi
from backend.llm import configured, run_tool, _numbers
from backend.mock_upi import generate_monthly_inflows, generate_transactions
from backend.retrieval import search_faq
from backend.rules import check_eligibility
from server import Handler

class ProjectTest(unittest.TestCase):
    def setUp(self):
        self.old=store.DB_PATH
        self.db_path=Path(__file__).resolve().parent/(".test_"+uuid.uuid4().hex+".sqlite3")
        store.DB_PATH=self.db_path; store.init_db()
    def tearDown(self):
        store.DB_PATH=self.old
        if self.db_path.exists(): self.db_path.unlink()
    def session(self,profile="stable_salaried"):
        return store.create_session(profile,"Hinglish")
    def to_kyc(self,profile="stable_salaried"):
        session=self.session(profile)
        result=process_message(session["session_id"],"mujhe 50k ka loan chahiye")
        self.assertEqual(result["state"],"CONSENT")
        result=consent(session["session_id"],True)
        return session["session_id"],result

    def test_all_synthetic_profiles_return_a_valid_decision(self):
        for profile_id,profile in PROFILES.items():
            with self.subTest(profile=profile_id):
                session=self.session(profile_id); store.update_session(session["session_id"],requested_amount=50000)
                decision=check_eligibility(store.get_session(session["session_id"]),profile)
                self.assertIn(decision["decision"],{"PRELIMINARY_OFFER","REVIEW_REQUIRED","NOT_ELIGIBLE"})
                self.assertTrue(decision["reason_codes"])
                self.assertEqual(decision["rules_version"],"rules_v1.2")
                if decision["decision"]=="PRELIMINARY_OFFER":
                    self.assertGreaterEqual(decision["eligible_amount"],5000)
                    self.assertLessEqual(decision["eligible_amount"],50000)

    def test_emi_boundaries_and_zero_interest(self):
        zero=calculate_emi(5000,0,1)
        self.assertEqual(zero["emi"],5000)
        self.assertEqual(zero["total_interest"],0)
        self.assertEqual(calculate_emi(5000000,60,36)["principal"],5000000)
        for amount,rate,months in [(4999,18,12),(5000001,18,12),(50000,-0.01,12),(50000,60.01,12),(50000,18,0),(50000,18,37)]:
            with self.subTest(amount=amount,rate=rate,months=months), self.assertRaises(ValueError):
                calculate_emi(amount,rate,months)

    def test_emi_and_fee_are_separate(self):
        result=calculate_emi(50000,18,12)
        self.assertEqual(result["emi"],4584.0)
        self.assertEqual(result["processing_fee"],999)
        self.assertEqual(result["total_cost_before_taxes"],result["total_repayment"]+999)
    def test_stable_profile_gets_preliminary_offer_with_reasons(self):
        session=self.session(); store.update_session(session["session_id"],requested_amount=50000)
        decision=check_eligibility(store.get_session(session["session_id"]),PROFILES["stable_salaried"])
        self.assertEqual(decision["decision"],"PRELIMINARY_OFFER")
        self.assertIn("R01_STABLE_INFLOW",decision["reason_codes"])
        self.assertEqual(decision["rules_version"],"rules_v1.2")
    def test_short_history_routes_for_review(self):
        session=self.session("short_history"); store.update_session(session["session_id"],requested_amount=50000)
        decision=check_eligibility(store.get_session(session["session_id"]),PROFILES["short_history"])
        self.assertEqual(decision["decision"],"REVIEW_REQUIRED")
        self.assertIn("R07_SHORT_HISTORY",decision["reason_codes"])
    def test_high_existing_emi_is_not_eligible(self):
        session=self.session("high_emi"); store.update_session(session["session_id"],requested_amount=50000)
        decision=check_eligibility(store.get_session(session["session_id"]),PROFILES["high_emi"])
        self.assertEqual(decision["decision"],"NOT_ELIGIBLE")
        self.assertIn("R04_HIGH_EXISTING_EMI",decision["reason_codes"])
    def test_consent_decline_stops_eligibility(self):
        session=self.session(); process_message(session["session_id"],"need 50000 loan")
        result=consent(session["session_id"],False)
        self.assertEqual(result["state"],"END")
        self.assertEqual(store.get_session(session["session_id"])["consent"],0)
        self.assertEqual(store.get_consent_records(session["session_id"])[0]["granted"],0)
    def test_typed_negative_consent_cannot_be_misread_as_grant(self):
        session=self.session(); process_message(session["session_id"],"need 50000 loan")
        result=process_message(session["session_id"],"I do not consent")
        self.assertEqual(result["state"],"END")
        self.assertEqual(store.get_session(session["session_id"])["consent"],0)
    def test_consent_acceptance_routes_to_kyc(self):
        sid,result=self.to_kyc()
        self.assertEqual(result["state"],"KYC")
        self.assertGreater(result["offer"]["amount"],0)
    def test_document_name_match_passes_and_masks_name(self):
        sid,_=self.to_kyc()
        with patch("backend.documents.image_quality",return_value=(92.0,[],{"width":1000,"height":800,"sharpness":200,"brightness":140,"glare_fraction":0.01})):
            result=verify_upload(sid,"dummy.png","image/png",b"fake","PAN","Name: Aarav Mehta PAN: ABCDE1234F")
        self.assertTrue(result["passed"])
        self.assertEqual(result["state"],"OFFER")
        self.assertNotEqual(result["extracted_name_masked"],"Aarav Mehta")
        self.assertEqual(result["name_match_score"],1.0)
    def test_document_mismatch_enters_retry_state(self):
        sid,_=self.to_kyc()
        with patch("backend.documents.image_quality",return_value=(92.0,[],{"width":1000,"height":800,"sharpness":200,"brightness":140,"glare_fraction":0.01})):
            result=verify_upload(sid,"dummy.png","image/png",b"fake","PAN","Name: Nisha Verma PAN: ABCDE1234F")
        self.assertFalse(result["passed"]); self.assertEqual(result["state"],"KYC_RETRY")
        self.assertTrue(any("Name does not match" in issue for issue in result["issues"]))
    def test_pan_format_is_required(self):
        sid,_=self.to_kyc()
        with patch("backend.documents.image_quality",return_value=(92.0,[],{"width":1000,"height":800,"sharpness":200,"brightness":140,"glare_fraction":0.01})):
            result=verify_upload(sid,"dummy.png","image/png",b"fake","PAN","Name: Aarav Mehta")
        self.assertFalse(result["passed"]); self.assertTrue(any("PAN format" in issue for issue in result["issues"]))


    def test_pdf_text_and_aadhaar_checksum_validation(self):
        from reportlab.pdfgen.canvas import Canvas
        from io import BytesIO
        from backend.documents import extract_pdf_text
        buffer=BytesIO(); canvas=Canvas(buffer); canvas.drawString(50,750,"Name: Aarav Mehta DOB: 12/05/1994 PAN: ABCDE1234F"); canvas.save()
        text,pages=extract_pdf_text(buffer.getvalue())
        self.assertIn("Aarav Mehta",text); self.assertEqual(pages,1)
        first11="23456789012"; valid=next(first11+str(d) for d in range(10) if valid_aadhaar_checksum(first11+str(d)))
        self.assertTrue(valid_aadhaar_checksum(valid))
        self.assertFalse(valid_aadhaar_checksum(valid[:-1]+str((int(valid[-1])+1)%10)))
        sid,_=self.to_kyc()
        buffer=BytesIO(); canvas=Canvas(buffer); canvas.drawString(50,750,f"Name: Aarav Mehta DOB: 12/05/1994 AADHAAR: {valid}"); canvas.save()
        result=verify_upload(sid,"sample.pdf","application/pdf",buffer.getvalue(),"AADHAAR")
        self.assertTrue(result["passed"])
        self.assertTrue(result["dob_extracted"])
        self.assertEqual(result["dob_masked"],"**/**/1994")
        self.assertEqual(result["extracted_id_masked"],"XXXX XXXX "+valid[-4:])
        self.assertNotIn(valid,result["extracted_id_masked"])




    def test_fastapi_serves_the_ui_and_emi_accepts_integer_json(self):
        from fastapi.testclient import TestClient
        from backend.api import app
        client=TestClient(app)
        page=client.get("/")
        self.assertEqual(page.status_code,200); self.assertIn("Sahay",page.text)
        quote=client.post("/api/emi",json={"amount":50000,"annual_rate_percent":18,"months":12})
        self.assertEqual(quote.status_code,200); self.assertEqual(quote.json()["emi"],4584.0)

    def test_local_handoff_desk_status_lifecycle(self):
        from fastapi.testclient import TestClient
        from backend.api import app
        client=TestClient(app); sid,_=self.to_kyc(); ticket=handoff(sid,"customer_requested")["ticket_id"]
        response=client.patch(f"/api/handoffs/{ticket}",json={"status":"IN_PROGRESS"})
        self.assertEqual(response.status_code,200)
        self.assertEqual(store.list_handoffs()[0]["status"],"IN_PROGRESS")
        self.assertEqual(client.patch(f"/api/handoffs/{ticket}",json={"status":"RESOLVED"}).status_code,200)

    def test_policy_upload_route_returns_local_ranked_citations(self):
        from fastapi.testclient import TestClient
        from reportlab.pdfgen.canvas import Canvas
        from io import BytesIO
        from backend.api import app
        buffer=BytesIO(); canvas=Canvas(buffer); canvas.drawString(40,750,"Policy coverage includes a sum assured of 2500000."); canvas.drawString(40,720,"Exclusions include a first-year suicide exclusion."); canvas.save()
        response=TestClient(app).post("/api/policies/explain",files={"document":("policy.pdf",buffer.getvalue(),"application/pdf")},data={"question":"What are the exclusions?"})
        self.assertEqual(response.status_code,200)
        result=response.json(); self.assertFalse(result["stored"]); self.assertTrue(result["passages"])
        self.assertIn("Exclusions",result["passages"][0]["text"])

    def test_speech_provider_is_opt_in_and_records_decline(self):
        from fastapi.testclient import TestClient
        from backend.api import app
        with patch.dict(os.environ,{"SPEECH_PROVIDER":"sarvam","SARVAM_API_KEY":""}):
            client=TestClient(app); session=self.session(); sid=session["session_id"]
            response=client.post("/api/voice/transcribe",data={"session_id":sid,"consent":"false"},files={"audio":("clip.webm",b"fake","audio/webm")})
            self.assertEqual(response.status_code,403)
            records=store.get_consent_records(sid)
            self.assertEqual(records[-1]["purpose"],"external_speech_transcription")
            self.assertEqual(records[-1]["granted"],0)

    def test_fastapi_stream_and_idempotent_consent(self):
        from fastapi.testclient import TestClient
        from backend.api import app
        client=TestClient(app)
        created=client.post("/api/sessions",json={"profile_id":"stable_salaried","language":"English"})
        sid=created.json()["session"]["session_id"]
        streamed=client.post(f"/api/sessions/{sid}/message/stream",json={"message":"need 50000 loan"},headers={"Idempotency-Key":"stream-test"})
        self.assertEqual(streamed.status_code,200)
        self.assertIn("text/event-stream",streamed.headers["content-type"])
        self.assertIn("event: result",streamed.text)
        first=client.post(f"/api/sessions/{sid}/consent",json={"granted":True},headers={"Idempotency-Key":"consent-once"})
        second=client.post(f"/api/sessions/{sid}/consent",json={"granted":True},headers={"Idempotency-Key":"consent-once"})
        self.assertEqual(first.status_code,200); self.assertEqual(second.json(),first.json())
        self.assertEqual(len(store.get_consent_records(sid)),1)

    def test_dashboard_exposes_recovery_and_labeled_baseline(self):
        from backend.analytics import dashboard, seed_synthetic
        seed_synthetic(30); data=dashboard()
        self.assertGreater(data["recovery_rate_percent"],0)
        self.assertEqual(data["completed_duration_sample_count"],22)
        self.assertIn("assumption",data["illustrative_baseline"]["label"])

    def test_fastapi_contract_validation_and_request_id(self):
        from fastapi.testclient import TestClient
        from backend.api import app
        client=TestClient(app)
        response=client.post("/api/emi",json={"amount":"50000","months":12})
        self.assertEqual(response.status_code,422)
        self.assertIn("X-Request-ID",response.headers)
        self.assertIn("error",response.json())
        response=client.get("/api/health")
        self.assertEqual(response.status_code,200)

    def test_uptime_monitor_head_checks(self):
        from fastapi.testclient import TestClient
        from backend.api import app
        client=TestClient(app)
        for path in ("/", "/api/health"):
            with self.subTest(path=path):
                response=client.head(path)
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.content,b"")

    def test_document_upload_rejects_unsupported_format_before_decode(self):
        sid,_=self.to_kyc()
        for filename,content_type in (("fake.svg","image/svg+xml"),("fake.gif","image/gif"),("fake.png","text/plain")):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                verify_upload(sid,filename,content_type,b"not an image","PAN","")

    def test_document_upload_rejects_oversize_and_bad_image_bytes(self):
        sid,_=self.to_kyc()
        with self.assertRaisesRegex(ValueError,"10 MB"):
            verify_upload(sid,"large.png","image/png",b"x"*(10*1024*1024+1),"PAN","")
        with self.assertRaisesRegex(ValueError,"Could not open"):
            verify_upload(sid,"bad.png","image/png",b"not an image","PAN","")

    def test_upload_outside_kyc_state_is_rejected(self):
        session=self.session()
        with self.assertRaisesRegex(ValueError,"eligibility"):
            verify_upload(session["session_id"],"dummy.png","image/png",b"fake","PAN","")

    def test_three_document_failures_create_context_ticket(self):
        sid,_=self.to_kyc()
        with patch("backend.documents.image_quality",return_value=(92.0,[],{"width":1000,"height":800,"sharpness":200,"brightness":140,"glare_fraction":0.01})):
            for _ in range(3): result=verify_upload(sid,"dummy.png","image/png",b"fake","PAN","Name: Wrong Person PAN: ABCDE1234F")
        self.assertEqual(result["state"],"HANDOFF"); self.assertTrue(result["ticket_id"].startswith("SH-"))

    def test_unclear_consent_does_not_record_decline_or_start_eligibility(self):
        session=self.session(); sid=session["session_id"]
        process_message(sid,"need 50000 loan")
        result=process_message(sid,"Can you explain what data you use?")
        self.assertEqual(result["state"],"CONSENT")
        self.assertEqual(store.get_session(sid)["consent"],0)
        self.assertEqual(store.get_consent_records(sid),[])

    def test_repeated_handoff_reuses_existing_ticket(self):
        sid,_=self.to_kyc()
        first=handoff(sid,"customer_requested")
        second=handoff(sid,"customer_requested")
        self.assertEqual(first["ticket_id"],second["ticket_id"])
        self.assertEqual(len(store.list_handoffs()),1)

    def test_handoff_keeps_state_context(self):
        sid,_=self.to_kyc(); result=process_message(sid,"I am frustrated, connect a person")
        self.assertEqual(result["state"],"HANDOFF"); ticket=store.list_handoffs()[0]
        self.assertEqual(ticket["session_id"],sid); self.assertIn("profile=stable_salaried",ticket["context_summary"])
    def test_sample_faq_returns_source_passage(self):
        results=search_faq("what is the processing fee and interest rate")
        self.assertTrue(results); self.assertEqual(results[0]["source"],"data/loan_faq.md")
    def test_mock_upi_generator_is_reproducible(self):
        series=generate_monthly_inflows("stable_salaried")
        self.assertEqual(series,generate_monthly_inflows("stable_salaried")); self.assertEqual(len(series),12)
        tx=generate_transactions("stable_salaried"); self.assertTrue(tx); self.assertTrue(all(x["description"].startswith("Synthetic") for x in tx))
    def test_llm_requires_explicit_opt_in(self):
        with patch.dict(os.environ,{"OPENAI_API_KEY":"x","OPENAI_MODEL":"configured","ENABLE_LLM":"false"}): self.assertFalse(configured())
    def test_tool_emi_uses_same_rules_calculator(self):
        tool=run_tool("simulate_emi",{"amount":50000,"annual_rate_percent":18,"months":12})
        self.assertEqual(tool["emi"],calculate_emi(50000,18,12)["emi"])


    def test_api_routes_return_400_for_wrong_field_types(self):
        class RequestStub:
            path="/api/emi"
            def __init__(self,path,data):
                self.path=path; self.data=data; self.headers={"Host":"127.0.0.1"}; self.response=None
            def local_request(self): return True
            def json_body(self): return self.data
            def send_json(self,status,data): self.response=(status,data)
            boolean_field=staticmethod(Handler.boolean_field)
            number_field=staticmethod(Handler.number_field)
            integer_field=staticmethod(Handler.integer_field)
        emi=RequestStub("/api/emi",{"amount":"50000","months":12})
        Handler.do_POST(emi)
        self.assertEqual(emi.response[0],400)
        consent_request=RequestStub("/api/sessions/missing/consent",{"granted":"false"})
        Handler.do_POST(consent_request)
        self.assertEqual(consent_request.response[0],400)

    def test_complete_demo_path_with_document_and_mock_mandate(self):
        sid,_=self.to_kyc()
        with patch("backend.documents.image_quality",return_value=(92.0,[],{"width":1000,"height":800,"sharpness":200,"brightness":140,"glare_fraction":0.01})):
            checked=verify_upload(sid,"dummy.png","image/png",b"fake","PAN","Name: Aarav Mehta PAN: ABCDE1234F")
        self.assertEqual(checked["state"],"OFFER")
        accepted=process_message(sid,"accept offer")
        self.assertEqual(accepted["state"],"MANDATE")
        completed=mock_mandate(sid,True)
        self.assertEqual(completed["state"],"DONE")
        self.assertEqual(store.get_session(sid)["state"],"DONE")

    def test_amount_parser_requires_loan_context_and_rejects_ranges(self):
        self.assertIsNone(parse_amount("I earn 50k per month"))
        self.assertIsNone(parse_amount("my age is 25"))
        self.assertIsNone(parse_amount("loan 5-10k"))
        self.assertIsNone(parse_amount("loan amount 5,00,00"))
        self.assertEqual(parse_amount("I need 50k loan"),50000)
        self.assertEqual(parse_amount("loan amount 5,00,000"),500000)

    def test_emi_rejects_nonfinite_and_fractional_inputs(self):
        for amount,rate,months in [(float("nan"),18,12),(50000,float("inf"),12),(50000,18,1.5)]:
            with self.subTest(amount=amount,rate=rate,months=months), self.assertRaises(ValueError):
                calculate_emi(amount,rate,months)


    def test_api_numeric_fields_reject_strings_nulls_and_nonfinite_values(self):
        self.assertEqual(Handler.number_field({"amount":50000},"amount",0),50000)
        for value in ("50000",None,True,float("nan"),float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Handler.number_field({"amount":value},"amount",0)
        for value in (1.2,"12",True,None):
            with self.subTest(months=value), self.assertRaises(ValueError):
                Handler.integer_field({"months":value},"months",12)

    def test_api_boolean_fields_reject_truthy_strings(self):
        self.assertTrue(Handler.boolean_field({"granted":True},"granted"))
        for value in ("false","true",1,None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Handler.boolean_field({"confirmed":value},"confirmed")


    def test_model_rewrite_safety_guard_rejects_unsafe_advice(self):
        import sys
        from types import SimpleNamespace
        captured={}
        class FakeResponse:
            output=[SimpleNamespace(type="message")]
            output_text="You should take this loan. The sample EMI is ₹4,584."
        class FakeClient:
            def __init__(self,**kwargs): self.responses=self
            def create(self,**kwargs): captured.update(kwargs); return FakeResponse()
        fake=SimpleNamespace(OpenAI=FakeClient)
        with patch.dict(os.environ,{"ENABLE_LLM":"true","OPENAI_API_KEY":"test","OPENAI_MODEL":"test"}), patch.dict(sys.modules,{"openai":fake}):
            from backend.llm import explain_with_model
            result,mode=explain_with_model("The sample EMI is ₹4,584.","English",{"state":"OFFER"})
        self.assertEqual(result,"The sample EMI is ₹4,584.")
        self.assertEqual(mode,"template_safety_guard")
        self.assertNotIn("raw user transcript",str(captured.get("input")))

    def test_number_validator_preserves_financial_values(self):
        self.assertEqual(_numbers("EMI ₹4,584 at 18% over 12 months"),_numbers("EMI is ₹4584 at 18% for 12 months"))
        self.assertNotEqual(_numbers("EMI ₹4,600 at 18% over 12 months"),_numbers("EMI ₹4,584 at 18% over 12 months"))

if __name__=="__main__": unittest.main()

