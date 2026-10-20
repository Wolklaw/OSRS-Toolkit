"""Run the service.

    python -m toolkit_web

Binds to localhost by default and speaks plain HTTP. Put a tunnel or a reverse proxy in front
of it for anything reachable from outside the machine, same as the sync service it sits beside.
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "toolkit_web.app:create_app",
        factory=True,
        host=os.environ.get("TOOLKIT_HOST", "127.0.0.1"),
        port=int(os.environ.get("TOOLKIT_PORT", "8100")),
        log_level=os.environ.get("TOOLKIT_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
