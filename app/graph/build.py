"""Compile the chat workflow. Guard, composer and finalize sit on fixed edges: the Supervisor can
only choose what happens between them, so it cannot skip safety checks."""

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from app.graph import nodes
from app.schemas.state import GraphState


@lru_cache
def get_graph():
    graph = StateGraph(GraphState)
    graph.add_node("guard", nodes.guard_node)
    graph.add_node("supervisor", nodes.supervisor_node)
    graph.add_node("specialists", nodes.specialists_node)
    graph.add_node("composer", nodes.composer_node)
    graph.add_node("finalize", nodes.finalize_node)

    graph.add_edge(START, "guard")
    graph.add_conditional_edges("guard", nodes.route_after_guard, ["supervisor", "composer"])
    graph.add_conditional_edges("supervisor", nodes.route_after_supervisor, ["specialists", "composer"])
    graph.add_conditional_edges("specialists", nodes.route_after_specialists, ["supervisor", "composer"])
    graph.add_edge("composer", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()
