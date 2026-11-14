# Dev-zeno/opendata_plr 참고 검토

확인일:2026-11-14. 사용자가 지정한 [저장소](https://github.com/Dev-zeno/opendata_plr)를 정적으로 검토했다. 검토 커밋은 `f1f4bc187d0e262815a25bae8d27ae12c9aa24cb`, 커밋 시각은2026-03-29 17:37:23 +09:00이다. 커밋 시각은 원본 데이터의 수집일이나 현황 기준시각을 의미하지 않는다. 서버·배포본을 실행하거나 인증키로 API를 호출하지 않았다.

## 실제로 활용할 부분

[public/library_data.json 원본](https://github.com/Dev-zeno/opendata_plr/blob/f1f4bc187d0e262815a25bae8d27ae12c9aa24cb/public/library_data.json)은 `stdgCd,pblibId,pblibNm,rdrmId,rdrmNm,rdrmUrl`을 가진222개 열람실 레코드다.

| 직접 집계 | 결과 |
|---|---:|
| 기관 복합키 `(stdgCd,pblibId)` | 91개 |
| 서로 다른 도서관 이름 | 88종 |
| 열람실 복합키 `(stdgCd,pblibId,rdrmId)` | 222개, 중복0 |
| URL 있는 열람실 | 205개 |
| URL 없는 열람실 | 17개 |
| URL 있는 기관 | 82개 |
| HTTP / HTTPS 링크 | 149 / 56 |
| jpg/png/svg/pdf 등 직접 이미지 URL | 0개 |
| 개별 좌석·시설 좌표 필드 | 없음 |

같은 이름의 중앙도서관 등을 합치지 않도록 **지역코드+기관ID**를 키로 사용한다. 91개는 이 파일에 존재하는 후보 기관 수이며, 전국 API가 현재 지원하는 모든 기관의 수가 아니다. `missing_seat_maps.md`에 적힌 URL 누락12건은 현재JSON의17건과 달라 그대로 인용하지 않는다. 별도32개 누락 목록도 현재 API 전수 응답을 입증하지 않는다.

기존 파일의 사실 메타데이터를 선별해 다음처럼 재구성했다.

- [기관별 후보91행](../catalog/reference_library_candidates.csv), [JSON](../catalog/reference_library_candidates.json)
- [열람실 링크 후보222행](../catalog/reference_room_candidates.csv), [JSON](../catalog/reference_room_candidates.json)

**링크205개는 접속하지 않았다.** 해당 주소가 현재 정상인지, 공식 링크인지, 번호가 보이는지, 좌표를 추출할 수 있는지, 시설도면이 있는지 전부 아직 미검토다. 기존27관의 직접 검토 결과와 동일한 수준의 ‘구축 가능’ 목록으로 취급하지 않는다. 후보 주소가 IP 기반 시스템 주소인 경우에도 공식 내비게이션과 연결 관계 및 접근정책부터 확인하고 숨은 경로를 추측하지 않는다.

## API 구현에서 참고할 점

[api/proxy-info.js](https://github.com/Dev-zeno/opendata_plr/blob/f1f4bc187d0e262815a25bae8d27ae12c9aa24cb/api/proxy-info.js)는 `/info_v2`, [api/proxy-reading-room.js](https://github.com/Dev-zeno/opendata_plr/blob/f1f4bc187d0e262815a25bae8d27ae12c9aa24cb/api/proxy-reading-room.js)는 `/rlt_rdrm_info_v2`를 호출한다. 이는 이번 조사에서 공식 Swagger로 확인한 경로와 일치한다. 그러나 코드에 엔드포인트가 있다는 사실은 실제 API 응답 가용성과 모든 도서관 지원을 입증하지 않는다.

새 수집기에서는 다음을 별도로 구현·검증해야 한다.

1. 본인에게 발급된 인증키를 환경변수에서 읽는다. 참고 저장소의 인증 값·환경 파일은 복사하거나 호출에 사용하지 않는다.
2. 공식 성공코드·본문 구조와 실응답을 확인한다. 참고 프록시는 `resultCode`를 검사하지 않는다.
3. 현재 프록시의 `pageNo=1,numOfRows=1000` 고정 호출을 전수 수집으로 간주하지 않는다. 전체 건수와 페이지 누락을 확인한다.
4. 원본 기준시각과 우리 수신시각을 구분한다. API가 성공하지 않으면 미상으로 처리한다.
5. 현재 static 데이터셋에는 현황 값을 저장하지 않는다. 이후 서비스가 잔여석을 사용하면 별도 현황 계층으로 분리한다.

## 그대로 가져오면 안 되는 동작

[App.tsx](https://github.com/Dev-zeno/opendata_plr/blob/f1f4bc187d0e262815a25bae8d27ae12c9aa24cb/src/App.tsx)를 정적 검토한 결과:

- mount 시 한 번 읽는 `useEffect`이며 정기 갱신 타이머는 확인되지 않는다. 실시간이라는 프로젝트 이름만으로 주기적 갱신을 보장할 수 없다.
- `lastUpdated = new Date()`는 원본 데이터 기준시각이 아닌 화면 처리 시각이다.
- 해당 기관의 `liveRooms`가 없으면 `availableSeats=totalSeats`로 처리한다. 새 서비스에서는 **미수신=null**로 둬야 한다.
- 실패 시 [constants.ts](https://github.com/Dev-zeno/opendata_plr/blob/f1f4bc187d0e262815a25bae8d27ae12c9aa24cb/src/constants.ts)의 `MOCK_LIBRARIES`로 대체한다. 이 예시 정원·잔여석·기관 속성을 실제 데이터로 저장하지 않는다.
- 수집 스크립트 주석에30분 주기가 있어도 `vercel.json`에서 cron 설정이 확인되지 않으므로 자동 수집이 운영 중이라고 단정하지 않는다.

명시적인 LICENSE 파일을 확인하지 못했다. 참고 저장소 전체, 코드, 빌드 파일, 환경 파일은 이 패키지에 복사하지 않았다. 이 패키지는 기관명·ID·주소라는 사실 메타데이터만 출처를 명시해 재구성했다. 코드나 웹이미지를 직접 재사용하기 전에는 해당 사용조건을 확인한다.

## 다음 에이전트의 확장 절차

1. `reference_library_candidates`에서 대상 기관을 하나 선택한다.
2. `(법정동코드,도서관ID)`로 `reference_room_candidates`의 열람실 링크를 찾는다.
3. 공식 도서관 페이지와 연결 관계, robots 및 원본 이용조건을 확인한다.
4. 공개 페이지가 좌석 버튼/이미지/SVG/캔버스 중 무엇인지 실제 확인한다.
5. 좌석번호·좌표·배경 프레임·시설 원본을 확인한 뒤 `library_feasibility`와 같은 판정 항목을 작성한다.
6. 직접 검토가 끝난 기관만 실제 구축 대상 목록으로 승격한다. 번호와 시설은 AI가 생성한 추측으로 채우지 않는다.
