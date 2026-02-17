import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from celine.nudging.api.routes.ingest import router as ingest_router
from celine.nudging.api.routes.webpush import router as webpush_router

load_dotenv()

logger = logging.getLogger(__name__)

default_static_path = Path(__file__).resolve().parents[1] / "tests" / "static"
STATIC_PATH = Path(os.getenv("STATIC_PATH", str(default_static_path))).resolve()

app = FastAPI(title="nudging-tool-api", version="0.1.0")

app.include_router(ingest_router, prefix="")
app.include_router(webpush_router)


if STATIC_PATH.exists():
    app.mount("/static", StaticFiles(directory=STATIC_PATH), name="static")
else:
    logger.warning(f"STATIC_PATH {STATIC_PATH} not found")
