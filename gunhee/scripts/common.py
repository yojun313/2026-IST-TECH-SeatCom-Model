"""공통 스키마 및 제한된 HTTP 접근. 응답 원문은 저장하지 않는다."""
import io
import json
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
DATE = datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()
UA = 'SeatComMetadata/1.0 (university hackathon; static seat layout metadata research; no occupancy storage)'
ROOM_COLUMNS = ['대학명','도서관명','열람실ID','열람실명','열람실영문명','층','전체좌석수','좌석배정방식(지정석/자유석)','노트북사용가능여부(Y/N)','운영시작시각','운영종료시각','1회최대이용시간(분)','배치도이미지URL','배치도가로픽셀','배치도세로픽셀','좌석좌표제공여부(Y/N)','출처URL','데이터기준일자']
SEAT_COLUMNS = ['대학명','도서관명','열람실ID','좌석ID','좌석번호','X좌표','Y좌표','가로','세로','정규화X(0~1)','정규화Y(0~1)','PC좌석여부','좌석유형','출처URL','데이터기준일자']
FACILITY_COLUMNS = ['대학명','도서관명','열람실ID','시설ID','시설유형(화장실/출입구/에어컨/창문/정수기/콘센트/기둥)','X좌표','Y좌표','비고','데이터기준일자']


class CollectionStopped(RuntimeError):
    pass


class Client:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers['User-Agent'] = UA
        self.last = time.monotonic()  # 별도 스크립트 연속 실행 시에도 첫 요청 전 대기

    def get(self, url, **kwargs):
        # 최초 요청 + 최대 두 번 재시도. 요청 종료 후부터 최소 1초.
        for attempt in range(3):
            time.sleep(max(0, 1.1 - (time.monotonic() - self.last)))
            try:
                r = self.session.get(url, timeout=30, allow_redirects=False, **kwargs)
            except requests.RequestException:
                self.last = time.monotonic()
                if attempt == 2:
                    raise CollectionStopped('요청 실패: ' + url.split('?')[0]) from None
                continue
            self.last = time.monotonic()
            if r.is_redirect or r.status_code in (401, 403):
                raise CollectionStopped('리다이렉트/접근 제한으로 중단: ' + url.split('?')[0])
            if r.status_code >= 500 and attempt < 2:
                continue
            if r.status_code != 200:
                raise CollectionStopped('HTTP %s: %s' % (r.status_code, url.split('?')[0]))
            r.encoding = 'utf-8'
            if 'text/html' in r.headers.get('Content-Type', '') and any(
                marker in r.text.lower() for marker in ('type="password"', "type='password'")
            ):
                raise CollectionStopped('로그인 폼 감지: ' + url.split('?')[0])
            return r
        raise CollectionStopped('요청 실패')


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def blank_room(**values):
    row = dict.fromkeys(ROOM_COLUMNS)
    row.update({'데이터기준일자': DATE, '좌석좌표제공여부(Y/N)': 'N'})
    row.update(values)
    return row


def download_image(client, url, university, room_id):
    if urlparse(url).scheme != 'https':
        raise CollectionStopped('HTTPS 이미지 URL 필요')
    body = client.get(url).content
    with Image.open(io.BytesIO(body)) as im:
        im.load()
        size = im.size
        path = DATA / 'images' / university / (room_id + '.jpg')
        path.parent.mkdir(parents=True, exist_ok=True)
        im.convert('RGB').save(path, 'JPEG', quality=95)
    return size
