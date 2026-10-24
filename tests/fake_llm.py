"""Scripted stand-in for get_structured_model: deterministic, offline, no quota."""

import re

from langchain_core.messages import AIMessage

from app.graph.nodes import keyword_route
from app.graph.plan import ComposerOutput, SupervisorPlan


def question_from(prompt: str) -> str:
    return re.search(r"Patient's message:\n(.*?)\n\nCompleted", prompt, re.DOTALL).group(1).strip()


def history_from(prompt: str) -> list[dict[str, str]]:
    block = re.search(r"Recent conversation:\n(.*?)\n\nPatient's message", prompt, re.DOTALL).group(1)
    turns = []
    for line in block.splitlines():
        speaker, _, content = line.partition(": ")
        if speaker in ("user", "assistant"):
            turns.append({"speaker": speaker, "content": content})
    return turns


def plan_by_keywords(prompt: str) -> SupervisorPlan:
    return keyword_route(question_from(prompt), history_from(prompt))


def quote_evidence_or_summarise(prompt: str) -> ComposerOutput:
    """Quote evidence E1 verbatim if present; otherwise a reply with no invented values."""
    first = re.search(r'<evidence id="E1" .*?injection_flagged="\w+">\n(.*?)\n</evidence>', prompt, re.DOTALL)
    if first:
        return ComposerOutput(answer=first.group(1), explanation="From hospital policy.", cited_evidence=["E1"])
    return ComposerOutput(answer="Here is what the hospital systems show for you.", explanation="From hospital systems.")


class FakeLLM:
    def __init__(self, plan=None, compose=None):
        self.calls: list[str] = []
        self.prompts: list[str] = []
        self.plan = plan or plan_by_keywords
        self.compose = compose or quote_evidence_or_summarise

    def __call__(self, schema, temperature=0.0):
        llm = self

        class Runnable:
            def invoke(self, messages):
                text = messages[-1].content
                llm.calls.append(schema.__name__)
                llm.prompts.append(text)
                parsed = llm.plan(text) if schema is SupervisorPlan else llm.compose(text)
                raw = AIMessage(content="", usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
                return {"raw": raw, "parsed": parsed, "parsing_error": None}

        return Runnable()
