"""Optional, schema-validated model calls for simulated data."""
from __future__ import annotations

import base64
import importlib.util
import ipaddress
import json
import logging
import os
from pathlib import Path
from time import perf_counter
from typing import Callable, Iterable
from urllib.parse import urlsplit


logger = logging.getLogger("ghosttrace.llm")


class ModelAnswerError(Exception):
    """Model configuration, request or answer could not be used."""


def is_configured() -> bool:
    return bool(os.environ.get("MODEL_BASE_URL"))


def is_foundry_agent_enabled() -> bool:
    enabled = os.environ.get("FOUNDRY_AGENT_ENABLED", "").lower() in {"1", "true", "yes"}
    if not enabled or not os.environ.get("FOUNDRY_PROJECT_ENDPOINT"):
        return False
    try:
        return all(importlib.util.find_spec(name) for name in (
            "azure.ai.projects", "azure.identity", "jsonschema",
        ))
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def prompt_agent_name() -> str:
    return os.environ.get("PROMPT_AGENT_NAME", "ghosttrace-explainer-prompt")


def _is_local(base_url: str) -> bool:
    url = urlsplit(base_url)
    if url.scheme not in ("http", "https") or not url.hostname:
        return False
    if url.hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(url.hostname).is_loopback
    except ValueError:
        return False


def _image_url(image: str | os.PathLike[str] | bytes) -> str:
    data = image if isinstance(image, bytes) else Path(image).read_bytes()
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif data.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif data.startswith((b"GIF87a", b"GIF89a")):
        mime = "image/gif"
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        raise ModelAnswerError("Images must be PNG, JPEG, GIF or WebP.")
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _reject_constant(value: str) -> None:
    raise ValueError("Non-standard JSON constant.")


def complete_json(
    system: str,
    user: str,
    schema_path: str | os.PathLike[str],
    images: Iterable[str | os.PathLike[str] | bytes] | None = None,
) -> dict:
    """Return one validated JSON object, retrying an invalid answer once."""
    if not is_configured():
        raise ModelAnswerError("MODEL_BASE_URL is not configured.")
    base_url = os.environ["MODEL_BASE_URL"]
    model = os.environ.get("MODEL_NAME")
    if not model:
        raise ModelAnswerError("MODEL_NAME is required when MODEL_BASE_URL is set.")
    api_key: str | Callable[[], str] | None = os.environ.get("MODEL_API_KEY")
    if api_key and not _is_local(base_url):
        raise ModelAnswerError("MODEL_API_KEY is allowed only for loopback endpoints.")

    try:
        import openai
        from jsonschema import Draft202012Validator
        from openai.types.chat import ChatCompletionContentPartParam, ChatCompletionMessageParam
    except ImportError:
        raise ModelAnswerError("Install requirements-foundry.txt to call a model.") from None

    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": (
            f"{system}\nReturn exactly one JSON object, with no Markdown, matching "
            f"this JSON schema:\n{json.dumps(schema)}"
        )},
        {"role": "user", "content": user},
    ]
    if images is not None:
        parts: list[ChatCompletionContentPartParam] = [
            {"type": "text", "text": user},
        ]
        for image in images:
            parts.append({"type": "image_url", "image_url": {"url": _image_url(image)}})
        messages[1] = {"role": "user", "content": parts}

    credential = None
    if not api_key:
        try:
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider
        except ImportError:
            raise ModelAnswerError("Install requirements-foundry.txt for keyless sign-in.") from None
        credential = DefaultAzureCredential()
        # Microsoft Learn: Configure keyless authentication with Microsoft Entra ID.
        # https://learn.microsoft.com/azure/foundry/foundry-models/how-to/configure-entra-id
        api_key = get_bearer_token_provider(
            credential,
            os.environ.get("MODEL_TOKEN_SCOPE", "https://ai.azure.com/.default"),
        )

    try:
        # SDK retries cover only rate limits, timeouts and 5xx; an invalid answer gets exactly one extra call below.
        with openai.OpenAI(base_url=base_url, api_key=api_key, max_retries=3, timeout=120) as client:
            for attempt in (1, 2):
                started = perf_counter()
                usage = None
                ok = False
                try:
                    response = client.chat.completions.create(
                        model=model,
                        messages=messages,
                        response_format={"type": "json_object"},
                    )
                    usage = response.usage
                    content = response.choices[0].message.content if response.choices else None
                    try:
                        answer = json.loads(content or "", parse_constant=_reject_constant)
                    except ValueError:
                        error = "Return valid JSON, not Markdown or non-standard constants."
                    else:
                        if not isinstance(answer, dict):
                            error = "Return one JSON object."
                        else:
                            invalid = next(validator.iter_errors(answer), None)
                            if invalid is None:
                                ok = True
                                return answer
                            # Do not echo model-produced values into diagnostics.
                            error = f"Match the supplied JSON schema ({invalid.validator} rule)."
                    if attempt == 1:
                        if content:
                            messages.append({"role": "assistant", "content": content})
                        messages.append({"role": "user", "content": error})
                except openai.OpenAIError:
                    raise ModelAnswerError("Model request failed; check endpoint and authentication.") from None
                finally:
                    logger.info(
                        "deployment=%s attempt=%s latency_ms=%.1f prompt_tokens=%s "
                        "completion_tokens=%s total_tokens=%s status=%s",
                        model, attempt, (perf_counter() - started) * 1000,
                        getattr(usage, "prompt_tokens", None),
                        getattr(usage, "completion_tokens", None),
                        getattr(usage, "total_tokens", None),
                        "ok" if ok else "failed",
                    )
    finally:
        if credential is not None:
            credential.close()

    logger.error("deployment=%s status=failed reason=invalid_answer_after_retry", model)
    raise ModelAnswerError("Model returned invalid JSON or a schema mismatch twice.")


def complete_prompt_agent_json(case: dict, schema_path: str | os.PathLike[str]) -> dict:
    """Invoke the configured Foundry prompt agent and validate its JSON response."""
    if not is_foundry_agent_enabled():
        raise ModelAnswerError("Foundry prompt agent is not enabled or its optional dependencies are missing.")

    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential
        from jsonschema import Draft202012Validator
    except ImportError:
        raise ModelAnswerError("Install requirements-foundry.txt to call the Foundry prompt agent.") from None

    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    agent_name = prompt_agent_name()
    credential = DefaultAzureCredential()
    project = None
    client = None
    try:
        project = AIProjectClient(endpoint=endpoint, credential=credential)
        client = project.get_openai_client()
        for attempt in (1, 2):
            started = perf_counter()
            ok = False
            usage = None
            try:
                response = client.responses.create(
                    input=json.dumps(case, ensure_ascii=False),
                    extra_body={"agent_reference": {"name": agent_name, "type": "agent_reference"}},
                )
                usage = getattr(response, "usage", None)
                answer = json.loads(response.output_text or "", parse_constant=_reject_constant)
                invalid = next(validator.iter_errors(answer), None) if isinstance(answer, dict) else True
                if invalid is None:
                    ok = True
                    return answer
            except (TypeError, ValueError):
                pass
            except Exception as exc:
                logger.warning("prompt_agent=%s request_error=%s", agent_name, type(exc).__name__)
                raise ModelAnswerError("Foundry prompt agent request failed; check endpoint and authentication.") from None
            finally:
                logger.info("deployment=%s attempt=%s latency_ms=%.1f input_tokens=%s "
                            "output_tokens=%s total_tokens=%s status=%s",
                            agent_name, attempt, (perf_counter() - started) * 1000,
                            getattr(usage, "input_tokens", None),
                            getattr(usage, "output_tokens", None),
                            getattr(usage, "total_tokens", None),
                            "ok" if ok else "failed")
    finally:
        for resource in (client, project, credential):
            close = getattr(resource, "close", None)
            if close:
                close()

    logger.error("prompt_agent=%s status=failed reason=invalid_answer_after_retry", agent_name)
    raise ModelAnswerError("Foundry prompt agent returned invalid JSON or a schema mismatch twice.")
