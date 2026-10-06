"""
One LLM interface, two providers (Claude + Gemini).

Usage anywhere in the project:

    llm = get_llm()                       # provider from .env (LLM_PROVIDER)
    llm = get_llm("gemini")               # or force one
    candidate = llm.extract(Candidate, system_prompt, text="...markdown...")
    candidate = llm.extract(Candidate, system_prompt, file=Path("scan.pdf"))   # vision

`extract()` always returns a validated Pydantic object of the class you pass.
Instructor does the heavy lifting: turns the Pydantic class into a tool/JSON
schema, sends it, parses the reply, and re-asks the LLM (max_retries) if the
reply fails validation.

The later "JD fit" step will reuse exactly this, with a JobFit model.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Type, TypeVar

import instructor
from pydantic import BaseModel

from app.core.config import IMAGE_EXTENSIONS, settings
from app.core.logger import get_logger

try:  # instructor >= 1.10
    from instructor.processing.multimodal import PDF, Image
except ImportError:  # older instructor
    from instructor.multimodal import PDF, Image  # type: ignore

log = get_logger("llm")
T = TypeVar("T", bound=BaseModel)

SUPPORTED = ("claude", "gemini")


class LLM:
    def __init__(self, provider: str):
        provider = provider.lower()
        if provider not in SUPPORTED:
            raise ValueError(f"Unknown LLM provider '{provider}'. Use one of {SUPPORTED}")
        self.provider = provider

        if provider == "claude":
            if not settings.anthropic_api_key:
                raise RuntimeError("ANTHROPIC_API_KEY missing in .env")
            from anthropic import Anthropic
            self.model = settings.claude_model
            self.client = instructor.from_anthropic(
                Anthropic(api_key=settings.anthropic_api_key), mode=instructor.Mode.TOOLS
            )
        else:
            if not settings.gemini_api_key:
                raise RuntimeError("GEMINI_API_KEY missing in .env")
            from google import genai
            self.model = settings.gemini_model
            self.client = instructor.from_genai(
                genai.Client(api_key=settings.gemini_api_key), mode=instructor.Mode.JSON
            )

    def extract(
        self,
        response_model: Type[T],
        system_prompt: str,
        text: Optional[str] = None,
        file: Optional[Path] = None,
        max_retries: int = 2,
    ) -> T:
        """Send text and/or a file (PDF/image, for vision) and get back `response_model`."""
        content: list = []
        if text:
            content.append(text)
        if file is not None:
            ext = file.suffix.lower()
            if ext == ".pdf":
                content.append(PDF.from_path(str(file)))
            elif ext in IMAGE_EXTENSIONS:
                content.append(Image.from_path(str(file)))
            else:
                raise ValueError(f"Vision input not supported for {ext}")
            content.append("(The candidate's CV/resume is the attached document.)")
        if not content:
            raise ValueError("Nothing to send: give text and/or file")

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content if len(content) > 1 or file else content[0]},
        ]

        kwargs = dict(model=self.model, messages=messages, response_model=response_model, max_retries=max_retries)
        if self.provider == "claude":
            kwargs["max_tokens"] = 4096
        return self.client.chat.completions.create(**kwargs)


_cache: dict[str, LLM] = {}


def get_llm(provider: Optional[str] = None) -> LLM:
    """Returns a cached LLM client for the given provider (default: LLM_PROVIDER in .env)."""
    provider = (provider or settings.llm_provider).lower()
    if provider not in _cache:
        _cache[provider] = LLM(provider)
        log.info("LLM ready: %s (%s)", provider, _cache[provider].model)
    return _cache[provider]
