"""Shared configuration for selecting Gemini or Groq chat models."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq

from config.llm import MAX_RETRIES, REQUEST_TIMEOUT_SECONDS, TEMPERATURE


def create_chat_model(
    request_timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> BaseChatModel:
    """Create the provider selected by LLM_PROVIDER using its own API key/model.

    Required .env settings:
    - LLM_PROVIDER: "gemini" or "groq"
    - GOOGLE_API_KEY and GEMINI_MODEL when using Gemini
    - GROQ_API_KEY and GROQ_MODEL when using Groq

    This function never switches providers automatically.
    """
    load_dotenv()
    provider = os.getenv("LLM_PROVIDER", "").strip().casefold()

    if provider == "gemini":
        api_key = os.getenv("GOOGLE_API_KEY")
        model_name = os.getenv("GEMINI_MODEL")
        if not api_key:
            raise ValueError("LLM_PROVIDER is gemini, but GOOGLE_API_KEY is missing.")
        if not model_name:
            raise ValueError("LLM_PROVIDER is gemini, but GEMINI_MODEL is missing.")
        return ChatGoogleGenerativeAI(
            model=model_name,
            api_key=api_key,
            temperature=TEMPERATURE,
            request_timeout=request_timeout,
            retries=MAX_RETRIES,
        )

    if provider == "groq":
        api_key = os.getenv("GROQ_API_KEY")
        model_name = os.getenv("GROQ_MODEL")
        if not api_key:
            raise ValueError("LLM_PROVIDER is groq, but GROQ_API_KEY is missing.")
        if not model_name:
            raise ValueError("LLM_PROVIDER is groq, but GROQ_MODEL is missing.")
        return ChatGroq(
            model=model_name,
            api_key=api_key,
            temperature=TEMPERATURE,
            timeout=request_timeout,
            max_retries=MAX_RETRIES,
        )

    raise ValueError(
        "LLM_PROVIDER must be set to either 'gemini' or 'groq' in .env."
    )
