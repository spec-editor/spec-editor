"""Secrets Provider — pluggable secret resolution.

Supports multiple backends for reading API keys, passwords, tokens.
Configured via ``local.yaml`` → ``secrets:`` section.

Backends:
    - ``env`` — read from ``.env`` / ``os.environ`` (default)
    - ``aws_secrets`` — AWS Secrets Manager
    - ``vault`` — HashiCorp Vault
    - ``noop`` — dummy provider (returns None for all keys)

Usage::

    from src.secrets import create_secret_provider

    secrets = create_secret_provider(project_path)
    api_key = secrets.get_secret("DEEPSEEK_API_KEY")
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class SecretProvider(ABC):
    """Abstract secret resolution backend.

    Implementations resolve named secrets from their backing store.
    Returns None for missing keys (never raises).
    """

    @abstractmethod
    def get_secret(self, key: str) -> str | None:
        """Resolve a secret by key. Returns None if not found."""
        ...

    def get_secret_required(self, key: str) -> str:
        """Resolve a required secret. Raises KeyError if missing."""
        value = self.get_secret(key)
        if value is None:
            raise KeyError(f"Required secret '{key}' not found")
        return value

    def get_config(self) -> dict[str, Any]:
        """Return the full secrets configuration dict (for introspection).

        Default returns empty — override in backends that support it.
        """
        return {}


# ── Backend implementations ────────────────────────────────────────


class EnvSecretProvider(SecretProvider):
    """Reads secrets from environment variables and .env files.

    Supports ``${VAR}`` substitution in YAML values via os.path.expandvars.
    """

    def __init__(self, extra_vars: dict[str, str] | None = None) -> None:
        import os

        self._env: dict[str, str] = dict(os.environ)
        if extra_vars:
            self._env.update(extra_vars)

    def get_secret(self, key: str) -> str | None:
        return self._env.get(key)


class NoopSecretProvider(SecretProvider):
    """Dummy provider — always returns None. For single-user / no-secret setups."""

    def get_secret(self, key: str) -> str | None:
        return None


class AwsSecretsProvider(SecretProvider):
    """AWS Secrets Manager backend (stub).

    Requires ``boto3`` and AWS credentials in environment.
    """

    def __init__(self, region: str = "us-east-1", secret_id: str = "") -> None:
        self._region = region
        self._secret_id = secret_id
        self._client: Any = None
        self._cache: dict[str, str] = {}

    def _ensure_client(self) -> None:
        if self._client is None:
            try:
                import boto3
                from botocore.exceptions import ClientError as _  # noqa: F401
            except ImportError:
                raise ImportError(
                    "boto3 is required for AWS Secrets Manager. "
                    "Install with: pip install boto3"
                )
            self._client = boto3.client("secretsmanager", region_name=self._region)

    def get_secret(self, key: str) -> str | None:
        if key in self._cache:
            return self._cache[key]
        self._ensure_client()
        try:
            import json

            response = self._client.get_secret_value(SecretId=self._secret_id or key)
            if "SecretString" in response:
                data = json.loads(response["SecretString"])
                value = data.get(key)
                if value:
                    self._cache[key] = value
                return value
        except Exception:
            pass
        return None


class VaultSecretProvider(SecretProvider):
    """HashiCorp Vault backend (stub).

    Requires ``hvac`` package and VAULT_ADDR + VAULT_TOKEN in environment.
    """

    def __init__(
        self, vault_addr: str = "", vault_token: str = "", mount_point: str = "secret"
    ) -> None:
        import os

        self._vault_addr = vault_addr or os.environ.get("VAULT_ADDR", "http://127.0.0.1:8200")
        self._vault_token = vault_token or os.environ.get("VAULT_TOKEN", "")
        self._mount_point = mount_point
        self._client: Any = None
        self._cache: dict[str, str] = {}

    def _ensure_client(self) -> None:
        if self._client is None:
            try:
                import hvac
            except ImportError:
                raise ImportError(
                    "hvac is required for HashiCorp Vault. "
                    "Install with: pip install hvac"
                )
            self._client = hvac.Client(url=self._vault_addr, token=self._vault_token)

    def get_secret(self, key: str) -> str | None:
        if key in self._cache:
            return self._cache[key]
        self._ensure_client()
        try:
            response = self._client.secrets.kv.v2.read_secret_version(
                path=key, mount_point=self._mount_point
            )
            data = response.get("data", {}).get("data", {})
            value = data.get(key, data.get("value"))
            if value:
                self._cache[key] = value
            return value
        except Exception:
            pass
        return None


# ── Factory ─────────────────────────────────────────────────────────


def create_secret_provider(project_path: str | Path) -> SecretProvider:
    """Create a SecretProvider from project configuration.

    Reads ``local.yaml`` → ``secrets:`` section:

    .. code-block:: yaml

        secrets:
          backend: env              # env | aws_secrets | vault | noop
          aws_secrets:
            region: us-east-1
            secret_id: spec-editor
          vault:
            addr: https://vault.example.com
            mount_point: secret

    Falls back to ``env`` if no config found.
    """
    import os

    proj = Path(project_path)

    # Default: env provider
    backend_name = "env"
    backend_config: dict[str, Any] = {}

    # Try local.yaml
    local_yaml = proj / "local.yaml"
    if local_yaml.exists():
        try:
            import yaml

            data = yaml.safe_load(local_yaml.read_text()) or {}
            secrets_cfg = data.get("secrets", {})
            backend_name = secrets_cfg.get("backend", "env")
            backend_config = secrets_cfg.get(backend_name, {})
        except Exception:
            pass

    # Also check env override
    backend_name = os.environ.get("SPEC_EDITOR__SECRETS_BACKEND", backend_name)

    if backend_name == "aws_secrets" or backend_name == "aws":
        return AwsSecretsProvider(
            region=backend_config.get("region", "us-east-1"),
            secret_id=backend_config.get("secret_id", ""),
        )
    elif backend_name == "vault":
        return VaultSecretProvider(
            vault_addr=backend_config.get("addr", ""),
            vault_token=backend_config.get("token", ""),
            mount_point=backend_config.get("mount_point", "secret"),
        )
    elif backend_name == "noop":
        return NoopSecretProvider()
    else:
        return EnvSecretProvider()
