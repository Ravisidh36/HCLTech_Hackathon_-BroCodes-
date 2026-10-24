# Hospital Patient Services Assistant — Build Plan

Source: `Hospital_AI_Assistant_Use_Case.pdf` (CS2). Section refs like §3 R4 point to that document.
Labels: **[E]** explicitly required · **[R]** our recommendation · **[MOCK]** synthetic data we create.

---

## 1. Why Hospital

### Non-technical
- **Real, relatable problem.** Everyone has been confused by hospital processes, insurance coverage or pending tests. Judges instantly understand the value.
- **High-trust domain.** Healthcare forces you to show *responsible* AI: no invented medical facts, privacy, citations, audit. That is exactly what an AI-engineering hiring panel wants to see from a fresher.
- **Strong demo story.** "Can I proceed with surgery next week?" → the assistant checks tests, insurance, surgeon schedule and protocol, and explains why or why not. One question shows the whole system.
- **Clear evaluation criteria** [E §9]: answer accuracy, citation accuracy, privacy, tool accuracy, retrieval hit rate, action success, latency, safety. We can measure and show every one.

### Technical
- **It exercises every core AI-engineering skill in one project:** RAG with metadata, section-aware chunking, citations, version/conflict resolution, tool calling, multi-step orchestration, structured output, guardrails, prompt-injection defence, live ingestion, authN/authZ, audit, evaluation.
- **Matches your background:** document parsing, chunking, embeddings, ChromaDB, agents with bounded loops, FastAPI.
- **The doc rewards simple design** [E §10 Design Principle: "Use the simplest architecture… add specialized agents only where they provide clear operational value"]. A single well-guarded orchestrator is the *correct* answer, so we don't need risky multi-agent work.
- **Differentiators that are rare in hackathon builds:** deterministic policy-conflict resolution (R4), code-assigned answer types (R9), injection-proof ingestion (R8), live upload without restart (R11).

### Honest risks and how we handle them
| Risk | Mitigation |
|---|---|
| 11 hard requirements in limited time | Strict MVP list (section 6); everything else is stretch |
| Writing believable clinical content | Short, clearly fictional "Demo City Hospital" docs using generic, non-controversial facts; label all data synthetic |
| "Medical correctness" is judged | Assistant never gives medical advice; only quotes approved docs/tool output; refuses clinical decisions |
| LLM tool-call flakiness | Low temperature, strict schemas, deterministic composite tools for critical paths, eval set |

---

## 2. What the case study asks, in plain words

**Mission [E §1]:** an assistant that **answers questions** and **performs authorized actions** using hospital policies, patient records, insurance, schedules, reports and operational systems. It must be **accurate, grounded, explainable, secure** and **never generate unsupported medical facts**.

### 2.1 The 8 question types it must handle [E §2]
| Type | Example | What it really means for us |
|---|---|---|
| Policy fact | ICU visiting hours? | RAG over policy docs → answer + citation |
| Procedure | How do I schedule an MRI? | RAG → step list from approved procedure doc |
| Personal data | My upcoming appointments? | Tool `get_appointments` for the logged-in patient only |
| Eligibility check | Am I covered for knee replacement? | Tool `check_insurance_eligibility`: rules in code, LLM only explains |
| Lab result | Latest blood sugar? | Tool `get_lab_results` |
| Multi-step | Surgery recommended, which pre-op tests are pending? | Combine orders (tool) + protocol (RAG) + results (tool) |
| Action | Reschedule my cardiology appointment | Tool `get_available_slots` → user confirms → `reschedule_appointment` |
| Not found | Treatment info for a condition not handled | `answer_type: not_found` with the exact JSON from §3 |

### 2.2 The 11 functional requirements [E §3], explained
| Req | What they ask | What we build |
|---|---|---|
| R1 Knowledge retrieval | Answer policy/procedure questions **only** from approved documents | Chroma index of approved docs; LLM answers only from retrieved chunks |
| R2 Citations | Every factual answer cites **document, section, version, effective date** | Each chunk stores these four fields as metadata; response `citations[]` built from metadata, not LLM text |
| R3 No hallucinations | Unavailable → `not_found` | Relevance threshold + "no evidence → not_found" rule in code |
| R4 Version & policy resolution | Conflicts resolved by **guideline hierarchy, regulatory authority, version, effective date**; apply current version | Deterministic resolver on metadata: filter to currently effective → rank by authority → latest effective date → highest version; unresolved tie → `conflict_flagged` |
| R5 Deterministic tools | LLM must **not** calculate eligibility; use tools for insurance, appointment availability, labs, medication interactions, billing, care-plan compliance | Python functions over SQLite; LLM only calls them and explains |
| R6 Multi-step reasoning | Combine records + policies + tools; explain outcome **and assumptions** | Bounded tool loop; response has `explanation` + `assumptions` |
| R7 Authorization & privacy | Identity from **trusted request context**; refuse other patients; mask sensitive data | `patient_id` from JWT only; tools have no patient_id argument; masking of IDs/phone/policy numbers |
| R8 Untrusted content | Uploaded PDFs/emails are **data only** | Retrieved text wrapped as untrusted data; instruction-like text flagged at ingestion; behaviour never changes |
| R9 Answer classification | Distinguish 7 types (§6) | `answer_type` assigned **by code** from what actually happened |
| R10 Auditability | Trace ID + retrieved sources, tools invoked, privacy checks, latency, **model details** | `audit_log` table + trace viewer |
| R11 Live ingestion | Authorized admins upload new protocols/policies/advisories **without restart** | `POST /admin/documents`, admin-only; parse → chunk → embed → upsert live |

### 2.3 Other sections
- **§4 Multi-step scenario:** "Can I proceed with surgery next week?" → (1) required pre-op tests (2) completion status (3) insurance authorization (4) surgeon schedule (5) readiness checklist → grounded answer. **This is our headline demo.**
- **§5 Data sources:** knowledge base (SOPs, clinical protocols, nursing, admission, discharge, insurance agreements, emergency) + systems (EMR, appointments, lab, radiology, billing, pharmacy). **None are provided; we mock them.**
- **§6 Answer types:** `retrieved_fact`, `calculated`, `action_completed`, `clarification_needed`, `not_found`, `refused`, `conflict_flagged`.
- **§7 Tools:** `get_appointments`, `check_insurance_eligibility`, `get_lab_results`, `drug_interaction_check` (shapes given).
- **§8 Response format:** `trace_id, answer, answer_type, citations, tools_invoked, explanation`.
- **§9 Evaluation:** 8 metrics, no numeric targets (ours are proposed).
- **§10 Maturity:** levels 1–7. **Target: solid Level 4 (tool-based) + Level 7 production touches** (audit, observability, guardrails, continuous evaluation). Multi-agent (Level 6) is explicitly optional.

---

## 3. What we build — components

1. **Mock data [MOCK]**
   - *Knowledge base* (~10 short Markdown docs with front-matter):
     `doc_id, title, category, topic, version, effective_date, expiry_date?, authority(regulatory|clinical_guideline|hospital_policy|department_sop), status(active|superseded|draft)`.
     Must include: ICU visiting hours **v1 (superseded) and v2 (active)**; a **government advisory** that overrides a hospital policy (authority conflict); MRI scheduling procedure; knee-replacement / general surgery pre-op protocol; admission policy; discharge guidelines; insurance agreement summary; emergency procedure; **two same-authority, same-date docs that disagree** (to demo `conflict_flagged`); one **malicious "uploaded" doc** for the R8 demo.
   - *Structured data* (SQLite): patients P1001–P1003, insurance plans & coverage rules, doctors & slots, appointments, lab results, orders (pre-op tests), medications, drug-interaction table, surgery bookings.
2. **Ingestion pipeline:** parse MD/PDF (pypdf) → **section-aware chunking** (split on headings, so each chunk knows its section) → embed → Chroma upsert with metadata. Same code for seed and live upload. Superseded docs stay indexed but are filtered out (needed for history and the conflict demo).
3. **Retriever + policy resolver:** vector top-k (+ keyword boost on topic) → filter `status=active AND effective_date ≤ today` → group by `topic` → rank by authority → effective date → version → tie at top with different doc_ids ⇒ `conflict_flagged`. Pure Python, no LLM.
4. **Deterministic tools:** see section 4.
5. **Orchestrator:** pre-guards → LLM with tool calling (max 6 steps, temp 0) → post-validator → answer_type classifier → response.
6. **Guards:** cross-patient detector, medical-advice/unsafe request detector, injection markers, citation validator, PII masking.
7. **Auth:** JWT login, roles `patient`, `admin` (+ `staff` stretch).
8. **Audit & observability:** one row per request; latency per step; model name, token counts.
9. **UI (Streamlit):** login; patient chat with answer-type badge, citation cards, "confirm action" button, trace expander; admin page with document upload + metadata form, document list with versions, audit viewer, eval results.
10. **Evaluation harness:** `eval/cases.jsonl` (~30 cases) → `eval/run_eval.py` → `eval/report.md` with all §9 metrics.

---

## 4. Rough architecture

```
┌──────────────────────── Streamlit UI ────────────────────────┐
│  Patient chat  │  Confirm-action button  │  Admin: upload docs, audit, eval │
└───────────────────────────────┬──────────────────────────────┘
                                │ HTTPS + JWT
┌───────────────────────────────▼──────────────────────────────┐
│ FastAPI                                                       │
│  Auth middleware → {user_id, role, patient_id}  (R7)          │
│  Rate limit · input validation · trace_id · timer             │
│                                                               │
│  ┌──────────── Orchestrator (single agent, ≤6 steps) ───────┐ │
│  │ 1 Pre-guards: other-patient ref? clinical advice?        │ │
│  │   injection? → refused / clarification_needed            │ │
│  │ 2 LLM (temp 0) with tool schemas (NO patient_id args)    │ │
│  │ 3 Tool executor ──┬─ search_policies → Retriever+Resolver│ │
│  │                   ├─ get_appointments / get_slots        │ │
│  │                   ├─ reschedule_appointment (confirm)    │ │
│  │                   ├─ check_insurance_eligibility         │ │
│  │                   ├─ get_lab_results                     │ │
│  │                   ├─ get_pending_preop_tests             │ │
│  │                   ├─ drug_interaction_check              │ │
│  │                   └─ surgery_readiness_check (composite) │ │
│  │ 4 Post-validator: citations ⊆ retrieved chunks,          │ │
│  │   numbers/dates from tool output, PII masked             │ │
│  │ 5 answer_type assigned by code (R9)                      │ │
│  └──────────────────────────────────────────────────────────┘ │
│  Audit writer (R10)          Admin ingestion API (R11)        │
└──────┬────────────────────────┬──────────────────┬────────────┘
       │                        │                  │
  SQLite (mock EMR,       ChromaDB (policy     audit_log table
  appointments, labs,     chunks + version
  insurance, pharmacy)    metadata)
```

### Request flow for the headline question
"Can I proceed with surgery next week?" (patient P1001 from JWT)
1. Pre-guards pass (no other patient, not asking for a clinical decision on medication).
2. LLM calls `get_pending_preop_tests()` → orders show ECG done, HbA1c pending.
3. LLM calls `search_policies("pre-operative requirements knee replacement")` → resolver returns protocol v3 §2 (active).
4. LLM calls `check_insurance_eligibility("Knee Replacement")` → covered, pre-auth required, status: approved.
5. LLM calls `surgery_readiness_check()` → deterministic checklist: tests ✗ (HbA1c pending), insurance ✓, surgeon slot ✓ (Tue 10:00).
6. Validator: every citation exists; all facts trace to tool output.
7. answer_type = `calculated` (readiness is a tool decision) with citations to the protocol + systems.
8. Answer: "Not yet: your HbA1c test is still pending, which protocol v3 §2 requires before surgery. Insurance pre-authorization is approved and Dr. X has a slot on Tuesday. Assumption: surgery type = knee replacement, from your active order."
9. Audit row written; trace_id returned.

### answer_type rules (code, in priority order)
1. Pre-guard blocked → `refused`
2. Model asked the user a question / required arg missing → `clarification_needed`
3. Resolver returned an unresolved tie → `conflict_flagged`
4. A state-changing tool succeeded → `action_completed`
5. Decision came from a deterministic tool → `calculated`
6. Answer supported by retrieved active chunks → `retrieved_fact`
7. Nothing relevant (below threshold) and no tool data → `not_found` (exact §3 message)

---

## 5. Data model, API, response

**Tables:** `users(id, username, pw_hash, role, patient_id)`, `patients(id, name, dob, phone, insurance_plan_id, policy_no)`, `insurance_plans(id, name)`, `coverage_rules(plan_id, procedure, covered, copay_pct, preauth_required, waiting_period_days)`, `preauthorizations(patient_id, procedure, status)`, `doctors(id, name, department)`, `slots(id, doctor_id, start, is_free)`, `appointments(id, patient_id, doctor_id, start, type, status)`, `lab_results(id, patient_id, test, value, unit, ref_range, taken_at)`, `orders(id, patient_id, test, reason, status)`, `medications(patient_id, drug)`, `drug_interactions(drug_a, drug_b, severity, note)`, `surgeries(id, patient_id, procedure, surgeon_id, planned_date)`, `pending_actions(id, patient_id, tool, args, expires_at)`, `documents(doc_id, title, version, effective_date, authority, status, uploaded_by, uploaded_at)`, `audit_log(trace_id, user_id, role, question_redacted, sources, tools, privacy_checks, answer_type, latency_ms, model, tokens, error, created_at)`.

**Endpoints**
| Method | Path | Role |
|---|---|---|
| POST | /auth/login | public |
| POST | /chat | patient |
| POST | /actions/{id}/confirm | owner patient |
| POST | /admin/documents (file + metadata) | admin |
| GET | /admin/documents | admin |
| GET | /admin/audit · /admin/audit/{trace_id} | admin |
| GET | /health | public |

**Response (extends §8):**
```json
{
  "trace_id": "HSP-93214",
  "answer": "…",
  "answer_type": "retrieved_fact",
  "citations": [{"document": "ICU Visiting Policy", "section": "2.1", "version": "2.0", "effective_date": "2026-07-01"}],
  "tools_invoked": [{"tool": "search_policies"}],
  "explanation": "…",
  "assumptions": [],
  "latency_ms": 2140
}
```

**Folder structure**
```
app/  main.py config.py db.py models.py schemas.py
      auth/  ingest/ (parse.py chunk.py index.py)  retrieval/ (retrieve.py resolver.py)
      tools/ (appointments.py insurance.py labs.py pharmacy.py readiness.py registry.py)
      agent/ (orchestrator.py prompts.py guards.py classify.py)  audit/  api/
data/kb/*.md   data/seed/*.json   data/uploads/
scripts/seed_db.py  scripts/build_index.py
eval/cases.jsonl  eval/run_eval.py  eval/report.md
tests/   ui/streamlit_app.py   README.md  .env.example  requirements.txt
```

**Stack:** Python 3.11, FastAPI, SQLAlchemy + SQLite, ChromaDB (persistent), sentence-transformers `all-MiniLM-L6-v2` (local) or provider embeddings, LLM SDK with native tool calling (plain bounded loop), pypdf, PyJWT, Streamlit, pytest.

---

## 6. Scope

**MVP (must demo):** R1, R2, R3, R4 (one superseded version + one authority override + one flagged conflict), R5 (appointments, slots, insurance, labs, drug interaction, readiness), R6 (surgery scenario), R7, R8, R9 (all 7 types), R10, R11, eval report.
**Stretch:** billing calculation tool, radiology results, staff role + human approval queue, hybrid BM25 + rerank, PDF email ingestion, Docker.
**Avoid:** multi-agent, LangGraph, fine-tuning, real FHIR/EMR integration, medical advice of any kind.

## 7. Plan (≈4 h, 3 people; adjust to your team)
| Time | Person A (backend/tools) | Person B (RAG/ingestion) | Person C (UI/eval/docs) |
|---|---|---|---|
| 0:00–0:40 | skeleton, models, seed JSON, auth | KB docs w/ front-matter, chunker | Streamlit shell against a fake API |
| 0:40–1:40 | 7 tools + unit tests, pending-action confirm | index build, retriever, resolver + tests, admin upload | eval cases (30) |
| 1:40–2:40 | orchestrator + guards + classifier (A+B together) | | UI wired to real API, citations, trace view |
| 2:40–3:20 | audit, masking, bug fixes | injection tests, live-ingest demo | run eval, report, README |
| 3:20–4:00 | **freeze** · rehearse demo · final commits | | |

## 8. Demo script (≈6 min)
1. "ICU visiting hours?" → `retrieved_fact`, cites v2 (not superseded v1).
2. Question where a govt advisory overrides hospital policy → advisory cited, explanation of hierarchy.
3. Question on the deliberately conflicting docs → `conflict_flagged`.
4. "Am I covered for knee replacement?" → `calculated` via tool.
5. "Can I proceed with surgery next week?" → multi-step trace.
6. "Reschedule my cardiology appointment" → slots → confirm → `action_completed`.
7. "Show P1002's lab results" → `refused`; raw API attempt → 403.
8. "Treatment for <condition not in KB>?" → `not_found` (exact §3 JSON).
9. Admin uploads new advisory → same question now answered differently, no restart.
10. Malicious uploaded doc → assistant unaffected.
11. Audit trace + eval report.
