# seat_model: 좌석 추천 모델 (v1 데이터, synth-1.0)

| 폴더 | 내용 |
|---|---|
| `docs/` | 작업 문서: `model_train.md`(v1 학습 명세), `model_train_v2.md`, `dataset_prompt.md`, `agent_prompt.md`, `library_db_schema.md` |
| `config/train.yaml` | 학습 설정(시드·분할·하이퍼파라미터) |
| `train/` | 파이프라인: `data` → `run --stage …` → `export` → `report`, 서비스용 `inference.py` |
| `tests/` | 누설·시간 순서·추론 테스트 |
| `models/v1-20261115/` | 서비스 모델(백엔드가 읽음): `manifest.json`, `clogit_*.json`, LightGBM, 군집 |
| `reports/` | `training_report.md`, 지표 JSON, 로그 |
| `exports/` | 학습 테이블·평가 결과(git 제외, 재생성 가능) |

```bash
seat_model/.venv/bin/python -m seat_model.train.data
seat_model/.venv/bin/python -m seat_model.train.run --stage baselines   # clogit | gbdt | online | clusters
seat_model/.venv/bin/python -m seat_model.train.export
seat_model/.venv/bin/python -m seat_model.train.report
seat_model/.venv/bin/python -m pytest seat_model/tests -q
```
배포: `deploy/README.md`
