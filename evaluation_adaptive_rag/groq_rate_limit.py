"""Pace Groq requests and retry rate-limit failures for evaluation model calls."""

from __future__ import annotations

import math
import os
import random
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from .constants import (
    DEFAULT_GROQ_MAX_RETRIES,
    DEFAULT_GROQ_OUTPUT_TOKEN_RESERVE,
    DEFAULT_GROQ_TPM_BUDGET,
    MAX_BACKOFF_SECONDS,
)


_RATE_LIMIT_MARKERS = (
    "rate_limit_exceeded",
    "too_many_requests",
    "rate limit reached",
)


def _content_text(value: Any) -> str:
    """Extract text from a string or message-like input for token estimation.

    Args:
        value: Prompt text, message object, or structured message content.
    Returns:
        Best-effort textual representation of the input.
    Called by: ``estimate_request_tokens``.
    """
    if isinstance(value, str):
        return value
    content = getattr(value, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(value)


def estimate_request_tokens(value: Any, output_reserve: int = 256) -> int:
    """Estimate prompt tokens from text length and add a completion reserve.

    Args:
        value: Prompt or message input to estimate.
        output_reserve: Extra token budget reserved for model output.
    Returns:
        A positive estimated token count.
    Called by: ``GroqRateLimiter.invoke`` before each API attempt.
    """
    prompt_chars = len(_content_text(value))
    estimated_prompt = math.ceil(prompt_chars / 4 * 1.25)
    return max(1, estimated_prompt + output_reserve)


def _is_rate_limit_error(error: Exception) -> bool:
    """Recognize common Groq or HTTP 429 rate-limit exception signatures.

    Args:
        error: Exception raised by a model API attempt.
    Returns:
        True when the exception indicates rate limiting, otherwise False.
    Called by: ``GroqRateLimiter.invoke`` before retrying a failed request.
    """
    status_code = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None)
    if status_code == 429:
        return True

    error_name = type(error).__name__.casefold()
    message = str(error).casefold()
    return (
        "ratelimit" in error_name
        or "rate_limit" in error_name
        or any(marker in message for marker in _RATE_LIMIT_MARKERS)
        or bool(re.search(r"\b429\b", message))
    )


def _retry_after_seconds(error: Exception) -> float | None:
    """Read a provider retry delay from headers or the exception message.

    Args:
        error: Rate-limit exception containing optional retry metadata.
    Returns:
        Retry delay in seconds, or None when no supported delay is present.
    Called by: ``GroqRateLimiter.invoke`` after a retryable exception.
    """
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers:
        retry_after = headers.get("retry-after") or headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except (TypeError, ValueError):
                pass
        retry_after_ms = headers.get("retry-after-ms")
        if retry_after_ms:
            try:
                return max(0.0, float(retry_after_ms) / 1000)
            except (TypeError, ValueError):
                pass

        reset = headers.get("x-ratelimit-reset-tokens")
        if reset:
            match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(ms|s|m)?\s*", str(reset), re.I)
            if match:
                value = float(match.group(1))
                unit = (match.group(2) or "s").casefold()
                return value / 1000 if unit == "ms" else value * 60 if unit == "m" else value

    message = str(error)
    match = re.search(
        r"please try again in\s+(\d+(?:\.\d+)?)\s*(ms|seconds?|s)\b",
        message,
        re.I,
    )
    if not match:
        return None
    value = float(match.group(1))
    return value / 1000 if match.group(2).casefold() == "ms" else value


class GroqRateLimiter:
    """Process-local weighted sliding-window limiter plus bounded 429 retries."""

    def __init__(
        self,
        tokens_per_minute: int = 6000,
        output_reserve: int = 256,
        max_retries: int = 5,
        max_backoff_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = lambda: random.uniform(0.05, 0.25),
    ) -> None:
        """Initialize token-window, retry, and injectable timing configuration.

        Args:
            tokens_per_minute: Weighted request budget over a rolling minute.
            output_reserve: Completion-token estimate added to each prompt.
            max_retries: Maximum additional attempts after a rate-limit error.
            max_backoff_seconds: Cap for fallback exponential retry delays.
            clock: Monotonic clock used to schedule token reservations.
            sleep: Wait function used for pacing and retry delays.
            jitter: Function returning a small random delay added to retries.
        Returns:
            None.
        Called by: ``from_environment`` and direct limiter construction.
        """
        if tokens_per_minute <= 0:
            raise ValueError("tokens_per_minute must be greater than zero.")
        if output_reserve < 0:
            raise ValueError("output_reserve must not be negative.")
        if max_retries < 0:
            raise ValueError("max_retries must not be negative.")
        self.tokens_per_minute = tokens_per_minute
        self.output_reserve = output_reserve
        self.max_retries = max_retries
        self.max_backoff_seconds = max_backoff_seconds
        self._clock = clock
        self._sleep = sleep
        self._jitter = jitter
        self._reservations: deque[dict[str, float]] = deque()
        self._lock = threading.Lock()
        self._next_request_at = 0.0
        self.api_attempts = 0
        self.rate_limit_retries = 0
        self.wait_seconds = 0.0
        self.estimated_tokens = 0

    @classmethod
    def from_environment(cls) -> GroqRateLimiter:
        """Build a limiter from the evaluation Groq environment settings.

        Args:
            None; values are read from process environment variables.
        Returns:
            A configured ``GroqRateLimiter`` instance.
        Called by: ``runner.run_evaluation`` when the model provider is Groq.
        """
        return cls(
            tokens_per_minute=int(
                os.getenv("EVAL_GROQ_TPM_BUDGET", str(DEFAULT_GROQ_TPM_BUDGET))
            ),
            output_reserve=int(
                os.getenv(
                    "EVAL_GROQ_OUTPUT_TOKEN_RESERVE",
                    str(DEFAULT_GROQ_OUTPUT_TOKEN_RESERVE),
                )
            ),
            max_retries=int(
                os.getenv("EVAL_MAX_RETRIES", str(DEFAULT_GROQ_MAX_RETRIES))
            ),
            max_backoff_seconds=float(
                os.getenv("EVAL_MAX_BACKOFF_SECONDS", str(MAX_BACKOFF_SECONDS))
            ),
        )

    def snapshot(self) -> dict[str, int | float]:
        """Return accumulated API, retry, wait, and token-estimate statistics.

        Args:
            None.
        Returns:
            A metrics mapping suitable for per-case deltas in trace records.
        Called by: ``runner.run_evaluation`` before and after each case.
        """
        return {
            "api_attempts": self.api_attempts,
            "rate_limit_retries": self.rate_limit_retries,
            "rate_limit_wait_ms": round(self.wait_seconds * 1000),
            "estimated_tokens": self.estimated_tokens,
        }

    def _wait_for_capacity(self, estimated_tokens: int) -> dict[str, float]:
        """Reserve weighted sliding-window capacity, sleeping until available.

        Args:
            estimated_tokens: Token budget to reserve for one API request.
        Returns:
            Reservation time and token count for the request.
        Called by: ``invoke`` before each attempt and directly by limiter tests.
        """
        if estimated_tokens > self.tokens_per_minute:
            raise ValueError(
                "Estimated request exceeds EVAL_GROQ_TPM_BUDGET; "
                "raise the budget or reduce prompt/output sizing."
            )
        while True:
            with self._lock:
                now = self._clock()
                scheduled_at = max(now, self._next_request_at)
                while (
                    self._reservations
                    and scheduled_at - self._reservations[0]["time"] >= 60
                ):
                    self._reservations.popleft()
                used = sum(item["tokens"] for item in self._reservations)
                if used + estimated_tokens <= self.tokens_per_minute:
                    reservation = {
                        "time": scheduled_at,
                        "tokens": float(estimated_tokens),
                    }
                    self._reservations.append(reservation)
                    self._next_request_at = (
                        scheduled_at + estimated_tokens * 60 / self.tokens_per_minute
                    )
                    self.estimated_tokens += estimated_tokens
                    wait_seconds = max(0.0, scheduled_at - now)
                else:
                    window_wait = max(
                        0.01,
                        60 - (scheduled_at - self._reservations[0]["time"]),
                    )
                    pacing_wait = max(0.0, scheduled_at - now)
                    wait_seconds = max(window_wait, pacing_wait)
                    reservation = None
            if reservation is not None:
                if wait_seconds:
                    self._sleep(wait_seconds)
                    self.wait_seconds += wait_seconds
                return reservation
            self._sleep(wait_seconds)
            self.wait_seconds += wait_seconds

    @staticmethod
    def _actual_token_count(response: Any) -> int | None:
        """Extract a reported total-token count from common response metadata.

        Args:
            response: Model result or nested structured-output response.
        Returns:
            Positive reported total token count, or None if unavailable.
        Called by: ``invoke`` after a successful API attempt.
        """
        if isinstance(response, dict):
            for value in response.values():
                actual = GroqRateLimiter._actual_token_count(value)
                if actual is not None:
                    return actual
            return None
        usage = getattr(response, "usage_metadata", None)
        if isinstance(usage, dict):
            total = usage.get("total_tokens")
            if isinstance(total, int) and total > 0:
                return total
        metadata = getattr(response, "response_metadata", None)
        if isinstance(metadata, dict):
            usage = metadata.get("token_usage") or metadata.get("usage")
            if isinstance(usage, dict):
                total = usage.get("total_tokens") or usage.get("total")
                if isinstance(total, int) and total > 0:
                    return total
        return None

    def invoke(self, function: Callable[[], Any], request: Any) -> Any:
        """Pace a model call and retry only rate-limit failures within the limit.

        Args:
            function: Zero-argument callable that performs one model request.
            request: Input used to estimate request token cost.
        Returns:
            The model result returned by ``function``.
        Called by: both model proxy ``invoke`` methods for evaluation calls.
        """
        estimate = estimate_request_tokens(request, self.output_reserve)
        for attempt in range(self.max_retries + 1):
            reservation = self._wait_for_capacity(estimate)
            self.api_attempts += 1
            try:
                result = function()
            except Exception as error:
                if not _is_rate_limit_error(error) or attempt >= self.max_retries:
                    raise
                self.rate_limit_retries += 1
                retry_after = _retry_after_seconds(error)
                if retry_after is None:
                    retry_after = min(
                        self.max_backoff_seconds,
                        2**attempt,
                    )
                wait_seconds = max(0.0, retry_after) + self._jitter()
                self._sleep(wait_seconds)
                self.wait_seconds += wait_seconds
                continue

            actual_tokens = self._actual_token_count(result)
            if actual_tokens is not None:
                with self._lock:
                    reservation["tokens"] = float(actual_tokens)
                self.estimated_tokens += actual_tokens - estimate
            return result
        raise RuntimeError("Groq retry loop ended unexpectedly.")


class _RateLimitedRunnable:
    def __init__(self, runnable: Any, limiter: GroqRateLimiter) -> None:
        """Wrap a structured-output runnable with a shared Groq rate limiter.

        Args:
            runnable: Underlying LangChain structured-output runnable.
            limiter: Limiter used before its model invocation.
        Returns:
            None.
        Called by: ``RateLimitedChatModel.with_structured_output``.
        """
        self._runnable = runnable
        self._limiter = limiter

    def invoke(self, input: Any, *args: Any, **kwargs: Any) -> Any:
        """Invoke the wrapped structured runnable through the shared limiter.

        Args:
            input: Runnable input to send to the model.
            *args: Additional positional invocation options.
            **kwargs: Additional keyword invocation options.
        Returns:
            The underlying runnable's result.
        Called by: LangChain graph code using structured output during evaluation.
        """
        return self._limiter.invoke(
            lambda: self._runnable.invoke(input, *args, **kwargs),
            input,
        )

    def __getattr__(self, name: str) -> Any:
        """Delegate attributes not defined by this proxy to the runnable.

        Args:
            name: Attribute requested by the LangChain consumer.
        Returns:
            The corresponding underlying runnable attribute.
        Called by: Python attribute lookup on the structured-output proxy.
        """
        return getattr(self._runnable, name)


class RateLimitedChatModel:
    """Proxy wrapping both direct and structured LangChain model invocations."""

    def __init__(self, model: Any, limiter: GroqRateLimiter) -> None:
        """Wrap a chat model and limiter for evaluation requests.

        Args:
            model: Underlying LangChain chat model.
            limiter: Shared rate limiter for model requests.
        Returns:
            None.
        Called by: ``runner.run_evaluation`` when Groq is the configured provider.
        """
        self._model = model
        self._limiter = limiter

    def invoke(self, input: Any, *args: Any, **kwargs: Any) -> Any:
        """Invoke the underlying chat model through the shared limiter.

        Args:
            input: Prompt or message input sent to the model.
            *args: Additional positional invocation options.
            **kwargs: Additional keyword invocation options.
        Returns:
            The underlying model's response.
        Called by: application graph nodes and ``classify_query`` during evaluation.
        """
        return self._limiter.invoke(
            lambda: self._model.invoke(input, *args, **kwargs),
            input,
        )

    def with_structured_output(self, *args: Any, **kwargs: Any) -> _RateLimitedRunnable:
        """Create a rate-limited proxy for the model's structured-output runnable.

        Args:
            *args: Structured-output schema and positional options.
            **kwargs: Structured-output configuration options.
        Returns:
            A runnable proxy that routes invocation through the limiter.
        Called by: application graph nodes and ``classify_query`` during evaluation.
        """
        runnable = self._model.with_structured_output(*args, **kwargs)
        return _RateLimitedRunnable(runnable, self._limiter)

    def __getattr__(self, name: str) -> Any:
        """Delegate model attributes not explicitly proxied.

        Args:
            name: Attribute requested by a graph or model consumer.
        Returns:
            The corresponding underlying model attribute.
        Called by: Python attribute lookup on the chat-model proxy.
        """
        return getattr(self._model, name)
