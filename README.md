# Sahay Financial Copilot

A local-first hackathon MVP for **“Make Insurance, Lending and Fintech simpler, faster and more human.”** The local demo includes a voice/chat loan journey, PDF policy explainer, document feedback, deterministic rules, a what-if EMI simulator, human handoff queue and synthetic drop-off analytics.

## Run locally

1. Install Python 3.10 or newer.
2. Install dependencies: `python -m pip install -r requirements.txt`
3. Start the server: `python server.py` (or `npm start` if Node.js is installed).
4. Open `http://127.0.0.1:4173`.

## Optional API keys

The demo works without API keys. To enable the optional OpenAI wording assistant locally, copy `.env.example` to `.env` in the project root, then set `OPENAI_API_KEY`, `OPENAI_MODEL` (default example: `gpt-4.1-mini`) and `ENABLE_LLM=true`. The OpenAI adapter only rewrites approved deterministic replies; it does not decide eligibility, approve loans or receive the raw user transcript. If the provider fails or returns unsafe/changed financial numbers, the app falls back to its deterministic reply.

For dedicated speech services, set `SPEECH_PROVIDER=sarvam` and `SARVAM_API_KEY` for Sarvam speech, or `SPEECH_PROVIDER=openai` and `OPENAI_API_KEY` for OpenAI transcription. Browser speech is the no-key default. External audio is sent only after the user explicitly grants voice consent in the app. Never put real keys in `.env.example`, source code or GitHub; `.env` is ignored by Git. On a hosting platform, add the same values in its Environment Variables/Secrets settings and redeploy. Live provider calls have not been verified in this demo.

The app uses local SQLite and FastAPI/Pydantic. Chat can stream over SSE; request IDs, input schemas and loopback rate limits are enabled in the local API. The app has a local Docker path too: `docker compose up --build`.

## Implemented MVP flow

- **Intent:** enter a requested amount and select one of five fictional UPI profiles.
- **Consent:** accept or decline a stated purpose before the eligibility check. The choice and disclosure version are written to the local consent dashboard.
- **Eligibility:** the versioned deterministic rules engine checks age, active-month history, inflow variability and EMI burden. Results include reason codes and plain language explanations.
- **Document check:** JPEG/PNG/WebP quality checks use OpenCV. Text-based PDFs are parsed in memory with pypdf. Optional Tesseract/manual OCR handles images; PAN shape and Aadhaar checksum checks are demo-only validations, not identity verification. Only masked IDs are returned; DOB is masked and no original file is stored.
- **Offer:** a deterministic reducing-balance EMI calculator produces an offer with rate, term, total interest, processing fee and total cost. The user can ask why or change tenure.
- **Mock mandate:** the user can complete a simulated e-NACH step. No bank is contacted and no money moves.
- **Analytics:** funnel, drop-off reasons, recovery rate, completion time, handoff metrics and a clearly labelled illustrative baseline use local sessions. The dashboard can add 30 marked synthetic sessions for a non-empty demo.
- **Handoff:** frustrated users, explicit requests and repeat document failures create a ticket with the current journey context.
- **Voice:** browser speech input and text-to-speech adapters let the customer confirm a transcript and hear a response. Hinglish support is limited; other languages are speech-input choices, not translated assistant replies.

## Explicit demo boundaries

This build does not connect real Paytm or UPI transactions, a credit bureau, regulated KYC, lender underwriting, disbursal, a real bank mandate or a live agent. The optional LLM adapter is wording-only and disabled unless explicitly configured. All five profiles, financial facts, rule outcomes and generated analytics are fictional. The rules engine's result is a simulated preliminary offer, never a real rejection or approval. Never upload real PAN, Aadhaar or financial documents. Images are processed in memory; only a masked name, quality score and match score are saved. Conversation text and original files are not stored.

OCR works only when Tesseract is separately installed and available to `pytesseract`. In its absence, the UI marks name entry as a manual demo fallback. OCR and image checks do not verify identity. The policy explainer uses local TF-IDF retrieval over sample docs or an uploaded PDF and returns source passages; it does not give coverage advice. Scanned PDFs without selectable text need OCR, which is not enabled for policy uploads.

## Tests and evals

Run:

```powershell
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python -m evals.run_golden
node --check src/app.js
```

The test suite covers EMI, eligibility tiers, consent, document retry/matching and handoff. The golden conversation set has 42 deterministic cases. CI runs syntax checks, unit tests and the golden set on pushes and pull requests.

## Repository map

- `index.html`, `src/`: responsive chat, application and analytics views
- `server.py`: local HTTP API and static server
- `backend/`: state machine, deterministic rules, finance, documents, persistence and analytics
- `config/rules.yaml`: versioned demo rules
- `data/mock_profiles.json`: synthetic transaction profiles
- `tests/`, `evals/`: unit checks and golden conversations
- `ARCHITECTURE.md`: production architecture, safety boundaries and roadmap
- `EVALUATION.md`: build-plan coverage, test results and remaining external dependencies

## Publish later

- GitHub: _to be added_
- Live demo: _to be added_
- Pitch deck: create after the repository and live demo URLs exist

See [EVALUATION.md](EVALUATION.md) for edge-case coverage, fixes and validation limits.
