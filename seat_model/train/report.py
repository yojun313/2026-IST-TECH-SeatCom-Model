"""10단계: 리포트 생성 → seat_model/reports/training_report.md

    seat_model/.venv/bin/python -m seat_model.train.report
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import kendalltau

from . import evaluate as E
from .common import load_config, write_json
from .userstate import SEAT_KEYS

M_SHOW = ["hit@1", "hit@5", "mrr", "ndcg@5", "oracle_hit@1", "exp_utility_pct"]
NAMES = {
    "random": "무작위", "entrance_nearest": "출입구 최근접", "popularity": "인기도", "service_rule": "현재 서비스 규칙",
    "equal_weight_rule": "동일 가중 규칙", "truth_upper_bound": "(상한) 참 효용",
    "clogit_a_full": "로짓 a 전체", "clogit_b_no_exp": "로짓 b 노출 제외", "clogit_g_no_profile": "로짓 g 취향 미입력(착석 기록만)",
    "clogit_h_profile_only": "로짓 h 관측 프로필만", "clogit_i_population": "로짓 i 개인화 없음",
    "clogit_a_full_with_logged_exposure": "로짓 a + 실제 노출(로그 재현, 참고)",
    "lgbm_a_full": "LightGBM a 전체", "lgbm_b_no_exp": "LightGBM b 노출 제외", "lgbm_g_no_profile": "LightGBM g 취향 미입력",
    "lgbm_h_profile_only": "LightGBM h 관측 프로필만", "xgb_a_full": "XGBoost a 전체",
}


def fmt(x, d=3):
    return "–" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{d}f}"


def table(rows, cols):
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" if i == 0 else "---:" for i in range(len(cols))) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def main():
    cfg = load_config()
    rep = Path(cfg["paths"]["reports"])
    exp = Path(cfg["paths"]["exports"])
    res = exp / "results"
    J = lambda n: json.loads((rep / n).read_text()) if (rep / n).exists() else {}
    data = json.loads((exp / "data_report.json").read_text())
    metrics = {**J("metrics_baselines.json"), **J("metrics_clogit.json"), **J("metrics_gbdt.json")}
    online = J("metrics_online.json")
    meta = pd.read_parquet(exp / "sessions.parquet")
    L = []
    L.append("# 좌석 추천 모델 학습 리포트 (v1 데이터, synth-1.0)\n")
    L.append("> 생성: `seat_model/.venv/bin/python -m seat_model.train.report`. 수치는 모두 합성 데이터 기준이다. "
             "학습 라벨은 `chosen_seat_id`만 썼고, 만족도(`satisfaction`, `ratingSubmitted`)는 특징·라벨·가중치·튜닝·선택 어디에도 쓰지 않았다.\n")

    # ---- 데이터
    L.append("## 1. 데이터\n")
    L.append(table([[k, v] for k, v in [
        ("세션(요청) 전체", f"{data['sessions_total']:,}"), ("학습·평가에 쓴 요청", f"{data['sessions_used']:,}"),
        ("후보 행(요청 × 빈자리 전체)", f"{data['rows']:,}"), ("특징 수", data["features"]),
        ("제외: 선택 없음", f"{data['excluded'].get('no_choice', 0):,}"), ("제외: 빈자리 1개 이하", f"{data['excluded'].get('avail_le_1', 0):,}"),
        ("사용자 train/val/test", f"{data['users']['train']}/{data['users']['val']}/{data['users']['test']}"),
        ("열람실 홀드아웃", ", ".join(data["room_holdout"])),
        ("요청당 빈자리 수 (중앙값 / 90% / 최대)", f"{data['n_avail_quantiles']['0.5']:.0f} / {data['n_avail_quantiles']['0.9']:.0f} / {data['n_avail_quantiles']['1']:.0f}"),
        ("추천 노출이 있었던 요청", f"{data['exposed_any_rate']:.1%}"), ("실제 선택이 추천 상위 5개 안", f"{data['chosen_in_exposed_rate']:.1%}"),
        ("필수 조건을 어긴 실제 선택", data["chosen_violates_requirements"]),
    ]], ["항목", "값"]))
    n_split = {s: int(E.split_mask(meta, s).sum()) for s in ("train", "val", "A", "B", "C", "train_C")}
    L.append("\n평가 집합 요청 수: " + ", ".join(f"{k} {v:,}" for k, v in n_split.items()) + "\n")

    # ---- 주 지표
    L.append("## 2. 모델 × 분할 × 지표\n")
    L.append("- HitRate·MRR: 실제 선택(`y_chosen`) 기준. NDCG@5·Oracle·예상 체험 효용: latent 참 효용 기준(평가 전용).\n"
             "- 모든 점수에 필수 조건 규칙 필터를 똑같이 적용했고, 노출 특징은 0(미노출)으로 두고 점수를 냈다.\n")
    for sp, title in (("A", "(A) 테스트 사용자"), ("B", "(B) 처음 보는 열람실"), ("C", "(C) 9월 학습 → 10월 평가"), ("val", "검증(모델 선택 기준)")):
        rows = []
        for m, v in metrics.items():
            if sp in v and isinstance(v[sp], dict):
                rows.append([NAMES.get(m, m)] + [fmt(v[sp].get(k)) for k in M_SHOW])
        if sp == "A":
            for m, v in (online.get("prequential_A") or {}).items():
                if m.startswith("online_"):
                    base = m[len("online_"):]
                    rows.append([f"온라인 학습: 로짓 {base} + 개인 계수 μ_u"] + [fmt(v.get(k)) for k in M_SHOW])
        L.append(f"### {title}\n")
        L.append(table(rows, ["모델"] + M_SHOW) + "\n")

    # ---- 부트스트랩
    L.append("## 3. 사용자 단위 부트스트랩 95% 신뢰구간 (A, 기준 = 현재 서비스 규칙)\n")
    mA = meta[E.split_mask(meta, "A")].reset_index(drop=True)
    per = {}
    for name in ["service_rule", "clogit_a_full", "clogit_b_no_exp", "clogit_g_no_profile", "clogit_h_profile_only",
                 "lgbm_a_full", "lgbm_b_no_exp", "lgbm_g_no_profile", "xgb_a_full"]:
        p = res / f"{name}__A.parquet"
        if p.exists():
            per[name] = pd.read_parquet(p)
    for name in ["online_i_population", "online_h_profile_only", "online_a_full", "online_g_no_profile", "online_b_no_exp"]:
        p = res / f"{name}__A_prequential.parquet"
        if p.exists():
            d = pd.read_parquet(p)
            if (d.session_id.values == mA.session_id.values).all():
                per[name] = d
    bs = E.bootstrap(per, mA.user_id.values, "service_rule", ("mrr", "hit@1", "ndcg@5", "oracle_hit@1"), cfg["eval"]["bootstrap"], cfg["seed"])
    rows = []
    for name, d in bs.items():
        r = [NAMES.get(name, name.replace("online_", "온라인: 로짓 "))]
        for m in ("mrr", "ndcg@5", "oracle_hit@1"):
            x = d.get(m)
            r.append(f"{x['mean']:.3f} [{x['ci'][0]:.3f}, {x['ci'][1]:.3f}]" if x else "–")
            r.append(f"{x['diff_vs_ref']:+.3f} [{x['diff_ci'][0]:+.3f}, {x['diff_ci'][1]:+.3f}]" if x else "–")
        rows.append(r)
    L.append(table(rows, ["모델", "MRR", "Δ MRR", "NDCG@5", "Δ NDCG@5", "Oracle@1", "Δ Oracle@1"]) + "\n")
    write_json(rep / "bootstrap_A.json", bs)

    # ---- 중요도 복원
    L.append("## 4. 그룹 중요도 복원\n")
    tu = pd.read_parquet(E.truth_path(cfg).parent / "truth_users.parquet")
    truth_imp = {k: float(tu[f"w_{k}"].mean()) if f"w_{k}" in tu else 0.0 for k in SEAT_KEYS}
    truth_imp["noise_index"] = float(0.5 * tu["noise_aversion"].mean())
    coefs = J("clogit_coefficients.json")
    shap = J("gbdt_shap.json").get("a_full", {})
    rows = []
    learned = {}
    for v in ("i_population", "a_full"):
        ct = {c["feature"]: c for c in coefs.get(v, [])}
        learned[v] = {}
        for k in SEAT_KEYS:
            imp = sum(abs(ct[c]["beta_std"]) for c in (f"seat_{k}", f"match_{k}", f"inf_x_{k}") if c in ct)
            learned[v][k] = imp
    shap_k = {k: sum(v for c, v in shap.items() if c.endswith("_" + k)) for k in SEAT_KEYS} if shap else {}
    for k in SEAT_KEYS:
        ct = {c["feature"]: c for c in coefs.get("i_population", [])}
        b = ct.get(f"seat_{k}", {})
        rows.append([k, fmt(truth_imp[k]), fmt(b.get("beta_std")), fmt(b.get("se")), fmt(learned["a_full"][k]),
                     fmt(shap_k.get(k)) if shap_k else "–"])
    L.append("- 로짓 i(개인화 없음)의 `seat_` 계수는 인구 평균 선호다. a 전체의 중요도는 `seat_`·`match_`·`inf_x_` 계수 절댓값(표준화) 합이다.\n")
    L.append(table(rows, ["좌석 특징", "latent 평균 중요도", "β_pop(i)", "SE", "a 중요도 합", "LightGBM |SHAP| 합"]) + "\n")
    ks = [k for k in SEAT_KEYS]
    tv = [truth_imp[k] for k in ks]
    taus = {f"로짓 a vs latent": kendalltau([learned["a_full"][k] for k in ks], tv).statistic}
    taus["로짓 i |β_pop| vs latent"] = kendalltau([learned["i_population"][k] for k in ks], tv).statistic
    if shap_k:
        taus["LightGBM SHAP vs latent"] = kendalltau([shap_k[k] for k in ks], tv).statistic
    L.append("켄달 타우: " + ", ".join(f"{k} = {v:.3f}" for k, v in taus.items()) + "\n")
    if shap:
        grp = {}
        for c, v in shap.items():
            g = c.split("_", 1)[0]
            grp[g] = grp.get(g, 0) + v
        tot = sum(grp.values())
        L.append("LightGBM a 그룹별 |SHAP| 비중: " + ", ".join(f"{g} {v / tot:.1%}" for g, v in sorted(grp.items(), key=lambda x: -x[1])) + "\n")

    # ---- 온라인 학습
    if online:
        L.append("## 5. 착석 기록으로 취향 파악 · 연속 반영 (11·12단계)\n")
        L.append("### 5.1 검증 분할로 고른 온라인 설정\n")
        L.append(table([[b, s["mode"], s["prior_var"], fmt(s["val_mrr_static"]), fmt(s["val_mrr"])] for b, s in online["selected"].items()],
                       ["전역 모델", "갱신 우도", "τ²", "검증 MRR 정적", "검증 MRR 온라인"]) + "\n")
        L.append("### 5.2 과거 착석 횟수별 성능 (A, 시간 순서 prequential)\n")
        cv = pd.DataFrame(online["curve_by_history"])
        for metric in ("mrr", "ndcg@5"):
            pv = cv.pivot_table(index="model", columns="hist_n", values=metric)
            order = ["0", "1-2", "3-5", "6-10", "11-20", "21-40", "41+"]
            pv = pv[[c for c in order if c in pv.columns]]
            rows = [[i] + [fmt(x) for x in r] for i, r in zip(pv.index, pv.values)]
            L.append(f"**{metric}**\n")
            L.append(table(rows, ["모델"] + list(pv.columns)) + "\n")
        nn = cv[cv.model == cv.model.iloc[0]].set_index("hist_n")["n"]
        L.append("구간별 요청 수: " + ", ".join(f"{k} {int(v):,}" for k, v in nn.items()) + "\n")
        L.append("### 5.3 취향 복원: 추정 μ_u와 latent 취향(방향 × 중요도)의 스피어만 상관 (테스트 사용자)\n")
        rec = online["recovery_spearman"].get("i_population", {})
        keys = [k for k in SEAT_KEYS if any(k in r for r in rec.values())]
        rows = []
        for n, r in rec.items():
            if r.get("_mean_rho") is None:
                continue
            rows.append([f"착석 {n}회 시점" if n != "final" else "마지막(전 기간)", r["_users"]] + [fmt(r.get(k), 2) for k in keys] + [fmt(r.get("_mean_rho"), 2)])
        obs = online.get("observed_profile_spearman", {})
        rows.append(["(비교) 관측 카카오톡 프로필", "–"] + [fmt(obs.get(k), 2) if k in obs else "없음" for k in keys] + ["–"])
        L.append("- 전역 모델 = 로짓 i(개인화 없음). 관측 프로필을 전혀 쓰지 않고 착석 기록만으로 추정한 값이다.\n")
        L.append(table(rows, ["시점", "사용자"] + keys + ["평균"]) + "\n")
        L.append("### 5.4 전역 모델 주간 웜스타트 재학습 vs 정적 모델(첫 2주 학습) — 테스트 사용자, 다음 주 요청\n")
        wk = pd.DataFrame(online["weekly_retrain"])
        pv = wk.pivot_table(index="week", columns="model", values="mrr")
        rows = [[w, int(wk[(wk.week == w)].n.iloc[0]), int(wk[(wk.week == w)].train_sessions.iloc[0])] + [fmt(x, 4) for x in r] for w, r in zip(pv.index, pv.values)]
        L.append(table(rows, ["주", "평가 요청", "학습 요청"] + [f"MRR {c}" for c in pv.columns]) + "\n")
        L.append("### 5.5 착석 기록으로 추정한 취향 프로필 예시 (테스트 사용자, 관측 프로필 미사용)\n")
        for u, p in list(online.get("example_inferred_profiles", {}).items())[:3]:
            items = ", ".join(f"{q['featureId']}→{q['target']:.0f} (중요도 {q['importance']:.2f}, 확신도 {q['confidence']:.2f})" for q in p["preferences"][:5])
            L.append(f"- `{u}` 착석 {p['n_sessions']}회: {items or '보고할 만큼 뚜렷한 취향 없음'}")
        L.append("")

    # ---- 세부 그룹
    main_name = "clogit_b_no_exp"
    p = res / f"{main_name}__A.parquet"
    if p.exists():
        L.append(f"## 6. 세부 그룹별 성능 ({NAMES[main_name]}, A)\n")
        ps = pd.read_parquet(p)
        arch = tu.set_index("user_id").archetype
        mA2 = mA.assign(archetype=mA.user_id.map(arch).values)
        cand_ctx = pd.read_parquet(exp / "candidates.parquet", columns=["session_id", "ctx_occ"],
                                   filters=[("session_id", "in", list(mA.session_id))]).drop_duplicates("session_id").set_index("session_id")
        mA2["occ"] = mA.session_id.map(cand_ctx.ctx_occ).values
        for col, bins, labels, title in (("archetype", None, None, "archetype(평가 전용)"),
                                         ("n_unknown", [0, 3, 5, 8], ["0~2", "3~4", "5+"], "관측 unknown 개수"),
                                         ("has_profile", None, None, "관측 프로필 유무"), ("laptop_req", None, None, "노트북 필수 조건"),
                                         ("occ", [0, 0.3, 0.6, 0.8, 1.01], ["<30%", "30~60%", "60~80%", "80%+"], "혼잡도"),
                                         ("n_avail", [0, 10, 30, 60, 1000], ["2~9", "10~29", "30~59", "60+"], "빈자리 수")):
            bt = E.bucket_table(ps, mA2, col, bins, labels)
            rows = [[i, int(r["n"])] + [fmt(r[m]) for m in ("hit@1", "hit@5", "mrr", "ndcg@5")] for i, r in bt.iterrows()]
            L.append(f"**{title}**\n")
            L.append(table(rows, [col, "n", "hit@1", "hit@5", "mrr", "ndcg@5"]) + "\n")

    sel = J("model_selection.json")
    if sel:
        L.append("## 7. 최종 모델과 선택 이유\n")
        L.append(sel.get("text", "") + "\n")
    L.append("## 8. 해당 없음(v1 데이터 한계)과 한계\n")
    L.append("- 해당 없음: 온보딩 설문 특징, laptop_carried, config 우선순위 제약(4단계 c·d·e), 결과 신호 가중치(7단계 W2·W3), "
             "IPS·오프폴리시 평가(노출 확률 없음: 로깅 정책이 관측 프로필 점수 상위 5개로 결정적), '노출 없는 선택' 지표.\n"
             "- 노출 편향: 실제 선택의 81%가 추천 상위 5개 안에서 나왔다. `y_chosen` 지표는 로깅 정책(현재 서비스 규칙)을 흉내 낼수록 높아진다. "
             "그래서 `y_chosen` 지표와 참 효용 지표의 순위가 다를 수 있고, 실제 서비스에서는 A/B 테스트로 확인해야 한다.\n"
             "- 합성 데이터: 참 효용이 생성기와 같은 선형 형태라 로짓에 유리하다. 실제 사용자 행동에서는 비선형 모델의 이점이 더 클 수 있다.\n"
             "- 숨은 취향 중 칸막이·출입구 거리는 카카오톡 프로필에 항목이 없다. 착석 기록 추정(5.3)으로만 드러난다.\n"
             "- 6단계(신경망, GPU)와 7단계 실험은 문서 규칙대로 이 결과를 보고한 뒤 승인받고 진행한다.\n")
    (rep / "training_report.md").write_text("\n".join(L), encoding="utf-8")
    print("written", rep / "training_report.md")


if __name__ == "__main__":
    main()
