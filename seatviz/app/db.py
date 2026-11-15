"""SeatViz 데이터 접근: 백엔드와 같은 SQLite 카탈로그(catalog_documents)를 읽기 전용으로 읽는다.

뷰어 코드가 쓰던 MongoDB 조회 형태(find/find_one/count/정렬·건너뛰기·제한)를 그대로 쓸 수 있게
필요한 만큼만 흉내 낸다. 지원 연산자: 같음, None(없음), $in, $ne, $gt/$gte/$lt/$lte, $regex.
집계는 group()/scalar 질의를 SQL 로 직접 쓴다.

공개 서비스 보호:
  - 연결은 mode=ro 라서 뷰어는 DB를 바꿀 수 없다.
  - 동시에 도는 뷰어 질의 수(VIEWER_MAX_QUERIES)와 질의 하나의 실행 시간(VIEWER_QUERY_SECONDS)을 제한해
    같은 프로세스의 추천 API 가 느려지지 않게 한다. 필드 이름은 정규식으로 검사하고 값은 모두 바인딩한다.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

from fastapi import HTTPException

APP_DIR = Path(__file__).resolve().parent

MAX_QUERIES = int(os.getenv("VIEWER_MAX_QUERIES", "2"))
QUERY_SECONDS = float(os.getenv("VIEWER_QUERY_SECONDS", "20"))
_slots = threading.BoundedSemaphore(MAX_QUERIES)
_db_path: Path | None = None

FIELD_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*")
# 원본 MongoDB 의 _id 는 내보낼 때 뺐다. 아래 업무 ID 와 같은 값이었으므로 그대로 되살린다.
ID_FIELD = {
    "libraries": "library_id",
    "rooms": "room_id",
    "maps": "map_id",
    "seats": "seat_id",
    "synth_users": "user_id",
    "synth_sessions": "session_id",
    "synth_scenarios": "scenario_id",
    "synth_events": "event_id",
}


def configure(path):
    global _db_path
    _db_path = Path(path)


def _doc_id(coll, doc):
    if coll == "synth_seat_env":
        return f"{doc.get('scenario_id')}|{doc.get('seat_id')}"
    if coll == "meta":
        return "synthetic_manifest" if "generator_version" in doc else "registry"
    f = ID_FIELD.get(coll)
    return doc.get(f) if f else None


def _path(field):
    if not FIELD_RE.fullmatch(field):
        raise HTTPException(400, f"허용되지 않는 필드 이름: {field}")
    return "$." + field


def _regexp(pattern, value):
    if value is None:
        return 0
    try:
        return 1 if re.search(pattern, str(value)) else 0
    except re.error:
        return 0


def query(sql, params=()):
    """읽기 전용 연결에서 SQL 실행 → 행 목록. 동시 실행 수·실행 시간 제한."""
    if _db_path is None:
        raise RuntimeError("seatviz.db.configure(path) 가 호출되지 않았습니다")
    if not _slots.acquire(timeout=QUERY_SECONDS):
        raise HTTPException(
            503, "뷰어 요청이 몰려 있습니다. 잠시 후 다시 시도해 주세요."
        )
    try:
        db = sqlite3.connect(
            _db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30
        )
        try:
            db.create_function("REGEXP", 2, _regexp, deterministic=True)
            deadline = time.monotonic() + QUERY_SECONDS
            db.set_progress_handler(
                lambda: 1 if time.monotonic() > deadline else 0, 10_000
            )
            try:
                return db.execute(sql, params).fetchall()
            except sqlite3.OperationalError as e:
                if "interrupted" in str(e):
                    raise HTTPException(
                        503, "질의가 너무 오래 걸려 중단했습니다. 조건을 좁혀 주세요."
                    ) from None
                raise
        finally:
            db.close()
    finally:
        _slots.release()


def _id_condition(coll, value):
    """_id 조건 → (SQL, params, 후처리 필터)."""
    if coll == "synth_seat_env":
        ids = value["$in"] if isinstance(value, dict) else [value]
        pairs = {tuple(str(i).split("|", 1)) for i in ids if "|" in str(i)}
        if not pairs:
            return "0", [], None
        scen = sorted({p[0] for p in pairs})
        sql = (
            f"json_extract(document, '$.scenario_id') IN ({','.join('?' * len(scen))})"
        )
        return sql, scen, lambda d: (d.get("scenario_id"), d.get("seat_id")) in pairs
    if coll == "meta":
        want = set(value["$in"] if isinstance(value, dict) else [value])
        return "1", [], lambda d: _doc_id("meta", d) in want
    field = ID_FIELD.get(coll)
    if not field:
        return "0", [], None
    sql, params = _conditions({field: value})
    return sql, params, None


def _conditions(filt):
    sql, params = [], []
    for field, cond in (filt or {}).items():
        if field.startswith("$"):
            raise HTTPException(400, f"허용되지 않는 연산자: {field}")
        expr = f"json_extract(document, '{_path(field)}')"
        if not isinstance(cond, dict):
            if isinstance(cond, (list, dict)):
                raise HTTPException(400, "배열·객체 값은 같음 조건에 쓸 수 없습니다")
            if cond is None:
                sql.append(f"{expr} IS NULL")
            else:
                sql.append(f"{expr} = ?")
                params.append(cond)
            continue
        for op, v in cond.items():
            if op == "$in":
                if not isinstance(v, list):
                    raise HTTPException(400, "$in 은 배열이어야 합니다")
                vals = [x for x in v if x is not None]
                parts = []
                if vals:
                    parts.append(f"{expr} IN ({','.join('?' * len(vals))})")
                    params.extend(vals)
                if len(vals) != len(v):
                    parts.append(f"{expr} IS NULL")
                sql.append("(" + " OR ".join(parts) + ")" if parts else "0")
            elif op == "$ne":
                if v is None:
                    sql.append(f"{expr} IS NOT NULL")
                else:
                    sql.append(f"({expr} IS NULL OR {expr} != ?)")
                    params.append(v)
            elif op in ("$gt", "$gte", "$lt", "$lte"):
                sql.append(
                    f"{expr} {dict(gt='>', gte='>=', lt='<', lte='<=')[op[1:]]} ?"
                )
                params.append(v)
            elif op == "$regex":
                sql.append(
                    f"{expr} REGEXP ?"
                )  # SQLite: X REGEXP Y → regexp(Y, X), 첫 인자가 패턴
                params.append(str(v))
            else:
                raise HTTPException(400, f"지원하지 않는 연산자: {op}")
    return " AND ".join(sql) or "1", params


def _project(doc, projection):
    if not projection:
        return doc
    inc = {k for k, v in projection.items() if v and k != "_id"}
    if inc:
        out = {}
        if projection.get("_id", 1) and "_id" in doc:
            out["_id"] = doc["_id"]
        for f in inc:
            _copy(doc, out, f.split("."))
        return out
    out = json.loads(json.dumps(doc))
    for f, v in projection.items():
        if not v:
            _drop(out, f.split("."))
    return out


def _copy(src, dst, parts):
    if not isinstance(src, dict) or parts[0] not in src:
        return
    k, v = parts[0], src[parts[0]]
    if len(parts) == 1:
        dst[k] = v
    elif isinstance(v, list):  # 배열 안 문서의 필드 투영(MongoDB 와 같음)
        cur = dst.setdefault(k, [{} for _ in v])
        for s, d in zip(v, cur):
            if isinstance(s, dict):
                _copy(s, d, parts[1:])
    elif isinstance(v, dict):
        _copy(v, dst.setdefault(k, {}), parts[1:])


def _drop(doc, parts):
    if isinstance(doc, list):
        for x in doc:
            _drop(x, parts)
    elif isinstance(doc, dict) and parts[0] in doc:
        if len(parts) == 1:
            del doc[parts[0]]
        else:
            _drop(doc[parts[0]], parts[1:])


class Cursor:
    def __init__(self, coll, filt, projection):
        self.coll, self.filt, self.projection = coll, filt or {}, projection
        self._sort, self._skip, self._limit = None, 0, None

    def sort(self, field, direction=1):
        self._sort = (field, direction)
        return self

    def skip(self, n):
        self._skip = max(0, int(n))
        return self

    def limit(self, n):
        self._limit = max(0, int(n))
        return self

    def _sql(self, select):
        filt = dict(self.filt)
        post = None
        where, params = ["collection = ?"], [self.coll]
        if "_id" in filt:
            s, p, post = _id_condition(self.coll, filt.pop("_id"))
            where.append(s)
            params += p
        s, p = _conditions(filt)
        where.append(s)
        params += p
        return (
            f"SELECT {select} FROM catalog_documents WHERE " + " AND ".join(where),
            params,
            post,
        )

    def _order(self):
        if not self._sort:
            return " ORDER BY ordinal"
        field, d = self._sort
        direction = "DESC" if d == -1 else "ASC"
        if field == "_id":  # 내보낼 때 _id 오름차순으로 ordinal 을 매겼다
            return f" ORDER BY ordinal {direction}"
        return (
            f" ORDER BY json_extract(document, '{_path(field)}') {direction}, ordinal"
        )

    def __iter__(self):
        sql, params, post = self._sql("document")
        sql += self._order()
        if post is None and (self._limit is not None or self._skip):
            sql += " LIMIT ? OFFSET ?"
            params += [self._limit if self._limit is not None else -1, self._skip]
        docs = []
        for (raw,) in query(sql, params):
            d = json.loads(raw)
            d.setdefault("_id", _doc_id(self.coll, d))
            if post and not post(d):
                continue
            docs.append(d)
        if post is not None:
            end = None if self._limit is None else self._skip + self._limit
            docs = docs[self._skip : end]
        return iter([_project(d, self.projection) for d in docs])

    def count(self):
        sql, params, post = self._sql("count(*)")
        if post is None:
            return query(sql, params)[0][0]
        sql, params, post = self._sql("document")
        return sum(1 for (raw,) in query(sql, params) if post(json.loads(raw)))


class Collection:
    def __init__(self, name):
        self.name = name

    def find(self, filt=None, projection=None):
        return Cursor(self.name, filt, projection)

    def find_one(self, filt=None, projection=None):
        return next(iter(self.find(filt, projection).limit(1)), None)

    def count_documents(self, filt=None):
        return Cursor(self.name, filt, None).count()

    def estimated_document_count(self):
        return query(
            "SELECT count(*) FROM catalog_documents WHERE collection = ?", (self.name,)
        )[0][0]

    def group(self, key, filt=None, **aggs):
        """GROUP BY key → [{"_id": 값, 이름: 값...}]. key: 필드 경로, ("substr", 경로, 길이), ("bucket", 경로, 경계 목록)
        aggs: n=("count",) / 이름=("avg", 경로)."""
        cols, params = [], []
        if key is None:
            kexpr = "NULL"
        elif isinstance(key, tuple) and key[0] == "substr":
            kexpr = (
                f"substr(json_extract(document, '{_path(key[1])}'), 1, {int(key[2])})"
            )
        elif isinstance(key, tuple) and key[0] == "bucket":
            # MongoDB $bucket 과 같음: 경계[i] <= v < 경계[i+1] → 경계[i], 범위 밖은 NULL
            v = f"json_extract(document, '{_path(key[1])}')"
            b = [float(x) for x in key[2]]
            whens = " ".join(
                f"WHEN {v} >= {b[i]!r} AND {v} < {b[i + 1]!r} THEN {b[i]!r}"
                for i in range(len(b) - 1)
            )
            kexpr = f"CASE {whens} ELSE NULL END"
        else:
            kexpr = f"json_extract(document, '{_path(key)}')"
        for name, spec in aggs.items():
            if spec[0] == "count":
                cols.append("count(*)")
            elif spec[0] == "avg":
                cols.append(f"avg(json_extract(document, '{_path(spec[1])}'))")
            else:
                raise ValueError(spec)
        where, wparams = _conditions(filt or {})
        sql = (
            f"SELECT {kexpr} AS k, {', '.join(cols)} FROM catalog_documents "
            f"WHERE collection = ? AND {where} GROUP BY k ORDER BY k"
        )
        rows = query(sql, [self.name, *wparams, *params])
        names = list(aggs)
        return [{"_id": r[0], **dict(zip(names, r[1:]))} for r in rows]


class DB:
    def __getitem__(self, name):
        return Collection(name)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return Collection(name)

    def ping(self):
        query("SELECT value FROM catalog_metadata WHERE key = 'version'")


_db = DB()


def get_db():
    return _db


def clean(doc):
    """예전 MongoDB ObjectId 직렬화용. SQLite 문서는 이미 JSON 이라 그대로 돌려준다."""
    return doc
