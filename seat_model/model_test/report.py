"""7단계: 통과 기준 판정, 그래프 5장, 리포트·요약 JSON·버그 목록.

    seat_model/.venv/bin/python -m seat_model.model_test.report

입력: exports/test_v1/{dataset_report.json, eval/offline_summary.json, eval/base_slices.parquet,
      eval/service/service_summary.json, eval/service/latency.parquet, eval/consistency.json}
출력: seat_model/reports/{model_test_report.md, model_test_summary.json, model_test_bugs.md, figures/*.png}
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .pinned import REPO, load_config

MODELS = [
    "clogit_b_no_exp",
    "clogit_a_full",
    "lgbm_b_no_exp",
    "service_rule",
    "popularity",
    "entrance_nearest",
    "random",
]
LABEL = {
    "clogit_b_no_exp": "clogit(서비스)",
    "clogit_a_full": "clogit a_full",
    "lgbm_b_no_exp": "LightGBM",
    "service_rule": "현재 서비스 규칙",
    "popularity": "인기도",
    "entrance_nearest": "출입구 최근접",
    "random": "무작위",
}
# 검증한 기본 범주 팔레트(고정 순서, 모델 = 항목)
COLOR = dict(
    zip(
        MODELS,
        ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6"],
    )
)
INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
SVC = "clogit_b_no_exp"
REAL = ["real_0.25", "real_0.50", "real_0.75", "real_1.00"]
CHAIN = ["base_lowtrust", "form", "c_a2", "c_a4", "c_b", "real_1.00"]
CHAIN_LABEL = {
    "base_lowtrust": "기준",
    "form": "+A1 형태",
    "c_a2": "+A2 탈락",
    "c_a4": "+A4 상호작용",
    "c_b": "+B 고려·습관",
    "real_1.00": "+C1 숨은 취향",
}
DIM_LABEL = {
    "hist": "이력",
    "info": "관측 정보량",
    "room": "열람실",
    "crowd": "혼잡도",
    "archetype": "유형",
}


def setup_mpl():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    for p in Path("/usr/share/fonts/truetype/nanum").glob("NanumSquare*.ttf"):
        font_manager.fontManager.addfont(str(p))
    plt.rcParams.update(
        {
            "font.family": "NanumSquare",
            "axes.unicode_minus": False,
            "figure.facecolor": SURF,
            "axes.facecolor": SURF,
            "axes.edgecolor": GRID,
            "axes.labelcolor": INK2,
            "xtick.color": INK2,
            "ytick.color": INK2,
            "axes.grid": True,
            "grid.color": GRID,
            "grid.linewidth": 0.8,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.titlesize": 13,
            "axes.titlecolor": INK,
            "legend.frameon": False,
            "savefig.dpi": 300,
            "savefig.bbox": "tight",
        }
    )
    return plt


def m(summary, s, model, k):
    return summary[s]["metrics"][model][k]


# ---------------------------------------------------------------- 판정
def judge(cfg, off, slices, svc, cons):
    cr = cfg["criteria"]
    res = []
    b = off["base"]["metrics"]
    acc_ok, acc_detail = True, {}
    for k in ("ndcg@5", "oracle_hit@1"):
        for ref in cr["accuracy_baselines"]:
            lo_model, hi_ref = b[SVC][k]["ci"][0], b[ref][k]["ci"][1]
            ok = lo_model > hi_ref
            acc_ok &= ok
            acc_detail[f"{k} vs {ref}"] = {
                "model": b[SVC][k]["mean"],
                "ref": b[ref][k]["mean"],
                "model_ci_low": lo_model,
                "ref_ci_high": hi_ref,
                "pass": ok,
            }
    acc_detail["hit@3 vs service_rule(보조)"] = {
        "model": b[SVC]["hit@3"]["mean"],
        "ref": b["service_rule"]["hit@3"]["mean"],
        "diff_ci": b[SVC]["hit@3"]["diff_ci"],
    }
    res.append(("1. 정확도", acc_ok, acc_detail))

    overall = b[SVC]["ndcg@5"]["mean"]
    sl = slices[
        (slices.n_requests >= cfg["eval"]["slice_min_requests"])
        & (slices.n_users >= cfg["eval"]["slice_min_users"])
    ]
    gen = sl[sl.dim != "archetype"]
    bad = gen[
        (gen[f"{SVC}|ndcg@5"] < gen["service_rule|ndcg@5"])
        | (overall - gen[f"{SVC}|ndcg@5"] > cr["generalization_max_drop"])
    ]
    res.append(
        (
            "2. 일반화",
            bad.empty,
            {
                "overall_ndcg": overall,
                "judged_slices": int(len(gen)),
                "worst": gen.sort_values(f"{SVC}|ndcg@5")
                .head(3)[
                    [
                        "dim",
                        "level",
                        f"{SVC}|ndcg@5",
                        "service_rule|ndcg@5",
                        "n_requests",
                    ]
                ]
                .to_dict("records"),
                "failed": bad[["dim", "level"]].to_dict("records"),
            },
        )
    )
    ar = sl[sl.dim == "archetype"]
    worst = ar.sort_values(f"{SVC}|ndcg@5").iloc[0]
    res.append(
        (
            "3. 사용자 유형",
            bool((ar[f"{SVC}|ndcg@5"] >= ar["service_rule|ndcg@5"]).all()),
            {
                "worst_archetype": worst.level,
                "model": float(worst[f"{SVC}|ndcg@5"]),
                "service_rule": float(worst["service_rule|ndcg@5"]),
            },
        )
    )
    rob = {
        s: {
            "model": m(off, s, SVC, "ndcg@5")["mean"],
            "service_rule": m(off, s, "service_rule", "ndcg@5")["mean"],
            "diff_ci": m(off, s, SVC, "ndcg@5")["diff_ci"],
        }
        for s in cr["robustness_sets"]
        if s in off
    }
    rev = [s for s, v in rob.items() if v["model"] < v["service_rule"]]
    res.append(("4. 정답 변화 강건성", not rev, {"sets": rob, "reversed": rev}))
    v = svc["M9_hard_constraint"]
    rules_ok = (
        v["any_forbidden"] == cr["hard_constraint_violations"]
        and svc["exceptions_on_normal_data"] == cr["exceptions_on_normal_data"]
    )
    res.append(
        (
            "5. 규칙·안정성",
            rules_ok,
            {
                "M9": v,
                "exceptions_on_normal_data": svc["exceptions_on_normal_data"],
                "R_mismatch": sum(not r["matches_expected"] for r in svc["robustness"]),
            },
        )
    )
    cons_ok = svc["M1_determinism"]["rate"] >= cr["determinism"] and cons["passed"]
    res.append(
        (
            "6. 일관성",
            cons_ok,
            {
                "M1": svc["M1_determinism"],
                "M4_clogit_violations": cons["clogit"]["violations"],
                "M4_lgbm_violations": cons["lgbm"]["violations"],
                "inf_x_negative_warning": cons["clogit"]["inf_x_negative_warning"],
            },
        )
    )
    lat = svc["latency_ms"]
    res.append(("7. 서비스 요건", lat["p95"] <= cr["latency_p95_ms"], lat))
    return res


# ---------------------------------------------------------------- 그래프
def fig_models(plt, off, out):
    ks = [("ndcg@5", "NDCG@5"), ("oracle_hit@1", "Oracle@1"), ("hit@3", "HitRate@3")]
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.2), sharey=False)
    b = off["base"]["metrics"]
    for ax, (k, title) in zip(axes, ks):
        y = [b[mm][k]["mean"] for mm in MODELS]
        lo = [b[mm][k]["mean"] - b[mm][k]["ci"][0] for mm in MODELS]
        hi = [b[mm][k]["ci"][1] - b[mm][k]["mean"] for mm in MODELS]
        ax.bar(
            range(len(MODELS)),
            y,
            color=[COLOR[mm] for mm in MODELS],
            width=0.7,
            edgecolor=SURF,
            linewidth=2,
        )
        ax.errorbar(
            range(len(MODELS)),
            y,
            yerr=[lo, hi],
            fmt="none",
            ecolor=INK,
            elinewidth=1.2,
            capsize=3,
        )
        for i, v in enumerate(y[:4]):
            ax.text(
                i,
                v + 0.015,
                f"{v:.3f}",
                ha="center",
                va="bottom",
                fontsize=8,
                color=INK,
            )
        ax.set_xticks(
            range(len(MODELS)),
            [LABEL[mm] for mm in MODELS],
            rotation=40,
            ha="right",
            fontsize=8.5,
        )
        ax.set_title(title)
        ax.set_ylim(0, 1)
        ax.grid(axis="x", visible=False)
    fig.suptitle("모델별 추천 성능", color=INK, fontsize=14, y=1.02)
    fig.savefig(out / "01_model_comparison.png")
    plt.close(fig)


def fig_lambda(plt, off, out):
    xs = [0, 0.25, 0.5, 0.75, 1.0]
    sets = ["base_lowtrust"] + REAL
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.3))
    for ax, (k, title) in zip(axes, [("ndcg@5", "NDCG@5"), ("hit@3", "HitRate@3")]):
        for mm in MODELS:
            y = [m(off, s, mm, k)["mean"] for s in sets]
            lo = [m(off, s, mm, k)["ci"][0] for s in sets]
            hi = [m(off, s, mm, k)["ci"][1] for s in sets]
            ax.plot(xs, y, color=COLOR[mm], lw=2, marker="o", ms=5, label=LABEL[mm])
            ax.fill_between(xs, lo, hi, color=COLOR[mm], alpha=0.12, lw=0)
        for mm in (SVC, "service_rule"):
            ax.annotate(
                LABEL[mm],
                (1.0, m(off, "real_1.00", mm, k)["mean"]),
                xytext=(6, 0),
                textcoords="offset points",
                fontsize=8.5,
                color=INK2,
                va="center",
            )
        ax.set_xticks(xs)
        ax.set_xlabel("혼합 강도 λ")
        ax.set_title(title)
        ax.set_xlim(-0.05, 1.22)
    axes[1].legend(loc="center left", bbox_to_anchor=(1.02, 0.5), fontsize=9)
    fig.suptitle("정답 변화에 따른 성능", color=INK, fontsize=14, y=1.02)
    fig.savefig(out / "02_lambda_curve.png")
    plt.close(fig)


def fig_chain(plt, off, out):
    show = [SVC, "lgbm_b_no_exp", "service_rule", "popularity"]
    fig, ax = plt.subplots(figsize=(11, 4.3))
    w = 0.2
    for j, mm in enumerate(show):
        y = [m(off, s, mm, "ndcg@5")["mean"] for s in CHAIN]
        x = np.arange(len(CHAIN)) + (j - 1.5) * w
        ax.bar(
            x,
            y,
            width=w - 0.02,
            color=COLOR[mm],
            label=LABEL[mm],
            edgecolor=SURF,
            linewidth=1.5,
        )
    ax.set_xticks(range(len(CHAIN)), [CHAIN_LABEL[s] for s in CHAIN])
    ax.set_ylabel("NDCG@5")
    ax.set_ylim(0, 1)
    ax.grid(axis="x", visible=False)
    ax.legend(ncol=4, loc="upper right", fontsize=9)
    ax.set_title("정답 모듈별 누적 영향")
    fig.savefig(out / "03_cumulative_modules.png")
    plt.close(fig)


def fig_slices(plt, slices, cfg, out):
    from matplotlib.colors import LinearSegmentedColormap

    sl = slices[
        (slices.n_requests >= cfg["eval"]["slice_min_requests"])
        & (slices.n_users >= cfg["eval"]["slice_min_users"])
    ]
    cols = [f"{DIM_LABEL[d]}: {lv}" for d, lv in zip(sl.dim, sl.level)]
    M = np.array(
        [[sl[f"{mm}|ndcg@5"].values[i] for i in range(len(sl))] for mm in MODELS]
    )
    cmap = LinearSegmentedColormap.from_list(
        "blue", ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab"]
    )
    fig, ax = plt.subplots(figsize=(15, 4.6))
    im = ax.imshow(M, cmap=cmap, vmin=0.2, vmax=0.9, aspect="auto")
    for i in range(M.shape[0]):
        for j in range(M.shape[1]):
            ax.text(
                j,
                i,
                f"{M[i, j]:.2f}",
                ha="center",
                va="center",
                fontsize=7.5,
                color="#ffffff" if M[i, j] > 0.65 else INK,
            )
    ax.set_xticks(range(len(cols)), cols, rotation=45, ha="right", fontsize=8.5)
    ax.set_yticks(range(len(MODELS)), [LABEL[mm] for mm in MODELS], fontsize=9)
    ax.grid(False)
    cb = fig.colorbar(im, ax=ax, fraction=0.02, pad=0.01)
    cb.set_label("NDCG@5", color=INK2)
    ax.set_title("사용자 구간별 NDCG@5")
    fig.savefig(out / "04_slices_heatmap.png")
    plt.close(fig)


def fig_latency(plt, lat, limit, out):
    fig, ax = plt.subplots(figsize=(8, 4.2))
    ax.scatter(
        lat.n_avail, lat.ms, s=9, color=COLOR[SVC], alpha=0.35, lw=0, label="요청 1건"
    )
    bins = pd.cut(lat.n_avail, [0, 25, 50, 75, 100, 125, 150, 200])
    g = lat.groupby(bins, observed=True).ms.quantile(0.95)
    ax.plot(
        [iv.mid for iv in g.index],
        g.values,
        color=INK,
        lw=2,
        marker="o",
        ms=6,
        label="구간 p95",
    )
    ax.axhline(limit, color=INK2, lw=1.2, ls="--")
    ax.text(
        lat.n_avail.max(),
        limit,
        f"기준 {limit}ms",
        ha="right",
        va="bottom",
        fontsize=9,
        color=INK2,
    )
    ax.set_xlabel("빈자리 수")
    ax.set_ylabel("추천 1건 지연(ms)")
    ax.set_ylim(0, max(limit * 1.2, lat.ms.quantile(0.999) * 1.1))
    ax.legend(loc="upper left", fontsize=9)
    ax.set_title("응답 지연 시간")
    fig.savefig(out / "05_latency.png")
    plt.close(fig)


# ---------------------------------------------------------------- 리포트
def fmt(x, nd=3):
    return f"{x:.{nd}f}"


def write_report(cfg, ds, off, slices, svc, cons, verdict, out_md, bugs):
    b = off["base"]["metrics"]
    L = []
    L.append("# v1 좌석 추천 모델 테스트 리포트 (유저 테스트 데이터셋 test_v1)\n")
    L.append(
        f"대상: `seat_model/models/v1-20261115` (서비스 모델 `clogit_b_no_exp`). 데이터: 테스트 사용자 {ds['distribution_base_vs_train']['test']['sessions']:,}건 요청"
        f"(사용자 2,000명), 세트 {len(off)}개. 설정: `seat_model/config/model_test.yaml`, 명세: `gunhee/model_test.md`.\n"
    )
    L.append("## 요약\n")
    L.append("| 영역 | 판정 | 핵심 수치 |\n|---|---|---|")
    key = {
        "1. 정확도": f"NDCG@5 {fmt(b[SVC]['ndcg@5']['mean'])} vs 서비스 규칙 {fmt(b['service_rule']['ndcg@5']['mean'])} · Oracle@1 {fmt(b[SVC]['oracle_hit@1']['mean'])} vs {fmt(b['service_rule']['oracle_hit@1']['mean'])} · HitRate@3 {fmt(b[SVC]['hit@3']['mean'])} vs {fmt(b['service_rule']['hit@3']['mean'])}",
    }
    for name, ok, d in verdict:
        if name == "2. 일반화":
            w = d["worst"][0]
            txt = f"판정 구간 {d['judged_slices']}개, 최저 {DIM_LABEL[w['dim']]} {w['level']} {fmt(w[f'{SVC}|ndcg@5'])}(전체 {fmt(d['overall_ndcg'])})"
        elif name == "3. 사용자 유형":
            txt = f"최저 유형 {d['worst_archetype']}: {fmt(d['model'])} vs 서비스 규칙 {fmt(d['service_rule'])}"
        elif name == "4. 정답 변화 강건성":
            r1 = d["sets"]["real_1.00"]
            txt = f"real_1.00 NDCG@5 {fmt(r1['model'])} vs 서비스 규칙 {fmt(r1['service_rule'])}, 역전 세트 {len(d['reversed'])}개"
        elif name == "5. 규칙·안정성":
            txt = f"노트북 금지 좌석 노출 {d['M9']['any_forbidden']}건(노트북 필수 요청 {d['M9']['laptop_req_requests']:,}건), 정상 데이터 예외 {d['exceptions_on_normal_data']}건, R 기대 불일치 {d['R_mismatch']}건"
        elif name == "6. 일관성":
            txt = f"M1 {d['M1']['same']}/{d['M1']['checked']} 동일, M4 위반 {len(d['M4_clogit_violations']) + len(d['M4_lgbm_violations'])}건(경고: inf_x 음수 {len(d['inf_x_negative_warning'])}개)"
        elif name == "7. 서비스 요건":
            txt = f"p50 {fmt(d['p50'], 2)}ms · p95 {fmt(d['p95'], 2)}ms · p99 {fmt(d['p99'], 2)}ms (기준 p95 ≤ {cfg['criteria']['latency_p95_ms']}ms)"
        else:
            txt = key[name]
        L.append(f"| {name} | {'통과' if ok else '**실패**'} | {txt} |")
    L.append("")

    L.append("## 1. 모델 비교 (base: 가정이 맞는 세계)\n")
    L.append("![모델 비교](figures/01_model_comparison.png)\n")
    L.append(
        "| 모델 | NDCG@5 | Oracle@1 | 참 효용 백분위 | HitRate@1 | HitRate@3 | MRR |\n|---|---|---|---|---|---|---|"
    )
    for mm in MODELS:
        r = b[mm]
        c = lambda k: (
            f"{fmt(r[k]['mean'])} [{fmt(r[k]['ci'][0])}, {fmt(r[k]['ci'][1])}]"
        )
        L.append(
            f"| {LABEL[mm]} | {c('ndcg@5')} | {c('oracle_hit@1')} | {fmt(r['exp_utility_pct']['mean'])} | {fmt(r['hit@1']['mean'])} | {c('hit@3')} | {fmt(r['mrr']['mean'])} |"
        )
    L.append("")
    L.append(_interp_models(off))

    L.append("## 2. 정답 로직 변화에 대한 강건성\n")
    L.append("![λ 곡선](figures/02_lambda_curve.png)\n")
    L.append(
        "| 세트 | λ | clogit(서비스) NDCG@5 | LightGBM | 서비스 규칙 | 인기도 | 서비스 − 규칙 (95% CI) | clogit HitRate@3 | 규칙 HitRate@3 |\n|---|---|---|---|---|---|---|---|---|"
    )
    for s in [
        "base",
        "base_resim",
        "base_lowtrust",
        "form",
        "c_a2",
        "c_a4",
        "c_b",
    ] + REAL:
        if s not in off:
            continue
        g = lambda mm, k="ndcg@5": m(off, s, mm, k)["mean"]
        d = m(off, s, SVC, "ndcg@5")
        L.append(
            f"| `{s}` | {cfg['sets'][s]['lambda']} | {fmt(g(SVC))} | {fmt(g('lgbm_b_no_exp'))} | {fmt(g('service_rule'))} | {fmt(g('popularity'))} | "
            f"{d['diff_vs_ref']:+.3f} [{d['diff_ci'][0]:+.3f}, {d['diff_ci'][1]:+.3f}] | {fmt(g(SVC, 'hit@3'))} | {fmt(g('service_rule', 'hit@3'))} |"
        )
    L.append("")
    L.append(_interp_lambda(off))
    L.append("![누적 모듈](figures/03_cumulative_modules.png)\n")
    L.append(_interp_chain(off))

    L.append("## 3. 구간별 성능 (base)\n")
    L.append("![구간 히트맵](figures/04_slices_heatmap.png)\n")
    sl = slices[
        (slices.n_requests >= cfg["eval"]["slice_min_requests"])
        & (slices.n_users >= cfg["eval"]["slice_min_users"])
    ]
    L.append(
        "| 구간 | 요청 | 사용자 | clogit(서비스) NDCG@5 | LightGBM | 서비스 규칙 | clogit HitRate@3 |\n|---|---|---|---|---|---|---|"
    )
    for _, r in sl.iterrows():
        L.append(
            f"| {DIM_LABEL[r.dim]}: {r.level} | {r.n_requests:,} | {r.n_users:,} | {fmt(r[f'{SVC}|ndcg@5'])} | {fmt(r['lgbm_b_no_exp|ndcg@5'])} | "
            f"{fmt(r['service_rule|ndcg@5'])} | {fmt(r[f'{SVC}|hit@3'])} |"
        )
    L.append("")
    L.append(_interp_slices(sl))

    L.append("## 4. 서비스 트랙 (SeatRecommender 직접 호출, base)\n")
    sq, cd = svc["seq"], svc["cold"]
    L.append(
        "| 모드 | NDCG@5 | Oracle@1 | HitRate@1 | HitRate@3 | MRR |\n|---|---|---|---|---|---|"
    )
    for nm, r in (("순차(착석마다 observe)", sq), ("콜드(이력·온라인 갱신 없음)", cd)):
        L.append(
            f"| {nm} | {fmt(r['ndcg@5'])} | {fmt(r['oracle_hit@1'])} | {fmt(r['hit@1'])} | {fmt(r['hit@3'])} | {fmt(r['mrr'])} |"
        )
    L.append("")
    L.append("순차 모드의 이력 수별 성능\n")
    L.append("| 이력(착석 수) | 요청 | NDCG@5 | HitRate@3 |\n|---|---|---|---|")
    for k, v in svc["seq_by_history"].items():
        L.append(f"| {k} | {v['n']:,} | {fmt(v['ndcg@5'])} | {fmt(v['hit@3'])} |")
    L.append("")
    L.append(
        f"- 순차 모드는 오프라인 clogit(NDCG@5 {fmt(b[SVC]['ndcg@5']['mean'])})에 사용자별 온라인 갱신(μ)이 더해진 실제 서비스 동작이다. "
        f"콜드 모드 대비 NDCG@5 {sq['ndcg@5'] - cd['ndcg@5']:+.3f}, HitRate@3 {sq['hit@3'] - cd['hit@3']:+.3f}."
    )
    L.append(
        f"- 필수 조건(0.5단계 하드 필터 적용 후): 노트북 필수 요청 {svc['M9_hard_constraint']['laptop_req_requests']:,}건에서 금지 좌석 노출 "
        f"{svc['M9_hard_constraint']['any_forbidden']}건, 조건 만족 좌석이 없어 빈 결과 {svc['M9_hard_constraint']['no_eligible']}건. "
        f"실제 선택이 조건 밖 좌석인 요청 {svc['M9_hard_constraint']['chosen_ineligible']}건(관측 오류로 노트북 필수가 붙은 사용자). 이 요청은 하드 필터 때문에 선택 좌석을 맞힐 수 없다."
    )
    L.append(f"- 모델 폴더 파일 변경 없음: {svc['model_dir_unchanged']}\n")
    L.append("![지연 시간](figures/05_latency.png)\n")
    L.append(
        f"지연 시간(k = 5, 단일 프로세스, {svc['latency_ms']['n']:,}건): p50 {fmt(svc['latency_ms']['p50'], 2)}ms, p95 {fmt(svc['latency_ms']['p95'], 2)}ms, p99 {fmt(svc['latency_ms']['p99'], 2)}ms.\n"
    )

    L.append("## 5. 일관성·견고성\n")
    c = cons["clogit"]
    L.append(
        f"- **M1 결정성**: {svc['M1_determinism']['same']}/{svc['M1_determinism']['checked']} 동일(이력 있는 사용자 포함, 같은 상태로 2회 호출)."
    )
    L.append(
        "- **M4 방향성(clogit 계수)**: "
        + ", ".join(f"{k} {v:+.3f}" for k, v in c["match_coef"].items())
        + f" → 위반 {len(c['violations'])}건. 경고: inf_x 음수 "
        + ", ".join(f"{k} {v:+.4f}" for k, v in c["inf_x_negative_warning"].items())
        + "."
    )
    lg = cons["lgbm"]["direction"]
    L.append(
        "- **M4 방향성(LightGBM, 실제 테스트 행)**: match 값과 기여도의 순위상관 "
        + ", ".join(f"{k} {v['spearman']:+.2f}" for k, v in lg.items() if v)
        + f" → 위반 {len(cons['lgbm']['violations'])}건."
    )
    L.append(
        "\n| 코드 | 입력 | 기대 동작 | 실제 동작 | 기대 일치 |\n|---|---|---|---|---|"
    )
    for r in svc["robustness"]:
        L.append(
            f"| {r['code']} | {r['input']} | {r['expected']} | {r['behavior']} | {'예' if r['matches_expected'] else '**아니오**'} |"
        )
    L.append(
        f"\n기대와 다른 동작은 `seat_model/reports/model_test_bugs.md`에 정리했다({len(bugs)}건).\n"
    )

    L.append("## 6. 데이터셋 요약\n")
    L.append(
        "| 세트 | λ | 모듈 | 추천 신뢰도 배율 | 정답 상관 ρ | 정답 1위 일치 | 라벨 반응도 | 평가 요청 |\n|---|---|---|---|---|---|---|---|"
    )
    for s, v in ds["sets"].items():
        sp = cfg["sets"][s]
        L.append(
            f"| `{s}` | {sp['lambda']} | {', '.join(sp['modules']) or '–'} | {v['rec_trust_multiplier']:.2f} | {fmt(v['rho_truth_vs_base_mean'])} | "
            f"{fmt(v['truth_top1_agree_with_base'])} | {v['label_response']:+.3f} | {off[s]['data']['sessions_used']:,} |"
        )
    L.append("")

    L.append("## 7. 모델별 강점·약점과 권장\n")
    L.append(_interp_recommend(off, svc))
    L.append("## 한계\n")
    L.append(
        "- 모든 데이터와 정답은 합성이다. 정답 로직(A1~C1, B, R)도 우리가 정한 가정이라, 어느 로직이 실제와 가까운지는 이 테스트로 알 수 없다(사람 라벨 미진행)."
    )
    L.append(
        "- 선택이 확률적이라 `y_chosen` 지표의 세트 간 차이는 작다(라벨 반응도 최대 +0.041). 세트 비교는 참 효용 기준 지표로 읽는다."
    )
    L.append(
        "- `service_rule`은 생성기의 노출 정책과 같은 식이라 `y_chosen` 지표에서 유리하다."
    )
    L.append(
        "- 배포 환경(synth-1.1 + 칸막이 수정)과 설문 형식 입력의 서비스 조건 테스트셋(T2)은 아직 없다. 이 결과는 모델이 학습한 환경 기준이다."
    )
    L.append(
        "- XGBoost(저장된 모델 없음)와 신경망(미학습)은 평가하지 않았다. 테스트 결과로 모델을 다시 학습하거나 튜닝하지 않았다.\n"
    )
    out_md.write_text("\n".join(L), encoding="utf-8")


def _interp_models(off):
    b = off["base"]["metrics"]
    s, l, r = b[SVC], b["lgbm_b_no_exp"], b["service_rule"]
    return (
        f"- 서비스 모델은 참 효용 기준에서 현재 서비스 규칙보다 크게 높다(NDCG@5 {s['ndcg@5']['diff_vs_ref']:+.3f}, "
        f"Oracle@1 {s['oracle_hit@1']['diff_vs_ref']:+.3f}). 실제 선택 기준(HitRate@3)의 차이는 {s['hit@3']['diff_vs_ref']:+.3f}로 더 작다. "
        f"노출 목록이 서비스 규칙과 같은 식이라 선택 라벨이 규칙 쪽으로 기울어 있기 때문이다.\n"
        f"- LightGBM은 서비스 모델과 비슷하다(NDCG@5 {l['ndcg@5']['mean']:.3f} vs {s['ndcg@5']['mean']:.3f}). "
        f"`clogit_a_full`은 노출 특징을 중립(0)으로 두고 점수를 내므로 선택 기준 지표가 낮다.\n"
    )


def _interp_lambda(off):
    a0, a1 = (
        m(off, "base_lowtrust", SVC, "ndcg@5")["mean"],
        m(off, "real_1.00", SVC, "ndcg@5")["mean"],
    )
    r0, r1 = (
        m(off, "base_lowtrust", "service_rule", "ndcg@5")["mean"],
        m(off, "real_1.00", "service_rule", "ndcg@5")["mean"],
    )
    l0, l1 = (
        m(off, "base_lowtrust", "lgbm_b_no_exp", "ndcg@5")["mean"],
        m(off, "real_1.00", "lgbm_b_no_exp", "ndcg@5")["mean"],
    )
    return (
        f"- λ가 0 → 1로 가면 서비스 모델 NDCG@5는 {a0:.3f} → {a1:.3f}({a1 - a0:+.3f}), LightGBM은 {l0:.3f} → {l1:.3f}({l1 - l0:+.3f}), "
        f"서비스 규칙은 {r0:.3f} → {r1:.3f}({r1 - r0:+.3f})다. 정답이 우리 가정과 멀어질수록 학습 모델의 우위가 "
        f"{a0 - r0:.3f}에서 {a1 - r1:.3f}로 바뀐다.\n"
        f"- `base` 대비 `base_lowtrust`는 추천 신뢰도만 낮춘 대조군이다. 정답이 같으므로 두 세트의 차이는 선택 과정 변화의 효과다.\n"
    )


def _interp_chain(off):
    seq = [m(off, s, SVC, "ndcg@5")["mean"] for s in CHAIN]
    deltas = [
        f"{CHAIN_LABEL[CHAIN[i]]} {seq[i] - seq[i - 1]:+.3f}"
        for i in range(1, len(CHAIN))
    ]
    worst = min(range(1, len(CHAIN)), key=lambda i: seq[i] - seq[i - 1])
    return (
        f"- 서비스 모델 NDCG@5의 단계별 변화: " + ", ".join(deltas) + ".\n"
        f"- 가장 큰 하락은 **{CHAIN_LABEL[CHAIN[worst]]}** 단계다. 선택 과정 모듈(B)은 정답(효용)을 바꾸지 않으므로 NDCG 변화는 이력 특징을 통해서만 생긴다.\n"
    )


def _interp_slices(sl):
    s = sl.sort_values(f"{SVC}|ndcg@5")
    lo, hi = s.iloc[0], s.iloc[-1]
    return (
        f"- 가장 낮은 구간은 {DIM_LABEL[lo.dim]} {lo.level}({lo[f'{SVC}|ndcg@5']:.3f}), 가장 높은 구간은 {DIM_LABEL[hi.dim]} {hi.level}"
        f"({hi[f'{SVC}|ndcg@5']:.3f})다. 모든 판정 구간에서 서비스 모델이 서비스 규칙보다 높은지는 요약 표 2번에 있다.\n"
    )


def _interp_recommend(off, svc):
    b = off["base"]["metrics"]
    s, l = b[SVC], b["lgbm_b_no_exp"]
    same = abs(s["ndcg@5"]["mean"] - l["ndcg@5"]["mean"]) < 0.01
    return (
        f"- **clogit(서비스)**: 참 효용·선택 기준 모두 상위권이고, 계수 부호가 해석 가능하며 지연이 짧다(p95 {svc['latency_ms']['p95']:.1f}ms). "
        f"온라인 갱신으로 착석 기록이 쌓일수록 좋아진다(순차 − 콜드 NDCG@5 {svc['seq']['ndcg@5'] - svc['cold']['ndcg@5']:+.3f}).\n"
        f"- **LightGBM**: 서비스 모델과 성능이 {'비슷하다' if same else '다르다'}(NDCG@5 {l['ndcg@5']['mean']:.3f}). 해석·온라인 갱신 경로가 없다.\n"
        f"- **현재 서비스 규칙**: 관측 프로필만 쓰므로 숨은 취향과 이력을 반영하지 못한다.\n"
        f"- **권장**: 현재 서비스 모델(`clogit_b_no_exp` + 온라인 갱신)을 유지한다. 위 강건성 표에서 역전되는 세트가 있으면 그 모듈이 가리키는 취향(예: 탈락 조건, 숨은 취향)을 특징으로 보강하는 것을 검토한다.\n"
    )


def bug_list(svc, cons):
    bugs = []
    loc = {
        "R1": "seat_model/train/inference.py (`_block` → `candidate_block`: 빈자리 0개면 빈 인덱스 배열이 float형)",
        "R4": "seat_model/train/data.py:137 `candidate_block` (빈자리 평균 중심화로 한 좌석의 NaN이 모든 좌석 점수에 전파)",
        "R5": "seat_model/train/data.py `candidate_block` (특징 범위 검사·클리핑 없음)",
        "R6": "seat_model/train/inference.py `scenario_from_snapshot` (중복 seat_id를 검사하지 않음)",
        "R7": "seat_model/train/inference.py:53 `DESK_TYPES.index`, data.py `WEATHERS.index`",
        "R8": 'seat_model/train/data.py `UserPrefs.__init__` (`p["importance"]` 직접 접근)',
    }
    sev = {
        "R1": "높음(예외 중단)",
        "R4": "높음(추천 전체가 NaN 점수)",
        "R5": "경미~높음(inf면 전체 NaN)",
        "R6": "경미",
        "R7": "높음(예외 중단)",
        "R8": "높음(예외 중단)",
    }
    for r in svc["robustness"]:
        if not r["matches_expected"]:
            bugs.append(
                {
                    "code": r["code"],
                    "input": r["input"],
                    "behavior": r["behavior"],
                    "expected": r["expected"],
                    "location": loc.get(r["code"], "-"),
                    "severity": sev.get(r["code"], "경미"),
                }
            )
    for k, v in cons["clogit"]["inf_x_negative_warning"].items():
        bugs.append(
            {
                "code": "M4(경고)",
                "input": f"clogit inf_x_{k} 계수 {v:+.4f}",
                "behavior": "착석 기록으로 추정한 취향과 반대 방향(작은 값)",
                "expected": "0 이상",
                "location": "seat_model/models/v1-20261115/clogit_b_no_exp.json",
                "severity": "경미",
            }
        )
    return bugs


def write_bugs(bugs, path):
    L = [
        "# 모델 테스트 버그 목록\n",
        "`seat_model/reports/model_test_report.md` 5장 견고성·일관성 검사에서 기대와 다르게 동작한 항목. 코드는 고치지 않았다.\n",
        "| 코드 | 재현 입력 | 실제 동작 | 기대 동작 | 위치 | 심각도 |\n|---|---|---|---|---|---|",
    ]
    for b in bugs:
        L.append(
            f"| {b['code']} | {b['input']} | {b['behavior']} | {b['expected']} | {b['location']} | {b['severity']} |"
        )
    L.append(
        "\n재현: `seat_model/.venv/bin/python -m seat_model.model_test.service_suite` (robustness 함수가 base 세트 요청 하나로 입력을 만든다).\n"
    )
    path.write_text("\n".join(L), encoding="utf-8")


def main():
    cfg = load_config()
    E = Path(cfg["paths"]["exports"])
    rep = REPO / "seat_model" / "reports"
    figs = rep / "figures"
    figs.mkdir(parents=True, exist_ok=True)
    ds = json.loads((E / "dataset_report.json").read_text())
    off = json.loads((E / "eval" / "offline_summary.json").read_text())
    slices = pd.read_parquet(E / "eval" / "base_slices.parquet")
    svc = json.loads((E / "eval" / "service" / "service_summary.json").read_text())
    lat = pd.read_parquet(E / "eval" / "service" / "latency.parquet")
    cons = json.loads((E / "eval" / "consistency.json").read_text())
    missing = [s for s in cfg["sets"] if s not in off]
    if missing:
        raise SystemExit(f"오프라인 평가가 끝나지 않은 세트: {missing}")
    verdict = judge(cfg, off, slices, svc, cons)
    plt = setup_mpl()
    fig_models(plt, off, figs)
    fig_lambda(plt, off, figs)
    fig_chain(plt, off, figs)
    fig_slices(plt, slices, cfg, figs)
    fig_latency(plt, lat, cfg["criteria"]["latency_p95_ms"], figs)
    bugs = bug_list(svc, cons)
    write_bugs(bugs, rep / "model_test_bugs.md")
    write_report(
        cfg, ds, off, slices, svc, cons, verdict, rep / "model_test_report.md", bugs
    )
    summary = {
        "verdict": {n: {"pass": ok, "detail": d} for n, ok, d in verdict},
        "offline": {
            s: {mm: {k: v[mm][k]["mean"] for k in v[mm]} for mm in v}
            for s, v in ((s, off[s]["metrics"]) for s in off)
        },
        "service": {
            k: svc[k]
            for k in (
                "seq",
                "cold",
                "M1_determinism",
                "M9_hard_constraint",
                "latency_ms",
                "exceptions_on_normal_data",
                "model_dir_unchanged",
            )
        },
        "consistency": cons,
        "bugs": bugs,
    }
    (rep / "model_test_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1, default=float)
    )
    for n, ok, _ in verdict:
        print(n, "통과" if ok else "실패")


if __name__ == "__main__":
    main()
