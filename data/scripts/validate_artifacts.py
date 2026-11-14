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
    # library dataset CSVs have table-specific Korean headers and serialize
    # nested JSON cells. Validate those against each bundle below instead of
    # comparing their Korean columns to the snake_case JSON keys here.
    for path in ROOT.rglob('*.csv'):
        if (ROOT/'libraries') in path.parents:
            continue
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
    # Validate the per-library training bundles and their Korean CSV copies.
    library_root=ROOT/'libraries'
    schema=load(ROOT/'docs/target_schema.json')
    try:
        import jsonschema
        validator=jsonschema.Draft202012Validator(schema)
    except ImportError:
        raise RuntimeError('도서관 번들 스키마 검증에는 jsonschema 설치가 필요합니다.')
    tables=['libraries','rooms','maps','seats','facilities','room_features','seat_features',
            'walkway_nodes','walkway_edges','map_transforms','sources']
    csv_headers={}
    library_count=0
    library_totals={table:0 for table in tables}
    banned_data_keys={'state','seatTime','occupancy','occupied','reserved_by','current_user','seat_status'}
    def inspect_keys(value):
        if isinstance(value,dict):
            assert not (banned_data_keys & set(value)), banned_data_keys & set(value)
            for nested in value.values(): inspect_keys(nested)
        elif isinstance(value,list):
            for nested in value: inspect_keys(nested)
    for directory in sorted(library_root.iterdir()):
        bundle_path=directory/'dataset.json'
        if not directory.is_dir() or not bundle_path.exists():
            continue
        bundle=load(bundle_path); library_count+=1
        assert bundle['schema_version']=='1.1', bundle_path
        assert len(bundle['libraries'])==1 and bundle['libraries'][0]['library_id']==directory.name, bundle_path
        validator.validate(bundle)
        inspect_keys(bundle)
        maps={record['map_id']:record for record in bundle['maps']}
        rooms={record['room_id']:record for record in bundle['rooms']}
        seats={record['seat_id']:record for record in bundle['seats']}
        sources={record['source_id'] for record in bundle['sources']}
        nodes={record['node_id']:record for record in bundle['walkway_nodes']}
        for table in tables:
            records=bundle[table]
            library_totals[table]+=len(records)
            assert all(record.get('library_id')==directory.name for record in records), (directory.name,table,'library_id')
            assert load(directory/f'{table}.json')==records, directory/f'{table}.json'
            csv_path=directory/f'{table}.csv'
            assert csv_path.read_bytes().startswith(b'\xef\xbb\xbf'), csv_path
            with csv_path.open(encoding='utf-8-sig',newline='') as f:
                reader=csv.reader(f); header=next(reader); row_count=sum(1 for _ in reader)
            assert header[-1]=='데이터기준일자', csv_path
            assert csv_headers.setdefault(table,header)==header, (table,header)
            assert row_count==len(records), csv_path
        for seat in bundle['seats']:
            box=seat['bbox']; map_record=maps[seat['map_id']]
            assert box['width']>0 and box['height']>0
            assert 0<=box['x'] and 0<=box['y'] and box['x']+box['width']<=map_record['width']+1e-6 and box['y']+box['height']<=map_record['height']+1e-6, (directory.name,seat['seat_id'],'bbox')
            assert seat['room_id'] in rooms and seat['source_id'] in sources
        for facility in bundle['facilities']:
            map_record=maps[facility['map_id']]
            geometry=facility['geometry']
            points=[geometry['coordinates']] if geometry['type']=='Point' else geometry['coordinates']
            for point in points:
                assert 0<=point[0]<=map_record['width'] and 0<=point[1]<=map_record['height'], (directory.name,facility['facility_id'],'geometry')
            assert facility['source_id'] in sources
        for feature in bundle['room_features']:
            assert feature['room_id'] in rooms and feature['source_id'] in sources
        for feature in bundle['seat_features']:
            assert feature['seat_id'] in seats and feature['source_id'] in sources
        for node in bundle['walkway_nodes']:
            assert node['map_id'] in maps and node['source_id'] in sources
        for edge in bundle['walkway_edges']:
            assert edge['from_node_id'] in nodes and edge['to_node_id'] in nodes and edge['source_id'] in sources
        for transform in bundle['map_transforms']:
            assert transform['from_map_id'] in maps and transform['to_map_id'] in maps and transform['source_id'] in sources
    assert library_count==27, library_count
    checks.append('도서관27관: schema 1.1/배열·개별JSON/일관된 CSV헤더·BOM·행수·library_id/상태필드 제외 확인')
    checks.append('도서관27관: 좌석 bbox 범위, 시설 좌표 범위, 열람실·좌석·지도·출처 외래키 확인')
    for markdown in [ROOT/'README.md',ROOT/'BUILD_STATUS.md',ROOT/'docs/HANDOFF.md',ROOT/'docs/OPENDATA_PLR.md']:
        for target in re.findall(r'\]\(([^)]+)\)',markdown.read_text(encoding='utf-8')):
            if '://' in target or target.startswith('#'):
                continue
            assert (markdown.parent/target.split('#')[0]).exists(),(markdown,target)
    checks.append('README/HANDOFF/참고 저장소 검토서 내부 링크 유효')
    output={'status':'passed','checks':checks,'library_dataset_counts':library_totals,'limitations':[
        '무결성 통과는 OCR 실제 정확도·물리 좌표 정합성·현행성·서비스 이미지 이용조건을 입증하지 않음.',
        '모든 좌석 2111건과 시설 73건은 model_candidate이며 현장/사람 검수 완료가 아님.',
        '일부 기관은 지도 이미지·좌석번호·시설도·위경도·실시간 좌석 공급이 미확보.'
    ],'데이터기준일자':date.today().isoformat()}
    (ROOT/'validation/integrity.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(output,ensure_ascii=False,indent=2))


if __name__=='__main__':
    validate()
