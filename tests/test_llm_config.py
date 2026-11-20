"""Provider selection tests that do not make real API requests."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from llm_config import create_chat_model


class LlmConfigTests(unittest.TestCase):
    """Verify that the provider flag selects the matching key and model."""

    def test_selects_groq_and_its_credentials(self):
        """Verify selects groq and its credentials.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        environment = {
            "LLM_PROVIDER": "groq",
            "GROQ_API_KEY": "groq-test-key",
            "GROQ_MODEL": "groq-test-model",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch("llm_config.load_dotenv"),
            patch("llm_config.ChatGroq") as groq_factory,
            patch("llm_config.ChatGoogleGenerativeAI") as gemini_factory,
        ):
            create_chat_model(request_timeout=45)

        groq_factory.assert_called_once_with(
            model="groq-test-model",
            api_key="groq-test-key",
            temperature=0.1,
            timeout=45,
            max_retries=0,
        )
        gemini_factory.assert_not_called()

    def test_selects_gemini_and_its_credentials(self):
        """Verify selects gemini and its credentials.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        environment = {
            "LLM_PROVIDER": " GEMINI ",
            "GOOGLE_API_KEY": "gemini-test-key",
            "GEMINI_MODEL": "gemini-test-model",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch("llm_config.load_dotenv"),
            patch("llm_config.ChatGoogleGenerativeAI") as gemini_factory,
            patch("llm_config.ChatGroq") as groq_factory,
        ):
            create_chat_model(request_timeout=30)

        gemini_factory.assert_called_once_with(
            model="gemini-test-model",
            api_key="gemini-test-key",
            temperature=0.1,
            request_timeout=30,
            retries=0,
        )
        groq_factory.assert_not_called()

    def test_missing_selected_provider_key_fails_without_fallback(self):
        """Verify missing selected provider key fails without fallback.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        environment = {"LLM_PROVIDER": "groq", "GROQ_MODEL": "groq-test-model"}
        with (
            patch.dict(os.environ, environment, clear=True),
            patch("llm_config.load_dotenv"),
            patch("llm_config.ChatGoogleGenerativeAI") as gemini_factory,
        ):
            with self.assertRaisesRegex(ValueError, "GROQ_API_KEY is missing"):
                create_chat_model()

        gemini_factory.assert_not_called()

    def test_unknown_provider_is_rejected(self):
        """Verify unknown provider is rejected.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        with (
            patch.dict(os.environ, {"LLM_PROVIDER": "other"}, clear=True),
            patch("llm_config.load_dotenv"),
        ):
            with self.assertRaisesRegex(ValueError, "gemini.*groq"):
                create_chat_model()


if __name__ == "__main__":
    unittest.main()
