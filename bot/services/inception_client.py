"""Cliente de la API de InceptionLabs (modelos Mercury).

La API es compatible con OpenAI, así que el chat usa el SDK oficial `openai`
apuntando a `INCEPTION_BASE_URL`. Los endpoints de FIM y Next Edit no existen en
el SDK, así que se llaman directamente con `httpx`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator, Mapping, Sequence

import httpx
from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    RateLimitError,
)

log = logging.getLogger(__name__)

FIM_MODEL = "mercury-edit-2"
EDIT_MODEL = "mercury-edit-2"


class InceptionError(RuntimeError):
    """Error controlado al hablar con la API, con un mensaje apto para el usuario."""


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass(frozen=True)
class StreamUpdate:
    """Fragmento de una respuesta en streaming.

    En modo normal `text` es un delta (hay que concatenarlo). Con `diffusing`
    activo la API envía el texto completo reescrito en cada bloque y
    `snapshot` es True: hay que reemplazar lo mostrado, no añadirlo.
    """

    text: str = ""
    snapshot: bool = False
    usage: Usage | None = None


def _usage(value: Any) -> Usage:
    """Normaliza el objeto `usage` de la API (con o sin `*_details`)."""
    if value is None:
        return Usage()

    details = getattr(value, "completion_tokens_details", None)
    reasoning = getattr(details, "reasoning_tokens", 0) or 0
    return Usage(
        prompt_tokens=getattr(value, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(value, "completion_tokens", 0) or 0,
        total_tokens=getattr(value, "total_tokens", 0) or 0,
        reasoning_tokens=reasoning,
    )


class InceptionClient:
    """Envoltorio fino sobre la API: traduce errores y expone métodos simples."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout: float,
        diffusing: bool = False,
        max_retries: int = 2,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._diffusing = diffusing
        self._chat = AsyncOpenAI(
            api_key=api_key,
            base_url=self._base_url,
            timeout=timeout,
            max_retries=max_retries,
        )
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def aclose(self) -> None:
        await self._chat.close()
        await self._http.aclose()

    # ------------------------------------------------------------------ chat

    def _chat_kwargs(
        self,
        *,
        model: str,
        reasoning_effort: str | None,
        temperature: float | None,
        max_tokens: int | None,
        response_format: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Parámetros comunes; omite los None para no enviar nulls a la API."""
        kwargs: dict[str, Any] = {"model": model}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_completion_tokens"] = max_tokens
        if response_format is not None:
            kwargs["response_format"] = dict(response_format)

        # `reasoning_effort` y `diffusing` son extensiones de Inception: van por
        # extra_body para que funcionen con cualquier versión del SDK.
        extra: dict[str, Any] = {}
        if reasoning_effort:
            extra["reasoning_effort"] = reasoning_effort
        if self._diffusing:
            extra["diffusing"] = True
        if extra:
            kwargs["extra_body"] = extra
        return kwargs

    async def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        model: str,
        reasoning_effort: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: Mapping[str, Any] | None = None,
    ) -> tuple[str, Usage]:
        """Petición de chat sin streaming. Devuelve (texto, consumo)."""
        kwargs = self._chat_kwargs(
            model=model,
            reasoning_effort=reasoning_effort,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )
        try:
            completion = await self._chat.chat.completions.create(
                messages=list(messages), **kwargs
            )
        except AuthenticationError as exc:
            raise InceptionError(
                "La API key de Inception no es válida. Revisa INCEPTION_API_KEY."
            ) from exc
        except RateLimitError as exc:
            raise InceptionError(
                "La API de Inception alcanzó su límite de peticiones. Espera un momento."
            ) from exc
        except (APITimeoutError, APIConnectionError) as exc:
            raise InceptionError("No se pudo conectar con la API de Inception.") from exc
        except APIError as exc:
            raise InceptionError(f"La API devolvió un error: {exc.message}") from exc

        text = (completion.choices[0].message.content or "") if completion.choices else ""
        return text, _usage(completion.usage)

    async def stream(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        model: str,
        reasoning_effort: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamUpdate]:
        """Chat en streaming. Emite deltas y, al final, el consumo de tokens."""
        kwargs = self._chat_kwargs(
            model=model,
            reasoning_effort=reasoning_effort,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=None,
        )
        try:
            stream = await self._chat.chat.completions.create(
                messages=list(messages),
                stream=True,
                stream_options={"include_usage": True},
                **kwargs,
            )
        except AuthenticationError as exc:
            raise InceptionError(
                "La API key de Inception no es válida. Revisa INCEPTION_API_KEY."
            ) from exc
        except RateLimitError as exc:
            raise InceptionError(
                "La API de Inception alcanzó su límite de peticiones. Espera un momento."
            ) from exc
        except (APITimeoutError, APIConnectionError) as exc:
            raise InceptionError("No se pudo conectar con la API de Inception.") from exc
        except APIError as exc:
            raise InceptionError(f"La API devolvió un error: {exc.message}") from exc

        usage: Usage | None = None
        try:
            async for chunk in stream:
                if getattr(chunk, "usage", None):
                    usage = _usage(chunk.usage)
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                content = getattr(delta, "content", None) if delta else None
                if content:
                    yield StreamUpdate(text=content, snapshot=self._diffusing)
        except APIError as exc:
            raise InceptionError(f"La API cortó el streaming: {exc.message}") from exc

        if usage is not None:
            yield StreamUpdate(usage=usage)

    # --------------------------------------------------------------- código

    async def fim(
        self,
        prompt: str,
        *,
        suffix: str = "",
        max_tokens: int = 1000,
        model: str = FIM_MODEL,
    ) -> str:
        """Autocompletado inline: texto que encaja entre `prompt` y `suffix`."""
        payload: dict[str, Any] = {"model": model, "prompt": prompt, "max_tokens": max_tokens}
        if suffix:
            payload["suffix"] = suffix
        data = await self._post("/fim/completions", payload)
        try:
            return data["choices"][0]["text"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise InceptionError("El endpoint FIM devolvió una respuesta inesperada.") from exc

    async def next_edit(self, content: str, *, max_tokens: int = 1200, model: str = EDIT_MODEL) -> str:
        """Next Edit: reescribe una región de código a partir de su contexto."""
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens,
        }
        data = await self._post("/edit/completions", payload)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise InceptionError("El endpoint de edición devolvió una respuesta inesperada.") from exc

    # ------------------------------------------------------------- interno

    async def _post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            response = await self._http.post(path, json=dict(payload))
        except httpx.TimeoutException as exc:
            raise InceptionError("La API tardó demasiado en responder.") from exc
        except httpx.HTTPError as exc:
            raise InceptionError("No se pudo conectar con la API de Inception.") from exc

        if response.status_code >= 400:
            raise InceptionError(self._error_message(response))
        try:
            return response.json()
        except ValueError as exc:
            raise InceptionError("La API devolvió una respuesta que no es JSON.") from exc

    def _error_message(self, response: httpx.Response) -> str:
        """Extrae el mensaje de error de la API con un fallback por código HTTP."""
        detail = ""
        try:
            body = response.json()
            detail = str(body.get("error", {}).get("message") or "")
        except (ValueError, AttributeError):
            detail = ""

        if response.status_code == 401:
            return "La API key de Inception no es válida. Revisa INCEPTION_API_KEY."
        if response.status_code == 402:
            return "La cuenta de Inception no tiene saldo o está inactiva."
        if response.status_code == 404:
            return detail or "El modelo solicitado no está disponible en esta cuenta."
        if response.status_code == 429:
            return "La API de Inception alcanzó su límite de peticiones. Espera un momento."
        return detail or f"La API respondió con HTTP {response.status_code}."
