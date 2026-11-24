"""ORM models. Importing this package registers every table on Base.metadata."""

from app.database.base import Base
from app.models.tables import (
    AgentExecution,
    EvaluationResult,
    EvaluationRun,
    ResearchProject,
    ResearchReport,
    ResearchSource,
    ResearchTask,
    User,
    UserSettings,
)

__all__ = [
    "AgentExecution",
    "Base",
    "EvaluationResult",
    "EvaluationRun",
    "ResearchProject",
    "ResearchReport",
    "ResearchSource",
    "ResearchTask",
    "User",
    "UserSettings",
]
