"""SeatViz 앱. 백엔드(backend.main)가 / 에 붙여 같은 프로세스·같은 SQLite 로 제공한다."""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .routes import api, pages


def create_app(database_path) -> FastAPI:
    db.configure(database_path)
    app = FastAPI(
        title="SeatViz",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.mount(
        "/static", StaticFiles(directory=str(db.APP_DIR / "static")), name="static"
    )
    app.include_router(api.router)
    app.include_router(pages.router)

    @app.get("/healthz")
    def healthz():
        try:
            db.get_db().ping()
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return JSONResponse(
                {"ok": False, "error": type(e).__name__}, status_code=503
            )

    return app


def warm():
    """첫 방문이 느리지 않게 개요·전체 통계를 미리 계산한다(백엔드 시작 후 백그라운드)."""
    api.overview()
    api.stats(None)
