"""Compile the chat workflow. Guard, composer and finalize sit on fixed edges: the Supervisor can
only choose what happens between them, so it cannot skip safety checks."""

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from app.graph import care, nodes
from app.schemas.state import GraphState


@lru_cache
def get_graph():
    graph = StateGraph(GraphState)
    graph.add_node("guard", nodes.guard_node)
    graph.add_node("supervisor", nodes.supervisor_node)
    graph.add_node("specialists", nodes.specialists_node)
    graph.add_node("care_fanout", care.care_fanout)
    graph.add_node("care_preop", care.care_preop)
    graph.add_node("care_insurance", care.care_insurance)
    graph.add_node("care_surgeon", care.care_surgeon)
    graph.add_node("care_readiness", care.care_readiness)
    graph.add_node("composer", nodes.composer_node)
    graph.add_node("finalize", nodes.finalize_node)

    graph.add_edge(START, "guard")
    graph.add_conditional_edges("guard", nodes.route_after_guard, ["supervisor", "composer"])
    graph.add_conditional_edges("supervisor", nodes.route_after_supervisor, ["specialists", "composer"])
    graph.add_conditional_edges(
        "specialists", nodes.route_after_specialists, ["care_fanout", "supervisor", "composer"]
    )
    # Care Coordination: three independent reads in parallel, joined by the readiness rules.
    for branch in care.BRANCH_NODES:
        graph.add_edge("care_fanout", branch)
    graph.add_edge(care.BRANCH_NODES, "care_readiness")
    graph.add_conditional_edges("care_readiness", nodes.route_after_readiness, ["supervisor", "composer"])

    graph.add_edge("composer", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()
