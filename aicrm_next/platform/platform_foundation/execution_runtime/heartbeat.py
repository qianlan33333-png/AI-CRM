from __future__ import annotations

import threading
from collections.abc import Callable


class LeaseHeartbeat:
    """Renew one lease without hiding ownership loss from the caller."""

    def __init__(
        self,
        renew: Callable[[], bool],
        *,
        interval_seconds: float = 10,
    ) -> None:
        self._renew = renew
        self._interval_seconds = max(0.05, float(interval_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.lost = False

    def __enter__(self) -> "LeaseHeartbeat":
        self._thread = threading.Thread(target=self._run, name="aicrm-lease-heartbeat", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self._interval_seconds * 2))

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                owned = bool(self._renew())
            except Exception:
                owned = False
            if not owned:
                self.lost = True
                self._stop.set()
                return
