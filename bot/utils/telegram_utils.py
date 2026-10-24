"""Utilidades de Telegram: troceado de mensajes, bloques de código y streaming."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from html import escape

from telegram import Message
from telegram.constants import ParseMode
from telegram.error import BadRequest, RetryAfter, TelegramError

log = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 4096
MAX_CHUNKS = 5  # límite de mensajes por respuesta para no inundar el chat
STREAM_CURSOR = " ▌"

_LANGUAGE_RE = re.compile(r"[^A-Za-z0-9+#._-]")
_FENCE_RE = re.compile(r"^```[A-Za-z0-9+#._-]*\s*\n(.*?)\n?```$", re.DOTALL)


def split_message(text: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Trocea un texto largo respetando párrafos y palabras."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = remaining.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit  # una sola palabra gigante: corte duro
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return [chunk for chunk in chunks if chunk]


async def reply_long(message: Message, text: str, *, limit: int = MAX_MESSAGE_LENGTH) -> None:
    """Envía un texto dividido en varios mensajes si supera el límite de Telegram."""
    chunks = split_message(text, limit)
    if not chunks:
        await message.reply_text("(respuesta vacía)")
        return
    for chunk in chunks[:MAX_CHUNKS]:
        await message.reply_text(chunk)
    if len(chunks) > MAX_CHUNKS:
        await message.reply_text("… (respuesta recortada por longitud)")


def strip_code_fences(text: str) -> str:
    """Quita un bloque ``` que envuelva toda la respuesta (el modelo a veces lo añade)."""
    stripped = text.strip()
    match = _FENCE_RE.match(stripped)
    if match:
        return match.group(1).strip("\n")
    if stripped.startswith("```") and stripped.endswith("```") and len(stripped) > 6:
        return stripped.strip("`").strip("\n")
    return stripped


async def reply_code(message: Message, code: str, language: str | None = None) -> None:
    """Envía código con formato monoespaciado, con fallback a texto plano."""
    code = code.strip("\n")
    if not code:
        await message.reply_text("El modelo no devolvió código.")
        return

    safe_language = _LANGUAGE_RE.sub("", (language or "")).strip(".")
    escaped = escape(code)
    if safe_language:
        block = f'<pre><code class="language-{safe_language}">{escaped}</code></pre>'
    else:
        block = f"<pre>{escaped}</pre>"

    # El HTML escapado puede superar el límite: si no cabe, se manda en texto plano.
    if len(block) <= MAX_MESSAGE_LENGTH - 96 and code.count("\n") < 200:
        await message.reply_text(block, parse_mode=ParseMode.HTML)
        return
    await reply_long(message, code)


def get_args(message: Message) -> str:
    """Texto que sigue al comando, sin el comando."""
    text = message.text or message.caption or ""
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def split_pair(args: str) -> tuple[str, str]:
    """Divide `izquierda | derecha`; sin separador, todo es la izquierda."""
    if "|" not in args:
        return args.strip(), ""
    left, right = args.split("|", 1)
    return left.strip(), right.strip()


KNOWN_LANGUAGES = frozenset(
    {
        "python", "py", "python3", "javascript", "js", "typescript", "ts", "node",
        "kotlin", "java", "go", "golang", "rust", "c", "cpp", "c++", "c#", "csharp",
        "php", "ruby", "rb", "sql", "bash", "shell", "sh", "html", "css", "json",
        "yaml", "yml", "xml", "swift", "dart", "scala", "r", "lua", "perl",
        "haskell", "elixir", "erlang", "matlab", "asm",
    }
)


def split_language(args: str) -> tuple[str, str]:
    """Interpreta `/code <lenguaje> <descripción>` (el lenguaje es opcional).

    Solo se toma la primera palabra como lenguaje si es un lenguaje conocido;
    así `/code una función que sume` no confunde "una" con un lenguaje.
    """
    parts = args.split(maxsplit=1)
    if len(parts) == 2 and parts[0].lower() in KNOWN_LANGUAGES:
        return parts[0], parts[1].strip()
    return "", args.strip()


class StreamingMessage:
    """Muestra una respuesta en construcción editando un único mensaje.

    Telegram limita la frecuencia de edición por chat, así que las
    actualizaciones se espacian `interval` segundos. El texto que excede el
    límite del mensaje se envía al final, troceado, como mensajes adicionales.
    """

    def __init__(
        self, origin: Message, *, interval: float = 1.5, limit: int = MAX_MESSAGE_LENGTH
    ) -> None:
        self._origin = origin
        self._interval = max(0.3, interval)
        self._limit = limit
        self._message: Message | None = None
        self._last_edit_at = 0.0
        self._last_display = ""

    def _display(self, text: str) -> str:
        budget = self._limit - len(STREAM_CURSOR)
        if len(text) <= budget:
            return text + STREAM_CURSOR
        return text[: budget - 1].rstrip() + "…" + STREAM_CURSOR

    async def update(self, text: str) -> None:
        """Refresca el mensaje respetando el intervalo mínimo entre ediciones."""
        if self._message is not None and time.monotonic() - self._last_edit_at < self._interval:
            return
        await self._render(self._display(text))

    async def finish(self, text: str) -> None:
        """Escribe el texto final completo (sin cursor) y el resto troceado."""
        chunks = split_message(text)
        if not chunks:
            await self.fail("El modelo no devolvió texto. Prueba a reformular la petición.")
            return

        first, rest = chunks[0], chunks[1:]
        if self._message is None:
            self._message = await self._origin.reply_text(first)
        elif first != self._last_display:
            await self._edit(first)

        for chunk in rest[: MAX_CHUNKS - 1]:
            await self._origin.reply_text(chunk)
        if len(rest) > MAX_CHUNKS - 1:
            await self._origin.reply_text("… (respuesta recortada por longitud)")

    async def fail(self, text: str) -> None:
        """Muestra un aviso de error, editando si ya hay algo en pantalla."""
        if self._message is None:
            await self._origin.reply_text(text)
        else:
            await self._edit(text)

    async def _edit(self, text: str) -> None:
        assert self._message is not None
        try:
            await self._message.edit_text(text)
            self._last_display = text
        except RetryAfter as exc:
            await asyncio.sleep(float(exc.retry_after) + 0.5)
        except BadRequest as exc:
            if "not modified" not in str(exc).lower():
                log.warning("No se pudo editar el mensaje: %s", exc)
        except TelegramError as exc:
            log.warning("Error de Telegram al editar el mensaje: %s", exc)

    async def _render(self, display: str) -> None:
        if display == self._last_display:
            return
        try:
            if self._message is None:
                self._message = await self._origin.reply_text(display)
                self._last_display = display
            else:
                await self._edit(display)
        except TelegramError as exc:
            log.warning("Error de Telegram al enviar el mensaje: %s", exc)
        finally:
            self._last_edit_at = time.monotonic()
