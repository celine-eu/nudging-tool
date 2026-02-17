from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from celine.nudging.api.routes.ingest import router as ingest_router
from celine.nudging.api.routes.webpush import router as webpush_router

load_dotenv()
app = FastAPI(title="nudging-tool-api", version="0.1.0")

app.include_router(ingest_router, prefix="")

app.include_router(webpush_router)
app.mount("/static", StaticFiles(directory="tests/static"), name="static")
