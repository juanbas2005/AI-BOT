"""Comandos de código: `/code`, `/fim` y `/edit`."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from bot.handlers.common import run_request
from bot.utils.telegram_utils import get_args, split_language, split_pair

log = logging.getLogger(__name__)

CODE_USAGE = (
    "Uso: /code <lenguaje> <descripción>\n"
    "Ejemplo: /code python leer un CSV y sumar una columna\n\n"
    "El lenguaje es opcional: /code una función que invierta una lista"
)

FIM_USAGE = (
    "Uso: /fim <prefijo> | <sufijo opcional>\n"
    "Completa el código que falta entre las dos partes.\n"
    "Ejemplo: /fim def factorial(n): return n * | print(factorial(5))"
)

EDIT_USAGE = (
    "Uso: /edit <código> | <instrucción>\n"
    "O responde a un mensaje con código y escribe: /edit <instrucción>\n"
    "Ejemplo: /edit def suma(a, b): return a - b | corrige la resta para que sume"
)


async def code_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    args = get_args(message) if message is not None else ""
    if not args:
        await message.reply_text(CODE_USAGE)
        return

    language, description = split_language(args)
    await run_request(
        update,
        context,
        lambda ctx, user_id, username: ctx.code.generate(
            user_id, language or None, description, username
        ),
        as_code=True,
        language=language or None,
    )


async def fim_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    args = get_args(message) if message is not None else ""
    if not args:
        await message.reply_text(FIM_USAGE)
        return

    prefix, suffix = split_pair(args)
    if not prefix:
        await message.reply_text(FIM_USAGE)
        return

    await run_request(
        update,
        context,
        lambda ctx, user_id, username: ctx.code.fim(user_id, prefix, suffix),
        as_code=True,
    )


async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    args = get_args(message)
    if not args:
        await message.reply_text(EDIT_USAGE)
        return

    replied = message.reply_to_message
    if replied is not None and (replied.text or replied.caption):
        code = (replied.text or replied.caption or "").strip()
        instruction = args
    else:
        code, instruction = split_pair(args)

    if not code.strip() or not instruction.strip():
        await message.reply_text(EDIT_USAGE)
        return

    await run_request(
        update,
        context,
        lambda ctx, user_id, username: ctx.code.next_edit(user_id, code, instruction),
        as_code=True,
    )


def register(application: Application) -> None:
    application.add_handler(CommandHandler("code", code_command))
    application.add_handler(CommandHandler("fim", fim_command))
    application.add_handler(CommandHandler("edit", edit_command))
