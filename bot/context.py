"""Dependencias compartidas que se guardan en `application.bot_data`."""

from __future__ import annotations

from dataclasses import dataclass

from bot.config import Config
from bot.database.db import Database
from bot.services.chat_service import ChatService
from bot.services.code_service import CodeService
from bot.services.image_service import ImageService
from bot.services.inception_client import InceptionClient
from bot.utils.rate_limiter import RateLimiter


@dataclass
class BotContext:
    config: Config
    client: InceptionClient
    db: Database
    chat: ChatService
    code: CodeService
    images: ImageService
    limiter: RateLimiter

    def consume_quota(self, user_id: int) -> float:
        """Devuelve 0 si la petición se permite; si no, segundos a esperar."""
        return self.limiter.check(user_id)
