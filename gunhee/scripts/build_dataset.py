"""수집 중간 결과를 CSV/JSON으로 합치고 누락·좌표·좌석수를 검증한다."""
import csv
import io
import json
from collections import Counter
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from PIL import Image

from common import DATA, ROOM_COLUMNS, SEAT_COLUMNS, FACILITY_COLUMNS, save_json


def export(name, columns, rows):
    path = DATA / (name + '.csv')
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction='raise', lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    save_json(DATA / (name + '.json'), [{c:row.get(c) for c in columns} for row in rows])
    assert path.read_bytes().startswith(b'\xef\xbb\xbf')
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == columns
        assert len(list(reader)) == len(rows)
    assert columns[-1] == '데이터기준일자'
    for row in rows:
        assert date.fromisoformat(row['데이터기준일자']).isoformat() == row['데이터기준일자']
        assert row.get('대학명') and row.get('열람실ID'), '필수 식별자 결측'
        for column in columns:
            if column.endswith('(Y/N)') or column == 'PC좌석여부':
                assert row.get(column) in (None, 'Y', 'N'), '잘못된 Y/N 값'
    return {column:sum(row.get(column) in (None, '') for row in rows) for column in columns}


def build():
    rooms, seats = [], []
    for source in ('static', 'korea'):
        path = DATA / 'source_metadata' / (source + '.json')
        if path.exists():
            payload = json.loads(path.read_text(encoding='utf-8'))
            rooms.extend(payload['rooms'])
            seats.extend(payload['seats'])
    if not rooms:
        raise ValueError('열람실 수집 결과가 없습니다. static_sources.py를 먼저 실행하세요.')
    room_map = {(r['대학명'],r['열람실ID']):r for r in rooms}
    assert len(room_map) == len(rooms), '열람실 ID 중복'
    assert len({(s['대학명'],s['열람실ID'],s['좌석ID']) for s in seats}) == len(seats), '좌석 ID 중복'
    coordinate_issues = []
    for seat in seats:
        room = room_map[(seat['대학명'],seat['열람실ID'])]
        for pos, dim, normalized in [('X좌표','배치도가로픽셀','정규화X(0~1)'),('Y좌표','배치도세로픽셀','정규화Y(0~1)')]:
            seat[normalized] = float(seat[pos]) / room[dim] if seat.get(pos) is not None and room.get(dim) else None
        w, h = room.get('배치도가로픽셀'), room.get('배치도세로픽셀')
        if not w or not h or any(seat.get(c) is None for c in ('X좌표','Y좌표','가로','세로')):
            coordinate_issues.append({'열람실ID':seat['열람실ID'],'좌석ID':seat['좌석ID'],'문제':'이미지 크기/좌표 결측'})
        elif not (0 <= float(seat['X좌표']) <= float(seat['X좌표']) + float(seat['가로']) <= w and
                  0 <= float(seat['Y좌표']) <= float(seat['Y좌표']) + float(seat['세로']) <= h):
            coordinate_issues.append({'열람실ID':seat['열람실ID'],'좌석ID':seat['좌석ID'],'문제':'사각형 이미지 범위 초과'})
    counts = Counter((s['대학명'],s['열람실ID']) for s in seats)
    comparisons = []
    for room in rooms:
        count = counts[(room['대학명'],room['열람실ID'])]
        expected = room['전체좌석수']
        status = ('좌표 미제공(비교 불가)' if room['좌석좌표제공여부(Y/N)'] != 'Y'
                  else '전체좌석수 미상' if expected is None else '일치' if count == int(expected) else '불일치')
        comparisons.append({'대학명':room['대학명'],'열람실ID':room['열람실ID'],'열람실명':room['열람실명'],
                            '전체좌석수':expected,'수집좌석행수':count,'검증결과':status,'데이터기준일자':room['데이터기준일자']})
    missing = {}
    for name, cols, rows in [('열람실표준데이터',ROOM_COLUMNS,rooms),('열람실좌석위치데이터',SEAT_COLUMNS,seats),('열람실시설위치데이터',FACILITY_COLUMNS,[])]:
        missing[name] = export(name, cols, rows)
    export('좌석수검증결과', list(comparisons[0]), comparisons)
    # 좌석이 실제로 수집된 열람실에만 미리보기를 만든다. 좌표 원점/스케일은 시각 검증 필요.
    preview_dir = DATA / 'preview'
    preview_dir.mkdir(exist_ok=True)
    for old in preview_dir.glob('*.png'):
        old.unlink()
    previews = []
    for key, room in room_map.items():
        selected = [s for s in seats if (s['대학명'],s['열람실ID']) == key]
        image = DATA / 'images' / room['대학명'] / (room['열람실ID'] + '.jpg')
        if not selected or not image.exists():
            continue
        with Image.open(image) as im:
            fig, ax = plt.subplots(figsize=(14, 8))
            ax.imshow(im, origin='upper')
            for seat in selected:
                ax.add_patch(Rectangle((float(seat['X좌표']),float(seat['Y좌표'])),float(seat['가로']),float(seat['세로']),
                                       linewidth=0.6, edgecolor='red',facecolor='none'))
            ax.set_xlim(0, im.width)
            ax.set_ylim(im.height, 0)
            ax.set_aspect('equal')
            path = preview_dir / (room['열람실ID'] + '.png')
            fig.savefig(path, dpi=150, bbox_inches='tight')
            plt.close(fig)
            previews.append(str(path.relative_to(DATA)))
        if len(previews) == 4:
            break
    summary = []
    for university in ('고려대학교','KAIST','충남대학교'):
        selected = [r for r in rooms if r['대학명'] == university]
        known = [r['전체좌석수'] for r in selected if r['전체좌석수'] is not None]
        summary.append({'대학명':university,'열람실수':len(selected),'수집좌석행수':sum(s['대학명'] == university for s in seats),
                        '알려진전체좌석수합계':sum(known) if known else None,
                        '좌표제공열람실비율':sum(r['좌석좌표제공여부(Y/N)'] == 'Y' for r in selected)/len(selected) if selected else None})
    save_json(DATA / 'validation.json', {'대학별요약':summary,'결측치수':missing,
        '좌석수불일치':[r for r in comparisons if r['검증결과'] == '불일치'],
        '좌표오류':coordinate_issues,'좌표미리보기':previews,
        '좌표정합성검증':'미수행: 좌석 좌표 없음' if not seats else '미리보기 수동 확인 필요',
        '인코딩및컬럼검증':'통과: UTF-8 BOM, 컬럼 순서, 마지막 날짜 컬럼, CSV 행 수 확인'})
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    build()
