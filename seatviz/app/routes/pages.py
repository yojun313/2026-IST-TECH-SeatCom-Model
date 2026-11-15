"""HTML 페이지. 각 페이지는 _layout.html 을 상속하고 데이터는 /api 에서 JS 로 불러온다."""
from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from app.db import APP_DIR

router = APIRouter()
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

NAV = (
    ("overview", "/", "fa-gauge-high", "개요"),
    ("floor", "/floor", "fa-building-columns", "도서관 뷰어"),
    ("real", "/real", "fa-map-location-dot", "실데이터"),
    ("users", "/users", "fa-users", "사용자"),
    ("sessions", "/sessions", "fa-route", "세션"),
    ("stats", "/stats", "fa-chart-column", "통계"),
    ("browse", "/browse", "fa-database", "데이터 탐색"),
)


def page(request: Request, name: str, title: str, **ctx):
    return templates.TemplateResponse(request=request, name=f"{name}.html",
                                      context={"active": name, "nav": NAV, "title": title, **ctx})


@router.get("/")
def overview(request: Request):
    return page(request, "overview", "개요")


@router.get("/floor")
def floor(request: Request, lib: str | None = None, scenario: str | None = None):
    return page(request, "floor", "도서관 뷰어", lib=lib or "", scenario=scenario or "")


@router.get("/real")
def real(request: Request, lib: str | None = None):
    return page(request, "real", "실데이터", lib=lib or "")


@router.get("/users")
def users(request: Request):
    return page(request, "users", "사용자")


@router.get("/users/{uid}")
def user(request: Request, uid: str):
    return page(request, "user", "사용자 상세", uid=uid)


@router.get("/sessions")
def sessions(request: Request):
    return page(request, "sessions", "세션")


@router.get("/sessions/{sid}")
def session(request: Request, sid: str):
    return page(request, "session", "세션 상세", sid=sid)


@router.get("/stats")
def stats(request: Request):
    return page(request, "stats", "통계")


@router.get("/browse")
def browse(request: Request, c: str | None = None):
    return page(request, "browse", "데이터 탐색", coll=c or "")
