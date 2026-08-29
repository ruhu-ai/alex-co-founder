"""Closed contracts for Co-Founder's current data-source reliability slice.

This is deliberately a product-specific registry, not a connector framework.
Provider-, model-, and request-authored strings must be validated here before
they can select a role, status, collection contract, or effect kind.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypeVar


class ClosedValue(str, Enum):
    """String enum whose serialized value is the only accepted spelling."""


class ConnectorId(ClosedValue):
    UPLOAD = "upload"
    DRIVE = "drive"
    ALEX_DRIVE = "alex_drive"
    FOUNDER_GMAIL = "founder_gmail"
    ALEX_MAIL = "alex_mail"
    ALEX_CALENDAR = "alex_calendar"
    CALENDAR = "calendar"
    BROWSER = "browser"


class DataSourceRole(ClosedValue):
    KNOWLEDGE = "knowledge"
    CONTEXT = "context"
    EVENT = "event"
    ACTION_DESTINATION = "action_destination"


class ConnectionAuthKind(ClosedValue):
    BUILTIN = "builtin"
    GOOGLE_OAUTH = "google_oauth"
    TOKEN_REF = "token_ref"


class ConnectionStatus(ClosedValue):
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    DISCONNECTING = "DISCONNECTING"
    DISCONNECTED = "DISCONNECTED"


class SourceGrantStatus(ClosedValue):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    SOURCE_MISSING = "SOURCE_MISSING"


class SourceKind(ClosedValue):
    FILE = "file"


class IngestionScope(ClosedValue):
    REFERENCE_ONLY = "reference_only"
    PROFILE = "profile"


class ExternalEventKind(ClosedValue):
    MAIL_CONFIRMATION = "mail_confirmation"
    MAIL_REQUEST = "mail_request"
    MAIL_RESULT = "mail_result"
    MAIL_UPDATE = "mail_update"
    CALENDAR_CHANGED = "calendar_changed"
    DEADLINE_CRITICAL = "deadline_critical"


class ContentRisk(ClosedValue):
    CLEAR = "CLEAR"
    INJECTION_SUSPECTED = "INJECTION_SUSPECTED"
    WITHHELD = "WITHHELD"


class EventProcessingStatus(ClosedValue):
    RECEIVED = "RECEIVED"
    APPLYING = "APPLYING"
    APPLIED = "APPLIED"
    INBOXED = "INBOXED"
    FAILED = "FAILED"


class CorrelationStatus(ClosedValue):
    PENDING = "PENDING"
    EXACT = "EXACT"
    AMBIGUOUS = "AMBIGUOUS"
    UNMATCHED = "UNMATCHED"


class CorrelationBasis(ClosedValue):
    CAUSAL_ACTION = "causal_action"
    PORTAL_REGISTRATION = "portal_registration"
    PROVIDER_THREAD = "provider_thread"
    FOUNDER_RESOLUTION = "founder_resolution"


class DeliveryStatus(ClosedValue):
    PENDING = "PENDING"
    ENQUEUED = "ENQUEUED"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    NOT_REQUIRED = "NOT_REQUIRED"


class FounderInboxKind(ClosedValue):
    UNMATCHED_EVENT = "UNMATCHED_EVENT"
    AMBIGUOUS_EVENT = "AMBIGUOUS_EVENT"


class FounderInboxStatus(ClosedValue):
    UNREAD = "UNREAD"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class InboxResolution(ClosedValue):
    LINKED_TO_APPLICATION = "LINKED_TO_APPLICATION"
    DISMISSED_BY_FOUNDER = "DISMISSED_BY_FOUNDER"


class ExternalActionKind(ClosedValue):
    SEND_EMAIL = "send_email"
    SEND_FOUNDER_EMAIL = "send_founder_email"
    CREATE_CALENDAR_EVENT = "create_calendar_event"
    EXPORT_DRIVE_FILE = "export_drive_file"
    EXPORT_ALEX_DRIVE_FILE = "export_alex_drive_file"
    CREATE_PORTAL_ACCOUNT = "create_portal_account"
    SUBMIT_APPLICATION = "submit_application"
    H4S_SEND_EMAIL = "h4s_send_email"
    H4S_CREATE_CALENDAR_EVENT = "h4s_create_calendar_event"


# Effect kinds that may only ever be prepared inside a provisioned H4S sandbox.
# prepare_external_action requires a sandbox context for exactly these values.
SANDBOX_ONLY_ACTION_KINDS: frozenset[str] = frozenset({
    ExternalActionKind.H4S_SEND_EMAIL.value,
    ExternalActionKind.H4S_CREATE_CALENDAR_EVENT.value,
})


class ExternalActionStatus(ClosedValue):
    PREPARED = "PREPARED"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"


class EvidenceAuthority(ClosedValue):
    UNCONFIRMED_EVIDENCE = "unconfirmed_evidence"


class VerificationLevel(ClosedValue):
    UNCONFIRMED_EVIDENCE = "UNCONFIRMED_EVIDENCE"
    EVIDENCE_VERIFIED = "EVIDENCE_VERIFIED"
    FOUNDER_CONFIRMED = "FOUNDER_CONFIRMED"
    SUPERSEDED = "SUPERSEDED"


class SafeErrorCode(ClosedValue):
    AUTH_REQUIRED = "auth_required"
    PERMISSION_DENIED = "permission_denied"
    SCOPE_MISSING = "scope_missing"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_REJECTED = "provider_rejected"
    SOURCE_NOT_SELECTED = "source_not_selected"
    SOURCE_MISSING = "source_missing"
    SOURCE_REVOKED = "source_revoked"
    OWNER_MISMATCH = "owner_mismatch"
    VERSION_CONFLICT = "version_conflict"
    LEASE_CONFLICT = "lease_conflict"
    INVALID_CONTRACT = "invalid_contract"
    APPROVAL_MISSING = "approval_missing"
    APPROVAL_BINDING_MISSING = "approval_binding_missing"
    APPROVAL_BINDING_MISMATCH = "approval_binding_mismatch"
    DISPATCH_FAILED = "dispatch_failed"
    PROVENANCE_FAILED = "provenance_failed"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    REMOTE_REVOCATION_UNCERTAIN = "remote_revocation_uncertain"


@dataclass(frozen=True)
class ConnectorContract:
    connector_id: ConnectorId
    roles: frozenset[DataSourceRole]
    auth_kind: ConnectionAuthKind
    allowed_scope: str


CONNECTOR_REGISTRY: dict[ConnectorId, ConnectorContract] = {
    ConnectorId.UPLOAD: ConnectorContract(
        ConnectorId.UPLOAD, frozenset({DataSourceRole.KNOWLEDGE}),
        ConnectionAuthKind.BUILTIN,
        "one founder-provided file; explicit owner session; profile or reference_only"),
    ConnectorId.DRIVE: ConnectorContract(
        ConnectorId.DRIVE,
        frozenset({DataSourceRole.KNOWLEDGE,
                   DataSourceRole.ACTION_DESTINATION}),
        ConnectionAuthKind.GOOGLE_OAUTH,
        "founder-selected files for reads; app-produced files only for export"),
    ConnectorId.ALEX_DRIVE: ConnectorContract(
        ConnectorId.ALEX_DRIVE,
        frozenset({DataSourceRole.KNOWLEDGE,
                   DataSourceRole.ACTION_DESTINATION}),
        ConnectionAuthKind.GOOGLE_OAUTH,
        "Alex role-owned Drive with full provider read/write scope; "
        "consequential mutations remain exact approval-gated"),
    ConnectorId.FOUNDER_GMAIL: ConnectorContract(
        ConnectorId.FOUNDER_GMAIL,
        frozenset({DataSourceRole.EVENT, DataSourceRole.ACTION_DESTINATION}),
        ConnectionAuthKind.GOOGLE_OAUTH,
        "one founder-selected label for reads; exact approval-gated send; no modify/delete"),
    ConnectorId.ALEX_MAIL: ConnectorContract(
        ConnectorId.ALEX_MAIL,
        frozenset({DataSourceRole.EVENT, DataSourceRole.ACTION_DESTINATION}),
        ConnectionAuthKind.GOOGLE_OAUTH,
        "role mailbox reads/events; exact approval-gated send"),
    ConnectorId.ALEX_CALENDAR: ConnectorContract(
        ConnectorId.ALEX_CALENDAR,
        frozenset({DataSourceRole.CONTEXT, DataSourceRole.ACTION_DESTINATION}),
        ConnectionAuthKind.GOOGLE_OAUTH,
        "Alex role calendar reads; exact approval-gated event create/update/cancel"),
    ConnectorId.ALEX_DRIVE: ConnectorContract(
        ConnectorId.ALEX_DRIVE,
        frozenset({DataSourceRole.KNOWLEDGE,
                   DataSourceRole.ACTION_DESTINATION}),
        ConnectionAuthKind.GOOGLE_OAUTH,
        "Alex-selected files for reads; app-produced files only for approval-gated export"),
    ConnectorId.CALENDAR: ConnectorContract(
        ConnectorId.CALENDAR,
        frozenset({DataSourceRole.CONTEXT,
                   DataSourceRole.ACTION_DESTINATION}),
        ConnectionAuthKind.GOOGLE_OAUTH,
        "upcoming/free-busy reads; exact approval-gated create; no edit/delete"),
    ConnectorId.BROWSER: ConnectorContract(
        ConnectorId.BROWSER,
        frozenset({DataSourceRole.KNOWLEDGE,
                   DataSourceRole.ACTION_DESTINATION}),
        ConnectionAuthKind.BUILTIN,
        "existing URL/network/action policy; in-app observation only"),
}


EnumT = TypeVar("EnumT", bound=ClosedValue)


def require_closed(value: str | ClosedValue, enum_type: type[EnumT]) -> EnumT:
    """Return a closed value or raise for programmer-authored contract drift."""
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unknown {enum_type.__name__}") from exc


def validate_connector_role(connector_id: str, role: str) -> bool:
    """Whether the current product registry enables this exact role."""
    try:
        connector = require_closed(connector_id, ConnectorId)
        wanted = require_closed(role, DataSourceRole)
    except ValueError:
        return False
    return wanted in CONNECTOR_REGISTRY[connector].roles


def _bounded_identity(value: str, field: str, *, limit: int = 512) -> str:
    value = str(value or "").strip()
    if not value or len(value) > limit or any(ord(ch) < 32 for ch in value):
        raise ValueError(f"invalid {field}")
    return value


def _stable_id(prefix: str, namespace: str, *parts: str) -> str:
    normalized = [_bounded_identity(part, "identity") for part in parts]
    digest = hashlib.sha256((namespace + "".join(normalized)).encode()).hexdigest()
    return prefix + digest[:32]


def data_connection_id(founder_id: str, connector_id: str,
                       account_ref: str = "default") -> str:
    require_closed(connector_id, ConnectorId)
    return _stable_id("dc_", "data-connection:v1", founder_id,
                      connector_id, account_ref)


def source_grant_id(founder_id: str, connection_id: str,
                    provider_source_id: str) -> str:
    return _stable_id("sg_", "source-grant:v1", founder_id,
                      connection_id, provider_source_id)


def external_event_id(founder_id: str, connection_id: str,
                      provider_event_id: str) -> str:
    return _stable_id("xe_", "external-event:v1", founder_id,
                      connection_id, provider_event_id)


def founder_inbox_id(founder_id: str, event_id: str, item_kind: str) -> str:
    require_closed(item_kind, FounderInboxKind)
    return _stable_id("fi_", "founder-inbox:v1", founder_id,
                      event_id, item_kind)


def external_action_id(founder_id: str, action_kind: str,
                       idempotency_key: str) -> str:
    require_closed(action_kind, ExternalActionKind)
    return _stable_id("xa_", "external-action:v1", founder_id,
                      action_kind, idempotency_key)


def canonical_hash(value: Any, *, max_bytes: int = 32_768) -> str:
    """Hash bounded JSON metadata with deterministic key/list encoding.

    Callers must pass safe metadata, not raw provider/document bodies. The cap
    prevents a receipt helper from becoming a covert large-content store.
    """
    from services.canonical import canonical_hash as platform_hash

    return platform_hash(
        value, domain="data-source-receipt", prefixed=False,
        max_bytes=max_bytes)


def values(enum_type: type[EnumT]) -> frozenset[str]:
    """Serialized values for schema/tests without accepting arbitrary input."""
    return frozenset(item.value for item in enum_type)
