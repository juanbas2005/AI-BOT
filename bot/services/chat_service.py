"""Lógica de conversación: memoria por usuario, streaming y tareas puntuales."""

from __future__ import annotations

import logging
from typing import AsyncIterator

from bot.config import Config
from bot.database.db import Database, UserSettings
from bot.services.inception_client import InceptionClient, StreamUpdate, Usage

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Eres un asistente experto, claro y directo. Respondes en el idioma del usuario "
    "(por defecto, español) y adaptas la extensión a lo que se te pide. "
    "El texto se muestra en Telegram, que no renderiza Markdown complejo: usa párrafos, "
    "listas con guiones y bloques de código con triple acento grave; evita tablas y HTML."
)

SUMMARY_SYSTEM = (
    "Resume el texto del usuario de forma concisa y fiel. Devuelve solo el resumen, "
    "sin comentarios ni introducciones, en el idioma original del texto."
)

ANALYSIS_SYSTEM = (
    "Analiza el texto del usuario y responde con esta estructura, en el idioma del texto:\n"
    "1. Puntos clave (3-6 viñetas).\n"
    "2. Sentimiento (positivo, negativo o neutro) con una frase de justificación.\n"
    "3. Entidades relevantes (personas, organizaciones, lugares, fechas, cifras).\n"
    "Sé breve y no inventes datos que no aparezcan en el texto."
)

TRANSLATE_SYSTEM = (
    "Traduce el texto del usuario al idioma que se indique. Devuelve únicamente la "
    "traducción, sin explicaciones. Conserva el tono, el formato y los nombres propios."
)


class ChatService:
    def __init__(self, client: InceptionClient, db: Database, config: Config) -> None:
        self._client = client
        self._db = db
        self._config = config

    async def settings(self, user_id: int, username: str | None = None) -> UserSettings:
        """Preferencias del usuario (modelo y esfuerzo), creándolo si es nuevo."""
        return await self._db.get_or_create_user(
            user_id,
            username,
            default_model=self._config.default_model,
            default_effort=self._config.default_reasoning_effort,
        )

    async def stream_reply(
        self, user_id: int, text: str, username: str | None = None
    ) -> AsyncIterator[StreamUpdate]:
        """Conversa con memoria. Emite fragmentos y, al final, el consumo."""
        settings = await self.settings(user_id, username)
        history = await self._db.get_history(user_id, self._config.history_limit)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *history,
            {"role": "user", "content": text},
        ]

        chunks: list[str] = []
        usage: Usage | None = None
        async for update in self._client.stream(
            messages,
            model=settings.model,
            reasoning_effort=settings.reasoning_effort,
            temperature=self._config.temperature,
            max_tokens=self._config.max_tokens,
        ):
            if update.usage is not None:
                usage = update.usage
                continue
            if update.text:
                chunks.append(update.text)
            yield update

        answer = "".join(chunks).strip()
        if not answer:
            return

        # Solo se guarda el turno si la respuesta llegó completa.
        await self._db.add_message(user_id, "user", text)
        await self._db.add_message(user_id, "assistant", answer)
        if usage is not None:
            await self._db.add_usage(
                user_id, settings.model, usage.prompt_tokens, usage.completion_tokens
            )

    async def complete(
        self,
        user_id: int,
        *,
        system: str,
        user_text: str,
        username: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Petición puntual (resumen, análisis, traducción) sin tocar el historial."""
        settings = await self.settings(user_id, username)
        answer, usage = await self._client.complete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user_text},
            ],
            model=settings.model,
            reasoning_effort=settings.reasoning_effort,
            temperature=self._config.temperature if temperature is None else temperature,
            max_tokens=max_tokens or self._config.max_tokens,
        )
        await self._db.add_usage(
            user_id, settings.model, usage.prompt_tokens, usage.completion_tokens
        )
        return answer.strip()

    async def summarize(self, user_id: int, text: str, username: str | None = None) -> str:
        return await self.complete(user_id, system=SUMMARY_SYSTEM, user_text=text, username=username)

    async def analyze(self, user_id: int, text: str, username: str | None = None) -> str:
        return await self.complete(user_id, system=ANALYSIS_SYSTEM, user_text=text, username=username)

    async def translate(
        self, user_id: int, target_language: str, text: str, username: str | None = None
    ) -> str:
        prompt = f"Idioma destino: {target_language}\n\nTexto:\n{text}"
        return await self.complete(
            user_id,
            system=TRANSLATE_SYSTEM,
            user_text=prompt,
            username=username,
            temperature=0.6,  # menos variabilidad al traducir
        )
