# model_test: 유저 테스트 데이터셋 (v1, synth-1.0)

설정은 `seat_model/config/model_test.yaml` 한 곳에서 관리한다. 산출물은 `seat_model/exports/test_v1/`(git 제외)에 있다.

```bash
# 0) 고정 생성기 재현성 검사(학습 산출물과 세션 단위 비교) → 통과 시 학습 환경을 env/에 복사
seat_model/.venv/bin/python -m seat_model.model_test.reproduce --work <임시 폴더>
# 1) 테스트 사용자·요청·세트별 선택·평가용 참 효용 생성(결정적: 다시 돌려도 같은 파일)
seat_model/.venv/bin/python -m seat_model.model_test.generate
# 2) MongoDB user_test에 세트 하나 적재(세트를 바꿀 때마다 다시 실행)
seat_model/.venv/bin/python -m seat_model.model_test.load_user_test --set base
```

| 파일 | 역할 |
|---|---|
| `gen_v1_pinned.py` | 커밋 adb80af 생성기(synth-1.0) 원본 그대로. **수정 금지**(실행 시 git blob 해시로 확인) |
| `pinned.py` | 고정 생성기를 경로만 바꿔 불러온다. `data/libraries/synthetic`에는 쓰지 못하게 막는다 |
| `truth.py` | 정답 로직: `U_truth(λ) = (1−λ)·U_base + λ·U_new`, 모듈 A1·A2·A4·C1(효용), B1·B3(선택 과정) |
| `reproduce.py` | 재현성 검사 |
| `generate.py` | 데이터셋 생성 + 점검 리포트(`dataset_report.json`) |
| `load_user_test.py` | `user_test` DB 적재(다른 DB 이름이면 거부) |

## 테스트셋

모든 세트가 같은 사용자 2,000명, 같은 요청 109,230건(시각·열람실·점유·노출)을 공유한다. 세트마다 선택과 정답만 다르다.

| 세트 | λ | 모듈 | 의미 |
|---|---|---|---|
| `base` | 0 | – | 가정이 맞는 세계(고정 생성기 출력 그대로) |
| `base_resim` | 0 | – | 대조군 1: 정답은 base와 같고 선택 난수만 다름(선택 무작위성의 바닥값) |
| `base_lowtrust` | 0 | R | 대조군 2: 정답은 base, 추천 신뢰도만 25%로(R 효과 분리) |
| `form` | 1 | A1, R | 효용 형태만 변경(가우시안 이상점) |
| `c_a2` | 1 | +A2 | + 탈락 조건 |
| `c_a4` | 1 | +A4 | + 상황 상호작용 |
| `c_b` | 1 | +B1+B3 | + 고려집합·습관 |
| `real_0.25`~`real_1.00` | 0.25~1 | 전부(+C1 숨은 취향) | 현실형 세트, 혼합 강도별(추천 신뢰도도 λ에 따라 낮아짐) |

R(추천 신뢰도): `rec_trust × (1 − λ_R·(1 − 0.25))`. 노출 목록(현재 서비스 규칙)보다 참 효용을 따라 고르게 해서, 선택 라벨이 정답 변화에 반응하게 한다.
`dataset_report.json`의 `label_response`(선택이 세트 정답 1위를 따르는 비율 − base 정답 1위를 따르는 비율)로 반응 정도를 확인한다.

## 평가에서 쓰는 법

- 특징: `FeatureReader("user_test")`. 허용 목록은 학습과 같다(latent·events 접근 시 LeakageError).
- 정답: `sets/<세트>/eval_only/truth.parquet`의 `u_true`(후보 빈자리별). 노트북 금지 좌석은 −inf.
- 요청 단위 진단: `sets/<세트>/choices.parquet`(Oracle 좌석, 선택 좌석의 참 효용 순위, 재현 시드 `replay_seed`).
