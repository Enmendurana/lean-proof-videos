"""Windows-friendly launcher and server entrypoint for Lean Proof Studio."""

from __future__ import annotations

import argparse
from pathlib import Path
import threading
import time
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser

import uvicorn

from proof_video.studio.app import create_app
from proof_video.studio.security import StudioSecurity


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 43128


def _healthy(url: str) -> bool:
    try:
        with urlopen(f"{url}/api/health", timeout=0.6) as response:
            return response.status == 200
    except (OSError, URLError):
        return False


def _open_when_ready(url: str) -> None:
    for _ in range(100):
        if _healthy(url):
            webbrowser.open(url)
            return
        time.sleep(0.1)


def _launch_url(root: Path, base_url: str) -> str:
    security = StudioSecurity(root / ".lean-proof-video-web")
    return f"{base_url}/?token={security.issue_bootstrap_token()}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local Lean Proof Studio")
    parser.add_argument(
        "command", nargs="?", choices=("start", "status"), default="start"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    base_url = f"http://{args.host}:{args.port}"
    if args.command == "status":
        print("running" if _healthy(base_url) else "stopped")
        return 0 if _healthy(base_url) else 1
    launch_url = _launch_url(root, base_url)
    if _healthy(base_url):
        if not args.no_browser:
            webbrowser.open(launch_url)
        return 0

    app = create_app(root)
    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)
    app.state.shutdown_callback = lambda: setattr(server, "should_exit", True)
    if not args.no_browser:
        threading.Thread(
            target=_open_when_ready,
            args=(launch_url,),
            daemon=True,
        ).start()
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
