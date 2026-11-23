"""``python -m server`` -- start the live-play server on localhost.

Local single-user only: bound to 127.0.0.1, no reload, no CORS, no authentication.
The API docs are at ``/docs``; the replay viewer is at ``/``.
"""

from __future__ import annotations

import uvicorn

from server.app import app

HOST = "127.0.0.1"
PORT = 8000


def main() -> None:
    """Run uvicorn (blocking) with the project's defaults."""
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
