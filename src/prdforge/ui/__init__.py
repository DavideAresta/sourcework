"""The web UI: a browser front end for the mesh, and a settings editor.

It is an A2A *client*, not an agent - it drives the orchestrator over the same
protocol the CLI uses, and adds the things a browser needs and a protocol does
not: persistence, live progress, file uploads and a place to change settings.
"""

from prdforge.ui.app import DEFAULT_HOST, PORT, build_app, serve

__all__ = ["DEFAULT_HOST", "PORT", "build_app", "serve"]
