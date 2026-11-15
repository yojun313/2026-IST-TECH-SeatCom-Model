"""수신한 공식 카탈로그와 문서 확인 자원을 CSV/Markdown으로 정리한다."""
import csv
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT.parent if ROOT.name == '도서관_데이터셋_조사_근거' else ROOT
DATE = '2026-11-14'
raw = json.loads((ROOT / 'library_dataset_metadata.json').read_text())
extra = json.loads((ROOT / 'library_dataset_supplement.json').read_text())

def category(name, ident):
    if ident in {'15049230', '15151627'}:
        return '이용정책·대출조건'
    if re.search('좌석|열람실', name):
        return '좌석·공간 이용'
    if re.search('장애|대체자료|DeepQA|점역', name):
        return '장애인·접근성'
    if re.search('장난감|물품|실험장비', name):
        return '물품·장비 대여'
    if re.search('공공개방|공유플랫폼|공유자원|시설달력', name):
        return '공간·시설 공유'
    if re.search('설문|실태조사|이용자 현황', name):
        return '전국 통계·조사'
    if re.search('희망|납품|도서구매', name):
        return '장서수요·구매'
    if re.search('추천|큐레이션|인기도서|인기대출|베스트|신간|신착', name):
        return '추천·신간·큐레이션'
    if re.search('대출|대여현황', name):
        return '대출·이용 이력'
    if re.search('동아리|강좌|강사|프로그램|독서교실|영화상영|문화학교|행사', name):
        return '프로그램·동아리·학습'
    if re.search('OAK|국가학술|ISBN|기록원|관보|서지|웹DB', name):
        return '디지털 아카이브·연구자료'
    return '시설·운영·접근성'

USE = {
    '이용정책·대출조건': '기관별 대출·연장·변상 조건 비교와 이용정책 안내',
    '좌석·공간 이용': '혼잡도 분석·이석/취소 패턴 분석·좌석 안내',
    '장애인·접근성': '접근 가능한 자료 탐색·대체자료 수요와 공급 분석',
    '물품·장비 대여': '책 이외 자원의 검색·대여조건 비교·수요 분석',
    '공간·시설 공유': '모임 공간·공유자원 탐색과 이용조건 비교',
    '전국 통계·조사': '지역별 격차·이용자 요구·독서환경 분석',
    '장서수요·구매': '희망도서 수요·구입 우선순위·조달 흐름 분석',
    '추천·신간·큐레이션': '주제·대상별 추천과 뉴스-도서 연결',
    '대출·이용 이력': '자료 이용 추이·수요·연체/반납 관련 지표 분석',
    '프로그램·동아리·학습': '시간·대상·관심사 기반 프로그램/모임 탐색',
    '디지털 아카이브·연구자료': '지식 검색·지역 기록 탐색·서지 연결',
    '시설·운영·접근성': '방문계획·휴관 안내·도서관 지도·운영 분석',
    '도서관 정보나루 API': '도서 검색·소장/대출 집계·추천·이슈 연결',
    'AI 학습·문서처리': '요약·정보추출 모델 실험',
    '해외 서지·추천·독서': '추천 모델 실험·서지/저자/주제 연결',
    '해외 도서관 운영': '대출·장서 변화와 이용 수요 분석',
    '뉴스·이슈 연계': '뉴스 주제와 도서·모임·지역 서비스를 연결',
}

NOTES = {
    '15049662': '원 운영시스템이 2024년 종료되어 과거 데이터만 제공. 현재 인기순위가 아님.',
    '15049663': '원 운영시스템이 2024년 종료되어 과거 데이터만 제공. 현재 보유목록이 아님.',
    '15146813': '카탈로그는 코로나19 이후 프로그램 중단을 명시. 과거 상영 일정 분석용.',
    '15142580': '공식 문서는 실시간으로 안내. 참여 지자체/도서관 범위와 개별 응답의 기준시각은 실제 호출로 확인해야 함.',
    '15171450': '좌석 예약 이력은 2017~2024년이며 동백·흥덕도서관 기록. 실시간 빈자리 데이터 아님.',
    '15154878': '좌석·열람실 ID와 좌석명 등 기본정보. 실제 점유·잔여좌석을 의미하지 않음.',
    '15078215': '좌석 기본정보 스냅샷. 설명의 실시간 활용 예시만으로 실시간 API라고 판단하지 않기.',
    '15063649': '일자별 이용 통계와 강제종료 집계. 현재 빈자리 API와 구분.',
    '15137510': '공개 필드는 질문관리번호·질문·질문유형 등 설문 문항 중심. 응답결과·개인별 만족도 데이터라고 단정하지 않기.',
    '15049230': '이름은 콘텐츠대출정보지만 실제 필드는 최대대출권수·대출기간·연기횟수·예약건수 등 정책값. 대출이력 아님.',
    '15151627': '책나래 참여기관의 파손·분실 정책 문구. 일반 대출정책 전체가 아님.',
    '15146809': '홈페이지 콘텐츠 관리 메타데이터. 도서 본문·동아리 참여자·콘텐츠 열람기록이 아님.',
    '15137811': '배너명·링크 등 사이트 운영정보. 클릭/행동 로그가 아님.',
    '15171694': '도서관 제공 웹DB 목록. 해당 유료 DB의 실제 콘텐츠가 공개되는 것은 아님.',
    '15168714': '전자책 서지·소장 목록. 전자책 원문 이용권이나 실시간 대출정보가 자동 제공되는 것은 아님.',
    '15152499': '북큐레이션 오디오 관련 정보. 실제 음원 파일·재배포 허용 여부는 별도 확인.',
    '15044574': '2015년 운영현황 등 과거 스냅샷. 현재 프로그램 접수정보로 쓰지 않기.',
    '15051844': '2016년 기준 파일명. 현재 장난감도서관 위치·운영은 재확인 필요.',
    '15153049': '카탈로그 현재 명칭은 대출현황정보, 과거 파일명은 대출이력. 실제 공개 컬럼에 맞춰 분석.',
    '15156344': '대체자료 서지 목록. 일반 이용자의 원문 다운로드 권한과 구분.',
    '15095893': 'DeepQA 관련 데이터의 공개 범위·실제 파일 구성 확인 후 사용.',
    '15163292': 'DeepQA 구축 관련 데이터. 다른 DeepQA 항목과 중복 범위 확인.',
    '15104976': '국립어린이청소년도서관 사서추천도서 파일자료와 원천이 겹칠 수 있음.',
    '15151124': '국립어린이청소년도서관 사서추천도서 API와 원천이 겹칠 수 있음.',
    '15125914': '업무추진비 집행자료이며 도서관 전체 예산·전체 도서구입비 자료는 아님.',
    '15162792': '교통약자 이동편의 조사. 도서관 직접 자료가 아니라 방문 접근성 분석 보조자료.',
    '15129449': '청소년 공간·프로그램과 도서관 커뮤니티를 연결하는 보조자료.',
    '15090075': '평생학습 강사 관리정보. 공개 인력목록이지 실시간 상담·예약 가능 명단이 아님.',
}

def plain(value):
    if value is None:
        return ''
    if isinstance(value, str):
        return re.sub(r'\s+', ' ', value).strip()
    if isinstance(value, list):
        return '; '.join(plain(v) for v in value)
    if isinstance(value, dict):
        return plain(value.get('name') or value.get('value') or value.get('url') or '')
    return str(value)

def relation(cat):
    if cat in {'物品', '물품·장비 대여'}:
        return '기능 확장: 수집·분류·대여·반납'
    if cat == '공간·시설 공유':
        return '기능 확장: 공용공간·예약·커뮤니티'
    if cat in {'디지털 아카이브·연구자료', '해외 서지·추천·독서', 'AI 학습·문서처리'}:
        return '기능 연결: 자료 수집·분류·보존·검색·추천'
    if cat == '뉴스·이슈 연계':
        return '보조 연결: 시사자료·주제 탐색·커뮤니티'
    return '도서관 직접 또는 독서·학습 관련'

rows = []
excluded = []
for source in raw:
    d = source.get('metadata', {})
    if not d.get('name') or '해당 데이터는 존재하지 않습니다' in plain(d.get('description')):
        excluded.append(source)
        continue
    ident = source['id']
    name = plain(d.get('name')) or source['title']
    cat = category(name, ident)
    properties = {v.get('name'): plain(v.get('value')) for v in d.get('additionalProperty', []) if isinstance(v, dict)}
    fields = '; '.join(plain(v.get('name')) for v in d.get('variableMeasured', []) if isinstance(v, dict))
    description = plain(d.get('description'))
    if not fields:
        fields = '카탈로그 개요: ' + description[:180]
    access = {'openapi': 'Open API', 'standard': '표준데이터; 파일/API 제공 탭 확인', 'fileData': '파일데이터'}.get(source['type'], source['type'])
    notes = NOTES.get(ident, '공개 시점·지역·실제 컬럼 확인. 파일데이터는 실시간 상태로 간주하지 않기.')
    rows.append({
        '분류': cat, '자료명': name, '제공기관': plain(d.get('creator')), '도서관과의연결': relation(cat),
        '제공방식': access, '형식': plain(d.get('encodingFormat')), '주요항목_또는_개요': fields,
        '추천활용_분석제안': USE.get(cat, ''), '갱신주기': properties.get('업데이트 주기', ''),
        '카탈로그수정일': plain(d.get('dateModified')), '접근조건': '활용신청·인증키 필요; 공식 상세문서 확인' if source['type']=='openapi' else '파일 다운로드 경로·로그인 여부는 상세페이지 확인',
        '이용허락범위': plain(d.get('license')), '확인수준': source['check'], '주의사항': notes,
        '상세URL': plain(d.get('url')) or f"https://www.data.go.kr/data/{ident}/{source['type']}.do",
        '메타데이터URL': source['metadata_url'], 'API경로': '', '확인일': DATE,
    })

for item in extra:
    cat = item['category']
    rows.append({
        '분류': cat, '자료명': item['name'], '제공기관': item['provider'], '도서관과의연결': relation(cat),
        '제공방식': item['access'], '형식': '공식 문서 확인', '주요항목_또는_개요': item['fields'],
        '추천활용_분석제안': USE.get(cat, ''), '갱신주기': '원문 확인', '카탈로그수정일': '',
        '접근조건': item['access'], '이용허락범위': '원문 라이선스·이용조건 확인', '확인수준': item['checked'],
        '주의사항': item['notes'], '상세URL': item['url'], '메타데이터URL': '', 'API경로': item['endpoint'], '확인일': DATE,
    })

groups = ['시설·운영·접근성','좌석·공간 이용','이용정책·대출조건','대출·이용 이력','장서수요·구매','추천·신간·큐레이션','프로그램·동아리·학습','장애인·접근성','공간·시설 공유','물품·장비 대여','전국 통계·조사','도서관 정보나루 API','AI 학습·문서처리','디지털 아카이브·연구자료','해외 서지·추천·독서','해외 도서관 운영','뉴스·이슈 연계']
rows.sort(key=lambda r: (groups.index(r['분류']), r['자료명']))
for idx, row in enumerate(rows, 1):
    row['번호'] = idx
columns = ['번호'] + [key for key in rows[0] if key != '번호']
with (OUTPUT / '도서관_관련_데이터셋_목록.csv').open('w', encoding='utf-8-sig', newline='') as stream:
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
(OUTPUT / '도서관_관련_데이터셋_목록.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))

def esc(value):
    return str(value).replace('|', '\\|').replace('\n', ' ')

counts = Counter(row['분류'] for row in rows)
lines = [
    '# 도서관 관련 데이터셋·API·공개자료 목록', '',
    f'조사일: {DATE} / 총 {len(rows)}개 조회·다운로드 자원 / 공공데이터포털 유효 카탈로그 {len(raw)-len(excluded)}건', '',
    '도서관 직접 자료뿐 아니라 독서·학습, 공용공간, 물품 대여, 디지털 아카이브, 뉴스까지 포함했습니다. 자료 단위는 독립 데이터셋만이 아니라 API 세부 조회, 덤프, 통계표도 포함합니다. 동일 원천의 파일/API, 통합조회/세부조회는 중복될 수 있어 서로 독립적인 표본 수로 합산하면 안 됩니다.', '',
    '## 확인 범위', '',
    f'- 공공데이터포털 {len(raw)}건의 카탈로그 JSON을 수신해 대조했고, 존재하지 않는다고 응답한 {len(excluded)}건을 제외한 {len(raw)-len(excluded)}건을 목록에 넣었습니다. 원본 파일 전체나 인증 API가 실제 동작함을 보증하는 검증은 아닙니다.',
    '- 나머지 자원: 공식 상세페이지·개발 문서에서 제공 경로를 확인했습니다. 인증된 API 호출이나 원본 파일 전체 다운로드를 완료했다는 뜻은 아닙니다.',
    '- CSV/JSON에는 실제 공개 컬럼, 접근조건, 갱신주기, 주의사항, 출처 URL을 넣었습니다. 활용 아이디어는 조사자의 제안입니다.',
    '- 카탈로그 수정일은 데이터의 기준일과 다를 수 있습니다. 서비스 소개의 활용 예시를 실제 제공 컬럼으로 간주하지 않았습니다.',
    '- 이름에 대출·예약·설문이 들어가도 실제 데이터는 정책값·좌석 기본정보·설문 문항만일 수 있습니다.',
    '- 공개 메타데이터와 책/기사/영화/음원 원문의 이용권은 별개입니다. CSV의 원문 이용허락범위를 함께 확인하세요.', '',
    '## 먼저 볼 만한 자료', '',
    '| 용도 | 추천 데이터 | 확인한 내용 |', '|---|---|---|',
    '| 실제 빈자리 안내 | [전국 공공도서관 열람실 실시간 API](https://www.data.go.kr/data/15142580/openapi.do) | 잔여좌석·운영현황. 참여범위·응답시각 확인 필요 |',
    '| 예약 취소·이석 분석 | [용인시 열람실 그룹·좌석 예약](https://www.data.go.kr/data/15171450/fileData.do) | 2017~2024년, 동백·흥덕도서관 이력 |',
    '| 정책 비교 | [인천 통합전자도서관 콘텐츠대출정보](https://www.data.go.kr/data/15049230/fileData.do) | 대출권수·기간·연장·예약 정책값 |',
    '| 정책 문구 검색 | [책나래 대출 파손 정책](https://www.data.go.kr/data/15151627/fileData.do) | 참여기관별 파손·분실 대응 규정 |',
    '| 희망도서 수요 | [과천시 비치희망자료](https://www.data.go.kr/data/15144821/fileData.do) | 신청일·도서·가격 등 |',
    '| 커뮤니티 매칭 | [광산구 도서관 동아리](https://www.data.go.kr/data/15062365/fileData.do) | 동아리·대상·시간·장소·활동분야 |',
    '| 일정·헛걸음 방지 | [용인시 행사·일정 달력](https://www.data.go.kr/data/15171148/fileData.do) | 휴관·프로그램·행사·메이커스페이스 등 일정 |',
    '| 공간 공유 | [공유누리 API](https://www.eshare.go.kr/OpenApi/Info/list.do) | 회의실·강당·교육·물품·장비 등 |',
    '| 도서 추천 | [goodbooks-10k](https://github.com/zygmuntz/goodbooks-10k) | 도서·평점·태그·읽을목록. 과거 연구 데이터 |',
    '| 뉴스에서 독서로 | [정보나루 API](https://www.data4library.kr/apiUtilization) | 키워드·도서검색·소장·추천·이용집계 |', '',
    '## 분류별 건수', '', '| 분류 | 자원 수 |', '|---|---:|',
]
lines.extend(f'| {cat} | {counts[cat]} |' for cat in groups if counts[cat])
lines += ['', '## 전체 목록', '', '각 항목의 링크는 검색결과 페이지가 아닌 공식 상세페이지·공식 개발 문서입니다. 정보나루 세부 API 등은 동일한 공식 문서로 연결되며 CSV/JSON의 API경로에서 구분할 수 있습니다.', '']
for cat in groups:
    subset = [row for row in rows if row['분류'] == cat]
    if not subset:
        continue
    lines += [f'### {cat} ({len(subset)}개)', '', '| 번호 | 자료·제공처 | 방식 | 주요항목·개요 | 주의사항 |', '|---:|---|---|---|---|']
    for row in subset:
        overview = row['주요항목_또는_개요']
        if len(overview) > 210:
            overview = overview[:210] + '… (CSV/JSON 참조)'
        lines.append(f"| {row['번호']} | [{esc(row['자료명'])}]({row['상세URL']})<br>{esc(row['제공기관'])} | {esc(row['제공방식'])} | {esc(overview)} | {esc(row['주의사항'])} |")
    lines += ['']
lines += [
    '## 데이터 결합 아이디어', '',
    '| 서비스 방향 | 결합할 데이터 | 만들 수 있는 최소 기능 |', '|---|---|---|',
    '| 뉴스 기반 지역 독서모임 | 뉴스 + 도서 키워드 + 동아리/강좌 + 대관시설 | 뉴스 주제와 관련된 책·모임·장소 탐색 |',
    '| 헛걸음 없는 도서관 | 도서관표준 + 실시간 열람실 + 휴관/행사 달력 | 지금 이용 가능한 도서관과 확인시각 안내 |',
    '| 읽고 싶은 책을 빨리 찾기 | 서지/ISBN + 소장도서관 + 희망도서 + 대출 집계 | 소장 경로·대체 도서 제안. 예약 확정은 별도 연동 필요 |',
    '| 물건 도서관 | 공유누리 물품/장비 + 장난감 분류 + 대여소 | 필요한 물건의 제공처·조건 비교 |',
    '| 이용정책 길잡이 | 표준데이터 대출조건 + 전자도서관 정책 + 책나래 파손규정 | 상황별 적용 규정과 원문 출처 표시 |',
    '| 도서관 접근성 지도 | 도서관 좌표 + 장애인 자료/시설 + 이동편의 조사 | 이용 가능한 자료·시설·방문 장벽 안내 |', '',
    '## 이번 수집에서 확보했다고 볼 수 없는 것', '',
    '- 전국 모든 도서관의 개인별 독서이력·회원정보·예약대기열.',
    '- 공개 좌석 기본정보만으로 파악하는 실제 착석·장기이석 여부.',
    '- 강좌 목록만으로 파악하는 실제 참여율·노쇼·실시간 잔여정원.',
    '- 설문 문항 목록만으로 파악하는 이용자의 만족도·서술형 응답.',
    '- 서지·콘텐츠 목록에 실린 책·기사·영화·음원 전체의 자유로운 원문 이용권.', '',
    '## 추가 탐색 경로', '',
    '- [공공데이터포털](https://www.data.go.kr/): 도서관 + 대출이력/희망도서/좌석예약/휴관일/독서동아리/대체자료/분실정책.',
    '- [도서관 정보나루](https://www.data4library.kr/): 장서/대출 파일, 테마자료와 공모전 자료의 공개 범위 확인.',
    '- [국가도서관통계시스템](https://www.mcst.go.kr/libsta/): 관종·지역·연도별 통계와 결과표.',
    '- [서울 열린데이터광장](https://data.seoul.go.kr/), [경기데이터드림](https://data.gg.go.kr/): 지역별 세부 자료.',
    '- [공유누리](https://www.eshare.go.kr/OpenApi/Info/list.do): 공용공간·물품·장비·교육 자원.',
    '- [문화빅데이터 국립중앙도서관 센터](https://www.bigdata-culture.kr/bigdata/user/data_market/agency/detail.do?id=center_nl_org): 협약 종료로 업데이트·상담 중단 안내가 있으므로 최신자료는 원 제공처와 비교.',
]
(OUTPUT / '도서관_관련_데이터셋_목록.md').write_text('\n'.join(lines) + '\n')
assert len(rows) == len(raw) + len(extra) - len(excluded)
assert len({str(row['번호']) for row in rows}) == len(rows)
assert all(row['상세URL'].startswith('https://') for row in rows)
assert all(row['자료명'] and row['분류'] and row['확인수준'] for row in rows)
print(json.dumps({'total':len(rows),'official_catalogs':len(raw),'by_category':dict(counts)}, ensure_ascii=False, indent=2))
