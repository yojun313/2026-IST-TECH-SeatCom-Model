"""선택 확장용: 인증키와 공식 상세기능/정적 필드 매핑을 제공해야 실행한다.

예: python scripts/collect_public_api.py --config public_api_config.json
설정 구조는 data/README.md 참조. 응답 원문/현황 필드는 저장하지 않는다.
"""
import argparse
import json
import os
from urllib.parse import unquote

from common import Client, CollectionStopped, DATA, DATE, save_json

BASE = 'https://apis.data.go.kr/B551982/plr_v2'
# 시간별 방문/이용/잔여/예약 정보는 매핑 대상에 포함하지 않는다.
STATIC_TARGETS = {'도서관ID','도서관명','열람실ID','열람실명','층','전체좌석수','주소','위도','경도'}


def collect(config):
    operation = config['operation']
    fields = config['fields']  # {공식 영문 응답 필드: 저장할 한글 정적 컬럼}
    if not operation or '/' in operation or not operation.replace('_', '').isalnum():
        raise ValueError('공식 문서에서 확인한 단일 상세기능 이름이 필요합니다.')
    if not fields or not set(fields.values()) <= STATIC_TARGETS:
        raise ValueError('허용된 정적 컬럼만 매핑할 수 있습니다.')
    forbidden = ('state', 'seattime', 'used', 'remaining', 'available', 'visitor', 'reserved', 'usecnt', 'rsv', 'rmnd')
    if any(any(word in key.lower() for word in forbidden) for key in fields):
        raise ValueError('현황 필드는 수집할 수 없습니다.')
    client = Client()
    rows, page = [], 1
    while True:
        params = {'serviceKey':unquote(os.environ['DATA_GO_KR_KEY']), 'pageNo':page,
                  'numOfRows':100,'type':'JSON'}
        if config.get('stdgCd'):
            params['stdgCd'] = config['stdgCd']
        r = client.get(BASE + '/' + operation, params=params)
        try:
            result = r.json()
            response = result.get('response', result)
            header, body = response['header'], response['body']
            if str(header.get('resultCode')) not in ('00', '0', 'NORMAL_SERVICE'):
                raise ValueError('API 오류')
            items = body.get('items') or []
            if isinstance(items, dict):
                items = items.get('item') or []
            if isinstance(items, dict):
                items = [items]
            if not isinstance(items, list):
                raise ValueError('items 구조 오류')
            for item in items:
                if not all(key in item for key in fields):
                    raise ValueError('매핑 필드 누락')
                row = {target:item[source] for source, target in fields.items()}
                row.update({'출처URL':BASE + '/' + operation,'데이터기준일자':DATE})
                rows.append(row)
            total = int(body['totalCount'])
        except (ValueError, KeyError, TypeError):
            raise CollectionStopped('API 오류/응답 구조 변경. 원문과 인증키는 저장하지 않습니다.') from None
        if len(rows) >= total:
            break
        if not items:
            raise CollectionStopped('페이지 누락: 총 건수 이전에 빈 응답')
        page += 1
    save_json(DATA / 'public_library_static.json', rows)
    print('공공도서관 정적 레코드:', len(rows))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', help='공식 문서로 확인한 operation, fields 매핑 JSON')
    args = parser.parse_args()
    if not os.environ.get('DATA_GO_KR_KEY'):
        print('건너뜀: DATA_GO_KR_KEY가 없습니다.')
    elif not args.config:
        parser.error('키가 있습니다. 공식 상세기능과 정적 필드 매핑을 --config로 지정하세요.')
    else:
        try:
            collect(json.loads(open(args.config, encoding='utf-8').read()))
        except (CollectionStopped, ValueError) as exc:
            print('중단:', exc)
            raise SystemExit(2)
