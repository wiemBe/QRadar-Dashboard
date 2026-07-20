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

# Phase 3 read capabilities. Offense records carry parsed usernames, source
# addresses and analyst assignments, and the rule/coverage views describe where
# detection is absent -- a map of where to attack. None of it may be served to
# an unauthenticated caller, so these routes declare a permission rather than
# relying on the endpoint being read-only.
#
# All three are satisfied by the `read:*` wildcard, so an existing read-only
# role keeps working; what changes is that *some* principal is now required.
PERM_OFFENSE_READ = "read:offenses"
PERM_RULE_READ = "read:rules"
PERM_COVERAGE_READ = "read:coverage"
PERM_PROVIDER_READ = "read:providers"


class PermissionDenied(Exception):
    def __init__(self, permission: str) -> None:
        self.permission = permission
        super().__init__(f"missing required permission: {permission}")


def require_permission(principal: Principal, permission: str) -> None:
    if not principal.has(permission):
        raise PermissionDenied(permission)
