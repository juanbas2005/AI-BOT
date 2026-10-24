"""Chat con IA: `/chat <mensaje>` y mensajes directos, con streaming."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from bot.context import BotContext
from bot.handlers.common import guard
from bot.services.inception_client import InceptionError
from bot.utils.telegram_utils import StreamingMessage, get_args

log = logging.getLogger(__name__)

USAGE = "Uso: /chat <mensaje> o escríbeme directamente el mensaje."


async def chat_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = get_args(update.effective_message) if update.effective_message else ""
    if not args:
        await update.effective_message.reply_text(USAGE)
        return
    await _reply(update, context, args)


async def plain_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or not message.text:
        return
    await _reply(update, context, message.text)


async def _reply(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Pide la respuesta al modelo y la va escribiendo en un único mensaje."""
    ctx: BotContext = context.bot_data["ctx"]
    if not await guard(update, ctx):
        return

    user = update.effective_user
    message = update.effective_message
    assert user is not None and message is not None

    chat = update.effective_chat
    if chat is not None:
        await chat.send_action(ChatAction.TYPING)

    editor = StreamingMessage(message, interval=ctx.config.stream_edit_interval)
    buffer = ""

    try:
        async for chunk in ctx.chat.stream_reply(user.id, text, user.username):
            if chunk.usage is not None:
                continue  # el consumo se guarda en el servicio
            buffer = chunk.text if chunk.snapshot else buffer + chunk.text
            await editor.update(buffer)
    except InceptionError as exc:
        log.warning("Error de la API en /chat: %s", exc)
        await editor.fail(f"⚠️ {exc}")
        return
    except Exception:
        log.exception("Error inesperado en /chat")
        await editor.fail("⚠️ Ocurrió un error inesperado. Inténtalo de nuevo.")
        return

    await editor.finish(buffer)


def register(application: Application) -> None:
    application.add_handler(CommandHandler("chat", chat_command))
    # En grupos el bot solo responde a comandos; el chat libre queda para privados.
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, plain_message)
    )
