import logging
from pathlib import Path

from fastapi import FastAPI, Response, HTTPException

logger = logging.getLogger(__name__)

app = FastAPI(title="XiaoLan Travel Calendar")

DATA_DIR: str = "./data"


def set_data_dir(d: str):
    global DATA_DIR
    DATA_DIR = d


@app.get("/trips/{trip_id}/calendar.ics")
async def get_calendar(trip_id: str):
    ics_path = Path(DATA_DIR) / "trips" / trip_id / "calendar.ics"
    if not ics_path.exists():
        raise HTTPException(status_code=404, detail="Calendar not found")
    return Response(
        content=ics_path.read_bytes(),
        media_type="text/calendar",
        headers={
            "Content-Disposition": f'attachment; filename="{trip_id}.ics"',
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
