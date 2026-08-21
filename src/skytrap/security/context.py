from dataclasses import dataclass

from skytrap.core.context import WorkspaceContext


@dataclass
class AuthorizationScope:
    """The DISCOVER -> ANALYZE -> VERIFY SAFELY -> ASSESS RISK -> REMEDIATE -> RETEST
    workflow only ever applies to targets the user has actually scoped in. Local,
    read-only, defensive checks against the current repository are always in scope
    (they touch nothing but files already on this machine). Anything that sends
    traffic to a network target (DNS/TLS/network/web) requires the user to name that
    exact target explicitly on the command line — SkyTrap never infers or expands a
    target, and never turns a finding into exploitation regardless of scope."""

    local_repository: bool = True
    authorized_network_targets: frozenset[str] = frozenset()

    def allows_network_target(self, target: str) -> bool:
        return target in self.authorized_network_targets

    @classmethod
    def for_explicit_target(cls, target: str) -> "AuthorizationScope":
        """A target passed directly by the user on the command line (e.g. `skytrap
        security dns example.com`) is, by that act, explicitly authorized for that
        one check — SkyTrap doesn't second-guess a target the user typed themselves,
        but never expands scope beyond exactly what they named."""
        return cls(local_repository=True, authorized_network_targets=frozenset({target}))


@dataclass
class SecurityContext:
    workspace: WorkspaceContext
    scope: AuthorizationScope
    ci_mode: bool = False
    fail_threshold: str | None = None  # e.g. "high" — used by --ci
