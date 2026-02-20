"""HTTP API server with authentication, RBAC, and tenancy.

Provides REST endpoints for all converge operations. All reads come from
projections over the event log. All writes produce events.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("converge.server")

from converge import agents, analytics, engine, event_log, projections, risk
from converge.models import AgentPolicy, Event, EventType, Intent, Status, now_iso


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

ROLE_RANK = {"viewer": 0, "operator": 1, "admin": 2}

API_ROLE_MAP: dict[str, str] = {
    "/api/auth/whoami": "viewer",
    "/api/summary": "viewer",
    "/api/intents": "viewer",
    "/api/metrics/integration": "viewer",
    "/api/policy/recent": "viewer",
    "/api/queue/state": "viewer",
    "/api/queue/summary": "viewer",
    "/api/compliance/report": "viewer",
    "/api/compliance/alerts": "viewer",
    "/api/compliance/thresholds": "viewer",
    "/api/health/repo/now": "viewer",
    "/api/health/repo/trend": "viewer",
    "/api/health/change": "viewer",
    "/api/health/change/trend": "viewer",
    "/api/health/entropy/trend": "viewer",
    "/api/risk/recent": "viewer",
    "/api/risk/review": "viewer",
    "/api/risk/shadow/recent": "viewer",
    "/api/risk/gate/report": "viewer",
    "/api/risk/policy": "viewer",
    "/api/impact/edges": "viewer",
    "/api/diagnostics/recent": "viewer",
    "/api/agent/policy": "viewer",
    "/api/events": "viewer",
    "/api/predictions": "viewer",
    "/api/audit/recent": "operator",
    "/api/compliance/thresholds/history": "operator",
}


def _parse_api_keys() -> dict[str, dict[str, str]]:
    """Parse CONVERGE_API_KEYS env var: key:role:actor[:tenant[:scopes]]"""
    raw = os.environ.get("CONVERGE_API_KEYS", "")
    if not raw:
        return {}
    keys = {}
    for entry in raw.split(","):
        parts = entry.strip().split(":")
        if len(parts) >= 3:
            k, role, actor = parts[0], parts[1], parts[2]
            tenant = parts[3] if len(parts) > 3 else None
            scopes = parts[4] if len(parts) > 4 else None
            hashed = hashlib.sha256(k.encode()).hexdigest()
            keys[hashed] = {"role": role, "actor": actor, "tenant": tenant, "scopes": scopes, "key_prefix": k[:4]}
    return keys


def _auth_required() -> bool:
    return os.environ.get("CONVERGE_AUTH_REQUIRED", "1") == "1"


def _authorize_request(headers: dict[str, str], path: str) -> dict[str, Any] | None:
    """Returns principal dict or None if unauthorized."""
    if not _auth_required():
        return {"role": "admin", "actor": "anonymous", "tenant": None}

    api_key = headers.get("x-api-key", "")
    if api_key:
        hashed = hashlib.sha256(api_key.encode()).hexdigest()
        registry = _parse_api_keys()
        principal = registry.get(hashed)
        if principal is None:
            return None
        required_role = API_ROLE_MAP.get(path, "admin")
        if ROLE_RANK.get(principal["role"], -1) < ROLE_RANK.get(required_role, 99):
            return None
        return principal

    return None


# ---------------------------------------------------------------------------
# GitHub webhook verification
# ---------------------------------------------------------------------------

def _verify_github_signature(secret: str, body: bytes, signature: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

class ConvergeHandler(BaseHTTPRequestHandler):
    db_path: str = ""
    webhook_secret: str = ""

    def _headers_dict(self) -> dict[str, str]:
        return {k.lower(): v for k, v in self.headers.items()}

    def _query_params(self) -> dict[str, str]:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        return {k: v[0] for k, v in qs.items()}

    def _json_response(self, data: Any, status: int = 200):
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length)

    def _check_auth(self) -> dict[str, Any] | None:
        path = urlparse(self.path).path
        if path == "/health" or not path.startswith("/api"):
            return {"role": "admin", "actor": "system", "tenant": None}
        principal = _authorize_request(self._headers_dict(), path)
        if principal is None:
            self._json_response({"error": "Unauthorized"}, 401)
        return principal

    def _enforce_tenant(self, requested_tid: str | None, principal_tid: str | None,
                        principal: dict[str, Any]) -> str | None:
        """Resolve tenant ID, enforcing scoping for non-admin principals.

        Returns the tenant ID to use, or None if a 400/403 was sent.
        """
        tid = requested_tid or principal_tid
        if not tid:
            self._json_response({"error": "tenant_id required"}, 400)
            return None
        if principal_tid and tid != principal_tid and principal.get("role") != "admin":
            self._json_response({"error": "Forbidden: cannot access another tenant"}, 403)
            return None
        return tid

    def do_GET(self):
        principal = self._check_auth()
        if principal is None:
            return

        path = urlparse(self.path).path
        params = self._query_params()
        tenant = principal.get("tenant") or params.get("tenant_id")
        db = self.db_path

        try:
            if path == "/health":
                self._json_response({"status": "ok", "timestamp": now_iso()})

            elif path == "/api/auth/whoami":
                self._json_response(principal)

            elif path == "/api/summary":
                health = projections.repo_health(db, tenant_id=tenant)
                qs = projections.queue_state(db, tenant_id=tenant)
                self._json_response({"health": health.to_dict(), "queue": qs.to_dict()})

            elif path == "/api/intents":
                intents = event_log.list_intents(db, status=params.get("status"), tenant_id=tenant)
                self._json_response([i.to_dict() for i in intents])

            elif path == "/api/metrics/integration":
                self._json_response(projections.integration_metrics(db, tenant_id=tenant))

            elif path == "/api/policy/recent":
                events = event_log.query(db, event_type=EventType.POLICY_EVALUATED, tenant_id=tenant,
                                         limit=int(params.get("limit", "50")))
                self._json_response(events)

            elif path == "/api/queue/state":
                self._json_response(projections.queue_state(db, tenant_id=tenant).to_dict())

            elif path == "/api/queue/summary":
                qs = projections.queue_state(db, tenant_id=tenant)
                self._json_response({"total": qs.total, "by_status": qs.by_status, "pending_count": len(qs.pending)})

            elif path == "/api/health/repo/now":
                self._json_response(projections.repo_health(db, tenant_id=tenant).to_dict())

            elif path == "/api/health/repo/trend":
                self._json_response(projections.health_trend(db, tenant_id=tenant,
                                    days=int(params.get("days", "30"))))

            elif path == "/api/health/change":
                iid = params.get("intent_id", "")
                if not iid:
                    self._json_response({"error": "intent_id required"}, 400)
                    return
                self._json_response(projections.change_health(db, iid, tenant_id=tenant))

            elif path == "/api/health/change/trend":
                self._json_response(projections.change_health_trend(db, tenant_id=tenant,
                                    days=int(params.get("days", "30"))))

            elif path == "/api/health/entropy/trend":
                self._json_response(projections.entropy_trend(db, tenant_id=tenant,
                                    days=int(params.get("days", "30"))))

            elif path == "/api/risk/recent":
                events = event_log.query(db, event_type=EventType.RISK_EVALUATED, tenant_id=tenant,
                                         limit=int(params.get("limit", "50")))
                self._json_response(events)

            elif path == "/api/risk/review":
                iid = params.get("intent_id", "")
                if not iid:
                    self._json_response({"error": "intent_id required"}, 400)
                    return
                self._json_response(analytics.risk_review(db, iid, tenant_id=tenant))

            elif path == "/api/risk/shadow/recent":
                events = event_log.query(db, event_type=EventType.RISK_SHADOW_EVALUATED, tenant_id=tenant,
                                         limit=int(params.get("limit", "50")))
                self._json_response(events)

            elif path == "/api/risk/gate/report":
                events = event_log.query(db, event_type=EventType.POLICY_EVALUATED, tenant_id=tenant, limit=1000)
                blocked = [e for e in events if e["payload"].get("verdict") == "BLOCK"]
                self._json_response({
                    "total_evaluations": len(events),
                    "total_blocked": len(blocked),
                    "block_rate": round(len(blocked) / max(len(events), 1), 3),
                    "recent_blocks": blocked[:20],
                })

            elif path == "/api/risk/policy":
                policies = event_log.list_risk_policies(db, tenant_id=tenant)
                self._json_response(policies)

            elif path == "/api/impact/edges":
                events = event_log.query(db, event_type=EventType.RISK_EVALUATED,
                                         intent_id=params.get("intent_id"), tenant_id=tenant, limit=1)
                edges = events[0]["payload"].get("impact_edges", []) if events else []
                self._json_response(edges)

            elif path == "/api/diagnostics/recent":
                iid = params.get("intent_id")
                events = event_log.query(db, event_type=EventType.RISK_EVALUATED, intent_id=iid, tenant_id=tenant, limit=1)
                # Diagnostics are derived from risk eval
                if events:
                    self._json_response(events[0]["payload"].get("findings", []))
                else:
                    self._json_response([])

            elif path == "/api/agent/policy":
                self._json_response(agents.list_policies(db, tenant_id=tenant))

            elif path == "/api/compliance/report":
                self._json_response(projections.compliance_report(db, tenant_id=tenant).to_dict())

            elif path == "/api/compliance/alerts":
                report = projections.compliance_report(db, tenant_id=tenant)
                self._json_response(report.alerts)

            elif path == "/api/compliance/thresholds":
                self._json_response(event_log.list_compliance_thresholds(db, tenant_id=tenant))

            elif path == "/api/compliance/thresholds/history":
                events = event_log.query(db, event_type=EventType.COMPLIANCE_THRESHOLDS_UPDATED, tenant_id=tenant, limit=50)
                self._json_response(events)

            elif path == "/api/audit/recent":
                events = event_log.query(db, tenant_id=tenant, limit=int(params.get("limit", "100")))
                self._json_response(events)

            elif path == "/api/events":
                events = event_log.query(
                    db,
                    event_type=params.get("type"),
                    intent_id=params.get("intent_id"),
                    agent_id=params.get("agent_id"),
                    tenant_id=tenant,
                    since=params.get("since"),
                    limit=int(params.get("limit", "100")),
                )
                self._json_response(events)

            elif path == "/api/predictions":
                self._json_response(projections.predict_issues(db, tenant_id=tenant))

            else:
                self._json_response({"error": "Not found"}, 404)

        except Exception as e:
            import traceback
            traceback.print_exc()
            self._json_response({"error": str(e)}, 500)

    def do_POST(self):
        principal = self._check_auth()
        if principal is None:
            return

        path = urlparse(self.path).path
        tenant = principal.get("tenant")
        db = self.db_path

        try:
            body = self._read_body()
            try:
                data = json.loads(body) if body else {}
            except json.JSONDecodeError:
                self._json_response({"error": "Invalid JSON body"}, 400)
                return

            if path == "/api/risk/policy":
                tid = self._enforce_tenant(data.get("tenant_id"), tenant, principal)
                if tid is None:
                    return
                event_log.upsert_risk_policy(db, tid, data)
                self._json_response({"ok": True, "tenant_id": tid})

            elif path == "/api/agent/policy":
                if "agent_id" not in data:
                    self._json_response({"error": "Missing required field: agent_id"}, 400)
                    return
                pol = AgentPolicy.from_dict(data)
                result = agents.set_policy(db, pol)
                self._json_response(result)

            elif path == "/api/agent/authorize":
                missing = [f for f in ("agent_id", "action", "intent_id") if f not in data]
                if missing:
                    self._json_response({"error": f"Missing required fields: {', '.join(missing)}"}, 400)
                    return
                result = agents.authorize(
                    db,
                    agent_id=data["agent_id"],
                    action=data["action"],
                    intent_id=data["intent_id"],
                    tenant_id=data.get("tenant_id") or tenant,
                    human_approvals=data.get("human_approvals", 0),
                )
                self._json_response(result)

            elif path == "/api/compliance/thresholds":
                tid = self._enforce_tenant(data.get("tenant_id"), tenant, principal)
                if tid is None:
                    return
                event_log.upsert_compliance_thresholds(db, tid, data)
                event_log.append(db, Event(
                    event_type=EventType.COMPLIANCE_THRESHOLDS_UPDATED,
                    tenant_id=tid,
                    payload=data,
                ))
                self._json_response({"ok": True, "tenant_id": tid})

            elif path == "/integrations/github/webhook":
                self._handle_github_webhook(body)

            else:
                self._json_response({"error": "Not found"}, 404)

        except Exception as e:
            import traceback
            traceback.print_exc()
            self._json_response({"error": str(e)}, 500)

    def _handle_github_webhook(self, body: bytes):
        headers = self._headers_dict()
        sig = headers.get("x-hub-signature-256", "")
        event_type = headers.get("x-github-event", "")
        delivery_id = headers.get("x-github-delivery", "")

        if not self.webhook_secret:
            if _auth_required():
                self._json_response({"error": "Webhook signature verification not configured"}, 403)
                return
            # Dev mode: accept without signature
        elif not _verify_github_signature(self.webhook_secret, body, sig):
            self._json_response({"error": "Invalid signature"}, 401)
            return

        # Idempotency: skip if this delivery was already processed (O(1) PK lookup)
        if delivery_id and event_log.is_duplicate_delivery(self.db_path, delivery_id):
            self._json_response({"ok": True, "delivery_id": delivery_id, "duplicate": True})
            return

        data = json.loads(body)

        event_log.append(self.db_path, Event(
            event_type=EventType.WEBHOOK_RECEIVED,
            payload={"github_event": event_type, "delivery_id": delivery_id, "action": data.get("action", "")},
            evidence={"delivery_id": delivery_id},
        ))
        if delivery_id:
            event_log.record_delivery(self.db_path, delivery_id)

        if event_type == "pull_request" and data.get("action") in ("opened", "synchronize"):
            pr = data.get("pull_request", {})
            source = pr.get("head", {}).get("ref", "")
            target = pr.get("base", {}).get("ref", "main")
            pr_number = pr.get("number", 0)
            repo_full_name = data.get("repository", {}).get("full_name", "")
            tenant = os.environ.get("CONVERGE_GITHUB_DEFAULT_TENANT")

            # Namespace intent ID by repo to prevent cross-repo collisions
            intent_id = f"{repo_full_name}:pr-{pr_number}" if repo_full_name else f"pr-{pr_number}"

            intent = Intent(
                id=intent_id,
                source=source,
                target=target,
                status=Status.READY,
                created_by="github-webhook",
                tenant_id=tenant,
                semantic={"problem_statement": pr.get("title", ""), "objective": pr.get("title", "")},
                technical={"source_ref": source, "target_ref": target,
                           "initial_base_commit": pr.get("base", {}).get("sha", ""),
                           "repo": repo_full_name},
            )
            event_log.upsert_intent(self.db_path, intent)
            event_log.append(self.db_path, Event(
                event_type=EventType.INTENT_CREATED,
                intent_id=intent.id,
                tenant_id=tenant,
                payload=intent.to_dict(),
            ))

        self._json_response({"ok": True, "delivery_id": delivery_id})

    def log_message(self, format, *args):
        pass  # Suppress default logging


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------

def serve(
    db_path: str | Path,
    host: str = "127.0.0.1",
    port: int = 9876,
    webhook_secret: str = "",
):
    """Start the HTTP API server."""
    event_log.init(db_path)
    ConvergeHandler.db_path = str(db_path)
    ConvergeHandler.webhook_secret = webhook_secret or os.environ.get("CONVERGE_GITHUB_WEBHOOK_SECRET", "")

    if not ConvergeHandler.webhook_secret:
        log.warning("CONVERGE_GITHUB_WEBHOOK_SECRET not set — webhook signature verification is DISABLED")

    server = ThreadingHTTPServer((host, port), ConvergeHandler)
    print(json.dumps({"event": "server_started", "host": host, "port": port, "timestamp": now_iso()}))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
