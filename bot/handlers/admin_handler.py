"""Comandos de administración (solo para los IDs de ADMIN_IDS)."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from bot.context import BotContext

log = logging.getLogger(__name__)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: BotContext = context.bot_data["ctx"]
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return

    if not ctx.config.admin_ids:
        await message.reply_text(
            "No hay administradores configurados. Añade tu ID numérico en ADMIN_IDS dentro de .env."
        )
        return
    if user.id not in ctx.config.admin_ids:
        await message.reply_text("Este comando es solo para administradores.")
        return

    stats = await ctx.db.get_global_stats()
    fmt = lambda value: f"{value:,}".replace(",", ".")  # noqa: E731 - formato local
    await message.reply_text(
        "<b>Estadísticas globales</b>\n\n"
        f"Usuarios: {fmt(stats.users)}\n"
        f"Mensajes guardados: {fmt(stats.messages)}\n"
        f"Peticiones a la IA: {fmt(stats.requests)}\n"
        f"Tokens de entrada: {fmt(stats.prompt_tokens)}\n"
        f"Tokens de salida: {fmt(stats.completion_tokens)}",
        parse_mode="HTML",
    )


def register(application: Application) -> None:
    application.add_handler(CommandHandler("stats", stats_command))
