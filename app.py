"""Entry point for the SalesVoice AI development server.

Run with the project virtual environment:

    .\\.venv\\Scripts\\python.exe app.py

Microphone access in the browser requires a secure context, so use the
localhost address printed on start-up (http://127.0.0.1:5000) rather than the
LAN IP address.
"""

from __future__ import annotations

from salesvoice import create_app
from salesvoice.config import load_settings

app = create_app()


def main() -> None:
    settings = load_settings()
    host = settings.host
    port = settings.port
    debug = settings.debug
    database = app.extensions["salesvoice"]["database"]

    print("=" * 70)
    print("  SalesVoice AI - Stage 2")
    print("=" * 70)
    print(f"  Dashboard : http://127.0.0.1:{port}/dashboard")
    print(f"  Homepage  : http://127.0.0.1:{port}/")
    print(f"  Model     : {settings.model_name}")
    print(f"  Assistant : {'ready' if settings.gemini_configured else 'NOT CONFIGURED'}")
    print(f"  Database  : {settings.resolved_database_path}")
    print(f"  Saved calls: {database.count_calls()}")
    print(f"  Recordings : {settings.resolved_recordings_dir} (local only, excluded from Git)")
    print("  Tip       : open the localhost URL above so the browser allows microphone access.")
    print("=" * 70)

    app.run(host=host, port=port, debug=debug, use_reloader=debug)


if __name__ == "__main__":
    main()
