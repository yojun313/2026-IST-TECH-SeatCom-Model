"""번호가 표시된 좌석 화면을 정적 CSV/JSON/SVG로 구조화한다.

현재 검증 프로필은 사용자가 제공한 성남중앙 A구역 캡처다.
색은 사각형 검출에만 쓰며 이용상태/성별/색상은 출력하지 않는다.
도면에 표시되지 않은 시설이나 누락된 좌석번호를 생성하지 않는다.
"""

import argparse
import csv
import hashlib
import json
import re
from datetime import date
from html import escape
from pathlib import Path

import cv2
from rapidocr import RapidOCR


def structure(image_path, output_dir, frame, expected, data_date):
    image_path = Path(image_path)
    raw = cv2.imread(str(image_path))
    if raw is None:
        raise ValueError("입력 이미지를 읽을 수 없습니다.")
    height, width = raw.shape[:2]
    fx, fy, fw, fh = frame
    if not (0 <= fx < fx + fw <= width and 0 <= fy < fy + fh <= height):
        raise ValueError("좌석도 표시영역이 이미지 범위를 벗어납니다.")
    mask = cv2.inRange(
        cv2.cvtColor(raw, cv2.COLOR_BGR2HSV), (0, 80, 70), (179, 255, 255)
    )
    mask[:fy] = 0
    mask[fy + fh :] = 0
    mask[:, :fx] = 0
    mask[:, fx + fw :] = 0
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    boxes = []
    for x, y, w, h, area in stats[1:]:
        # 이 프로필의 좌석 버튼 크기에 대한 제한. 다른 배치도는 별도 조정 필요.
        if 40 <= w <= 100 and 35 <= h <= 100 and area > w * h * 0.65:
            boxes.append((int(x) - 1, int(y) - 1, int(w) + 2, int(h) + 2))
    engine = RapidOCR(params={"Global.log_level": "error"})
    rows = []
    for x, y, w, h in sorted(boxes, key=lambda b: (b[1], b[0])):
        crop = raw[y + 4 : y + h - 4, x + 4 : x + w - 4]
        text_mask = cv2.inRange(crop, (0, 0, 0), (60, 60, 60))
        result = engine(
            cv2.cvtColor(255 - text_mask, cv2.COLOR_GRAY2BGR),
            use_det=False,
            use_cls=False,
        )
        text = result.txts[0] if result.txts else None
        confidence = float(result.scores[0]) if result.scores else None
        valid = bool(re.fullmatch(r"A[0-9]+", text or ""))
        rows.append(
            {
                "도서관명": "성남중앙도서관",
                "열람실명": "3층1열람실(A구역)",
                "기하객체ID": "SN-CT-A-G%03d" % (len(rows) + 1),
                "좌석번호": text if valid else None,
                "공식좌석ID": None,
                "캡처X": x,
                "캡처Y": y,
                "X좌표": x - fx,
                "Y좌표": y - fy,
                "가로": w,
                "세로": h,
                "정규화X(0~1)": round((x - fx) / fw, 8),
                "정규화Y(0~1)": round((y - fy) / fh, 8),
                "정규화가로": round(w / fw, 8),
                "정규화세로": round(h / fh, 8),
                "OCR신뢰도": confidence,
                "검수상태": "우선검수필요"
                if not valid or confidence < 0.98
                else "자동추출_전수검수전",
                "출처구분": "사용자제공스크린샷",
                "출처URL": "http://www.snlib.go.kr/ct/contents/roomStatus.do",
                "데이터기준일자": data_date,
            }
        )
    if not rows:
        raise ValueError("검출 좌석이 없습니다.")
    labels = [row["좌석번호"] for row in rows if row["좌석번호"]]
    repeated = sorted({label for label in labels if labels.count(label) > 1})
    missing = [
        i
        for i in range(1, max([int(s[1:]) for s in labels], default=0) + 1)
        if "A" + str(i) not in labels
    ]
    report = {
        "도서관명": "성남중앙도서관",
        "열람실명": "3층1열람실(A구역)",
        "검증프로필": "사용자 캡처의 번호 표시 좌석 사각형",
        "원본파일명": image_path.name,
        "원본SHA256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
        "원본가로": width,
        "원본세로": height,
        "좌석도표시영역": {"x": fx, "y": fy, "width": fw, "height": fh},
        "좌표계": "좌석도 표시영역 좌상단 원점; X 오른쪽/Y 아래; 단위는 캡처 픽셀",
        "표기전체좌석수": expected,
        "검출사각형수": len(rows),
        "인식좌석번호수": len(labels),
        "고유좌석번호수": len(set(labels)),
        "좌석번호중복": repeated,
        "우선검수좌석": [
            r["좌석번호"] for r in rows if r["검수상태"] == "우선검수필요"
        ],
        "번호수열내미등장번호": missing,
        "좌석수대조": "일치" if len(rows) == expected else "불일치",
        "범위검증": all(
            0 <= r["X좌표"] <= r["X좌표"] + r["가로"] <= fw
            and 0 <= r["Y좌표"] <= r["Y좌표"] + r["세로"] <= fh
            for r in rows
        ),
        "한계": [
            "OCR 신뢰도는 실제 정확도가 아님. 169개 전수 번호 검수 미완료.",
            "그림은 A구역만 포함. B구역과 4층 열람실은 구조화하지 않음.",
            "미등장 번호는 근거 없이 생성하지 않음. 공식 좌석ID 매핑 미완료.",
            "시설 위치, 벽, 문, 축척, 실제 미터 거리, 책상 형태는 이 좌석 버튼에서 확정할 수 없음.",
            "SVG는 상태를 제거한 좌석 버튼 배치 재구성. 실제 건축 평면도가 아님.",
            "좌석 색상/성별/이용 상태/이용 시간은 출력하지 않음.",
        ],
        "데이터기준일자": data_date,
    }
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = "seongnam_central_A"
    for subdir in ("source_data/extracted", "validation"):
        (output_dir / subdir).mkdir(exist_ok=True)
    (output_dir / "source_data/extracted" / (stem + ".json")).write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (output_dir / "source_data/extracted" / (stem + ".csv")).open(
        "w", encoding="utf-8-sig", newline=""
    ) as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    (output_dir / "validation" / (stem + ".json")).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {fw} {fh}" role="img" aria-label="성남중앙 A구역 정적 좌석 배치 재구성">',
        "<title>사용자 캡처에서 추출한 좌석 버튼 위치. 이용 상태 및 시설 정보 없음.</title>",
        f'<rect width="{fw}" height="{fh}" fill="white"/>',
    ]
    for row in rows:
        x, y, w, h = (row[c] for c in ("X좌표", "Y좌표", "가로", "세로"))
        label = escape(row["좌석번호"] or "?")
        svg.extend(
            [
                f'<g id="{row["기하객체ID"]}" data-seat-number="{label}">',
                f"<title>{label}: 자동 추출, 검수 전</title>",
                f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#edf2f7" stroke="#334155"/>',
                f'<text x="{x + w / 2}" y="{y + h / 2}" text-anchor="middle" dominant-baseline="middle" font-size="20" font-family="sans-serif">{label}</text>',
                "</g>",
            ]
        )
    svg.append("</svg>")
    (output_dir / "libraries/seongnam-central/assets" / (stem + ".svg")).write_text(
        "\n".join(svg) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "검출사각형수",
                    "고유좌석번호수",
                    "좌석수대조",
                    "범위검증",
                    "우선검수좌석",
                    "번호수열내미등장번호",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("--output", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument(
        "--frame",
        type=int,
        nargs=4,
        required=True,
        metavar=("X", "Y", "WIDTH", "HEIGHT"),
    )
    parser.add_argument("--expected", type=int, required=True)
    parser.add_argument("--date", default=date.today().isoformat())
    args = parser.parse_args()
    structure(args.image, args.output, args.frame, args.expected, args.date)
