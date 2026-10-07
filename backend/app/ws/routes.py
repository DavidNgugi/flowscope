import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.db import db
from app.ws.manager import ws_manager

logger = logging.getLogger("flowscope.ws")

router = APIRouter()


async def _job_snapshot(job_id: str) -> dict | None:
    job = await db.fetchone("SELECT * FROM jobs WHERE id = ?", (job_id,))
    if job is None:
        return None
    events = await db.fetchall(
        "SELECT * FROM job_events WHERE job_id = ? ORDER BY id DESC LIMIT 50", (job_id,)
    )
    return {
        "type": "snapshot",
        "job_id": job["id"],
        "video_id": job["video_id"],
        "status": job["status"],
        "progress": {"current": job["progress_current"], "total": job["progress_total"]},
        "message": job["stage_detail"],
        "error_message": job["error_message"],
        "events": [
            {
                "stage": e["stage"],
                "message": e["message"],
                "progress": {"current": e["progress_current"], "total": e["progress_total"]},
                "ts": e["ts"],
            }
            for e in reversed(events)
        ],
    }


@router.websocket("/ws/jobs/{job_id}")
async def job_progress_ws(websocket: WebSocket, job_id: str) -> None:
    await websocket.accept()
    snapshot = await _job_snapshot(job_id)
    if snapshot is None:
        await websocket.send_json({"type": "error", "job_id": job_id, "message": "job not found"})
        await websocket.close()
        return

    await ws_manager.register(job_id, websocket)
    await websocket.send_json(snapshot)
    try:
        while True:
            # Client has nothing to say beyond keepalive pings; ignore payloads.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await ws_manager.unregister(job_id, websocket)
