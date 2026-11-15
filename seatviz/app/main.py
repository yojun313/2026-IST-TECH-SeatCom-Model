from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

from app.db import APP_DIR, get_db  # noqa: E402
from app.routes import api, pages  # noqa: E402

app = FastAPI(title="SeatViz", docs_url="/api/docs", redoc_url=None)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
app.include_router(api.router)
app.include_router(pages.router)


@app.get("/healthz")
def healthz():
    try:
        get_db().command("ping")
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": type(e).__name__}, status_code=503)
