"""SalesVoice AI - Gemini API connection test.

Loads GEMINI_API_KEY and GEMINI_MODEL from a local .env file, sends one short
text-generation request, and prints the reply. The API key is never printed.
"""

from __future__ import annotations

import os
import sys
from typing import NoReturn

from dotenv import load_dotenv
from google import genai
from google.genai import errors


def _exit(message: str) -> NoReturn:
    print(f"[ERROR] {message}", file=sys.stderr)
    raise SystemExit(1)


def _report_api_error(exc: errors.APIError, model: str) -> NoReturn:
    code = getattr(exc, "code", None)
    status = (getattr(exc, "status", None) or "").upper()
    status_text = status or "unknown"

    if code == 429 or "RESOURCE_EXHAUSTED" in status or "QUOTA" in status:
        _exit(
            f"Quota or rate limit reached for model '{model}'. "
            "Check usage and billing in Google AI Studio, then try again later."
        )
    if code in (400, 401, 403) or "API_KEY_INVALID" in status or "PERMISSION_DENIED" in status:
        _exit(
            f"The API key was rejected (HTTP {code}, {status_text}). "
            "Verify GEMINI_API_KEY in your .env file."
        )
    if code == 404 or "NOT_FOUND" in status:
        _exit(
            f"Model '{model}' was not found or is not available to this API key. "
            "Update GEMINI_MODEL in your .env file (see .env.example)."
        )
    if code is not None and 500 <= code < 500 + 100:
        _exit(
            f"Gemini server error (HTTP {code}, {status_text}). "
            "The service may be temporarily unavailable - try again later."
        )
    _exit(f"Gemini API error (HTTP {code}, {status_text}).")


def main() -> None:
    load_dotenv()

    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key or api_key == "your_api_key_here":
        _exit(
            "GEMINI_API_KEY is not configured. Copy .env.example to .env "
            "and set GEMINI_API_KEY inside the .env file."
        )

    model = (os.getenv("GEMINI_MODEL") or "").strip()
    if not model or model == "your_model_name_here":
        _exit(
            "GEMINI_MODEL is not configured. Set it in your .env file "
            "(see .env.example for the format)."
        )

    try:
        client = genai.Client(api_key=api_key)
    except ValueError:
        _exit("The Gemini client rejected the configuration. Check GEMINI_API_KEY in your .env file.")

    prompt = "Reply with exactly: SalesVoice AI connection OK"

    try:
        response = client.models.generate_content(model=model, contents=prompt)
    except errors.APIError as exc:
        _report_api_error(exc, model)
    except Exception as exc:
        _exit(
            f"Request to Gemini failed ({type(exc).__name__}). "
            "Check your internet connection and try again."
        )

    text = (response.text or "").strip()
    if not text:
        _exit(
            "Gemini returned an empty response (the prompt may have been "
            "blocked by safety settings)."
        )

    print(f"[OK] Model '{model}' responded:")
    print(text)


if __name__ == "__main__":
    main()
