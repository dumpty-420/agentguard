"""agents/worker_agents.py — Minimal worker node functions for the agent graph.

Each function is a LangGraph node that:
  1. Calls ``LLMClient.complete()`` to do its work
  2. Appends its name to ``completed_steps`` for resume testability
  3. Appends its output to ``messages``

The workers are deliberately simple — the point of Phase 3 is the runtime
infrastructure (checkpointing, failover, cost governance), not agent
intelligence.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def make_researcher_node(llm_client: Any) -> Any:
    """Factory to create a researcher node with injected LLMClient."""
    async def researcher_node(state: dict[str, Any]) -> dict[str, Any]:
        """Gather information on the topic."""
        topic = state.get("topic", "general")

        if llm_client is not None:
            response = await llm_client.complete([
                {"role": "user", "content": f"Research the topic: {topic}"},
            ])
        else:
            response = f"[researcher] Gathered data on '{topic}'."

        logger.info("Researcher completed for topic=%s", topic)
        return {
            "messages": [f"researcher: {response}"],
            "completed_steps": ["researcher"],
        }
    return researcher_node


def make_analyst_node(llm_client: Any) -> Any:
    """Factory to create an analyst node with injected LLMClient."""
    async def analyst_node(state: dict[str, Any]) -> dict[str, Any]:
        """Analyze the research findings."""
        messages = state.get("messages", [])

        if llm_client is not None:
            context = "\n".join(messages)
            response = await llm_client.complete([
                {"role": "user", "content": f"Analyze these findings:\n{context}"},
            ])
        else:
            response = f"[analyst] Analyzed {len(messages)} finding(s)."

        logger.info("Analyst completed with %d prior messages.", len(messages))
        return {
            "messages": [f"analyst: {response}"],
            "completed_steps": ["analyst"],
        }
    return analyst_node


def make_synthesizer_node(llm_client: Any) -> Any:
    """Factory to create a synthesizer node with injected LLMClient."""
    async def synthesizer_node(state: dict[str, Any]) -> dict[str, Any]:
        """Synthesize the research and analysis into a final report."""
        messages = state.get("messages", [])

        if llm_client is not None:
            context = "\n".join(messages)
            response = await llm_client.complete([
                {"role": "user", "content": f"Synthesize into a report:\n{context}"},
            ])
        else:
            response = f"[synthesizer] Produced report from {len(messages)} message(s)."

        logger.info("Synthesizer completed with %d prior messages.", len(messages))
        return {
            "messages": [f"synthesizer: {response}"],
            "completed_steps": ["synthesizer"],
        }
    return synthesizer_node


# Expose legacy node function wrappers for backward compatibility (e.g. tests)
researcher_node = make_researcher_node(None)
analyst_node = make_analyst_node(None)
synthesizer_node = make_synthesizer_node(None)
