"""Configuración central del bot, cargada desde variables de entorno.

Nunca escribas claves en el código: define un `.env` a partir de `.env.example`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Modelos de chat soportados por la API de Inception (docs: Models & Pricing).
DEFAULT_MODELS = ("mercury-2", "mercury-2.5", "mercury-3")
REASONING_EFFORTS = ("instant", "low", "medium", "high")

# Límites documentados por Inception para el parámetro temperature.
MIN_TEMPERATURE = 0.5
MAX_TEMPERATURE = 1.0


class ConfigError(RuntimeError):
    """Falta una variable obligatoria o su valor no es válido."""


def _text(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def _integer(name: str, default: int) -> int:
    raw = _text(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} debe ser un número entero, no {raw!r}") from exc


def _number(name: str, default: float) -> float:
    raw = _text(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} debe ser un número, no {raw!r}") from exc


def _boolean(name: str, default: bool = False) -> bool:
    raw = _text(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "si", "sí", "on"}


def _integer_set(name: str) -> frozenset[int]:
    raw = _text(name)
    if raw is None:
        return frozenset()
    ids: set[int] = set()
    for piece in raw.replace(" ", "").split(","):
        if not piece:
            continue
        try:
            ids.add(int(piece))
        except ValueError as exc:
            raise ConfigError(f"ADMIN_IDS contiene un ID no numérico: {piece!r}") from exc
    return frozenset(ids)


@dataclass(frozen=True)
class Config:
    telegram_token: str
    inception_api_key: str
    inception_base_url: str
    default_model: str
    available_models: tuple[str, ...]
    default_reasoning_effort: str
    max_tokens: int
    temperature: float
    history_limit: int
    db_path: Path
    rate_limit_requests: int
    rate_limit_window: float
    request_timeout: float
    admin_ids: frozenset[int]
    stream_edit_interval: float
    diffusing: bool
    image_enabled: bool
    image_url_template: str
    image_size: int
    image_timeout: float
    log_level: str


def load_config() -> Config:
    """Carga y valida la configuración. Lanza ConfigError si algo falta."""
    load_dotenv()

    telegram_token = _text("TELEGRAM_BOT_TOKEN") or _text("BOT_TOKEN")
    missing = []
    if not telegram_token:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not _text("INCEPTION_API_KEY"):
        missing.append("INCEPTION_API_KEY")
    if missing:
        raise ConfigError(
            "faltan variables de entorno: "
            + ", ".join(missing)
            + ". Copia .env.example a .env y rellena los valores."
        )

    default_model = _text("MERCURY_MODEL", DEFAULT_MODELS[0]) or DEFAULT_MODELS[0]
    models_raw = _text("MERCURY_MODELS", ",".join(DEFAULT_MODELS)) or ""
    available_models = tuple(model.strip() for model in models_raw.split(",") if model.strip())
    if default_model not in available_models:
        raise ConfigError(
            f"MERCURY_MODEL ({default_model}) debe aparecer en MERCURY_MODELS ({', '.join(available_models)})."
        )

    effort = (_text("MERCURY_REASONING_EFFORT", "medium") or "medium").lower()
    if effort not in REASONING_EFFORTS:
        raise ConfigError(
            f"MERCURY_REASONING_EFFORT debe ser uno de {', '.join(REASONING_EFFORTS)}; recibido {effort!r}."
        )

    temperature = _number("MERCURY_TEMPERATURE", 0.75)
    # La API documenta 0.5–1.0; fuera de rango lo reinicia al valor por defecto.
    temperature = min(MAX_TEMPERATURE, max(MIN_TEMPERATURE, temperature))

    image_template = _text("IMAGE_PROVIDER_URL", "https://image.pollinations.ai/prompt/{prompt}") or ""
    if "{prompt}" not in image_template:
        raise ConfigError("IMAGE_PROVIDER_URL debe incluir el marcador {prompt}.")

    return Config(
        telegram_token=telegram_token,
        inception_api_key=_text("INCEPTION_API_KEY") or "",
        inception_base_url=_text("INCEPTION_BASE_URL", "https://api.inceptionlabs.ai/v1") or "",
        default_model=default_model,
        available_models=available_models,
        default_reasoning_effort=effort,
        max_tokens=_integer("MERCURY_MAX_TOKENS", 4096),
        temperature=temperature,
        history_limit=max(0, _integer("HISTORY_LIMIT", 20)),
        db_path=Path(_text("DB_PATH", "data/bot.db") or "data/bot.db"),
        rate_limit_requests=max(1, _integer("RATE_LIMIT_REQUESTS", 10)),
        rate_limit_window=max(1.0, _number("RATE_LIMIT_WINDOW", 60.0)),
        request_timeout=max(1.0, _number("REQUEST_TIMEOUT", 120.0)),
        admin_ids=_integer_set("ADMIN_IDS"),
        stream_edit_interval=max(0.3, _number("STREAM_EDIT_INTERVAL", 1.5)),
        diffusing=_boolean("INCEPTION_DIFFUSING", False),
        image_enabled=_boolean("IMAGE_ENABLED", True),
        image_url_template=image_template,
        image_size=max(128, _integer("IMAGE_SIZE", 1024)),
        image_timeout=max(5.0, _number("IMAGE_TIMEOUT", 90.0)),
        log_level=(_text("LOG_LEVEL", "INFO") or "INFO").upper(),
    )
