"""CLI for Converge: grouped subcommands.

Commands:
  converge intent {create, list, status}
  converge simulate
  converge validate
  converge merge confirm
  converge queue {run, reset, inspect}
  converge policy {eval, calibrate}
  converge risk {eval, shadow, gate, review, policy}
  converge health {now, trend, change, change-trend, entropy, predict}
  converge compliance {report, alerts, threshold}
  converge agent {policy, authorize}
  converge audit {prune, events}
  converge export {decisions}
  converge metrics
  converge archaeology
  converge predictions
  converge serve
"""

from __future__ import annotations

import sys

from converge.cli._helpers import _out
from converge.cli._parser import build_parser
from converge.cli.admin import (
    cmd_agent_authorize,
    cmd_agent_policy_get,
    cmd_agent_policy_list,
    cmd_agent_policy_set,
    cmd_archaeology,
    cmd_audit_events,
    cmd_audit_prune,
    cmd_compliance_alerts,
    cmd_compliance_report,
    cmd_compliance_threshold_get,
    cmd_compliance_threshold_list,
    cmd_compliance_threshold_set,
    cmd_export_decisions,
    cmd_health_change,
    cmd_health_change_trend,
    cmd_health_entropy,
    cmd_health_now,
    cmd_health_predict,
    cmd_health_trend,
    cmd_metrics,
    cmd_predictions,
    cmd_serve,
    cmd_worker,
)
from converge.cli.intents import (
    cmd_intent_create,
    cmd_intent_list,
    cmd_intent_status,
    cmd_simulate,
    cmd_validate,
)
from converge.cli.queue import (
    cmd_merge_confirm,
    cmd_queue_inspect,
    cmd_queue_reset,
    cmd_queue_run,
)
from converge.cli.risk_cmds import (
    cmd_policy_calibrate,
    cmd_policy_eval,
    cmd_risk_eval,
    cmd_risk_gate,
    cmd_risk_policy_get,
    cmd_risk_policy_set,
    cmd_risk_review,
    cmd_risk_shadow,
)


# ===================================================================
# Dispatch
# ===================================================================

_DISPATCH = {
    ("intent", "create"): cmd_intent_create,
    ("intent", "list"): cmd_intent_list,
    ("intent", "status"): cmd_intent_status,
    ("simulate", None): cmd_simulate,
    ("validate", None): cmd_validate,
    ("merge", "confirm"): cmd_merge_confirm,
    ("queue", "run"): cmd_queue_run,
    ("queue", "reset"): cmd_queue_reset,
    ("queue", "inspect"): cmd_queue_inspect,
    ("policy", "eval"): cmd_policy_eval,
    ("policy", "calibrate"): cmd_policy_calibrate,
    ("risk", "eval"): cmd_risk_eval,
    ("risk", "shadow"): cmd_risk_shadow,
    ("risk", "gate"): cmd_risk_gate,
    ("risk", "review"): cmd_risk_review,
    ("risk", "policy-set"): cmd_risk_policy_set,
    ("risk", "policy-get"): cmd_risk_policy_get,
    ("health", "now"): cmd_health_now,
    ("health", "trend"): cmd_health_trend,
    ("health", "change"): cmd_health_change,
    ("health", "change-trend"): cmd_health_change_trend,
    ("health", "entropy"): cmd_health_entropy,
    ("compliance", "report"): cmd_compliance_report,
    ("compliance", "alerts"): cmd_compliance_alerts,
    ("compliance", "threshold-set"): cmd_compliance_threshold_set,
    ("compliance", "threshold-get"): cmd_compliance_threshold_get,
    ("compliance", "threshold-list"): cmd_compliance_threshold_list,
    ("agent", "policy-set"): cmd_agent_policy_set,
    ("agent", "policy-get"): cmd_agent_policy_get,
    ("agent", "policy-list"): cmd_agent_policy_list,
    ("agent", "authorize"): cmd_agent_authorize,
    ("audit", "prune"): cmd_audit_prune,
    ("audit", "events"): cmd_audit_events,
    ("metrics", None): cmd_metrics,
    ("archaeology", None): cmd_archaeology,
    ("predictions", None): cmd_predictions,
    ("export", "decisions"): cmd_export_decisions,
    ("health", "predict"): cmd_health_predict,
    ("serve", None): cmd_serve,
    ("worker", None): cmd_worker,
}

# Map subcmd attr names to the dispatch key
_SUBCMD_ATTR = {
    "intent": "intent_cmd",
    "merge": "merge_cmd",
    "queue": "queue_cmd",
    "policy": "policy_cmd",
    "risk": "risk_cmd",
    "health": "health_cmd",
    "compliance": "compliance_cmd",
    "agent": "agent_cmd",
    "audit": "audit_cmd",
    "export": "export_cmd",
}


def main(argv: list[str] | None = None) -> int:
    from converge import event_log as el

    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if not args.command:
        parser.print_help()
        return 1

    # Ensure DB exists
    el.init(args.db)

    # Resolve dispatch key
    subcmd_attr = _SUBCMD_ATTR.get(args.command)
    subcmd = getattr(args, subcmd_attr, None) if subcmd_attr else None
    key = (args.command, subcmd)

    handler = _DISPATCH.get(key)
    if handler is None:
        # Try command-only (no subcommand)
        handler = _DISPATCH.get((args.command, None))
    if handler is None:
        parser.print_help()
        return 1

    return handler(args)
