"""SourceWork as a hosted service.

The local distribution is one operator on their own machine; this one is the
same core wrapped in the seams it was built with: an authenticator resolves
who is asking (Google and GitHub in the OIDC pass, a dev token in this
scaffold), a Postgres store keeps runs tenant-scoped instead of in one
SQLite file, and the app mounts core unchanged. It is web-only by
construction - this package installs no console scripts, so the only way in
is a browser.

Phase 0: the shell. One default tenant, behavior identical to local, so the
whole product is already served before any of the tenancy machinery is
load-bearing.
"""

__version__ = "0.0.0"
