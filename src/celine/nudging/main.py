import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from celine.nudging.security.auth import AuthMiddleware

from celine.nudging.api.routes.webpush import router as webpush_router
from celine.nudging.api.routes.notifications import router as notifications_router

from celine.nudging.api.routes.admin import admin_routers


def create_app():

    load_dotenv()

    logger = logging.getLogger(__name__)

    default_static_path = Path(__file__).resolve().parent / "tests" / "static"
    STATIC_PATH = Path(os.getenv("STATIC_PATH", str(default_static_path))).resolve()

    app = FastAPI(title="nudging-tool-api", version="0.1.0")

    app.add_middleware(AuthMiddleware)

    app.include_router(webpush_router)
    app.include_router(notifications_router)

    for ar in admin_routers:
        app.include_router(ar, prefix="/admin", tags=["admin"])

    if STATIC_PATH.exists():
        app.mount("/static", StaticFiles(directory=STATIC_PATH), name="static")
    else:
        logger.warning(f"STATIC_PATH {STATIC_PATH} not found")

    return app


if __name__ == "__main__":
    app = create_app()
