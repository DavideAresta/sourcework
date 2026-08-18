"""``python -m sourcework_cloud`` — start the hosted service.

Web-only by construction. The local distribution's CLI (`sourcework generate`,
`serve`, `app`, …) does not exist here; the only door is the browser, and this
module is the ASGI runner behind it.
"""

from __future__ import annotations

import argparse


def main() -> None:
    from sourcework_cloud.app import serve

    parser = argparse.ArgumentParser(description="Run the SourceWork hosted service.")
    parser.add_argument(
        "command", nargs="?", default="serve", choices=["serve"],
        help="the only thing this package does (default: serve)",
    )
    parser.add_argument("--host", default="0.0.0.0", help="address to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="port to bind (default: 8080)")
    args = parser.parse_args()
    serve(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
