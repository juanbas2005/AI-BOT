"""Comandos de utilidad y preferencias del usuario."""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from bot.context import BotContext
from bot.handlers.common import guard, run_request
from bot.services.image_service import ImageServiceError
from bot.utils.telegram_utils import get_args

log = logging.getLogger(__name__)

EFFORT_LABELS = {
    "instant": "instantáneo",
    "low": "bajo",
    "medium": "medio",
    "high": "alto",
}

HELP_TEXT = (
    "<b>Qué puedo hacer</b>\n\n"
    "<b>💬 Conversación</b>\n"
    "/chat &lt;mensaje&gt; o escríbeme directamente (con memoria)\n"
    "/reset — borra el historial de la conversación\n\n"
    "<b>🧠 Razonamiento</b>\n"
    "/instant · /low · /medium · /high — profundidad de razonamiento\n"
    "/modelo — cambia de modelo Mercury\n\n"
    "<b>💻 Código</b>\n"
    "/code &lt;lenguaje&gt; &lt;descripción&gt; — genera código\n"
    "/fim &lt;prefijo&gt; | &lt;sufijo&gt; — autocompletado (FIM)\n"
    "/edit &lt;código&gt; | &lt;instrucción&gt; — edita un fragmento\n\n"
    "<b>📄 Texto</b>\n"
    "/resume &lt;texto&gt; — resumen\n"
    "/analiza &lt;texto&gt; — puntos clave, sentimiento y entidades\n"
    "/traduce &lt;idioma&gt; &lt;texto&gt; — traducción\n\n"
    "<b>🎨 Imagen</b>\n"
    "/imagen &lt;descripción&gt; — genera una imagen\n\n"
    "<b>⚙️ Información</b>\n"
    "/estado — tu consumo de tokens y preferencias\n\n"
    "En los comandos de texto puedes responder a un mensaje en vez de escribir el texto."
)

WELCOME_TEXT = (
    "¡Hola! Soy un bot de IA con los modelos <b>Mercury</b> de InceptionLabs.\n\n"
    "Puedo conversar recordando el hilo, escribir y editar código, resumir o analizar "
    "textos, traducir y generar imágenes.\n\n"
    "Escríbeme lo que necesites o usa /help para ver todos los comandos."
)


def _model_keyboard(models: tuple[str, ...], current: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"{'✅ ' if model == current else ''}{model}", callback_data=f"model:{model}")]
        for model in models
    ]
    return InlineKeyboardMarkup(rows)


def _model_text(current: str, effort: str) -> str:
    return (
        f"<b>Modelo actual:</b> {current}\n"
        f"<b>Razonamiento:</b> {effort} ({EFFORT_LABELS.get(effort, effort)})\n\n"
        "Elige un modelo:"
    )


def _number(value: int) -> str:
    """Miles con punto, como es habitual en español."""
    return f"{value:,}".replace(",", ".")


def _source_text(update: Update, args: str) -> str:
    """Texto del comando o, si se respondió a un mensaje, el de ese mensaje."""
    message = update.effective_message
    if not args and message is not None and message.reply_to_message is not None:
        replied = message.reply_to_message
        return (replied.text or replied.caption or "").strip()
    return args


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(WELCOME_TEXT, parse_mode="HTML")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP_TEXT, parse_mode="HTML")


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: BotContext = context.bot_data["ctx"]
    if update.effective_user is None or update.effective_message is None:
        return
    removed = await ctx.db.clear_history(update.effective_user.id)
    await update.effective_message.reply_text(
        f"🧹 Historial borrado ({removed} mensajes). Empezamos de cero."
    )


async def model_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: BotContext = context.bot_data["ctx"]
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return

    settings = await ctx.chat.settings(user.id, user.username)
    args = get_args(message).lower()
    if not args:
        await message.reply_text(
            _model_text(settings.model, settings.reasoning_effort),
            parse_mode="HTML",
            reply_markup=_model_keyboard(ctx.config.available_models, settings.model),
        )
        return

    if args not in ctx.config.available_models:
        await message.reply_text(
            "Modelo no disponible. Opciones: " + ", ".join(ctx.config.available_models)
        )
        return

    await ctx.db.set_model(user.id, args)
    await message.reply_text(f"✅ Ahora uso el modelo <b>{args}</b>.", parse_mode="HTML")


async def model_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None:
        return
    await query.answer()

    ctx: BotContext = context.bot_data["ctx"]
    data = query.data or ""
    model = data.split(":", 1)[1] if ":" in data else ""
    if model not in ctx.config.available_models:
        await query.answer("Ese modelo no está disponible.", show_alert=True)
        return

    await ctx.chat.settings(user.id, user.username)
    await ctx.db.set_model(user.id, model)
    settings = await ctx.chat.settings(user.id, user.username)
    await query.edit_message_text(
        _model_text(settings.model, settings.reasoning_effort)
        + "\n\n✅ Modelo actualizado.",
        parse_mode="HTML",
        reply_markup=_model_keyboard(ctx.config.available_models, settings.model),
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: BotContext = context.bot_data["ctx"]
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return

    settings = await ctx.chat.settings(user.id, user.username)
    usage = await ctx.db.get_user_usage(user.id)
    await message.reply_text(
        "<b>Tu estado</b>\n\n"
        f"Modelo: {settings.model}\n"
        f"Razonamiento: {settings.reasoning_effort}\n"
        f"Peticiones: {_number(usage.requests)}\n"
        f"Tokens de entrada: {_number(usage.prompt_tokens)}\n"
        f"Tokens de salida: {_number(usage.completion_tokens)}\n"
        f"Total: {_number(usage.total_tokens)}",
        parse_mode="HTML",
    )


def _effort_setter(effort: str):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        ctx: BotContext = context.bot_data["ctx"]
        user = update.effective_user
        message = update.effective_message
        if user is None or message is None:
            return
        await ctx.chat.settings(user.id, user.username)
        await ctx.db.set_reasoning_effort(user.id, effort)
        await message.reply_text(
            f"✅ Razonamiento en <b>{effort}</b> ({EFFORT_LABELS[effort]}).",
            parse_mode="HTML",
        )

    return handler


async def summarize_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = _source_text(update, get_args(update.effective_message) if update.effective_message else "")
    if not text:
        await update.effective_message.reply_text("Uso: /resume <texto> (o responde a un mensaje).")
        return
    await run_request(
        update,
        context,
        lambda ctx, user_id, username: ctx.chat.summarize(user_id, text, username),
    )


async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = _source_text(update, get_args(update.effective_message) if update.effective_message else "")
    if not text:
        await update.effective_message.reply_text("Uso: /analiza <texto> (o responde a un mensaje).")
        return
    await run_request(
        update,
        context,
        lambda ctx, user_id, username: ctx.chat.analyze(user_id, text, username),
    )


async def translate_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    args = get_args(message) if message is not None else ""
    parts = args.split(maxsplit=1)
    if len(parts) != 2:
        await message.reply_text(
            "Uso: /traduce <idioma> <texto>\nEjemplo: /traduce inglés Hola, ¿cómo estás?"
        )
        return
    language, text = parts[0], parts[1].strip()
    await run_request(
        update,
        context,
        lambda ctx, user_id, username: ctx.chat.translate(user_id, language, text, username),
    )


async def image_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx: BotContext = context.bot_data["ctx"]
    if not await guard(update, ctx):
        return

    message = update.effective_message
    prompt = get_args(message) if message is not None else ""
    if not prompt:
        await message.reply_text("Uso: /imagen <descripción de la imagen>")
        return

    await message.reply_text("🎨 Generando la imagen…")
    try:
        image = await ctx.images.generate(prompt)
    except ImageServiceError as exc:
        await message.reply_text(f"⚠️ {exc}")
        return
    except Exception:
        log.exception("Error inesperado generando la imagen")
        await message.reply_text("⚠️ No pude generar la imagen. Inténtalo más tarde.")
        return

    await message.reply_photo(photo=image, caption=f"🎨 {prompt[:900]}")


def register(application: Application) -> None:
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("reset", reset_command))
    application.add_handler(CommandHandler("modelo", model_command))
    application.add_handler(CommandHandler("estado", status_command))
    application.add_handler(CallbackQueryHandler(model_callback, pattern=r"^model:"))
    application.add_handler(CommandHandler("resume", summarize_command))
    application.add_handler(CommandHandler("analiza", analyze_command))
    application.add_handler(CommandHandler("traduce", translate_command))
    application.add_handler(CommandHandler("imagen", image_command))
    for effort in ("instant", "low", "medium", "high"):
        application.add_handler(CommandHandler(effort, _effort_setter(effort)))
