"""Limitador de peticiones por usuario (ventana deslizante en memoria)."""

from __future__ import annotations

import time
from collections import defaultdict, deque


class RateLimiter:
    """Máximo `max_requests` peticiones por clave dentro de `window_seconds`.

    Es suficiente para un bot de un solo proceso: el estado vive en memoria y se
    reinicia al arrancar. Para varios procesos haría falta Redis o similar.
    """

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self.max_requests = max(1, max_requests)
        self.window = max(1.0, window_seconds)
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    def check(self, key: int) -> float:
        """Registra la petición. Devuelve 0 si se permite; si no, los segundos de espera."""
        now = time.monotonic()
        hits = self._hits[key]

        # Descarta las marcas que ya salieron de la ventana.
        while hits and now - hits[0] > self.window:
            hits.popleft()

        if len(hits) >= self.max_requests:
            return max(0.0, self.window - (now - hits[0]))

        hits.append(now)
        return 0.0

    def reset(self, key: int) -> None:
        self._hits.pop(key, None)

    def purge(self) -> None:
        """Elimina claves sin actividad reciente para no crecer sin límite."""
        now = time.monotonic()
        for key in [k for k, hits in self._hits.items() if not hits or now - hits[-1] > self.window]:
            self._hits.pop(key, None)
