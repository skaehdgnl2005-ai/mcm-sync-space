"""manifest.jsonl — 1행 = 생성 시도 1회, append-only (06 §3 / 01 §4 "모든 생성 로깅").

append-only인 이유는 두 가지다.
1. 실패·거부 레코드가 지워지면 05 §7 리스크 분석의 원천 데이터가 사라진다.
2. 프로세스가 중간에 죽어도 이미 쓴 줄은 살아 있어야 재개(F1)가 성립한다.
   그래서 매 레코드마다 열고-쓰고-닫는다 (버퍼에 남겨두지 않는다).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

MANIFEST_NAME = "manifest.jsonl"


@dataclass
class Record:
    ts: str
    exp_id: str
    cell_id: str
    model: str
    model_id: str
    selfie: str
    product: str
    wear_position: str
    prompt_version: str
    rep: int
    prompt_text: str
    status: str
    image_path: Optional[str] = None
    latency_ms: int = 0
    est_cost_usd: float = 0.0
    error_message: Optional[str] = None
    provider_meta: dict = field(default_factory=dict)
    attempt: int = 1
    """1 = 최초 시도, 2 = 네트워크 오류 재시도분 (06 §5-6). 같은 cell_id가 2행일 수 있다."""

    def to_json(self) -> str:
        # ensure_ascii=False — 프롬프트에 한국어 DB 원문이 그대로 들어간다.
        return json.dumps(asdict(self), ensure_ascii=False)


def append(path: Path, record: Record) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(record.to_json() + "\n")


def read(path: Path) -> list[dict[str, Any]]:
    """깨진 줄은 건너뛴다 — 강제 종료로 마지막 줄이 잘렸다고 전체를 못 읽으면 안 된다."""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in _lines(path):
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _lines(path: Path) -> Iterator[str]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield line


def latest_by_cell(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """cell_id별 마지막 레코드. 재시도로 2행이 된 셀은 나중 시도가 결론이다."""
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        cell_id = row.get("cell_id")
        if cell_id:
            latest[cell_id] = row
    return latest
