"""Assemble the research StateGraph.

START -> planner_agent -> approval_gate --(Send per ready task)--> research_agent
              |                |                                  competitor_agent
              v                v                                  market_analysis_agent
           finalize         finalize                                   |
                                                                       v
      verification_agent <--(no ready tasks)-- collect_evidence <------+
          |        |  \\                         (more ready tasks -> Send again)
          |        |   +--> plan_followup --(Send research)--> ... (bounded by iterations)
          |        v
          |     finalize (no verifiable evidence)
          v
     report_agent -> quality_check -> complete -> END
          ^               |
          +-- (1 retry) --+--> finalize -> END
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents.base import RunContext
from app.graph import nodes
from app.graph.state import ResearchState, TaskPayload

RECURSION_LIMIT = 80  # supersteps; the graph's own bounds keep real runs far below this

_TASK_TARGETS = [*nodes.AGENT_NODES, "verification_agent", "finalize"]


def build_research_graph(
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> CompiledStateGraph[ResearchState, RunContext, ResearchState, ResearchState]:
    g: StateGraph[ResearchState, RunContext, ResearchState, ResearchState] = StateGraph(
        ResearchState, context_schema=RunContext
    )
    g.add_node("planner_agent", nodes.planner_node)
    g.add_node("approval_gate", nodes.approval_gate)
    g.add_node(nodes.RESEARCH, nodes.research_node, input_schema=TaskPayload)
    g.add_node(nodes.COMPETITOR, nodes.competitor_node, input_schema=TaskPayload)
    g.add_node(nodes.MARKET, nodes.market_node, input_schema=TaskPayload)
    g.add_node("collect_evidence", nodes.collect_evidence)
    g.add_node("verification_agent", nodes.verification_node)
    g.add_node("plan_followup", nodes.plan_followup)
    g.add_node("report_agent", nodes.report_node)
    g.add_node("quality_check", nodes.quality_check_node)
    g.add_node("complete", nodes.complete_node)
    g.add_node("finalize", nodes.finalize_node)

    g.add_edge(START, "planner_agent")
    g.add_conditional_edges(
        "planner_agent", nodes.route_after_planner, ["approval_gate", "finalize"]
    )
    g.add_conditional_edges("approval_gate", nodes.route_ready, _TASK_TARGETS)
    for agent in nodes.AGENT_NODES:
        g.add_edge(agent, "collect_evidence")
    g.add_conditional_edges("collect_evidence", nodes.route_ready, _TASK_TARGETS)
    g.add_conditional_edges(
        "verification_agent",
        nodes.route_after_verification,
        ["report_agent", "plan_followup", "finalize"],
    )
    g.add_conditional_edges("plan_followup", nodes.route_ready, _TASK_TARGETS)
    g.add_conditional_edges("report_agent", nodes.route_after_report, ["quality_check", "finalize"])
    g.add_conditional_edges(
        "quality_check", nodes.route_after_quality, ["complete", "report_agent", "finalize"]
    )
    g.add_edge("complete", END)
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)
