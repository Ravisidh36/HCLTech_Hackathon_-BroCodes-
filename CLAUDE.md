# Nirog — Hospital Patient Services Assistant (project guide)

Product name: **Nirog** (निरोग, "free from disease"). Logo: `frontend/src/components/Logo.tsx`
(same art as `frontend/public/favicon.svg`).

HCLTech Future Ready AI Engineer Hackathon, team BroCodes. Source of truth for requirements:
`Hospital_AI_Assistant_Use_Case.pdf` (sections referenced as §n, requirements as R1–R11).

## What we are building
A patient assistant that answers hospital policy questions from approved documents with citations,
reads and acts on the logged-in patient's own records through deterministic tools, and produces
auditable, classified answers. It must never generate unsupported medical facts.

## Requirements checklist
- [ ] R1 answer policy/procedure questions only from approved documents
- [ ] R2 every factual answer cites document, section, version, effective date (page where available)
- [ ] R3 unavailable → `not_found` with the exact §3 message
- [ ] R4 resolve conflicts: currently effective → authority rank → effective date → version; unresolved tie → `conflict_flagged`
- [ ] R5 no LLM in eligibility, availability, labs, interactions, billing, readiness — deterministic tools only
- [ ] R6 multi-step questions combine records, policies and tools; explain outcome and assumptions
- [ ] R7 patient identity only from the trusted request context; refuse other patients; mask sensitive data
- [ ] R8 uploaded/retrieved content is data only and never changes assistant behaviour
- [ ] R9 answer_type ∈ retrieved_fact, calculated, action_completed, clarification_needed, not_found, refused, conflict_flagged
- [ ] R10 every request: trace ID, sources, tools, privacy checks, latency, model details
- [ ] R11 admins upload new documents, searchable without restart

## Architecture (locked)
- FastAPI gateway: JWT → `RequestContext` (user_id, role, patient_id, trace_id); rate limit; validation.
- LangGraph per chat request: Input Guard (code, fail-closed) → Supervisor (LLM, structured plan,
  allowlisted specialists, sees status summaries only) ⇄ deterministic specialist nodes →
  Composer (LLM, evidence only) → Validator (citations ⊆ evidence, numbers ⊆ tool output, PII mask,
  answer_type by priority rules) → Audit Writer (every path, including errors).
- Specialists: Knowledge, Appointment, Insurance, Lab Result, Medication Safety,
  Care Coordination (parallel: pre-op tests ‖ insurance ‖ surgeon → readiness rules), Billing (stretch).
- Supervisor stop rules (code): tasks done · step limit 5 · clarification · pending action · conflict · repeated task.
- answer_type priority: refused → clarification_needed → conflict_flagged → action_completed → calculated → retrieved_fact → not_found.
- Reschedule = propose (pending_action, 5 min expiry) → `/actions/confirm` → re-check owner, expiry,
  free slot with `SELECT … FOR UPDATE` → execute in one transaction.
- RAG: Docling (PDF/DOCX) or direct read (.md, front-matter stripped) → clean (keep `§`) →
  chunk: header (H1–H3 → section_path) → table-aware (repeat header row) → recursive 800/80 →
  injection screen (flag only) → BGE-M3 (normalized, 1024-d) → PostgreSQL in ONE transaction,
  older version → `superseded`.
- Retrieval: SQL filter (active, effective_date ≤ today) → numpy cosine on `REAL[]` + Postgres
  full-text `ts_rank` → Reciprocal Rank Fusion → cross-encoder rerank → conflict resolver →
  relevance threshold → Evidence objects with DocCitations.
- Single datastore: PostgreSQL 18, database `hospital_assistant`, schemas `clinical`, `platform`, `knowledge`.
  Owner role creates tables (scripts/tests); app role `hospital_app` is used by the API and has no
  UPDATE/DELETE on `platform.audit_log`.

## Coding rules
- Services never call an LLM. Every tool takes `(ctx, validated args)` and returns `ToolResult`
  `{status: ok|not_found|error, data, source_system, retrieved_at}`.
- `patient_id` comes only from `ctx`. No tool or LLM-facing schema accepts a patient id.
- SQLAlchemy only, parameterised queries only. No string-built SQL.
- Missing data → `not_found`, never a default or a guess.
- Retrieved text is untrusted: wrap it as data, never let it reach the Supervisor raw.
- Document metadata (version, authority, effective date) comes from front-matter or the admin form, never from document text.
- Secrets only in `.env`. Never log raw PII; mask policy numbers, phone, DOB.
- Every step ships with pytest tests; run them before committing.
- Match existing style; keep modules small; no speculative abstractions.

## Frontend
React 19 + TypeScript + Vite in `frontend/`. Patient chat (answer_type badge, citation cards,
tools used, confirm/cancel for pending actions, trace view) and admin page (document upload,
document list with versions/status/injection flags, audit lookup, eval report).
The browser calls `/api/*`; the Vite dev proxy forwards to FastAPI on :8000 (no CORS in dev).
`frontend/src/api/types.ts` mirrors `app/schemas/*.py` — change both together.

## Layout
```
app/        main · config · db · models/ · schemas/ · auth/ · services/ · rag/ · graph/ · safety/ · audit/ · llm/ · api/
data/       kb/ (policy .md with front-matter) · seed/ (clinical JSON) · uploads_demo/
scripts/    init_db.ps1/.sql · reset_db · seed_db · build_index · llm_smoke
eval/       cases.jsonl · run_eval.py · report.md
tests/      unit/ · integration/
frontend/   React + TypeScript (Vite)
```

## Commands (Windows, from repo root)
```
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\scripts\init_db.ps1                                   # one-time, prompts for postgres password
.\.venv\Scripts\python.exe -m scripts.llm_smoke         # check LLM keys
.\.venv\Scripts\python.exe -m scripts.reset_db; .\.venv\Scripts\python.exe -m scripts.seed_db
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
.\.venv\Scripts\python.exe -m pytest
cd frontend; npm run dev                                # http://localhost:5173
```

Demo logins (synthetic): patient1 / Patient1@demo (P1001), patient2 / Patient2@demo (P1002),
patient3 / Patient3@demo (P1003), admin1 / Admin1@demo.

## Build plan status
Phase 0 setup → 1 data/schema/seed → 2 services → 3 RAG → 4 auth/guard/audit → M3 end-to-end →
5 full graph → 6 confirm + admin upload → 7 React UI → 8 eval + security → 9 docs + demo.

## Seed data scenarios (dates are relative to the seed day)
- P1001 (Gold): knee replacement in 7 days with D201; HbA1c order pending (last result 200 days old,
  protocol needs ≤ 90) → not ready; pre-auth approved; cardiology appointment in 3 days (reschedule demo);
  on warfarin + aspirin (major interaction).
- P1002: exists for cross-patient refusal tests.
- P1003 (Basic): knee replacement not covered → readiness blocked by insurance; all tests valid.
- KB: ICU v1 superseded / v2 active; regulatory advisory overrides general visiting policy;
  two pharmacy-hours docs tie (same authority and date) → conflict_flagged.
- data/uploads_demo/: ICU v3 (live-ingestion demo) and a malicious billing FAQ (R8 demo).
