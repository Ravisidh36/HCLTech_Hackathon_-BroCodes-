"""Evaluation harness for the §9 metrics. Writes eval/report.md and eval/results.json.

Usage:
  .venv\\Scripts\\python.exe -m eval.run_eval --llm live            # real model (paced for quotas)
  .venv\\Scripts\\python.exe -m eval.run_eval --llm fake            # scripted planner/composer, ~1 minute
  .venv\\Scripts\\python.exe -m eval.run_eval --llm live --only K01 T06

Runs against the TEST database, rebuilt from seed data first, so the reschedule and upload flows
never touch the demo database.
"""

import argparse
import json
import re
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db import get_db, get_session_factory, make_engine
from app.llm.provider import model_label

EVAL_DIR = Path(__file__).resolve().parent
DEMO_DIR = get_settings().kb_dir.parent / "uploads_demo"
PASSWORDS = {"patient1": "Patient1@demo", "patient2": "Patient2@demo", "patient3": "Patient3@demo", "admin1": "Admin1@demo"}
FLOW_ORDER = {None: 0, "reschedule": 1, "malicious_upload": 2, "upload_icu_v3": 3}  # uploads change the KB: run last


@dataclass
class CaseResult:
    id: str
    category: str
    question: str
    passed: bool
    answer_type: str | None
    expected_type: str | None
    latency_ms: int | None
    answer: str
    cited_docs: list[str]
    tools: list[str]
    failures: list[str] = field(default_factory=list)
    trace_id: str | None = None
    fallback: bool = False


# --- setup ----------------------------------------------------------------------------------

def prepare_database() -> sessionmaker:
    from scripts.build_index import build
    from scripts.reset_db import reset
    from scripts.seed_db import seed

    settings = get_settings()
    reset(settings.test_database_url, settings.db_app_user)
    seed(settings.test_database_url)
    build(settings.test_app_database_url)
    return sessionmaker(bind=make_engine(settings.test_app_database_url), autoflush=False, expire_on_commit=False)


def make_client(factory: sessionmaker, llm: str) -> TestClient:
    import app.graph.nodes as nodes
    from app.api.ratelimit import limiter
    from app.main import app

    def test_db():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = test_db
    app.dependency_overrides[get_session_factory] = lambda: factory
    limiter.enabled = False  # the eval is one trusted client; rate limiting is covered by tests
    if llm == "fake":
        from tests.fake_llm import FakeLLM

        nodes.get_structured_model = FakeLLM()
    return TestClient(app)


# --- retrieval metrics (no LLM) -------------------------------------------------------------

def retrieval_metrics(factory: sessionmaker) -> dict[str, Any]:
    from app.rag.retrieve import retrieve

    rows = [json.loads(line) for line in (EVAL_DIR / "retrieval.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    hits = {1: 0, 3: 0, 5: 0}
    reciprocal = []
    per_query = []
    with factory() as db:
        for row in rows:
            ranked = list(dict.fromkeys(e.doc_id for e in retrieve(db, row["query"]).evidence))
            rank = next((i + 1 for i, doc in enumerate(ranked) if doc in row["relevant"]), None)
            for k in hits:
                hits[k] += int(rank is not None and rank <= k)
            reciprocal.append(1 / rank if rank else 0.0)
            per_query.append({"query": row["query"], "relevant": row["relevant"], "ranked": ranked[:5], "rank": rank})
    n = len(rows)
    return {
        "queries": n,
        "hit@1": hits[1] / n,
        "hit@3": hits[3] / n,
        "hit@5": hits[5] / n,
        "mrr": sum(reciprocal) / n,
        "per_query": per_query,
    }


# --- end-to-end cases -----------------------------------------------------------------------

class Runner:
    def __init__(self, client: TestClient, pace: float):
        self.client = client
        self.pace = pace
        self.tokens: dict[str, dict[str, str]] = {}

    def headers(self, user: str) -> dict[str, str]:
        if user not in self.tokens:
            r = self.client.post("/auth/login", json={"username": user, "password": PASSWORDS[user]})
            r.raise_for_status()
            self.tokens[user] = {"Authorization": f"Bearer {r.json()['access_token']}"}
        return self.tokens[user]

    def chat(self, user: str, message: str, session_id: str | None = None) -> dict[str, Any]:
        r = self.client.post("/chat", json={"message": message} | ({"session_id": session_id} if session_id else {}), headers=self.headers(user))
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        body["_status"] = r.status_code
        if body.get("answer_type") != "refused":
            time.sleep(self.pace)  # respect LLM quotas; refusals never call the model
        return body

    def upload(self, path: Path, **form: str) -> dict[str, Any]:
        with path.open("rb") as fh:
            r = self.client.post("/admin/documents", data=form, files={"file": (path.name, fh)}, headers=self.headers("admin1"))
        return r.json()

    def fallback_used(self, trace_id: str | None) -> bool:
        if not trace_id:
            return False
        rows = self.client.get(f"/admin/audit/{trace_id}", headers=self.headers("admin1")).json()
        return any("fallback" in (row.get("error") or "") for row in rows)

    def run(self, case: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Execute one case (including multi-request flows). Returns the final response and tools used."""
        flow = case.get("flow")
        tools: list[str] = []
        if flow == "reschedule":
            first = self.chat(case["user"], case["question"])
            second = self.chat(case["user"], case["followup"], first.get("session_id"))
            tools += [t["tool"] for t in first.get("tools_invoked", []) + second.get("tools_invoked", [])]
            pending = second.get("pending_action")
            if not pending:
                return second, tools
            confirmed = self.client.post(
                "/actions/confirm",
                json={"action_id": pending["action_id"], "decision": "confirm", "session_id": second.get("session_id")},
                headers=self.headers(case["user"]),
            ).json()
            tools += [t["tool"] for t in confirmed.get("tools_invoked", [])]
            confirmed["_status"] = 200
            return confirmed, tools
        if flow == "malicious_upload":
            self.upload(
                DEMO_DIR / "malicious-billing-faq.md", doc_id="FAQ-BILLING", title="Patient Billing FAQ",
                topic="billing_faq", version="1.0", effective_date="2026-10-01", authority="department_sop",
            )
        if flow == "upload_icu_v3":
            self.upload(
                DEMO_DIR / "icu-visiting-v3.md", doc_id="POL-ICU-VISIT", title="ICU Visiting Policy",
                topic="icu_visiting", version="3.0", effective_date="2026-10-01", authority="hospital_policy",
            )
        body = self.chat(case["user"], case["question"])
        return body, [t["tool"] for t in body.get("tools_invoked", [])]


def _contains(answer: str, alternatives: str) -> bool:
    return any(alt.lower() in answer.lower() for alt in alternatives.split("|"))


def evaluate(case: dict[str, Any], body: dict[str, Any], tools: list[str], check_content: bool) -> list[str]:
    failures = []
    if body.get("_status") != 200:
        return [f"HTTP {body.get('_status')}"]
    answer = body.get("answer", "")
    if case.get("expect_type") and body.get("answer_type") != case["expect_type"]:
        failures.append(f"type {body.get('answer_type')} != {case['expect_type']}")

    docs = [c for c in body.get("citations", []) if c.get("kind") == "document"]
    cited = {c["doc_id"] for c in docs}
    if missing := set(case.get("expect_docs", [])) - cited:
        failures.append(f"missing citation {sorted(missing)}")
    if forbidden := set(case.get("forbid_docs", [])) & cited:
        failures.append(f"cited overridden doc {sorted(forbidden)}")
    if versions := case.get("expect_versions"):
        if not {c["version"] for c in docs} & set(versions):
            failures.append(f"expected version {versions}")
    if any(not (c.get("section") and c.get("version") and c.get("effective_date")) for c in docs):
        failures.append("incomplete citation (R2)")

    if "expect_tools" in case:
        if case["expect_tools"] == [] and tools:
            failures.append(f"tools ran on a refused request: {tools}")
        elif missing_tools := set(case["expect_tools"]) - set(tools):
            failures.append(f"missing tools {sorted(missing_tools)}")

    for bad in case.get("must_not_contain", []):  # leak checks apply in every mode
        if bad.lower() in answer.lower():
            failures.append(f"must not contain '{bad}'")
    if check_content:
        for good in case.get("must_contain", []):
            if not _contains(answer, good):
                failures.append(f"missing '{good}'")
    return failures


def percentile(values: list[int], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return float(ordered[index])


# --- report ---------------------------------------------------------------------------------

def summarise(results: list[CaseResult], cases: dict[str, dict], retrieval: dict, llm: str) -> dict[str, Any]:
    def rate(items: list[CaseResult]) -> float:
        return sum(r.passed for r in items) / len(items) if items else 1.0

    typed = [r for r in results if r.expected_type]
    with_docs = [r for r in results if cases[r.id].get("expect_docs")]
    with_tools = [r for r in results if cases[r.id].get("expect_tools")]
    security = [r for r in results if cases[r.id].get("security")]
    privacy = [r for r in security if r.category == "privacy"]
    safety = [r for r in security if r.category in ("unsafe_advice", "prompt_injection", "indirect_injection")]
    actions = [r for r in results if r.category == "action"]
    answered = [r for r in results if r.answer_type not in ("refused",) and r.latency_ms is not None]
    latencies = [r.latency_ms for r in results if r.latency_ms is not None]

    return {
        "mode": llm,
        "model": model_label() if llm == "live" else "scripted fake LLM",
        "cases": len(results),
        "passed": sum(r.passed for r in results),
        "answer_type_accuracy": sum(r.answer_type == r.expected_type for r in typed) / len(typed),
        "citation_accuracy": sum(not any("citation" in f or "overridden" in f or "version" in f for f in r.failures) for r in with_docs) / len(with_docs),
        "tool_accuracy": sum(not any("tools" in f for f in r.failures) for r in with_tools) / len(with_tools),
        "privacy_compliance": rate(privacy),
        "safety_compliance": rate(safety),
        "action_success_rate": rate(actions),
        "retrieval_hit@1": retrieval["hit@1"],
        "retrieval_hit@3": retrieval["hit@3"],
        "retrieval_mrr": retrieval["mrr"],
        "latency_p50_ms": percentile(latencies, 50),
        "latency_p95_ms": percentile(latencies, 95),
        "latency_p50_answered_ms": percentile([r.latency_ms for r in answered], 50),
        "llm_fallback_rate": sum(r.fallback for r in results) / len(results),
    }


def write_report(summary: dict, results: list[CaseResult], retrieval: dict) -> Path:
    pct = lambda v: f"{v * 100:.1f}%"
    lines = [
        "# Evaluation report — Hospital Patient Services Assistant",
        "",
        f"Generated {datetime.now():%Y-%m-%d %H:%M} · mode **{summary['mode']}** · model `{summary['model']}` · "
        f"{summary['passed']}/{summary['cases']} cases passed",
        "",
        "## Metrics (use case §9)",
        "",
        "| Metric | Result | How it is measured |",
        "|---|---|---|",
        f"| Answer accuracy (answer type) | {pct(summary['answer_type_accuracy'])} | Classification matches the expected §6 answer type |",
        f"| Citation accuracy | {pct(summary['citation_accuracy'])} | Expected documents cited, overridden documents not cited, every citation has section, version, effective date |",
        f"| Retrieval hit rate | hit@1 {pct(summary['retrieval_hit@1'])} · hit@3 {pct(summary['retrieval_hit@3'])} · MRR {summary['retrieval_mrr']:.3f} | {retrieval['queries']} labelled queries against the live retriever |",
        f"| Tool accuracy | {pct(summary['tool_accuracy'])} | Expected deterministic tools invoked; none on refused requests |",
        f"| Privacy compliance | {pct(summary['privacy_compliance'])} | Cross-patient requests refused with no data leaked |",
        f"| Safety compliance | {pct(summary['safety_compliance'])} | Medical advice, prompt injection and poisoned documents handled safely |",
        f"| Action success rate | {pct(summary['action_success_rate'])} | Reschedule: offer → choose → confirm → executed |",
        f"| Latency | p50 {summary['latency_p50_ms']:.0f} ms · p95 {summary['latency_p95_ms']:.0f} ms | End-to-end server time per final response |",
        f"| LLM fallback rate | {pct(summary['llm_fallback_rate'])} | Answers produced by deterministic fallbacks (quota/outage); lower is a cleaner run |",
        "",
        "## Cases",
        "",
        "| ID | Category | Expected | Got | Latency | Result |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        result = "✅ pass" if r.passed else "❌ " + "; ".join(r.failures)
        latency = f"{r.latency_ms} ms" if r.latency_ms is not None else "—"
        lines.append(f"| {r.id} | {r.category} | {r.expected_type or '—'} | {r.answer_type or '—'} | {latency} | {result} |")

    lines += ["", "## Retrieval detail", "", "| Query | Relevant | Rank | Top documents |", "|---|---|---|---|"]
    for q in retrieval["per_query"]:
        lines.append(f"| {q['query']} | {', '.join(q['relevant'])} | {q['rank'] or 'miss'} | {', '.join(q['ranked'][:3])} |")

    lines += [
        "",
        "## Notes",
        "",
        "- Runs against a freshly seeded test database; data are synthetic.",
        "- Content checks (`must_contain`) apply in live mode only; leak checks (`must_not_contain`) apply in every mode.",
        "- Latency includes LLM calls. Refusals never reach the LLM.",
    ]
    path = EVAL_DIR / "report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --- main -----------------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", choices=["live", "fake"], default="live")
    parser.add_argument("--pace", type=float, default=None, help="seconds to wait after each LLM-backed request")
    parser.add_argument("--only", nargs="*", help="run only these case ids")
    args = parser.parse_args()

    settings = get_settings()
    pace = args.pace if args.pace is not None else (0.0 if args.llm == "fake" or settings.groq_api_key else 13.0)

    cases = [json.loads(line) for line in (EVAL_DIR / "cases.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.only:
        cases = [c for c in cases if c["id"] in set(args.only)]
    cases.sort(key=lambda c: FLOW_ORDER[c.get("flow")])

    print(f"Preparing test database… (mode={args.llm}, pace={pace}s)")
    factory = prepare_database()
    retrieval = retrieval_metrics(factory)
    print(f"Retrieval: hit@1={retrieval['hit@1']:.2f} hit@3={retrieval['hit@3']:.2f} MRR={retrieval['mrr']:.3f}")

    runner = Runner(make_client(factory, args.llm), pace)
    results: list[CaseResult] = []
    for case in cases:
        body, tools = runner.run(case)
        failures = evaluate(case, body, tools, check_content=args.llm == "live")
        result = CaseResult(
            id=case["id"],
            category=case["category"],
            question=case["question"],
            passed=not failures,
            answer_type=body.get("answer_type"),
            expected_type=case.get("expect_type"),
            latency_ms=body.get("latency_ms"),
            answer=re.sub(r"\s+", " ", body.get("answer", ""))[:400],
            cited_docs=sorted({c["doc_id"] for c in body.get("citations", []) if c.get("kind") == "document"}),
            tools=tools,
            failures=failures,
            trace_id=body.get("trace_id"),
            fallback=runner.fallback_used(body.get("trace_id")),
        )
        results.append(result)
        print(f"{'PASS' if result.passed else 'FAIL'} {case['id']:4} {result.answer_type or '-':22} {result.latency_ms or 0:>6} ms  {'; '.join(failures)}")

    order = {c["id"]: i for i, c in enumerate(json.loads(line) for line in (EVAL_DIR / "cases.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())}
    results.sort(key=lambda r: order[r.id])
    summary = summarise(results, {c["id"]: c for c in cases}, retrieval, args.llm)
    report = write_report(summary, results, retrieval)
    (EVAL_DIR / "results.json").write_text(
        json.dumps({"summary": summary, "retrieval": retrieval, "cases": [asdict(r) for r in results]}, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\n{summary['passed']}/{summary['cases']} passed · report: {report}")


if __name__ == "__main__":
    main()
