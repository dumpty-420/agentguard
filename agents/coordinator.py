"""agents/coordinator.py — LangGraph supervisor graph for AgentGuard.

Defines a minimal multi-step agent graph:

    researcher → analyst → synthesizer

Each node is a worker from ``agents.worker_agents``.  The graph state
uses ``Annotated[list, operator.add]`` reducers so that each step's
output is appended (not overwritten) — this is essential for both
LangGraph checkpointing and for verifying resume correctness.

The coordinator does NOT know about failover, budgets, or checkpoints.
Those are composed around the graph by ``core.orchestrator``.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph




# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------

class AgentState(TypedDict):
    """Shared state flowing through the agent graph.

    Fields and their types (all msgpack-serializable):
        topic (str): The research topic.
        messages (list[str]): Accumulated outputs from each node.
        completed_steps (list[str]): Names of nodes that have finished.
    """

    topic: str
    messages: Annotated[list[str], operator.add]
    completed_steps: Annotated[list[str], operator.add]


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_agent_graph(
    researcher: Any = None,
    analyst: Any = None,
    synthesizer: Any = None,
) -> StateGraph:
    """Construct the uncompiled agent graph.

    The graph is linear:  researcher → analyst → synthesizer → END

    Returns:
        An uncompiled ``StateGraph`` ready to be compiled with a
        checkpointer by the orchestrator.
    """
    from agents.worker_agents import make_researcher_node, make_analyst_node, make_synthesizer_node

    r_node = researcher or make_researcher_node(None)
    a_node = analyst or make_analyst_node(None)
    s_node = synthesizer or make_synthesizer_node(None)

    builder = StateGraph(AgentState)

    # Register nodes.
    builder.add_node("researcher", r_node)
    builder.add_node("analyst", a_node)
    builder.add_node("synthesizer", s_node)

    # Linear edges.
    builder.add_edge(START, "researcher")
    builder.add_edge("researcher", "analyst")
    builder.add_edge("analyst", "synthesizer")
    builder.add_edge("synthesizer", END)

    return builder
