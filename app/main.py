import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.routers import hotels
from app.services.cache import cache_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Google Hotels Scraper API...")
    await cache_service.connect()
    yield
    await cache_service.disconnect()


app = FastAPI(
    title="Google Hotels Scraper API",
    description="High-scale reverse-engineered Google Hotels scraper. No browser automation.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(hotels.router, prefix="/api/v1", tags=["Hotels"])


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok", "service": "google-hotels-scraper"}


@app.exception_handler(Exception)
async def global_error_handler(request, exc):
    logger.error(f"Unhandled: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"error": "Internal server error", "detail": str(exc)})
