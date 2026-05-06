# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""
CLI entry point for the SoCMate dashboard.

Usage:
    python -m orchestrator.dashboard [--port 3000] [--no-open]
"""

import argparse
import importlib.util
import os
import sys
import webbrowser
from pathlib import Path

_SERVE_PY = Path(__file__).resolve().parent / "vscode-ext" / "serve.py"


def main():
    parser = argparse.ArgumentParser(
        prog="socmate-dashboard",
        description="SoCMate pipeline dashboard",
    )
    parser.add_argument("--port", "-p", type=int, default=3000)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--no-open", action="store_true",
                        help="Don't auto-open browser")
    args = parser.parse_args()

    if not _SERVE_PY.exists():
        print(f"ERROR: serve.py not found at {_SERVE_PY}", file=sys.stderr)
        sys.exit(1)

    # Import serve.py by file path (hyphenated parent dir prevents normal import)
    spec = importlib.util.spec_from_file_location("serve", str(_SERVE_PY))
    serve = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serve)

    dist_js = Path(serve.DIST_DIR) / "webview.js"
    if not dist_js.exists():
        print("ERROR: dist/webview.js not found. Build the frontend first:")
        print(f"  cd {_SERVE_PY.parent} && npm install && npm run build")
        sys.exit(1)

    if not args.no_open:
        url = f"http://{args.host}:{args.port}"
        # Delay browser open slightly so the server is ready
        import threading
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    from http.server import HTTPServer
    httpd = HTTPServer((args.host, args.port), serve.WebviewHandler)
    print(f"╔══════════════════════════════════════════════╗")
    print(f"║  SoCMate Dashboard                           ║")
    addr = f"http://{args.host}:{args.port}"
    print(f"║  {addr:<43s}║")
    print(f"╚══════════════════════════════════════════════╝")
    print(f"  Project root: {serve.PROJECT_ROOT}")
    print(f"  Press Ctrl+C to stop\n")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        httpd.server_close()


if __name__ == "__main__":
    main()
