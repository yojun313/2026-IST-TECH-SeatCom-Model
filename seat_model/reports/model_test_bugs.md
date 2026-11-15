# 모델 테스트 버그 목록

`seat_model/reports/model_test_report.md` 5장 견고성·일관성 검사에서 기대와 다르게 동작한 항목. 코드는 고치지 않았다.

| 코드 | 재현 입력 | 실제 동작 | 기대 동작 | 위치 | 심각도 |
|---|---|---|---|---|---|
| R1 | 후보 0개(전 좌석 점유) | 예외 IndexError: arrays used as indices must be of integer (or boolean) type | 빈 결과 + 사유 | seat_model/train/inference.py (`_block` → `candidate_block`: 빈자리 0개면 빈 인덱스 배열이 float형) | 높음(예외 중단) |
| R4 | 특징 결측(NaN): brightness_by_slot 1석 | 정상 반환: 추천 5개, 점수 NaN/inf 5개 | 오류 없이 처리, 결측 처리 방식 기록 | seat_model/train/data.py:137 `candidate_block` (빈자리 평균 중심화로 한 좌석의 NaN이 모든 좌석 점수에 전파) | 높음(추천 전체가 NaN 점수) |
| R4 | 특징 결측(NaN): airflow 1석 | 정상 반환: 추천 5개, 점수 NaN/inf 5개 | 오류 없이 처리, 결측 처리 방식 기록 | seat_model/train/data.py:137 `candidate_block` (빈자리 평균 중심화로 한 좌석의 NaN이 모든 좌석 점수에 전파) | 높음(추천 전체가 NaN 점수) |
| R4 | 특징 결측(NaN): outletAvailable 1석 | 정상 반환: 추천 5개, 점수 NaN/inf 5개 | 오류 없이 처리, 결측 처리 방식 기록 | seat_model/train/data.py:137 `candidate_block` (빈자리 평균 중심화로 한 좌석의 NaN이 모든 좌석 점수에 전파) | 높음(추천 전체가 NaN 점수) |
| R4 | 특징 결측(NaN): noise_index 1석 | 정상 반환: 추천 5개, 점수 NaN/inf 5개 | 오류 없이 처리, 결측 처리 방식 기록 | seat_model/train/data.py:137 `candidate_block` (빈자리 평균 중심화로 한 좌석의 NaN이 모든 좌석 점수에 전파) | 높음(추천 전체가 NaN 점수) |
| R4 | 특징 결측(null): airflow 1석 | 정상 반환: 추천 5개, 점수 NaN/inf 5개 | 오류 없이 처리, 결측 처리 방식 기록 | seat_model/train/data.py:137 `candidate_block` (빈자리 평균 중심화로 한 좌석의 NaN이 모든 좌석 점수에 전파) | 높음(추천 전체가 NaN 점수) |
| R5 | 범위 밖 값: deskWidth = inf | 정상 반환: 추천 5개, 점수 NaN/inf 5개 | 거부 또는 클리핑 | seat_model/train/data.py `candidate_block` (특징 범위 검사·클리핑 없음) | 경미~높음(inf면 전체 NaN) |
| R6 | 중복 좌석 ID | 정상 반환: 추천 5개 | 명시적 오류 | seat_model/train/inference.py `scenario_from_snapshot` (중복 seat_id를 검사하지 않음) | 경미 |
| R7 | 처음 보는 desk_type(bean_bag) | 예외 ValueError: 'bean_bag' is not in list | 오류 없이 처리 | seat_model/train/inference.py:53 `DESK_TYPES.index`, data.py `WEATHERS.index` | 높음(예외 중단) |
| R7 | 처음 보는 날씨(snow) | 예외 ValueError: 'snow' is not in list | 오류 없이 처리 | seat_model/train/inference.py:53 `DESK_TYPES.index`, data.py `WEATHERS.index` | 높음(예외 중단) |
| R8 | 관측 프로필 필드 누락: preferences 항목에 importance 없음 | 예외 KeyError: 'importance' | 기본값 경로 동작 | seat_model/train/data.py `UserPrefs.__init__` (`p["importance"]` 직접 접근) | 높음(예외 중단) |
| M4(경고) | clogit inf_x_brightness 계수 -0.0058 | 착석 기록으로 추정한 취향과 반대 방향(작은 값) | 0 이상 | seat_model/models/v1-20261115/clogit_b_no_exp.json | 경미 |
| M4(경고) | clogit inf_x_deskHeight 계수 -0.0227 | 착석 기록으로 추정한 취향과 반대 방향(작은 값) | 0 이상 | seat_model/models/v1-20261115/clogit_b_no_exp.json | 경미 |

재현: `seat_model/.venv/bin/python -m seat_model.model_test.service_suite` (robustness 함수가 base 세트 요청 하나로 입력을 만든다).
