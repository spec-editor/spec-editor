"""Auth Provider — pluggable authentication and authorization.

Supports RBAC, ABAC, and ReBAC (relationship-based) for enterprise
access control. Configured via ``local.yaml`` → ``auth:`` section.

Usage::

    from src.auth import create_auth_provider

    auth = create_auth_provider(project_path)
    if auth.check("alice", "MOD-001", "write"):
        storage.write_element(...)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class AuthProvider(ABC):
    """Abstract authentication and authorization backend.

    Implementations answer: "Can this user perform this action on this resource?"
    """

    @abstractmethod
    def check(
        self,
        user: str,
        resource: str,
        action: str,
        *,
        domain: str = "",
        context: dict[str, Any] | None = None,
    ) -> bool:
        """Check if a user can perform an action on a resource.

        Args:
            user: User identifier (email, username, API key ID)
            resource: Resource identifier (element ID, project slug, ``*`` for all)
            action: Action (``read``, ``write``, ``delete``, ``admin``)
            domain: Optional domain/tenant (project slug)
            context: Optional additional context (IP, time, etc.)

        Returns:
            True if access is granted.
        """
        ...

    @abstractmethod
    def get_roles(self, user: str, domain: str = "") -> list[str]:
        """Get all roles assigned to a user in a domain."""
        ...

    def add_policy(
        self,
        subject: str,
        resource: str,
        action: str,
        domain: str = "",
    ) -> bool:
        """Add an access policy rule. Optional — noop by default."""
        return False

    def remove_policy(
        self,
        subject: str,
        resource: str,
        action: str,
        domain: str = "",
    ) -> bool:
        """Remove an access policy rule. Optional — noop by default."""
        return False


# ── Backend implementations ────────────────────────────────────────


class NoopAuthProvider(AuthProvider):
    """Allow-all provider — for single-user and development setups."""

    def check(
        self,
        user: str,
        resource: str,
        action: str,
        *,
        domain: str = "",
        context: dict[str, Any] | None = None,
    ) -> bool:
        return True

    def get_roles(self, user: str, domain: str = "") -> list[str]:
        return ["admin"]


class CasbinAuthProvider(AuthProvider):
    """Casbin-based RBAC/ABAC provider.

    Casbin is a lightweight authorization library supporting
    RBAC, ABAC, and domain-based multi-tenancy. Policy is loaded
    from a CSV string or file.

    Requires ``casbin`` package.

    Model (built-in RBAC with domains)::

        [request_definition]
        r = sub, dom, obj, act

        [policy_definition]
        p = sub, dom, obj, act

        [role_definition]
        g = _, _, _

        [policy_effect]
        e = some(where (p.eft == allow))

        [matchers]
        m = g(r.sub, p.sub, r.dom) && r.dom == p.dom && keyMatch(r.obj, p.obj) && keyMatch(r.act, p.act)
    """

    # Built-in model for RBAC with domain support
    DEFAULT_MODEL = """
[request_definition]
r = sub, dom, obj, act

[policy_definition]
p = sub, dom, obj, act

[role_definition]
g = _, _, _

[policy_effect]
e = some(where (p.eft == allow))

[matchers]
m = g(r.sub, p.sub, r.dom) && r.dom == p.dom && keyMatch(r.obj, p.obj) && keyMatch(r.act, p.act)
"""

    def __init__(
        self,
        model_text: str = "",
        policy_csv: str = "",
        policy_file: str = "",
    ) -> None:
        self._model_text = model_text or self.DEFAULT_MODEL
        self._policy_csv = policy_csv
        self._policy_file = policy_file
        self._enforcer: Any = None

    def _ensure_enforcer(self) -> None:
        if self._enforcer is not None:
            return
        try:
            import casbin
        except ImportError:
            raise ImportError(
                "casbin is required for CasbinAuthProvider. "
                "Install with: pip install casbin"
            )

        import tempfile
        import os

        # Write model to temp file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".conf", delete=False, encoding="utf-8"
        ) as f:
            f.write(self._model_text)
            model_path = f.name

        try:
            if self._policy_file:
                self._enforcer = casbin.Enforcer(model_path, self._policy_file)
            elif self._policy_csv:
                self._enforcer = casbin.Enforcer(model_path)
                self._enforcer.load_policy_from_text(self._policy_csv)
            else:
                self._enforcer = casbin.Enforcer(model_path)
        finally:
            os.unlink(model_path)

    def check(
        self,
        user: str,
        resource: str,
        action: str,
        *,
        domain: str = "",
        context: dict[str, Any] | None = None,
    ) -> bool:
        self._ensure_enforcer()
        return bool(
            self._enforcer.enforce(user, domain or "default", resource, action)
        )

    def get_roles(self, user: str, domain: str = "") -> list[str]:
        self._ensure_enforcer()
        try:
            return self._enforcer.get_roles_for_user(user, domain or "default")
        except Exception:
            return []

    def add_policy(
        self,
        subject: str,
        resource: str,
        action: str,
        domain: str = "",
    ) -> bool:
        self._ensure_enforcer()
        try:
            return bool(
                self._enforcer.add_policy(subject, domain or "default", resource, action)
            )
        except Exception:
            return False

    def remove_policy(
        self,
        subject: str,
        resource: str,
        action: str,
        domain: str = "",
    ) -> bool:
        self._ensure_enforcer()
        try:
            return bool(
                self._enforcer.remove_policy(
                    subject, domain or "default", resource, action
                )
            )
        except Exception:
            return False


class OpenFGAProvider(AuthProvider):
    """OpenFGA (Relationship-Based Access Control) provider (stub).

    OpenFGA is a CNCF project implementing Google Zanzibar-style
    ReBAC. Ideal for element-level permission graphs.

    Requires ``openfga-sdk`` package and a running OpenFGA server.
    """

    def __init__(
        self,
        api_url: str = "http://localhost:8080",
        store_id: str = "",
        authorization_model_id: str = "",
    ) -> None:
        self._api_url = api_url
        self._store_id = store_id
        self._model_id = authorization_model_id
        self._client: Any = None

    def _ensure_client(self) -> None:
        if self._client is not None:
            return
        try:
            import openfga_sdk
            from openfga_sdk.client import OpenFgaClient
        except ImportError:
            raise ImportError(
                "openfga-sdk is required for OpenFGAProvider. "
                "Install with: pip install openfga-sdk"
            )
        self._client = OpenFgaClient(
            api_url=self._api_url,
            store_id=self._store_id,
            authorization_model_id=self._model_id,
        )

    def check(
        self,
        user: str,
        resource: str,
        action: str,
        *,
        domain: str = "",
        context: dict[str, Any] | None = None,
    ) -> bool:
        self._ensure_client()
        try:
            # Map action → OpenFGA relation
            relation = {
                "read": "viewer",
                "write": "editor",
                "delete": "owner",
                "admin": "owner",
            }.get(action, "viewer")

            result = self._client.check(
                user=f"user:{user}",
                relation=relation,
                object=f"element:{resource}",
            )
            return getattr(result, "allowed", False)
        except Exception:
            return False

    def get_roles(self, user: str, domain: str = "") -> list[str]:
        # OpenFGA doesn't have "roles" — it has relationships
        return []


# ── Factory ─────────────────────────────────────────────────────────


def create_auth_provider(project_path: str | Path) -> AuthProvider:
    """Create an AuthProvider from project configuration.

    Reads ``local.yaml`` → ``auth:`` section:

    .. code-block:: yaml

        auth:
          backend: casbin           # noop | casbin | openfga
          casbin:
            policy: |
              p, admin, *, *, *
              p, analyst, prompt3, aspect:*, read
              p, developer, prompt3, MOD-001, write
              g, alice, admin, prompt3
              g, bob, developer, prompt3
          openfga:
            api_url: http://localhost:8080
            store_id: ${OPENFGA_STORE_ID}
            model_id: ${OPENFGA_MODEL_ID}

    Falls back to ``noop`` (allow all) if no config found.
    """
    import os

    proj = Path(project_path)
    backend_name = "noop"
    backend_config: dict[str, Any] = {}

    local_yaml = proj / "local.yaml"
    if local_yaml.exists():
        try:
            import yaml

            data = yaml.safe_load(local_yaml.read_text()) or {}
            auth_cfg = data.get("auth", {})
            backend_name = auth_cfg.get("backend", "noop")
            backend_config = auth_cfg.get(backend_name, {})
        except Exception:
            pass

    backend_name = os.environ.get("SPEC_EDITOR__AUTH_BACKEND", backend_name)

    if backend_name == "casbin":
        return CasbinAuthProvider(
            policy_csv=backend_config.get("policy", ""),
            policy_file=backend_config.get("policy_file", ""),
        )
    elif backend_name == "openfga":
        return OpenFGAProvider(
            api_url=backend_config.get("api_url", "http://localhost:8080"),
            store_id=os.path.expandvars(backend_config.get("store_id", "")),
            authorization_model_id=os.path.expandvars(
                backend_config.get("model_id", "")
            ),
        )
    else:
        return NoopAuthProvider()
