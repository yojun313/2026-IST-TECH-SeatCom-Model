"""공개 카탈로그 메타데이터만 수집한다. 원본 대출·회원 데이터는 받지 않는다."""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent
sources = json.loads((ROOT / "library_dataset_sources.json").read_text())


def fetch(source):
    url = f"https://www.data.go.kr/catalog/{source['id']}/{source['type']}.json"
    try:
        response = requests.get(url, timeout=25)
        response.raise_for_status()
        metadata = response.json()
        return {
            **source,
            "metadata_url": url,
            "metadata": metadata,
            "check": "공식 카탈로그 JSON 수신",
        }
    except Exception as exc:
        return {
            **source,
            "metadata_url": url,
            "check": "검색 색인 확인; 카탈로그 수신 실패",
            "error": str(exc),
        }


results = []
with ThreadPoolExecutor(max_workers=6) as pool:
    for future in as_completed([pool.submit(fetch, source) for source in sources]):
        results.append(future.result())
results.sort(key=lambda row: row["id"])
(ROOT / "library_dataset_metadata.json").write_text(
    json.dumps(results, ensure_ascii=False, indent=2)
)
print(
    f"카탈로그 {len(results)}건: 수신 성공 {sum('metadata' in row for row in results)}건"
)
for row in results:
    if "metadata" not in row:
        print("실패:", row["id"], row["title"])
if results:
    print("메타데이터 필드:", ", ".join(results[0].get("metadata", {})))
