"""Export each library's canonical JSON arrays to consistent Korean-header CSV copies."""
import csv
import json
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
ROOT = LIB / "real"
TABLE_FIELDS = {
    "libraries": ["library_id", "name", "address", "latitude", "longitude", "timezone", "external_ids", "source_id", "verification"],
    "rooms": ["room_id", "library_id", "name", "floor_id", "capacity", "assignment", "usage_rules", "opening_hours", "source_id", "verification", "external_room_id", "official_room_id", "external_room_id_source_id", "opening_hours_source_id", "capacity_sources"],
    "maps": ["map_id", "library_id", "floor_id", "room_id", "width", "height", "unit", "origin", "x_direction", "y_direction", "meters_per_unit", "image_path", "map_kind", "source_id", "verification"],
    "seats": ["seat_id", "library_id", "external_seat_id", "seat_label", "room_id", "map_id", "bbox", "orientation_degrees", "source_id", "verification", "geometry_object_id"],
    "facilities": ["facility_id", "library_id", "map_id", "type", "geometry", "access_point", "attributes", "source_id", "verification"],
    "room_features": ["library_id", "room_id", "name", "value", "unit", "context", "method", "source_id", "confidence", "verification", "applies_to_all_seats", "normalized_value", "normalization_version"],
    "seat_features": ["library_id", "seat_id", "name", "value", "unit", "context", "method", "source_id", "confidence", "verification", "normalized_value", "normalization_version"],
    "walkway_nodes": ["library_id", "node_id", "map_id", "point", "kind", "target_id", "source_id", "verification"],
    "walkway_edges": ["library_id", "from_node_id", "to_node_id", "length", "unit", "accessible", "source_id", "verification"],
    "map_transforms": ["library_id", "from_map_id", "to_map_id", "kind", "matrix", "control_points", "source_id", "verification"],
    "sources": ["library_id", "source_id", "source_type", "url", "sha256", "document_date", "collected_on", "license_status", "notes", "local_path"],
}
KOREAN_HEADERS = {
    "library_id": "도서관ID", "name": "도서관명", "address": "주소", "latitude": "위도", "longitude": "경도", "timezone": "시간대", "external_ids": "외부ID", "source_id": "출처ID", "verification": "검수상태",
    "room_id": "열람실ID", "floor_id": "층ID", "capacity": "정원", "assignment": "좌석배정방식", "usage_rules": "이용규칙", "opening_hours": "운영시간", "external_room_id": "원본열람실ID", "official_room_id": "공식열람실ID", "external_room_id_source_id": "원본열람실ID출처ID", "opening_hours_source_id": "운영시간출처ID", "capacity_sources": "정원출처ID목록",
    "map_id": "지도ID", "width": "가로", "height": "세로", "unit": "단위", "origin": "원점", "x_direction": "X방향", "y_direction": "Y방향", "meters_per_unit": "단위당미터", "image_path": "이미지경로", "map_kind": "지도종류",
    "seat_id": "좌석ID", "external_seat_id": "공식좌석ID", "seat_label": "좌석번호", "bbox": "사각형", "orientation_degrees": "방향각", "geometry_object_id": "기하객체ID",
    "facility_id": "시설ID", "type": "시설유형", "geometry": "기하", "access_point": "접근점", "attributes": "속성",
    "value": "값", "context": "측정맥락", "method": "수집방법", "confidence": "신뢰도", "applies_to_all_seats": "모든좌석적용", "normalized_value": "정규화값", "normalization_version": "정규화버전",
    "node_id": "노드ID", "point": "좌표", "kind": "노드종류", "target_id": "대상ID", "from_node_id": "시작노드ID", "to_node_id": "도착노드ID", "length": "길이", "accessible": "접근가능",
    "from_map_id": "원본지도ID", "to_map_id": "대상지도ID", "matrix": "변환행렬", "control_points": "대응점",
    "source_type": "출처유형", "url": "URL", "sha256": "SHA256", "document_date": "자료기준일", "collected_on": "수집일", "license_status": "이용조건", "notes": "비고", "local_path": "로컬경로",
}


def cell(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def main():
    for directory in sorted(ROOT.iterdir()):
        bundle_path = directory / "dataset.json"
        report_path = directory / "build_report.json"
        if not directory.is_dir() or not bundle_path.exists() or not report_path.exists():
            continue
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        report = json.loads(report_path.read_text(encoding="utf-8"))
        reference_date = report["reference_date"]
        for table, fields in TABLE_FIELDS.items():
            headers = [KOREAN_HEADERS[field] for field in fields] + ["데이터기준일자"]
            rows = []
            for record in bundle[table]:
                unknown = set(record) - set(fields)
                if unknown:
                    raise ValueError(f"{directory.name}/{table}: fields not mapped: {sorted(unknown)}")
                rows.append([cell(record.get(field)) for field in fields] + [reference_date])
            with (directory / f"{table}.csv").open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(headers)
                writer.writerows(rows)


if __name__ == "__main__":
    main()
