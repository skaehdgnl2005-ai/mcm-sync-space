"""R6 데모 자산 패키징 — 05 §3 R6 / 00-common §9.

`r6_personas` 실험에서 **전항목 pass로 선별된 1장**을 페르소나별 납품 폴더로 옮긴다.

    experiments/_exports/demo/{persona_id}/photo.jpg · result.jpg · meta.json

**왜 experiments/_exports/인가**: 이 레포에는 `apps/`가 없다(B 레포 소유). 그리고
photo.jpg는 **동의된 실인물 사진**이고 result.jpg는 그 인물의 합성물이라, 루트 .gitignore가
`research/experiments/`를 통째로 미추적으로 막고 있는 구역 밖에 두면 안 된다 (06 §1 N4).
B 레포로의 전달 수단(PR·파일 전송)은 미확정이므로 **여기까지가 A의 산출물**이다.

**prep_assets.py와의 관계**: 정규화 규율(업스케일 금지 · EXIF 제거 · 알파→흰색)은 같지만
목적이 다르다. prep_assets는 **입력**(셀카·팩샷)을 실험 규격으로 맞추고, 이 파일은
**출력**을 납품 규격(3:4)으로 맞춘다. 그래서 별 파일이다.

3:4 규율:
- `result.jpg`는 gpt_base가 1152x1536(정확히 3:4)로 낸 산출물이라 **리사이즈·크롭하지 않는다.**
  검증만 하고 바이트 그대로 복사한다 — 재인코딩은 c1 판정 근거를 열화시킨다.
- `photo.jpg`는 셀카 원본 비율이 3:4가 아니다(686x1536 · 866x1496 · 1229x1536). 크롭 박스를
  **자동 계산하지 않고 SELECTION에 명시**한다. 자동 중앙 크롭은 세로로 긴 전신 셀카에서
  머리나 다리를 자르고, 그 판단은 result.jpg의 프레이밍과 나란히 봐야만 내릴 수 있다.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image

RESEARCH_ROOT = Path(__file__).resolve().parents[1]
EXP_DIR = RESEARCH_ROOT / "experiments" / "r6_personas"
OUT_ROOT = RESEARCH_ROOT / "experiments" / "_exports" / "demo"

TARGET_RATIO = 3 / 4
RATIO_TOLERANCE = 0.005


@dataclass(frozen=True)
class Persona:
    persona_id: str
    city: str
    purpose: str
    color: str
    sku: str
    wear_position: str
    selfie: Path
    photo_crop: tuple[int, int, int, int]
    """(left, top, right, bottom) — 셀카에서 잘라낼 3:4 영역. 원본 픽셀 좌표."""
    cell_id: str
    """전항목 pass로 선별된 이미지의 cell_id. scores.json이 근거다."""
    reason: str
    note: str = ""


def _crop_photo(persona: Persona, dst: Path) -> tuple[int, int]:
    with Image.open(persona.selfie) as im:
        im = im.convert("RGB")
        box = persona.photo_crop
        if box[0] < 0 or box[1] < 0 or box[2] > im.width or box[3] > im.height:
            raise ValueError(f"{persona.persona_id}: photo_crop {box} out of bounds {im.size}")
        out = im.crop(box)
    ratio = out.width / out.height
    if abs(ratio - TARGET_RATIO) > RATIO_TOLERANCE:
        raise ValueError(
            f"{persona.persona_id}: photo_crop ratio {ratio:.4f} != 3:4 — 박스를 고쳐라"
        )
    # 업스케일하지 않는다 (prep_assets와 같은 규율). 크롭 결과가 그대로 납품 해상도다.
    out.save(dst, "JPEG", quality=92, optimize=True)  # EXIF는 crop 시점에 이미 사라진다
    return out.size


def _copy_result(persona: Persona, dst: Path) -> tuple[int, int]:
    src = EXP_DIR / "images" / f"{persona.cell_id}.jpg"
    if not src.exists():
        raise FileNotFoundError(f"{persona.persona_id}: 선별 이미지가 없다 — {src}")
    with Image.open(src) as im:
        size = im.size
    ratio = size[0] / size[1]
    if abs(ratio - TARGET_RATIO) > RATIO_TOLERANCE:
        raise ValueError(f"{persona.persona_id}: result {size} is not 3:4 — 선별을 재확인하라")
    shutil.copyfile(src, dst)  # 재인코딩 없음
    return size


def _meta(persona: Persona, photo_size, result_size) -> dict:
    return {
        "persona_id": persona.persona_id,
        # 00-common §8 v2 slug. STEP1/2/3 각 1개 — 데모 재생 시 이 3개가 선택된 것으로 친다.
        "tags": {"city": persona.city, "purpose": persona.purpose, "color": persona.color},
        # 상품 카드(이름·소재·컬러&하드웨어·가격)는 **B의 제품 DB가 원본**이다.
        # 여기서 복제하면 두 곳이 갈라진다 — 조인 키만 넘긴다.
        "sku": persona.sku,
        "wear_position": persona.wear_position,
        "reason": persona.reason,
        "assets": {
            "photo": {"file": "photo.jpg", "w": photo_size[0], "h": photo_size[1]},
            "result": {"file": "result.jpg", "w": result_size[0], "h": result_size[1]},
        },
        "provenance": {
            "experiment": "r6_personas",
            "cell_id": persona.cell_id,
            "model": "gpt-image-2",
            "quality": "mid",
            "prompt_version": "pv1",
            "selfie": persona.selfie.name,
            "photo_crop": list(persona.photo_crop),
            "note": persona.note,
        },
    }


def build(personas: list[Persona], *, log=print) -> None:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    for persona in personas:
        out = OUT_ROOT / persona.persona_id
        out.mkdir(parents=True, exist_ok=True)
        photo_size = _crop_photo(persona, out / "photo.jpg")
        result_size = _copy_result(persona, out / "result.jpg")
        (out / "meta.json").write_text(
            json.dumps(_meta(persona, photo_size, result_size), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        log(f"{persona.persona_id}: photo {photo_size} · result {result_size} → {out}")
