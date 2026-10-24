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
