"""Explain / Fix with AI helpers."""

from .explain import ExplainResult, ask_followup, error_message_for_key, explain_issue
from .fix import (
    FixResult,
    apply_proposed_fix,
    apply_proposed_fixes,
    ask_fix_followup,
    fix_member_kind,
    propose_batch_fix,
    propose_fix,
)
from .litellm_client import litellm_available
from .overview import ask_overview_followup, explain_overview

__all__ = [
    "ExplainResult",
    "FixResult",
    "ask_followup",
    "ask_fix_followup",
    "ask_overview_followup",
    "apply_proposed_fix",
    "apply_proposed_fixes",
    "error_message_for_key",
    "explain_issue",
    "explain_overview",
    "fix_member_kind",
    "litellm_available",
    "propose_batch_fix",
    "propose_fix",
]

# Optional alt-text assessment API (lazy-compatible if symbols are incomplete).
try:
    from .alt_assess import (  # noqa: F401
        AltAssessResult,
        ask_alt_assess_followup,
        assess_alt_export,
    )

    __all__ += [
        "AltAssessResult",
        "ask_alt_assess_followup",
        "assess_alt_export",
    ]
except ImportError:
    pass
