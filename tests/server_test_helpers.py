"""Run an ASGI app under real uvicorn on a free local port, for end-to-end HTTP tests."""

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import uvicorn
from starlette.types import ASGIApp

STARTUP_TIMEOUT_SECONDS = 15


def free_local_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@contextmanager
def running_uvicorn(app: ASGIApp) -> Iterator[str]:
    """Yields the base URL (http://127.0.0.1:<port>) while the app is serving."""
    port = free_local_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while not server.started:
        if not thread.is_alive():
            raise RuntimeError("server exited during startup (see its log above)")
        if time.monotonic() > deadline:
            raise RuntimeError("server did not start in time")
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
