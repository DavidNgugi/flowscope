"""WebSocket connection registry, keyed by job_id.

All methods are async and only ever called from the event loop (WebSocket
accept/send and job-progress broadcasts both run there), so a plain dict is
safe -- but structural changes (register/unregister) go through a lock so a
new connection arriving mid-broadcast can't corrupt the per-job connection
set, and broadcasts iterate over a snapshot copy.
"""

import asyncio
import logging

from fastapi import WebSocket

logger = logging.getLogger("flowscope.ws")


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def register(self, job_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._connections.setdefault(job_id, set()).add(ws)

    async def unregister(self, job_id: str, ws: WebSocket) -> None:
        async with self._lock:
            conns = self._connections.get(job_id)
            if conns is not None:
                conns.discard(ws)
                if not conns:
                    self._connections.pop(job_id, None)

    async def broadcast(self, job_id: str, message: dict) -> None:
        async with self._lock:
            conns = list(self._connections.get(job_id, ()))
        dead: list[WebSocket] = []
        for ws in conns:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.get(job_id, set()).discard(ws)


ws_manager = ConnectionManager()
