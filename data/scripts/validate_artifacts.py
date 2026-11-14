"""저장소 루트 data/의 정적 추출 산출물을 네트워크 없이 점검한다."""
import csv
import json
import re
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FORBIDDEN={'state','seatTime','이용상태','사용좌석수','잔여좌석수','성별','색상','이용시간'}


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def validate():
    checks=[]
    for path in ROOT.rglob('*.json'):
        load(path)
    checks.append('모든 JSON 구문 정상')
    for path in ROOT.rglob('*.csv'):
        assert path.read_bytes().startswith(b'\xef\xbb\xbf'), path
        with path.open(encoding='utf-8-sig',newline='') as f:
            reader=csv.DictReader(f)
            columns=reader.fieldnames
            rows=list(reader)
        expected=load(path.with_suffix('.json'))
        assert len(rows)==len(expected), path
        assert columns[-1]=='데이터기준일자', path
        assert not FORBIDDEN.intersection(columns), path
        for a,b in zip(rows,expected):
            assert list(a)==list(b), path
            assert all(a[k]==('' if v is None else str(v)) for k,v in b.items()), path
            assert date.fromisoformat(a['데이터기준일자']).isoformat()==a['데이터기준일자']
        checks.append(f'{path.relative_to(ROOT)}: BOM/컬럼/전체값/JSON/날짜 일치')
    seats=load(ROOT/'seats/seongnam_central_A.json')
    report=load(ROOT/'validation/seongnam_central_A.json')
    frame=report['좌석도표시영역']
    assert len(seats)==report['검출사각형수']==report['표기전체좌석수']==169
    assert len({r['좌석번호'] for r in seats})==169
    assert len({r['기하객체ID'] for r in seats})==169
    assert all(re.fullmatch(r'A\d+',r['좌석번호']) for r in seats)
    for r in seats:
        x,y,w,h=(r[k] for k in ('X좌표','Y좌표','가로','세로'))
        assert 0<=x<x+w<=frame['width'] and 0<=y<y+h<=frame['height']
        assert r['캡처X']-frame['x']==x and r['캡처Y']-frame['y']==y
        for key,val in [('정규화X(0~1)',x/frame['width']),('정규화Y(0~1)',y/frame['height']),('정규화가로',w/frame['width']),('정규화세로',h/frame['height'])]:
            assert abs(r[key]-val)<1e-8
        assert r['공식좌석ID'] is None
    priority=[r['좌석번호'] for r in seats if r['검수상태']=='우선검수필요']
    assert priority==report['우선검수좌석'] and len(priority)==8
    checks.append('성남중앙169개: 고유번호/객체ID/좌표범위/변환/정규화/우선검수8개 일치')
    ns={'svg':'http://www.w3.org/2000/svg'}
    svg=ET.parse(ROOT/'maps/seongnam_central_A.svg').getroot()
    assert len(svg.findall('svg:g',ns))==169
    assert {n.text for n in svg.findall('.//svg:text',ns)}=={r['좌석번호'] for r in seats}
    checks.append('SVG169개 객체와 JSON좌석번호 일치')
    geometry=load(ROOT/'candidates/yongin_geometry.json')
    assert len(geometry)==1318
    assert len({r['기하객체ID'] for r in geometry})==1318
    assert all(r['공식좌석ID'] is None and r['좌석번호'] is None for r in geometry)
    assert all(r['가로']>0 and r['세로']>0 and r['X좌표']>=0 and r['Y좌표']>=0 for r in geometry)
    checks.append('용인1318개: 고유객체ID/양수크기/미확인 공식ID null 유지')
    libraries=load(ROOT/'catalog/library_feasibility.json')
    assert len(libraries)==len({r['도서관명'] for r in libraries})==27
    checks.append('도서관별 타당성27행/중복 없음')
    reference_rooms=load(ROOT/'catalog/reference_room_candidates.json')
    reference_libraries=load(ROOT/'catalog/reference_library_candidates.json')
    assert len(reference_rooms)==222 and len(reference_libraries)==91
    assert len({(r['법정동코드'],r['도서관ID'],r['열람실ID']) for r in reference_rooms})==222
    assert len({(r['법정동코드'],r['도서관ID']) for r in reference_libraries})==91
    assert sum(bool(r['좌석현황페이지후보URL']) for r in reference_rooms)==205
    checks.append('참고 저장소 후보91기관/222열람실/205주소/복합키 중복 없음')
    for markdown in [ROOT/'README.md',ROOT/'docs/HANDOFF.md',ROOT/'docs/OPENDATA_PLR.md']:
        for target in re.findall(r'\]\(([^)]+)\)',markdown.read_text(encoding='utf-8')):
            if '://' in target or target.startswith('#'):
                continue
            assert (markdown.parent/target.split('#')[0]).exists(),(markdown,target)
    checks.append('README/HANDOFF/참고 저장소 검토서 내부 링크 유효')
    output={'status':'passed','checks':checks,'limitations':[
        '무결성 통과는 OCR 실제 정확도·물리 좌표 정합성·현행성·서비스 이미지 이용조건을 입증하지 않음.',
        '169개 좌석 전수 눈검수와 용인1318개 공식ID 매핑은 미완료.',
        '에어컨·화장실 등의 시설 좌표를 아직 생성하지 않음.'
    ],'데이터기준일자':date.today().isoformat()}
    (ROOT/'validation/integrity.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(output,ensure_ascii=False,indent=2))


if __name__=='__main__':
    validate()
