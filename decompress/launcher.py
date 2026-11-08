"""One-click launcher used by Decompress.exe and the desktop shortcut.

With no arguments: starts the Progress Hub (unless one is already running) and the Decompress app,
then opens the app in your browser. Close the window (or press Ctrl+C) to stop both.
With arguments it behaves exactly like the `decompress` command, e.g. `Decompress.exe analyze clip.mp4`.

Data lives in %USERPROFILE%\\Decompress (override with DECOMPRESS_HOME).
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path


def _port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        return s.connect_ex((host, port)) != 0


def _first_free(port: int, tries: int = 20) -> int:
    for p in range(port, port + tries):
        if _port_free(p):
            return p
    raise RuntimeError(f"no free port in {port}-{port + tries}")


def _open_when_ready(url: str, port: int) -> None:
    for _ in range(120):
        if not _port_free(port):
            webbrowser.open(url)
            return
        time.sleep(0.25)


def launch(open_browser: bool = True, app_port: int = 8000, hub_port: int | None = None) -> None:
    hub_port = hub_port or int(os.environ.get("DECOMPRESS_HUB_PORT", "8765"))
    os.environ.setdefault("DECOMPRESS_HOME", str(Path.home() / "Decompress"))
    Path(os.environ["DECOMPRESS_HOME"]).mkdir(parents=True, exist_ok=True)
    os.chdir(os.environ["DECOMPRESS_HOME"])          # relative outputs (analyze --out) land here too

    try:
        from progress_hub import server as hub_server       # pip-installed
    except ImportError:
        from hub import server as hub_server                # source checkout / Decompress.exe
    if _port_free(hub_port):
        hub = hub_server.make_server("127.0.0.1", hub_port)
        threading.Thread(target=hub.serve_forever, daemon=True, name="hub").start()
        hub_note = f"Progress Hub  http://127.0.0.1:{hub_port}"
    else:
        hub_note = f"Progress Hub  http://127.0.0.1:{hub_port}  (already running)"

    import uvicorn
    from decompress import __version__
    from decompress.web.app import create_app

    port = _first_free(app_port)
    url = f"http://127.0.0.1:{port}"
    print(f"Decompress {__version__}\n  App           {url}\n  {hub_note}\n  Data          {os.environ['DECOMPRESS_HOME']}\n"
          "Close this window to stop.\n", flush=True)
    if open_browser:
        threading.Thread(target=_open_when_ready, args=(url, port), daemon=True).start()
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] not in ("--no-browser",):
        from decompress.__main__ import main as cli
        cli(args)
        return
    try:
        launch(open_browser="--no-browser" not in args)
    except KeyboardInterrupt:
        pass
    except Exception as e:  # keep the console open so the user can read the error
        print(f"\nDecompress failed to start: {type(e).__name__}: {e}", file=sys.stderr)
        if getattr(sys, "frozen", False):
            input("Press Enter to close...")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
