# Sahay MVP evaluation report

**Date:** 26 October 2026  
**Scope:** Local hackathon MVP only; all customer and transaction data are synthetic.

## Results

- **37 unit tests passed** across parsing, eligibility, consent, document checks, handoff, finance, API input validation and the complete mock journey.
- **42/42 golden conversation cases passed** for loan amount phrasing, ambiguous requests, amount boundaries, scam/OTP safety prompts and human handoff language.
- Python compile check passed: `python -m compileall -q backend server.py evals tests`.
- Frontend JavaScript syntax check passed: `node --check src/app.js`.

## Bugs found and fixed during the audit

- A salary statement such as “I earn 50k per month” and a bare age could be mistaken for a loan amount. Amount parsing now requires loan/request context.
- A range like “loan 5-10k” and malformed comma grouping could be interpreted as a valid amount. The parser now rejects these ambiguous forms while accepting valid Indian and international grouping.
- A vague response at the consent step was treated as consent decline. It now asks the user to answer yes/no and does not write a consent record or run eligibility until the user agrees.
- Non-finite EMI inputs (`NaN`, infinity), fractional month counts and invalid API JSON field types could yield invalid results or server errors. Calculator and API validation now reject these inputs.
- Uploads with unsupported image types could pass the initial format gate. The demo now accepts JPEG, PNG and WebP only, and rejects oversized, invalid and out-of-step uploads.
- Repeated handoff requests could create duplicate tickets. An open ticket is now reused for the same session.

## Coverage exercised

- Every synthetic profile returns a bounded decision with a rules version and reason codes.
- Consent accept, explicit decline, negative phrasing and unclear replies.
- Minimum/maximum EMI amount, tenure and rate; zero interest; invalid/non-finite values.
- Document name match, PAN-format failure, mismatch retry limit, unsupported format, malformed bytes, oversize input and wrong journey state.
- Full synthetic journey from intent through consent, eligibility, document check, offer, mock mandate and completion.
- API field types for booleans, numbers and whole-number months.
- Golden Hinglish/English loan requests, ambiguous amounts, amount limits, fraud/OTP warnings and human handoff.

## Not verified in this environment

- Browser-driven end-to-end interaction and visual rendering: local socket access is restricted here, so the app could not be opened against its local server from this test environment.
- Actual speech recognition/synthesis: depends on the user's browser and microphone permissions.
- Actual Tesseract OCR: requires a separately installed Tesseract binary; tests use controlled OCR text and mocked image metrics.
- Optional OpenAI wording mode: no live API call was made. It remains disabled by default and has only deterministic unit-level coverage.
- Paytm/UPI, bureau, lender underwriting, regulated KYC, disbursal, bank mandate and live human support: these are not connected in this MVP.
- Production security, load, concurrency and lender-policy validation: outside the local demo test scope.

## Run the checks

```powershell
python -m unittest discover -s tests -v
python -m evals.run_golden
node --check src/app.js
python -m compileall -q backend server.py evals tests
```

## Current implementation status against the build plan

| Build-plan item | Current status |
|---|---|
| Chat + speech | Chat streams over SSE. Browser speech is the default. Optional Sarvam STT/TTS and OpenAI transcription adapters are consent-gated and disabled until configured; no live provider call was made. |
| Rules + mock data | Implemented with deterministic YAML rules, reason codes, and five synthetic profiles. |
| Documents | Image quality checks, text-based PDF extraction, masked PAN/Aadhaar-format IDs, Verhoeff checksum, masked DOB and name matching. Scanned-policy OCR, Aadhaar identity verification and real KYC remain absent. |
| RAG and policy explanation | Local TF-IDF retrieval over sample loan/policy text and in-memory uploaded PDFs, with source labels. No embeddings/vector DB or approved real policy corpus. |
| EMI what-if | Interactive tenure slider calculates preview from the deterministic EMI endpoint; user can apply the selected tenure to the sample offer. |
| Drop-off dashboard | Funnel, failures, recovery rate, completion duration and handoffs implemented. “Traditional form” figures are explicitly illustrative assumptions, not measured results. |
| Human handoff | Local ticket queue supports open/in-progress/resolved states; no authenticated live support agent or external desk. |
| API/operations | FastAPI/Pydantic, SSE, request IDs, in-process rate limiting, in-process idempotency replay, request latency/status metrics. These controls are single-process demo safeguards, not production-grade distributed controls. |
| Deployment/pitch | Local Docker/CI project exists. GitHub remote, deployed URL, live-provider credentials, and pitch deck are still pending. The deck remains deferred until links are available. |

The local API and deterministic paths are testable without credentials. External speech and hosted deployment require provider/account setup; regulated identity and lending services require authorized partner sandboxes and compliance review.
