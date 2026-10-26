# Sahay Architecture

## Hackathon MVP scope from the detailed build plan

The current build implements the requested lean demo path: chat and browser/optional-provider voice adapters, a code-controlled state machine, deterministic eligibility and EMI rules, synthetic UPI profiles, image/PDF document checks, local TF-IDF retrieval, mock mandate, a local handoff queue, and expanded synthetic analytics. UPI, bureau, KYC, mandate and disbursal systems remain mocked. The MVP includes a local sample-PDF explainer; approved real insurance products and proactive services remain roadmap work.

## Runtime

```text
Browser UI + transcript-confirmed speech input + speech synthesis
                         ↓ REST / JSON / multipart
                 Python local API server
                         ↓
        Session state machine + deterministic tools
      ↙             ↓              ↓              ↘
Synthetic UPI   Rules engine    Document check    Finance engine
profiles       YAML + reason   OpenCV + optional OCR  EMI / offer
                   codes             ↓
                         SQLite persistence
              sessions, consent, events, docs, tickets
                         ↓
               Funnel and handoff dashboard
```

## State transitions

`INTENT → CONSENT → ELIGIBILITY → KYC → OFFER → MANDATE → DONE`

- A consent decline ends the sample check.
- Short profile history routes to a mock human review ticket.
- Age, activity, affordability and amount rules create a preliminary offer or a reason-coded demo rejection.
- Failed image quality or name matching moves to `KYC_RETRY`. Three failed attempts create a context handoff ticket.
- A passed check creates a sample offer. The user can ask why, modify tenure, or accept.
- Acceptance moves to `MANDATE`; confirmation creates a synthetic reference and ends at `DONE` without contacting a bank.
- Frustration signals and explicit human requests create a handoff ticket.

## Modules and APIs

- `backend/agent.py`: state machine, transition events, handoff context, simulated mandate.
- `backend/rules.py`, `backend/finance.py`: versioned eligibility, reason codes, offer and EMI calculations.
- `backend/documents.py`: OpenCV image checks, optional Tesseract OCR/manual demo fallback, fuzzy name match, retry threshold.
- `backend/store.py`: SQLite schema for sessions, consent, events, masked document facts, and handoff tickets.
- `backend/analytics.py`: live local funnel and explicitly simulated seed generation.
- `backend/conversation.py`, `backend/llm.py`: deterministic conversation shell plus an optional OpenAI Responses wording adapter. It is disabled by default and cannot make eligibility or pricing decisions.

The demo API uses Python standard-library HTTP with REST/JSON and multipart uploads, not the plan’s recommended FastAPI/Pydantic stack. It is loopback-only.

Routes: `GET /api/health`, `/api/profiles`, `/api/analytics`, `/api/handoffs`, `/api/sessions/{id}`, `/api/sessions/{id}/consent`; `POST /api/sessions`, `/api/sessions/{id}/message`, `/api/sessions/{id}/consent`, `/api/sessions/{id}/documents`, `/api/sessions/{id}/handoff`, `/api/sessions/{id}/mandate`, `/api/emi`, `/api/analytics/seed`, and `/api/reset`.

## Security and decision boundaries

The local server binds to loopback. It uses no authentication because it is a local demo and must not be exposed as a public service. Uploads are limited to 10 MB, processed in memory and not written to disk. The database stores synthetic profile IDs, reason codes, user consent decisions and masked document names. It does not store chat transcripts or original files. A production deployment needs identity/session authentication, rate limits, HTTPS, encrypted storage, retention and deletion controls, access roles, request IDs and audit review.

No language model makes eligibility, offer, pricing, credit or identity decisions. The optional OpenAI wording adapter remains disabled by default. Uploaded policy PDF passages are retrieved locally; raw documents are not persisted or sent to an LLM. The UI names the result as a sample preliminary offer. A production LLM could explain approved product documents and reason codes, but must not change a deterministic result. It would need PII masking, prompt-injection defenses, structured tool schemas, number validation, timeouts and a template fallback.

## Roadmap

1. Configure and evaluate an approved LLM against adversarial and financial-number safety sets; current local RAG uses TF-IDF, not a hosted/vector database.
2. Add a provider key and test Sarvam Hinglish ASR/Hindi TTS on representative consented clips. Browser speech remains the fallback.
3. Add maintained OCR and document validations after a dummy-ID evaluation set and privacy review.
4. Replace mock UPI, bureau, KYC, offer and mandate interfaces only with authorized partner APIs and explicit consent.
5. Add handoff desk authentication, ticket assignment and service-level tracking.
6. Expand the local sample-PDF policy explainer with approved real product documents and separately reviewed policy advice boundaries.
7. Add production authentication, durable cross-process idempotency, privacy/retention controls, LLM latency/cost telemetry, fairness reviews, CI deployment, A/B testing and a measured baseline pilot.
