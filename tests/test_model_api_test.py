"""Tests for the selected-provider API check without network requests."""

from __future__ import annotations

import unittest

from scripts.model_api_test import ask_model, extract_text, selected_model_description


class FakeModel:
    """Capture the sent question and return a fake LangChain chat response."""

    def __init__(self) -> None:
        self.question = ""

    def invoke(self, question: str):
        self.question = question

        class Response:
            content = [{"type": "text", "text": "Test model response."}]

        return Response()


class SelectedModelApiTestTests(unittest.TestCase):
    """Check that the test app sends the question and extracts model text."""

    def test_ask_model_sends_question_and_extracts_response(self):
        model = FakeModel()

        answer = ask_model(model, "What is 2 + 2?")

        self.assertEqual(model.question, "What is 2 + 2?")
        self.assertEqual(answer, "Test model response.")

    def test_empty_response_is_an_explicit_failure(self):
        class EmptyModel:
            def invoke(self, question: str):
                return type("Response", (), {"content": []})()

        with self.assertRaisesRegex(ValueError, "selected model returned no text"):
            ask_model(EmptyModel(), "Hello")

    def test_extract_text_handles_plain_text(self):
        self.assertEqual(extract_text(" Answer "), "Answer")

    def test_selected_model_description_uses_active_provider_without_key(self):
        from unittest.mock import patch

        with patch("scripts.model_api_test.load_dotenv"), patch.dict(
            "os.environ",
            {"LLM_PROVIDER": "groq", "GROQ_MODEL": "test-groq-model"},
            clear=True,
        ):
            self.assertEqual(
                selected_model_description(),
                "groq / test-groq-model",
            )


if __name__ == "__main__":
    unittest.main()
