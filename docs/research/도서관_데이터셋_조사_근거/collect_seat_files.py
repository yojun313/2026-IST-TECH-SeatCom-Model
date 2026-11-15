"""좌석 관련 공개 파일 5종을 공식 다운로드 절차로 수집한다(파일당 10MB 상한)."""

import csv
import io
import json
import re
import zipfile
from pathlib import Path
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent / "열람실_좌석추천_데이터"
DEST = ROOT / "원본"
DEST.mkdir(parents=True, exist_ok=True)
results = []
for ident in ["15171450", "15154878", "15078215", "15063649", "15032433"]:
    report = {"id": ident, "url": f"https://www.data.go.kr/data/{ident}/fileData.do"}
    try:
        session = requests.Session()
        response = session.get(report["url"], timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        button = soup.find(
            "button", onclick=lambda value: value and "fn_fileDataDown(" in value
        )
        if button is None:
            raise ValueError("공개 다운로드 버튼을 확인할 수 없음")
        args = re.findall("'([^']*)'", button["onclick"])
        response = session.post(
            "https://www.data.go.kr/tcs/dss/selectFileDataDownload.do",
            data={
                "publicDataPk": args[0],
                "publicDataDetailPk": args[1],
                "atchFileId": args[2],
                "fileDetailSn": args[3],
                "publicDataTyCode": "PR0051",
            },
            timeout=20,
        )
        response.raise_for_status()
        info = response.json()
        if not info.get("status"):
            raise ValueError("공식 다운로드 응답 실패; 인증 우회하지 않음")
        response = session.get(
            "https://www.data.go.kr/cmm/cmm/fileDownload.do",
            params={
                "atchFileId": info["atchFileId"],
                "fileDetailSn": info["fileDetailSn"],
            },
            timeout=25,
            stream=True,
        )
        response.raise_for_status()
        if "attachment" not in response.headers.get("Content-Disposition", "").lower():
            response.close()
            raise ValueError("파일 첨부 응답이 아님")
        data = bytearray()
        for chunk in response.iter_content(65536):
            data.extend(chunk)
            if len(data) > 10 * 1024 * 1024:
                response.close()
                raise ValueError("10MB 초과: 원본 다운로드 보류; 공식 링크 이용")
        response.close()
        zipped = data[:2] == b"PK"
        name = ident + (".zip" if zipped else ".csv")
        (DEST / name).write_bytes(data)
        report.update(
            {"status": "원본 다운로드 완료", "path": f"원본/{name}", "bytes": len(data)}
        )

        def inspect_csv(payload):
            for encoding in ["utf-8-sig", "cp949", "utf-16"]:
                try:
                    text = payload.decode(encoding)
                    reader = csv.reader(io.StringIO(text))
                    header = next(reader)
                    count = sum(1 for _ in reader)
                    return {"encoding": encoding, "columns": header, "rows": count}
                except (UnicodeError, StopIteration, csv.Error):
                    continue
            return {"note": "CSV 구조 별도 확인 필요"}

        if zipped:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                report["members"] = []
                for item in archive.infolist():
                    entry = {"name": item.filename, "bytes": item.file_size}
                    if (
                        item.filename.lower().endswith(".csv")
                        and item.file_size <= 30 * 1024 * 1024
                    ):
                        entry.update(inspect_csv(archive.read(item)))
                    report["members"].append(entry)
        else:
            report.update(inspect_csv(data))
    except Exception as exc:
        report.update({"status": "원본 수집 보류", "reason": str(exc)})
    results.append(report)
    print(ident, report["status"], report.get("bytes", ""), flush=True)
(ROOT / "원본_확인결과.json").write_text(
    json.dumps(results, ensure_ascii=False, indent=2)
)
for report in results:
    print(json.dumps(report, ensure_ascii=False), flush=True)
