"""robots 허용 시에만 지정된 고려대 엔드포인트를 수집한다."""
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

from common import (Client, CollectionStopped, DATA, DATE, UA, blank_room,
                    download_image, save_json)

BASE = 'https://librsv.korea.ac.kr'
LIBRARIES = {1:'중앙도서관',2:'중앙광장',3:'백주년기념 학술정보관',4:'과학도서관',5:'하나스퀘어',6:'법학도서관'}


def yn(value):
    if str(value).upper() in ('Y', 'N'):
        return str(value).upper()
    return None


def payload(client, url):
    r = client.get(url)
    try:
        value = r.json()
    except ValueError:
        raise CollectionStopped('JSON 응답 아님: ' + url) from None
    if value.get('code') != 1 or not isinstance(value.get('data'), list):
        raise CollectionStopped('응답 구조/인증 오류: ' + url)
    return value['data']


def collect():
    client = Client()
    robots_url = BASE + '/robots.txt'
    robots = client.get(robots_url).text
    policy = RobotFileParser()
    # 서버가 줄바꿈을 문자 그대로 \\r\\n으로 내보내는 경우도 처리한다.
    normalized_robots = robots.replace('\\r\\n', '\n').replace('\\n', '\n')
    policy.parse(normalized_robots.splitlines())
    paths = ['/libraries/lib-status/' + str(i) for i in LIBRARIES]
    paths += ['/libraries/seats/', '/resources/image/layout/']
    allowed = all(policy.can_fetch(UA, BASE + p) for p in paths)
    save_json(DATA / 'source_metadata/korea_robots.json', {
        '출처URL': robots_url, '데이터기준일자': DATE,
        'robots.txt': robots, '수집허용': allowed,
        '상태': '허용' if allowed else '수집금지로 중단; 열람실/좌석 API 미호출',
    })
    if not allowed:
        # 이전 산출물이 현 수집 결과로 오인되지 않도록 차단 상태를 명시한다.
        save_json(DATA / 'source_metadata/korea.json', {'status':'blocked_by_robots','date':DATE,'rooms':[],'seats':[]})
        raise CollectionStopped('robots.txt 수집 금지. 고려대 API와 이미지는 요청하지 않았습니다.')
    rooms, seats = [], []
    for lib_id, library in LIBRARIES.items():
        room_url = BASE + '/libraries/lib-status/' + str(lib_id)
        for raw in payload(client, room_url):
            room_id = str(raw['code'])
            seat_url = BASE + '/libraries/seats/' + room_id
            if not policy.can_fetch(UA, seat_url):
                raise CollectionStopped('robots.txt 좌석 경로 수집 금지')
            image_url = urljoin(BASE, raw['bgImg']) if raw.get('bgImg') else None
            width = height = None
            if image_url:
                if not image_url.startswith(BASE + '/') or not policy.can_fetch(UA, image_url):
                    raise CollectionStopped('허용되지 않은 배치도 경로')
                width, height = download_image(client, image_url, '고려대학교', room_id)
            rooms.append(blank_room(**{
                '대학명':'고려대학교','도서관명':library,'열람실ID':room_id,
                '열람실명':raw['name'],'열람실영문명':raw.get('nameEng'),
                '전체좌석수':raw.get('cnt'),'좌석배정방식(지정석/자유석)':'지정석',
                '노트북사용가능여부(Y/N)':yn(raw.get('noteBookYN')),
                '운영시작시각':raw.get('startTm'),'운영종료시각':raw.get('endTm'),
                '1회최대이용시간(분)':raw.get('maxMi'),'배치도이미지URL':image_url,
                '배치도가로픽셀':width,'배치도세로픽셀':height,
                '좌석좌표제공여부(Y/N)':'Y','출처URL':room_url,
            }))
            for seat in payload(client, seat_url):
                # 허용 목록만 복사한다. 이용 현황 및 이용 시각 필드는 저장하지 않는다.
                seats.append({
                    '대학명':'고려대학교','도서관명':library,'열람실ID':room_id,
                    '좌석ID':str(seat['code']),'좌석번호':str(seat['name']),
                    'X좌표':seat.get('xpos'),'Y좌표':seat.get('ypos'),
                    '가로':seat.get('width'),'세로':seat.get('height'),
                    'PC좌석여부':yn(seat.get('pcSeatYN')),'좌석유형':None,
                    '출처URL':seat_url,'데이터기준일자':DATE,
                })
    save_json(DATA / 'source_metadata/korea.json', {'status':'collected_unverified_coordinates','date':DATE,'rooms':rooms,'seats':seats})


if __name__ == '__main__':
    try:
        collect()
    except CollectionStopped as exc:
        print('중단:', exc)
        raise SystemExit(2)
