"""Request-scoped connector credential resolution.

The broker intentionally has no token cache. Callers provide only opaque grant
and action authority; a reviewed encrypted resolver returns a credential to the
adapter for the lifetime of one call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from services.durable_store import DurableStore, production_store

CredentialResolver = Callable[[dict[str, Any], frozenset[str]], Awaitable[Any]]
CredentialOperation = Callable[[Any], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class CredentialRequest:
    workspace_id: str
    actor_id: str
    connector_grant_id: str
    delegated_action_id: str
    provider_account: str
    required_scopes: frozenset[str]
    operation_kind: str


class ConnectorCredentialBroker:
    def __init__(self, resolver: CredentialResolver, *,
                 store: DurableStore | None = None):
        self._resolver = resolver
        self._store = store or production_store()

    async def execute(self, request: CredentialRequest,
                      operation: CredentialOperation) -> dict[str, Any]:
        """Resolve one credential, call one adapter, and never return the credential."""
        grant = await self._store.get("connector_credential_grants",
                                      request.connector_grant_id)
        action = await self._store.get("external_actions", request.delegated_action_id)
        if not grant or not action:
            return _error("reauth_required")
        if action.get("status") == "UNCERTAIN":
            return {"status": "error", "error": True,
                    "error_code": "action_uncertain",
                    "message": "Reconcile the prior provider attempt before retry."}
        if (grant.get("status") != "ACTIVE"
                or grant.get("workspace_id") != request.workspace_id
                or grant.get("provider_account") != request.provider_account
                or action.get("workspace_id") != request.workspace_id
                or action.get("actor_id") != request.actor_id
                or action.get("credential_grant_id") != request.connector_grant_id
                or action.get("provider_account") != request.provider_account
                or action.get("operation_kind") != request.operation_kind
                or frozenset(str(scope) for scope in action.get(
                    "authorized_scopes", [])) != request.required_scopes
                or action.get("status") != "PREPARED"):
            return _error("credential_authority_mismatch")
        granted = frozenset(str(scope) for scope in grant.get("scopes", []))
        if not request.required_scopes or not request.required_scopes.issubset(granted):
            return _error("scope_not_granted")
        try:
            credential = await self._resolver(grant, request.required_scopes)
        except Exception:
            return _error("reauth_required")
        if credential is None:
            return _error("reauth_required")
        try:
            result = await operation(credential)
        except Exception:
            return {"status": "error", "error": True,
                    "error_code": "provider_unavailable",
                    "message": "Connector operation failed."}
        if not isinstance(result, dict) or _contains_secret_key(result):
            return {"status": "error", "error": True,
                    "error_code": "unsafe_adapter_result",
                    "message": "Connector returned an unsafe result envelope."}
        return result


def _error(code: str) -> dict[str, Any]:
    return {"status": "error", "error": True, "error_code": code,
            "message": "Connector authorization must be refreshed."}


def _contains_secret_key(value: Any) -> bool:
    forbidden = {"token", "access_token", "refresh_token", "credential",
                 "credentials", "secret", "authorization", "signed_url"}
    if isinstance(value, dict):
        return any(str(key).lower() in forbidden or _contains_secret_key(nested)
                   for key, nested in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_secret_key(item) for item in value)
    return False
