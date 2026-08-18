"""The settings page, per tenant, on the hosted distribution.

The local page edits the process's own ``.env``. That is wrong twice for a
service: rewriting the running process's environment is a privilege escalation
waiting to happen, and one file cannot hold many tenants' values anyway. This
backend keeps the same allow-listed keys in Postgres, scoped by the same RLS
that scopes runs.

What *differs* from the local page is the offer, not the shape: a hosted
installation has no CLIs and no local model server, so :attr:`allowed_backends`
is the API family only. Because the settings routes serve whatever the backend
describes, filtering here is what keeps ``claude-code`` off the tenant's page -
and because ``env_file.FIELDS`` is still the one allow-list, a tenant cannot
even write it by posting the key directly.
"""

from __future__ import annotations

from typing import Any

from sourcework.config import API_BACKEND_IDS
from sourcework.ui import env_file

from sourcework_cloud.store import PostgresStore


class TenantSettingsBackend:
    """A :class:`~sourcework.ui.env_file.SettingsBackend` over Postgres.

    Synchronous like the local file backend (its protocol is synchronous), and
    restart-free: there is no mesh to restart for one tenant's settings, so a
    save never takes the whole service down.
    """

    label = "Tenant settings"
    default_profile = env_file.DEFAULT_PROFILE
    restartable = False
    allowed_backends = API_BACKEND_IDS

    def __init__(self, store: PostgresStore) -> None:
        self._store = store

    def read(self) -> dict[str, str]:
        return self._store.get_settings()

    def write(self, updates: dict[str, str]) -> list[str]:
        # The same rules the local page enforces, before anything is stored:
        # only keys in the allow-list, a masked secret means "untouched", and a
        # backend this distribution does not offer is rejected even when posted
        # by hand. `env_file.write` would apply them to a file; this applies
        # them to the tenant's row instead.
        return self._store.put_settings(
            env_file.filter_updates(self.read(), updates, allowed=self.allowed_backends)
        )

    def describe(self) -> list[dict[str, Any]]:
        return env_file.describe_values(self.read(), allowed=self.allowed_backends)

    def profiles_for(self) -> dict[str, dict[str, Any]]:
        return env_file.profiles_for_values(self.read(), allowed=self.allowed_backends)