"""python3 -I recommender/test_preference_adapter.py  (pytest로도 실행 가능)"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from preference_adapter import load_analysis, to_profile  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def P(**kw):
    return {
        "preferences": {
            k: {"preference": v[0], "confidence": v[1], "inference_type": v[2]}
            for k, v in kw.items()
        }
    }


def feats(prof):
    return {p["featureId"]: p for p in prof["preferences"]}


def test_both_vocabularies_equivalent():
    a = to_profile(
        P(laptop_usage=("yes", 0.9, "direct"), desk=("large", 0.8, "direct"))
    )
    b = to_profile(
        P(laptop_usage=("uses_laptop", 0.9, "direct"), desk=("Large", 0.8, "direct"))
    )
    assert (
        a["preferences"] == b["preferences"] and a["requirements"] == b["requirements"]
    )
    assert a["requirements"] == [
        {"featureId": "laptopAllowed", "operator": "eq", "value": 1}
    ]


def test_avoid_direct_airflow_is_direct_and_stronger_than_warm():
    d = feats(to_profile(P(air_conditioning=("avoid_direct_airflow", 0.8, "direct"))))[
        "airflow"
    ]
    w = feats(to_profile(P(air_conditioning=("warm", 0.8, "direct"))))["airflow"]
    assert d["target"] == 0.0 and w["target"] == 0.1
    assert d["importance"] > w["importance"]


def test_height_adjustable_kept_as_unmapped():
    prof = to_profile(P(desk=("height_adjustable", 0.7, "direct")))
    assert prof["preferences"] == []
    assert prof["unmapped"][0]["canonical"] == "height_adjustable"


def test_unknown_and_invalid_values():
    prof = to_profile(
        P(restroom=("unknown", 0.0, "unknown"), lighting=("neon", 0.9, "direct"))
    )
    assert prof["preferences"] == []
    assert any("neon" in w for w in prof["warnings"])


def test_contextual_lower_than_direct_and_low_conf_no_requirement():
    d = feats(to_profile(P(outlet=("required", 0.8, "direct"))))["outletAvailable"][
        "importance"
    ]
    c = feats(to_profile(P(outlet=("required", 0.8, "contextual"))))["outletAvailable"][
        "importance"
    ]
    assert d > c
    assert to_profile(P(laptop_usage=("yes", 0.45, "contextual")))["requirements"] == []


def test_teammate_sample_file_repaired():
    data, warns = load_analysis(
        os.path.join(REPO, "kakao_analysis", "schema", "kakaotalk_schema.json")
    )
    prof = to_profile(data)
    f = feats(prof)
    assert warns and "outletAvailable" in f and f["airflow"]["target"] == 0.1
    assert "participant_id" not in prof


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
