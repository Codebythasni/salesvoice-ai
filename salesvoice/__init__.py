"""SalesVoice AI - application package.

Stage 1 provides a Flask backend and a browser-based sales dashboard that
talks to the customer through the browser's speech recognition/synthesis
features and answers with Google Gemini.

The API key is only ever read on the server. It is never sent to the browser.
"""

from __future__ import annotations

from .app_factory import create_app

__all__ = ["create_app"]
