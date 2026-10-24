"""Helpers compartidos por los handlers."""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot.context import BotContext
from bot.services.inception_client import InceptionError
from bot.utils.telegram_utils import reply_code, reply_long, strip_code_fences

log = logging.getLogger(__name__)

# (contexto, user_id, username) -> texto
Producer = Callable[[BotContext, int, str | None], Awaitable[str]]


async def check_quota(update: Update, ctx: BotContext) -> bool:
    """Comprueba el rate limit. Avisa al usuario y devuelve False si se pasa."""
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return False

    wait = ctx.consume_quota(user.id)
    if wait > 0:
        await message.reply_text(
            f"⏳ Vas demasiado rápido. Espera {int(wait) + 1} s y vuelve a intentarlo.\n"
            f"Límite: {ctx.config.rate_limit_requests} peticiones cada "
            f"{int(ctx.config.rate_limit_window)} s."
        )
        return False
    return True


async def guard(update: Update, ctx: BotContext) -> bool:
    """Valida que el update trae usuario y mensaje, y que queda cuota."""
    if update.effective_user is None or update.effective_message is None:
        return False
    return await check_quota(update, ctx)


async def run_request(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    produce: Producer,
    *,
    as_code: bool = False,
    language: str | None = None,
) -> None:
    """Ejecuta una petición no-streaming y responde con manejo de errores."""
    ctx: BotContext = context.bot_data["ctx"]
    if not await guard(update, ctx):
        return

    user = update.effective_user
    message = update.effective_message
    assert user is not None and message is not None  # garantizado por guard()

    chat = update.effective_chat
    if chat is not None:
        await chat.send_action(ChatAction.TYPING)

    try:
        text = await produce(ctx, user.id, user.username)
    except InceptionError as exc:
        await message.reply_text(f"⚠️ {exc}")
        return
    except Exception:
        log.exception("Error inesperado procesando la petición")
        await message.reply_text(
            "⚠️ Ocurrió un error inesperado al contactar con la IA. Inténtalo de nuevo."
        )
        return

    if not text.strip():
        await message.reply_text("El modelo no devolvió contenido. Prueba a reformular la petición.")
        return

    if as_code:
        await reply_code(message, strip_code_fences(text), language)
    else:
        await reply_long(message, text)
