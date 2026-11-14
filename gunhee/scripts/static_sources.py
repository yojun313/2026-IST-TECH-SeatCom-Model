"""공식 안내 및 사용자 제공 표의 정적 열람 공간. 개별 좌석을 합성하지 않는다."""
import re

from common import Client, CollectionStopped, DATA, DATE, blank_room, download_image, save_json

KAIST = 'https://library.kaist.ac.kr/libGuidance/libUse/fcltyGuidance.do'
CNU = 'https://library.cnu.ac.kr/webcontent/info/312'
CNU_ROWS = [
    ('CNU-B2','지하 2층 열람실','B2',377,'지정석','Y'),
    ('CNU-B1','지하 1층 열람실','B1',212,'자유석',None),
    ('CNU-1F-AUR','아우름(자유열람실)','1F',223,None,None),
    ('CNU-2F-1','제1열람실','2F',440,'지정석','N'),
    ('CNU-2F-2-G','제2열람실 일반','2F',272,'지정석','N'),
    ('CNU-2F-2-L','제2열람실 노트북','2F',32,'지정석','Y'),
    ('CNU-2F-3','제3열람실','2F',712,'지정석','N'),
]


def collect():
    client = Client()
    html = client.get(KAIST).text
    if '한방향열람실' not in html or '개인열람석' not in html:
        raise CollectionStopped('KAIST 안내 내용 변경: 수동 확인 필요')
    # 제공된 1~4층 안내도만 사용하며 다른 이미지 경로를 탐색하지 않는다.
    dimensions = {}
    for floor in range(1, 5):
        url = 'https://library.kaist.ac.kr/common/images/kor/library_map0%d.jpg' % floor
        dimensions[floor] = download_image(client, url, 'KAIST', 'KAIST-%dF' % floor)
    rooms = []
    for floor in (3, 4):
        for suffix, name in [('ONE','한방향열람실'), ('IND','개인열람석')]:
            room_id = 'KAIST-%dF-%s' % (floor, suffix)
            width, height = dimensions[floor]
            # 열람 공간별 로컬 파일도 제공하되 출처는 동일한 층별 입체 안내도다.
            source = DATA / 'images/KAIST' / ('KAIST-%dF.jpg' % floor)
            (source.parent / (room_id + '.jpg')).write_bytes(source.read_bytes())
            rooms.append(blank_room(**{
                '대학명':'KAIST','도서관명':'학술문화관(도서관)','열람실ID':room_id,
                '열람실명':name,'층':'%dF' % floor,
                '배치도이미지URL':'https://library.kaist.ac.kr/common/images/kor/library_map0%d.jpg' % floor,
                '배치도가로픽셀':width,'배치도세로픽셀':height,'출처URL':KAIST,
            }))
    cnu_html = client.get(CNU).text
    tables = re.findall(r'<table\b.*?</table>', cnu_html, flags=re.S | re.I)
    if not any(all(str(row[3]) in table for row in CNU_ROWS) for table in tables):
        raise CollectionStopped('충남대 표 변경: 사용자 제공 좌석수와 공식 표 대조 필요')
    for room_id, name, floor, count, assignment, laptop in CNU_ROWS:
        rooms.append(blank_room(**{
            '대학명':'충남대학교','도서관명':'도서관','열람실ID':room_id,
            '열람실명':name,'층':floor,'전체좌석수':count,
            '좌석배정방식(지정석/자유석)':assignment,
            '노트북사용가능여부(Y/N)':laptop,'출처URL':CNU,
        }))
    save_json(DATA / 'source_metadata/static.json', {'date':DATE,'rooms':rooms,'seats':[],
        'notes':['충남대 운영시간/아우름 운영 중단은 기간 한정 안내이므로 별도 문서 참조.',
                 'KAIST 좌석배정방식은 공식 층별 안내에서 확인되지 않아 미상. 그룹스터디룸은 제외.']})
    print('정적 열람 공간 %d개, 개별 좌석 0개' % len(rooms))


if __name__ == '__main__':
    try:
        collect()
    except CollectionStopped as exc:
        print('중단:', exc)
        raise SystemExit(2)
