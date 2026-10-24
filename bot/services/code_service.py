"""Generación y edición de código con los modelos Mercury."""

from __future__ import annotations

import logging

from bot.config import Config
from bot.database.db import Database
from bot.services.inception_client import InceptionClient

log = logging.getLogger(__name__)

CODE_SYSTEM = (
    "Eres un ingeniero de software senior. Escribes código correcto, idiomático y "
    "conciso. Devuelve únicamente el código solicitado, sin explicaciones y sin "
    "envolverlo en bloques markdown."
)

# Marcadores del protocolo de Next Edit (documentados por Inception).
CURSOR_TAG = "<|cursor|>"


def build_next_edit_prompt(code: str, instruction: str, *, path: str = "snippet.py") -> str:
    """Construye el mensaje con el formato que espera `/v1/edit/completions`.

    El protocolo divide el contexto en tres bloques: fragmentos vistos
    recientemente, contenido del archivo con la región editable e historial de
    diffs. Desde Telegram no hay historial, así que se envían vacíos y la
    instrucción se coloca como comentario de la región editable.
    """
    editable = code.rstrip("\n")
    if CURSOR_TAG not in editable:
        editable = f"{editable} {CURSOR_TAG}"

    return (
        "<|recently_viewed_code_snippets|>\n\n<|/recently_viewed_code_snippets|>\n\n"
        "<|current_file_content|>\n"
        f"current_file_path: {path}\n"
        "'''\n"
        f"{instruction.strip()}\n"
        "'''\n"
        "<|code_to_edit|>\n"
        f"{editable}\n"
        "<|/code_to_edit|>\n"
        "<|/current_file_content|>\n\n"
        "<|edit_diff_history|>\n\n<|/edit_diff_history|>"
    )


def guess_file_name(language: str | None) -> str:
    """Nombre de archivo ficticio que ayuda al modelo con el lenguaje."""
    extension = {
        "python": "py",
        "py": "py",
        "javascript": "js",
        "js": "js",
        "typescript": "ts",
        "ts": "ts",
        "kotlin": "kt",
        "java": "java",
        "go": "go",
        "rust": "rs",
        "c": "c",
        "c++": "cpp",
        "cpp": "cpp",
        "csharp": "cs",
        "c#": "cs",
        "php": "php",
        "ruby": "rb",
        "sql": "sql",
        "bash": "sh",
        "shell": "sh",
        "html": "html",
        "css": "css",
        "json": "json",
        "yaml": "yaml",
    }.get((language or "").lower(), "txt")
    return f"snippet.{extension}"


class CodeService:
    def __init__(self, client: InceptionClient, db: Database, config: Config) -> None:
        self._client = client
        self._db = db
        self._config = config

    async def generate(
        self, user_id: int, language: str | None, description: str, username: str | None = None
    ) -> str:
        """Genera un fragmento de código a partir de una descripción."""
        settings = await self._db.get_or_create_user(
            user_id,
            username,
            default_model=self._config.default_model,
            default_effort=self._config.default_reasoning_effort,
        )
        target = language.strip() if language else "el lenguaje más adecuado"
        prompt = f"Lenguaje: {target}\nTarea: {description}"
        code, usage = await self._client.complete(
            [
                {"role": "system", "content": CODE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            model=settings.model,
            reasoning_effort=settings.reasoning_effort,
            temperature=self._config.temperature,
            max_tokens=self._config.max_tokens,
        )
        await self._db.add_usage(
            user_id, settings.model, usage.prompt_tokens, usage.completion_tokens
        )
        return code.strip()

    async def fim(self, user_id: int, prefix: str, suffix: str = "") -> str:
        """Autocompletado inline con `mercury-edit-2` (endpoint FIM)."""
        completion = await self._client.fim(prefix, suffix=suffix)
        # FIM no devuelve `usage` en la respuesta HTTP; se registra la petición.
        await self._db.add_usage(user_id, "mercury-edit-2", 0, 0)
        return completion.strip("\n")

    async def next_edit(
        self, user_id: int, code: str, instruction: str, language: str | None = None
    ) -> str:
        """Reescribe una región de código con `mercury-edit-2` (endpoint Edit)."""
        prompt = build_next_edit_prompt(
            code, instruction, path=guess_file_name(language)
        )
        completion = await self._client.next_edit(prompt)
        await self._db.add_usage(user_id, "mercury-edit-2", 0, 0)
        return completion.strip("\n")
