"""MongoDB `library` DB 접속. 접속 정보는 저장소 루트 .env(MONGO_*)를 읽는다(seatviz/.env 값이 우선)."""
import os
from functools import lru_cache
from pathlib import Path

from bson import ObjectId
from dotenv import dotenv_values
from pymongo import MongoClient

APP_DIR = Path(__file__).resolve().parent
REPO = APP_DIR.parents[1]


@lru_cache
def get_db():
    cfg = {**dotenv_values(REPO / ".env"), **{k: v for k, v in os.environ.items() if k.startswith("MONGO_")}}
    client = MongoClient(
        host=cfg.get("MONGO_HOST", "localhost"),
        port=int(cfg.get("MONGO_PORT", 27017)),
        username=cfg.get("MONGO_USER"),
        password=cfg.get("MONGO_PASSWORD"),
        authSource=cfg.get("MONGO_AUTH_DB", "admin"),
        serverSelectionTimeoutMS=5000,
    )
    return client[os.getenv("MONGO_DB", "library")]


def clean(doc):
    """ObjectId 등 JSON 직렬화 불가 값을 문자열로."""
    if isinstance(doc, list):
        return [clean(x) for x in doc]
    if isinstance(doc, dict):
        return {k: clean(v) for k, v in doc.items()}
    if isinstance(doc, ObjectId):
        return str(doc)
    return doc
