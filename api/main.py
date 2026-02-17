from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from api.routes.ingest import router as ingest_router
from api.routes.webpush import router as webpush_router
from dotenv import load_dotenv

load_dotenv()
app = FastAPI(title="nudging-tool-api", version="0.1.0")

app.include_router(ingest_router, prefix="")

app.include_router(webpush_router)
app.mount("/static", StaticFiles(directory="tests/static"), name="static")


