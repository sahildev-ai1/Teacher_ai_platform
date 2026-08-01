"""
Thin, provider-agnostic LLM layer.

- chat() / chat_json(): call Ollama (local daemon if OLLAMA_HOST is set, else
  Ollama Cloud) with retry + basic latency/error logging (bonus: observability).
- pedagogy_search(): OPTIONAL Tavily web search used ONLY to enrich teaching
  strategy / analogy / activity ideas in Stages 4-6. Per the client's grounding
  rule, secondary sources may improve pedagogy but must never introduce new
  facts/concepts -- so this is never used in Stage 2/3 (classification/
  knowledge extraction) and its output is always tagged as a "secondary idea"
  in prompts, never merged into the knowledge base. No-op if TAVILY_API_KEY
  is not set.
"""
import json
import logging
import time
from typing import Optional, List, Type, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from .config import settings

logger = logging.getLogger("teacher_ai.llm")
logging.basicConfig(level=logging.INFO)

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


def _endpoint() -> str:
    if settings.ollama_host:
        return f"{settings.ollama_host.rstrip('/')}/api/chat"
    return settings.ollama_cloud_url


def _headers() -> dict:
    headers = {"Content-Type": "application/json"}
    if not settings.ollama_host and settings.ollama_api_key:
        headers["Authorization"] = f"Bearer {settings.ollama_api_key}"
    return headers


def chat(
    prompt: str,
    system: Optional[str] = None,
    model: Optional[str] = None,
    json_mode: bool = False,
    max_retries: int = 3,
    timeout: float = 120.0,
) -> str:
    """Synchronous chat call with exponential-backoff retries. Returns raw text
    content. If json_mode is True, asks the model to respond with JSON only and
    strips markdown code fences before returning."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model or settings.ollama_model,
        "messages": messages,
        "stream": False,
    }
    if json_mode:
        payload["format"] = "json"

    last_err = None
    for attempt in range(1, max_retries + 1):
        start = time.time()
        try:
            resp = httpx.post(_endpoint(), json=payload, headers=_headers(), timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            content = data.get("message", {}).get("content", "")
            logger.info(
                "llm_call model=%s attempt=%d latency=%.2fs chars=%d",
                payload["model"], attempt, time.time() - start, len(content),
            )
            if json_mode:
                content = _strip_code_fence(content)
            return content
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            logger.warning("llm_call_failed attempt=%d error=%s", attempt, exc)
            time.sleep(min(2 ** attempt, 8))

    raise LLMError(f"LLM call failed after {max_retries} attempts: {last_err}")


def chat_json(prompt: str, system: Optional[str] = None, model: Optional[str] = None) -> dict:
    """Calls chat() in JSON mode and parses the result, with one repair retry
    if the model returns malformed JSON."""
    raw = chat(prompt, system=system, model=model, json_mode=True)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        repair_prompt = (
            "The following text was supposed to be valid JSON but failed to parse. "
            "Return ONLY corrected, valid JSON with the same information, no prose, "
            f"no markdown fences:\n\n{raw}"
        )
        raw2 = chat(repair_prompt, model=model, json_mode=True)
        return json.loads(raw2)


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def chat_structured(
    prompt: str,
    schema: Type[T],
    system: Optional[str] = None,
    model: Optional[str] = None,
    max_repair: int = 2,
) -> T:
    """chat_json() + pydantic validation, with up to `max_repair` extra calls
    that show the model its own validation errors and ask it to fix them.

    This matters more than it might look: an open ~30B model producing deeply
    nested JSON (5-8 keys, some nested lists of objects) WILL occasionally
    drop a required field or use the wrong type. Without this loop that
    surfaces as an unhandled 500 all the way up to the teacher's browser.
    """
    data = chat_json(prompt, system=system, model=model)
    last_err: Optional[ValidationError] = None
    for attempt in range(max_repair + 1):
        try:
            return schema.model_validate(data)
        except ValidationError as exc:
            last_err = exc
            logger.warning("schema_validation_failed schema=%s attempt=%d", schema.__name__, attempt)
            if attempt == max_repair:
                break
            repair_prompt = (
                "Your previous JSON response did not match the required schema.\n\n"
                f"Validation errors:\n{exc}\n\n"
                f"Required JSON schema:\n{json.dumps(schema.model_json_schema())}\n\n"
                f"Your previous response:\n{json.dumps(data, ensure_ascii=False)}\n\n"
                "Return ONLY corrected JSON that satisfies the schema exactly -- "
                "keep all the same content, just fix the structure/types/missing fields."
            )
            data = chat_json(repair_prompt, model=model)

    raise LLMError(f"Model output did not match schema {schema.__name__} after {max_repair} repair attempts: {last_err}")


def pedagogy_search(query: str, max_results: int = 3) -> List[str]:
    """Returns short snippets of teaching-strategy/analogy ideas from the web,
    or [] if Tavily isn't configured / the call fails. Callers must treat these
    as inspiration for HOW to teach, never as source material for WHAT is taught."""
    if not settings.tavily_api_key:
        return []
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=settings.tavily_api_key)
        result = client.search(query=f"teaching strategy analogy activity idea: {query}", max_results=max_results)
        return [r.get("content", "")[:400] for r in result.get("results", [])]
    except Exception as exc:  # noqa: BLE001
        logger.warning("tavily_search_failed error=%s", exc)
        return []
