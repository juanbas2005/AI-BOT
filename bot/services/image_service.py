"""Generación de imágenes con un proveedor externo.

La API de Inception no genera imágenes: aquí se usa por defecto Pollinations.ai,
que no requiere clave. Para usar otro proveedor basta con cambiar
`IMAGE_PROVIDER_URL` (debe incluir `{prompt}` y devolver la imagen directamente)
o sustituir esta clase por un cliente del servicio elegido.
"""

from __future__ import annotations

import logging
import secrets
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)


class ImageServiceError(RuntimeError):
    """Error controlado al generar una imagen."""


class ImageService:
    def __init__(
        self, *, enabled: bool, url_template: str, size: int, timeout: float
    ) -> None:
        self._enabled = enabled
        self._url_template = url_template
        self._size = size
        self._timeout = timeout

    async def generate(self, prompt: str) -> bytes:
        if not self._enabled:
            raise ImageServiceError(
                "La generación de imágenes está desactivada (IMAGE_ENABLED=false)."
            )

        prompt = prompt.strip()
        if not prompt:
            raise ImageServiceError("Escribe una descripción de la imagen que quieres.")

        url = self._url_template.format(prompt=quote(prompt, safe=""))
        params = {
            "width": self._size,
            "height": self._size,
            "nologo": "true",
            "seed": secrets.randbelow(1_000_000),
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
                response = await client.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise ImageServiceError("El servicio de imágenes tardó demasiado en responder.") from exc
        except httpx.HTTPError as exc:
            raise ImageServiceError("No se pudo conectar con el servicio de imágenes.") from exc

        if response.status_code != 200 or not response.content:
            raise ImageServiceError(
                f"El servicio de imágenes respondió con HTTP {response.status_code}."
            )

        content_type = response.headers.get("content-type", "")
        if not content_type.startswith("image/"):
            raise ImageServiceError(
                "El proveedor de imágenes no devolvió una imagen. Revisa IMAGE_PROVIDER_URL."
            )

        max_bytes = 10 * 1024 * 1024
        if len(response.content) > max_bytes:
            raise ImageServiceError("La imagen generada supera los 10 MB de Telegram.")

        log.info("Imagen generada (%s, %s bytes)", content_type, len(response.content))
        return response.content
