"""Standalone bounded history and pure planning; DATA, never SEND authority.

ConversationMemory owns external text history in memory. Persistence and model
calls belong to the host. C1 integration remains in the legacy runtime adapter.
"""
from ._values import (MemoryError, MemoryLimits, SourceKey, HistoryEntry, HistoryGroup,
    RetainedGroupPin, CoverageManifest, HistoryWindow, ModelPin, CountEvidence,
    ContextBudget, AttemptKey, AttemptRecord, CompactionDemand, SummaryRecord, ContextPlan)
from ._window import validate_window
from ._planner import plan_context, check_summary_applicability, make_summary

__all__ = ["MemoryError", "MemoryLimits", "SourceKey", "HistoryEntry", "HistoryGroup",
    "RetainedGroupPin", "CoverageManifest", "HistoryWindow", "ModelPin", "CountEvidence",
    "ContextBudget", "AttemptKey", "AttemptRecord", "CompactionDemand", "SummaryRecord",
    "ContextPlan", "validate_window", "plan_context", "check_summary_applicability", "make_summary"]

from .conversation import (ConversationMemory, ConversationSnapshot, MemoryContext,
                           CompactionJob, ExternalAttempt)
__all__ += ["ConversationMemory", "ConversationSnapshot", "MemoryContext",
            "CompactionJob", "ExternalAttempt"]
