"""Research depth profiles: how much work each depth level is allowed to do.

Sized so a *standard* run fits Groq's free-plan daily token allowance with
headroom: roughly 6 tasks x ~4K tokens + verification + report ~ 50-80K tokens.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import ResearchDepth


@dataclass(frozen=True)
class DepthProfile:
    max_tasks: int
    queries_per_task: int
    sources_per_task: int
    min_distinct_sources: int  # evidence sufficiency threshold
    excerpt_chars: int  # per source, inside agent prompts


PROFILES: dict[ResearchDepth, DepthProfile] = {
    ResearchDepth.QUICK: DepthProfile(4, 2, 3, 3, 1_000),
    ResearchDepth.STANDARD: DepthProfile(7, 3, 4, 6, 1_200),
    ResearchDepth.DEEP: DepthProfile(10, 4, 5, 10, 1_400),
}


def profile_for(depth: str) -> DepthProfile:
    return PROFILES[ResearchDepth(depth)]
