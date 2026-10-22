"""Gemini integration for the sales assistant.

The API key is read from the server-side environment only. It is never
exposed to the browser and never written to logs.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterable

from . import prompts
from .product import Product, get_product
from .summary import SUMMARY_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (1.0, 2.5)

# The HTTP timeout of a single Gemini request, in seconds. It has to be set
# explicitly: without it the SDK sends the request with no timeout at all,
# so a stalled connection keeps the dashboard waiting forever and the
# "Working" indicator never clears.
DEFAULT_TIMEOUT_SECONDS = 30.0

# The SDK retries on its own. We limit it to a single attempt and own the
# retry policy here, so the total waiting time stays bounded and the
# behaviour is easy to reason about (and to test).
SDK_RETRY_ATTEMPTS = 1

# HTTP status codes that are worth another attempt.
RETRYABLE_STATUS_CODES = (429, 500, 502, 503)


def redact_secret(text: str, secret: str | None) -> str:
    """Remove a secret from a string before it is written to a log."""
    if secret and text and secret in text:
        return text.replace(secret, "[redacted]")
    return text


class GeminiError(RuntimeError):
    """A Gemini failure that can be shown to the user as a friendly message.

    ``public_message`` is safe to return to the browser. ``detail`` is kept for
    server-side logging only and is never sent to the client. ``code`` is a
    short machine-readable reason and ``retryable`` tells the UI whether a
    Retry button makes sense for this failure.
    """

    def __init__(
        self,
        public_message: str,
        detail: str = "",
        status_code: int = 502,
        code: str = "gemini_error",
        retryable: bool = True,
    ) -> None:
        super().__init__(public_message)
        self.public_message = public_message
        self.detail = detail
        self.status_code = status_code
        self.code = code
        self.retryable = retryable


class GeminiNotConfiguredError(GeminiError):
    """Raised when no API key or model is configured on the server."""

    def __init__(self) -> None:
        super().__init__(
            "The assistant is not configured yet. Add GEMINI_API_KEY and GEMINI_MODEL "
            "to the .env file on the server and restart the app.",
            detail="GEMINI_API_KEY / GEMINI_MODEL missing",
            status_code=503,
            code="assistant_not_configured",
            retryable=False,
        )


class SalesAssistant:
    """Answers customer turns using Gemini.

    The class is deliberately small and injectable so that tests can replace it
    with a stub, and so a later stage can swap in a different provider.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        product: Product | None = None,
        client: Any | None = None,
        temperature: float = 0.6,
        max_output_tokens: int = 400,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not api_key or not model:
            raise GeminiNotConfiguredError()
        self._api_key = api_key
        self._model = model
        self._product = product or get_product()
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._timeout_seconds = max(1.0, float(timeout_seconds))
        self._client = client
        self._system_prompt = prompts.build_system_prompt(self._product)

    @property
    def model(self) -> str:
        return self._model

    @property
    def product(self) -> Product:
        return self._product

    @property
    def timeout_seconds(self) -> float:
        return self._timeout_seconds

    def total_budget_seconds(self) -> float:
        """The longest one chat request may take before it gives up.

        Two request timeouts plus a little room for the backoff sleeps. The
        budget is what the dashboard uses to size its own timeout, so the
        server always answers before the browser gives up.
        """
        return self._timeout_seconds * 2 + 5

    def _get_client(self) -> Any:
        if self._client is None:
            from google import genai  # imported lazily so tests can run without a key

            try:
                self._client = genai.Client(
                    api_key=self._api_key,
                    http_options=self._http_options(),
                )
            except Exception as exc:  # pragma: no cover - defensive
                raise GeminiError(
                    "The assistant could not be started on the server.",
                    detail=f"client init failed: {type(exc).__name__}",
                    status_code=503,
                    code="gemini_client_error",
                    retryable=False,
                ) from exc
        return self._client

    def _http_options(self) -> Any:
        """HTTP options for the Gemini client.

        ``timeout`` is in milliseconds and is the only thing that stops a
        stalled request from hanging the chat forever. The SDK's own retry
        loop is limited to a single attempt so that the retry policy lives in
        one place (here) and the total wait stays predictable.
        """
        from google.genai import types

        timeout_ms = max(1, int(self._timeout_seconds * 1000))
        try:
            retry_options = types.HttpRetryOptions(
                attempts=SDK_RETRY_ATTEMPTS,
                http_status_codes=list(RETRYABLE_STATUS_CODES),
            )
        except Exception:  # pragma: no cover - older SDK without retry options
            retry_options = None
        if retry_options is None:
            return types.HttpOptions(timeout=timeout_ms)
        return types.HttpOptions(timeout=timeout_ms, retry_options=retry_options)

    def _build_contents(self, history: Iterable[dict]) -> list[dict]:
        return prompts.build_conversation_contents(list(history))

    def _build_config(self, system_instruction: str) -> Any:
        from google.genai import types

        return types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=self._temperature,
            max_output_tokens=self._max_output_tokens,
        )

    def generate_reply(self, history: Iterable[dict]) -> str:
        """Return the assistant's spoken reply for the conversation so far."""
        contents = self._build_contents(history)
        if not contents:
            raise GeminiError(
                "The assistant received an empty request.",
                detail="empty history",
                status_code=400,
            )

        client = self._get_client()

        response = self._call_with_retry(client, contents, self._system_prompt)

        text = self._extract_text(response)
        if not text:
            raise GeminiError(
                "The assistant did not return an answer this time. Please try again.",
                detail="empty model response",
                status_code=502,
            )
        return text

    def generate_summary(self, transcript: str) -> str:
        """Summarise a finished call. Returns the raw model output."""
        if not transcript or not transcript.strip():
            raise GeminiError(
                "There is no transcript to summarise.",
                detail="empty transcript",
                status_code=400,
            )

        client = self._get_client()
        contents = [{"role": "user", "parts": [{"text": transcript}]}]

        response = self._call_with_retry(client, contents, SUMMARY_SYSTEM_PROMPT)

        text = self._extract_text(response)
        if not text:
            raise GeminiError(
                "The assistant did not return a summary this time.",
                detail="empty summary response",
                status_code=502,
            )
        return text

    def _call_with_retry(self, client: Any, contents: list[dict], system_instruction: str) -> Any:
        """Call Gemini, retrying a small number of transient failures.

        The total time spent here is bounded by :meth:`total_budget_seconds`,
        so a chat request always ends in an answer or in an error and never
        in an endless wait.
        """
        last_error: Exception | None = None
        deadline = time.monotonic() + self.total_budget_seconds()

        for attempt in range(MAX_ATTEMPTS):
            try:
                return client.models.generate_content(
                    model=self._model,
                    contents=contents,
                    config=self._build_config(system_instruction),
                )
            except Exception as exc:
                last_error = exc
                if attempt == MAX_ATTEMPTS - 1 or not self._is_transient(exc):
                    raise self._translate_error(exc) from exc
                delay = RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)]
                if time.monotonic() + delay >= deadline:
                    logger.warning(
                        "Giving up on Gemini after %.1fs of retrying (%s)",
                        self.total_budget_seconds(),
                        type(exc).__name__,
                    )
                    raise self._translate_error(exc) from exc
                logger.warning(
                    "Transient Gemini failure (%s), retrying in %.1fs",
                    type(exc).__name__,
                    delay,
                )
                time.sleep(delay)

        raise self._translate_error(last_error) if last_error else GeminiError(
            "The assistant could not complete the request. Please try again."
        )

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        """True for failures that are worth retrying (capacity / rate limits).

        Timeouts are deliberately *not* retried here: another 30 second
        attempt would leave the customer waiting far too long, and a timeout
        gets its own clear message instead.
        """
        if SalesAssistant._is_timeout(exc):
            return False
        code = getattr(exc, "code", None)
        status = (getattr(exc, "status", None) or "").upper()
        if code in (500, 502, 503):
            return True
        if "UNAVAILABLE" in status or "INTERNAL" in status:
            return True
        if code == 429 or "RESOURCE_EXHAUSTED" in status or "QUOTA" in status:
            return True
        return False

    @staticmethod
    def _is_timeout(exc: Exception) -> bool:
        if isinstance(exc, TimeoutError):
            return True
        try:  # httpx is a dependency of google-genai, but keep this optional.
            import httpx

            if isinstance(exc, httpx.TimeoutException):
                return True
        except Exception:  # pragma: no cover - httpx always installed in practice
            pass
        status = (getattr(exc, "status", None) or "").upper()
        return status == "DEADLINE_EXCEEDED" or getattr(exc, "code", None) == 504

    @staticmethod
    def _is_network_error(exc: Exception) -> bool:
        """True for connection failures (no route, DNS, refused, reset)."""
        if isinstance(exc, ConnectionError):
            return True
        try:
            import httpx

            return isinstance(exc, httpx.ConnectError)
        except Exception:  # pragma: no cover - httpx always installed in practice
            return False

    @staticmethod
    def _is_api_error(exc: Exception) -> bool:
        """True for errors raised by the Gemini SDK itself."""
        try:
            from google.genai import errors as genai_errors
        except Exception:  # pragma: no cover - the SDK is a hard dependency
            return False
        return isinstance(exc, genai_errors.APIError)

    def _scrub(self, text: str) -> str:
        """Remove the API key from a string that may be logged."""
        return redact_secret(text, self._api_key)

    @staticmethod
    def _extract_text(response: Any) -> str:
        text = getattr(response, "text", None)
        if not text:
            candidates = getattr(response, "candidates", None) or []
            parts: list[str] = []
            for candidate in candidates:
                content = getattr(candidate, "content", None)
                for part in getattr(content, "parts", None) or []:
                    part_text = getattr(part, "text", None)
                    if part_text:
                        parts.append(part_text)
            text = "\n".join(parts)
        return (text or "").strip()

    def translate_error(self, exc: Exception) -> GeminiError:
        """Classify any assistant failure into a safe, user-facing error.

        The route uses this so that a failure which did not come from
        :meth:`_call_with_retry` (an SDK error raised outside the retry
        loop, for example) still gets a specific message and a specific
        error code instead of a generic 500.
        """
        return self._translate_error(exc)

    def _translate_error(self, exc: Exception) -> GeminiError:
        """Map a Gemini SDK error onto a user-safe GeminiError.

        Each failure has its own message and its own error code, so
        "temporarily unavailable" is never used for an invalid key, a bad
        model name or a rate limit. The raw exception text is never returned
        to the browser and the API key is never logged.
        """
        code = getattr(exc, "code", None)
        status = (getattr(exc, "status", None) or "").upper()
        name = type(exc).__name__
        detail = self._scrub(f"{name} code={code} status={status}")
        message_text = str(exc).lower()

        if self._is_timeout(exc):
            return GeminiError(
                "The assistant took too long to respond, so no reply was generated. "
                "Your message is saved - press Retry to try again.",
                detail=detail,
                status_code=504,
                code="gemini_timeout",
                retryable=True,
            )

        if self._is_network_error(exc):
            return GeminiError(
                "The server could not reach Gemini. Check the internet connection "
                "and press Retry.",
                detail=detail,
                status_code=502,
                code="gemini_network",
                retryable=True,
            )

        if not self._is_api_error(exc):
            return GeminiError(
                "The assistant could not complete the request. Please try again.",
                detail=detail,
                status_code=502,
                code="gemini_error",
                retryable=True,
            )

        if code == 429 or "RESOURCE_EXHAUSTED" in status or "QUOTA" in status:
            return GeminiError(
                "The assistant is busy right now and hit a usage limit. "
                "Please wait a moment and try again.",
                detail=detail,
                status_code=429,
                code="gemini_rate_limited",
                retryable=True,
            )
        if (
            code in (401, 403)
            or "API_KEY_INVALID" in status
            or "PERMISSION_DENIED" in status
            or "UNAUTHENTICATED" in status
            or "api key not valid" in message_text
            or "api key expired" in message_text
        ):
            return GeminiError(
                "The assistant's server credentials were rejected, so no reply could be "
                "generated. Check the GEMINI_API_KEY value in the server's .env file.",
                detail=detail,
                status_code=503,
                code="gemini_auth",
                retryable=False,
            )
        if code == 404 or "NOT_FOUND" in status:
            return GeminiError(
                "The configured Gemini model was not found. Update GEMINI_MODEL "
                "in the server's .env file.",
                detail=detail,
                status_code=503,
                code="gemini_model_not_found",
                retryable=False,
            )
        if code == 400 or "INVALID_ARGUMENT" in status:
            return GeminiError(
                "Gemini rejected the request, so no reply could be generated. This is "
                "not a temporary outage - the model name or the server settings need "
                "a look. Your message is saved.",
                detail=detail,
                status_code=502,
                code="gemini_bad_request",
                retryable=False,
            )
        if code is not None and 500 <= code < 600:
            return GeminiError(
                "The Gemini service reported that the configured model "
                f"({self._model}) is unavailable right now. This is a temporary "
                "problem on Google's side, not with your call. Please try again shortly.",
                detail=detail,
                status_code=503,
                code="gemini_unavailable",
                retryable=True,
            )
        return GeminiError(
            "The assistant could not complete the request. Please try again.",
            detail=detail,
            status_code=502,
            code="gemini_error",
            retryable=True,
        )


def build_assistant(settings: Any, product: Product | None = None) -> SalesAssistant | None:
    """Create a :class:`SalesAssistant` from settings, or ``None`` if unconfigured."""
    if not getattr(settings, "gemini_configured", False):
        return None
    timeout = getattr(settings, "gemini_timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    return SalesAssistant(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        product=product or get_product(),
        timeout_seconds=timeout,
    )
