"""Role-based authorization.

Permissions are coarse capability strings on Role.permissions (see
app/models/identity.py). A user holds the union of their roles' permissions.
`admin:*` and `read:*` are wildcards. Checks are centralised here so routes
declare *what* they require, not *how* it is evaluated.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Principal:
    """The authenticated caller, resolved from the auth layer (local or OIDC)."""

    subject: str
    email: str | None = None
    permissions: frozenset[str] = field(default_factory=frozenset)

    def has(self, permission: str) -> bool:
        if permission in self.permissions:
            return True
        # Wildcard: "admin:*" grants "admin:anything"; "read:*" grants "read:*".
        prefix = permission.split(":", 1)[0]
        return f"{prefix}:*" in self.permissions or "admin:*" in self.permissions


# Capability constants used by routes/services.
PERM_SEARCH_EXECUTE = "search:execute"
PERM_SEARCH_WRITE = "search:write"
PERM_ALERT_ACK = "alert:ack"
PERM_ALERT_RESOLVE = "alert:resolve"
PERM_ADMIN = "admin:*"


class PermissionDenied(Exception):
    def __init__(self, permission: str) -> None:
        self.permission = permission
        super().__init__(f"missing required permission: {permission}")


def require_permission(principal: Principal, permission: str) -> None:
    if not principal.has(permission):
        raise PermissionDenied(permission)
