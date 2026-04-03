#!/usr/bin/env python3
"""CLI entry point for Hermes Dashboard."""

import argparse
import os
import sys
import webbrowser
from pathlib import Path

from hermes_dashboard import __version__
from hermes_dashboard.server import create_server


def find_hermes_dir():
    """Auto-detect Hermes data directory."""
    # Check env var first
    env = os.environ.get("HERMES_DIR") or os.environ.get("HERMES_HOME")
    if env and Path(env).exists():
        return Path(env)

    # Default location
    default = Path.home() / ".hermes"
    if default.exists():
        return default

    return None


def main():
    parser = argparse.ArgumentParser(
        prog="hermes-dashboard",
        description="🚀 Hermes Dashboard — Monitor your Hermes Agent from the browser",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  hermes-dashboard                     # Start on default port 7777
  hermes-dashboard -p 8080             # Custom port
  hermes-dashboard --hermes-dir /path  # Custom Hermes directory
  hermes-dashboard --no-open           # Don't auto-open browser
        """,
    )
    parser.add_argument(
        "-p", "--port",
        type=int,
        default=int(os.environ.get("HERMES_DASHBOARD_PORT", "7777")),
        help="Port to run on (default: 7777, env: HERMES_DASHBOARD_PORT)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--hermes-dir",
        type=str,
        default=None,
        help="Path to Hermes data directory (default: ~/.hermes, env: HERMES_DIR)",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Don't auto-open browser on start",
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"hermes-dashboard {__version__}",
    )

    args = parser.parse_args()

    # Find Hermes directory
    hermes_dir = Path(args.hermes_dir) if args.hermes_dir else find_hermes_dir()

    if not hermes_dir or not hermes_dir.exists():
        print("❌ Could not find Hermes data directory.")
        print()
        print("   Looked in:")
        print(f"     - ~/.hermes")
        print(f"     - $HERMES_DIR / $HERMES_HOME env vars")
        print()
        print("   Use --hermes-dir /path/to/.hermes to specify manually.")
        print()
        print("   Make sure Hermes Agent is installed: https://github.com/hermes-agent/hermes")
        sys.exit(1)

    state_db = hermes_dir / "state.db"
    if not state_db.exists():
        print(f"⚠️  Found {hermes_dir} but no state.db inside it.")
        print(f"   Run Hermes Agent at least once to create the database.")
        sys.exit(1)

    url = f"http://{args.host}:{args.port}"

    print()
    print("  ⚡ Hermes Dashboard")
    print(f"  {'─' * 40}")
    print(f"  🌐 URL:        {url}")
    print(f"  📂 Hermes Dir: {hermes_dir}")
    print(f"  🗄️  Database:   {state_db}")
    print(f"  {'─' * 40}")
    print()

    if not args.no_open:
        webbrowser.open(url)

    server = create_server(args.host, args.port, hermes_dir)

    try:
        print(f"  ✅ Server running. Press Ctrl+C to stop.\n")
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  👋 Shutting down...")
        server.server_close()


if __name__ == "__main__":
    main()
