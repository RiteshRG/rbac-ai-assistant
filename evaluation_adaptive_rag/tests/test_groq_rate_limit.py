"""Unit tests for Groq request pacing, retries, and LangChain proxy behavior."""

import unittest

from evaluation_adaptive_rag.groq_rate_limit import (
    GroqRateLimiter,
    RateLimitedChatModel,
)


class FakeClock:
    def __init__(self):
        """Initialize simulated time and collected sleep durations.

        Args:
            None.
        Returns:
            None.
        Called by: rate-limit test methods that inject this clock into a limiter.
        """
        self.value = 0.0
        self.sleeps = []

    def now(self):
        """Return the current simulated monotonic time.

        Args:
            None.
        Returns:
            Current simulated time in seconds.
        Called by: ``GroqRateLimiter`` through its injected clock callback.
        """
        return self.value

    def sleep(self, seconds):
        """Advance simulated time and record the requested delay.

        Args:
            seconds: Delay requested by the limiter.
        Returns:
            None.
        Called by: ``GroqRateLimiter`` through its injected sleep callback.
        """
        self.sleeps.append(seconds)
        self.value += seconds


class GroqRateLimitTests(unittest.TestCase):
    def test_retries_429_using_provider_retry_delay(self):
        """Verify retry-after text controls a single retry after a 429 error.

        Args:
            None.
        Returns:
            None; assertions inspect the result, attempts, retries, and delay.
        Called by: the unittest test runner during test discovery.
        """
        clock = FakeClock()
        limiter = GroqRateLimiter(
            tokens_per_minute=6000,
            output_reserve=0,
            max_retries=2,
            clock=clock.now,
            sleep=clock.sleep,
            jitter=lambda: 0,
        )
        responses = [
            RuntimeError("429 rate_limit_exceeded. Please try again in 2.5s."),
            "ok",
        ]

        def request():
            """Return the next scripted result or raise its scripted exception.

            Args:
                None.
            Returns:
                The next successful response in the test sequence.
            Called by: ``GroqRateLimiter.invoke`` for each simulated attempt.
            """
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        self.assertEqual(limiter.invoke(request, "small prompt"), "ok")
        self.assertEqual(limiter.api_attempts, 2)
        self.assertEqual(limiter.rate_limit_retries, 1)
        self.assertEqual(clock.sleeps, [2.5])

    def test_waits_until_weighted_sliding_window_has_capacity(self):
        """Verify a request waits for capacity after exceeding the token window.

        Args:
            None.
        Returns:
            None; assertion checks that simulated waits total one minute.
        Called by: the unittest test runner during test discovery.
        """
        clock = FakeClock()
        limiter = GroqRateLimiter(
            tokens_per_minute=100,
            output_reserve=0,
            clock=clock.now,
            sleep=clock.sleep,
            jitter=lambda: 0,
        )
        limiter._wait_for_capacity(60)
        limiter._wait_for_capacity(60)
        self.assertEqual(sum(clock.sleeps), 60)

    def test_smoothly_spaces_requests_before_window_is_full(self):
        """Verify token reservations pace requests before the window is full.

        Args:
            None.
        Returns:
            None; assertion checks the expected simulated pacing delay.
        Called by: the unittest test runner during test discovery.
        """
        clock = FakeClock()
        limiter = GroqRateLimiter(
            tokens_per_minute=100,
            output_reserve=0,
            clock=clock.now,
            sleep=clock.sleep,
            jitter=lambda: 0,
        )
        limiter._wait_for_capacity(30)
        limiter._wait_for_capacity(30)
        self.assertEqual(clock.sleeps, [18])

    def test_wraps_direct_and_structured_calls(self):
        """Verify the proxy limits both direct and structured model invocations.

        Args:
            None.
        Returns:
            None; assertions check both wrapped responses and API attempt count.
        Called by: the unittest test runner during test discovery.
        """
        class FakeRunnable:
            def invoke(self, value):
                """Return a marker response for a structured model invocation.

                Args:
                    value: Input passed through the structured-output proxy.
                Returns:
                    A formatted marker string.
                Called by: ``_RateLimitedRunnable.invoke`` through the limiter.
                """
                return f"structured:{value}"

        class FakeModel:
            def invoke(self, value):
                """Return a marker response for a direct model invocation.

                Args:
                    value: Input passed through the model proxy.
                Returns:
                    A formatted marker string.
                Called by: ``RateLimitedChatModel.invoke`` through the limiter.
                """
                return f"plain:{value}"

            def with_structured_output(self, schema):
                """Return the fake structured runnable for any requested schema.

                Args:
                    schema: Structured-output schema requested by the proxy.
                Returns:
                    A ``FakeRunnable`` instance.
                Called by: ``RateLimitedChatModel.with_structured_output``.
                """
                return FakeRunnable()

        limiter = GroqRateLimiter(
            output_reserve=0,
            sleep=lambda _: None,
            jitter=lambda: 0,
        )
        model = RateLimitedChatModel(FakeModel(), limiter)
        self.assertEqual(model.invoke("q"), "plain:q")
        self.assertEqual(model.with_structured_output(dict).invoke("q"), "structured:q")
        self.assertEqual(limiter.api_attempts, 2)

    def test_does_not_retry_non_rate_limit_errors(self):
        """Verify ordinary model errors propagate without retry or sleep.

        Args:
            None.
        Returns:
            None; assertions inspect the exception and retry-related counters.
        Called by: the unittest test runner during test discovery.
        """
        sleeps = []
        limiter = GroqRateLimiter(
            output_reserve=0,
            max_retries=5,
            sleep=sleeps.append,
            jitter=lambda: 0,
        )

        def request():
            """Raise a non-rate-limit error for the no-retry assertion.

            Args:
                None.
            Returns:
                No value; always raises the simulated API-key error.
            Called by: ``GroqRateLimiter.invoke`` for the test request.
            """
            raise RuntimeError("invalid API key")

        with self.assertRaisesRegex(RuntimeError, "invalid API key"):
            limiter.invoke(request, "prompt")
        self.assertEqual(limiter.api_attempts, 1)
        self.assertEqual(limiter.rate_limit_retries, 0)
        self.assertEqual(sleeps, [])


if __name__ == "__main__":
    unittest.main()
