"""Upload prepared assets and publish a GitHub Release using gh authentication."""

import argparse
import json
from pathlib import Path
import subprocess

from .seed_download import digest, read_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("deploy/artifacts"))
    parser.add_argument(
        "--target",
        required=True,
        help="Commit SHA containing the matching release.json",
    )
    args = parser.parse_args()
    m = read_manifest(args.directory)
    asset = args.directory / m["asset"]
    if digest(asset) != m["sha256"] or asset.stat().st_size != m["bytes"]:
        raise ValueError("Release asset does not match manifest")
    sums = args.directory / "SHA256SUMS"
    if sums.read_text() != f"{m['sha256']}  {m['asset']}\n":
        raise ValueError("SHA256SUMS does not match manifest")
    base = ["gh", "release"]
    repo = ["--repo", m["repository"]]
    subprocess.run(["gh", "auth", "status"], check=True)
    existing = subprocess.run(
        base + ["view", m["tag"], *repo, "--json", "isDraft"],
        capture_output=True,
        text=True,
    )
    if existing.returncode == 0:
        if not json.loads(existing.stdout)["isDraft"]:
            raise RuntimeError(
                "Release is already published; use a new version instead of replacing assets"
            )
        # Resume a previous partial upload only while the release is still a draft.
    else:
        notes = args.directory / "RELEASE_NOTES.md"
        notes.write_text(
            "SeatCom SQLite 초기 데이터\n\n"
            f"MongoDB library의 15개 컬렉션, 총 {sum(m['counts'].values()):,}개 문서를 포함합니다. "
            "실제 회원·개인 분석 데이터는 포함하지 않습니다.\n\n"
            "저장소를 클론한 뒤 `uv sync --frozen`과 `uv run --frozen python -m backend`를 실행하면 "
            "자동으로 다운로드·검증·적재합니다. 직접 받은 파일은 `SEATCOM_SEED_PATH`로 지정할 수 있습니다.\n\n"
            "배포 파일: 압축 SQLite, release.json, SHA256SUMS.\n"
        )
        subprocess.run(
            base
            + [
                "create",
                m["tag"],
                *repo,
                "--draft",
                "--target",
                args.target,
                "--title",
                "SeatCom SQLite data " + m["tag"],
                "--notes-file",
                str(notes),
            ],
            check=True,
        )
    subprocess.run(
        base
        + [
            "upload",
            m["tag"],
            *repo,
            str(asset),
            str(args.directory / "release.json"),
            str(sums),
            "--clobber",
        ],
        check=True,
    )
    subprocess.run(base + ["edit", m["tag"], *repo, "--draft=false"], check=True)
    print("https://github.com/" + m["repository"] + "/releases/tag/" + m["tag"])


if __name__ == "__main__":
    main()
