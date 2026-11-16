"""Small direct chat-provider check, independent of LangGraph and ChromaDB."""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv
from langchain_core.language_models.chat_models import BaseChatModel

from config.llm import REQUEST_TIMEOUT_SECONDS
from llm_config import create_chat_model


def create_selected_model() -> BaseChatModel:
    """Create the provider selected by LLM_PROVIDER in the local .env file."""
    return create_chat_model(request_timeout=REQUEST_TIMEOUT_SECONDS)


def selected_model_description() -> str:
    """Return the configured provider and model name, never an API key."""
    load_dotenv()
    provider = os.getenv("LLM_PROVIDER", "").strip().casefold()
    model_variable = {
        "gemini": "GEMINI_MODEL",
        "groq": "GROQ_MODEL",
    }.get(provider)
    if model_variable is None:
        raise ValueError("LLM_PROVIDER must be set to either 'gemini' or 'groq'.")
    model_name = os.getenv(model_variable)
    if not model_name:
        raise ValueError(f"{model_variable} is missing from .env.")
    return f"{provider} / {model_name}"


def extract_text(response: Any) -> str:
    """Get text from a LangChain chat response, including text content blocks."""
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            block["text"]
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        ]
        return "\n".join(part.strip() for part in parts if part.strip())
    return ""


def ask_model(model: Any, question: str) -> str:
    """Send only the question to the selected provider and return its response."""
    response = model.invoke(question)
    answer = extract_text(response)
    if not answer:
        raise ValueError("The selected model returned no text. Check its API response.")
    return answer


def main() -> int:
    """Prompt for one question and report whether the selected provider answered."""
    question = input("Ask the selected model a question: ").strip()
    if not question:
        print("Question cannot be empty.")
        return 1

    try:
        model = create_selected_model()
        model_description = selected_model_description()
    except ValueError as exc:
        print(f"Configuration error: {exc}")
        return 1

    print(f"Testing {model_description} (timeout: {REQUEST_TIMEOUT_SECONDS}s)...")
    try:
        answer = ask_model(model, question)
    except Exception as exc:
        print(f"Model request failed: {exc}")
        return 1

    print("\nThe selected model responded successfully:\n")
    print(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
