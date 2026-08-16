"""어댑터 레지스트리 — models.yaml의 `adapter` 문자열 → ProviderClient.

import는 **lazy**다. `ENGINE=mock`처럼 fake만 쓰는 실행에서 google-genai·openai
SDK가 설치돼 있지 않아도 러너가 뜬다 (06 §11-3의 승격 규율과 동형).
"""

from __future__ import annotations

import importlib
from typing import Any

_KNOWN_ADAPTERS = ("fake", "google_genai", "openai_images", "fal_flux")

_UNBUILT = {
    "google_genai": "B4에서 작성 (06 §12) — 지금은 --fake로 실행하세요.",
    "openai_images": "B4에서 작성 (06 §12) — 지금은 --fake로 실행하세요.",
    "fal_flux": "컨틴전시 전용 — R1 전멸(G1) 시에만 작성 (05 §3).",
}


def load(adapter: str) -> Any:
    """`providers/{adapter}.py`의 build()를 호출해 클라이언트를 만든다."""
    if adapter not in _KNOWN_ADAPTERS:
        raise ValueError(
            f"unknown adapter {adapter!r}; known: {', '.join(_KNOWN_ADAPTERS)}"
        )
    try:
        module = importlib.import_module(f"{__name__}.{adapter}")
    except ModuleNotFoundError as exc:
        if exc.name == f"{__name__}.{adapter}":
            raise RuntimeError(
                f"adapter {adapter!r} is not implemented yet: {_UNBUILT.get(adapter, '')}"
            ) from exc
        # SDK 미설치 — 어느 패키지가 없는지 그대로 보여준다.
        raise RuntimeError(
            f"adapter {adapter!r} needs a missing package: {exc.name}. "
            "pip install -r requirements.txt"
        ) from exc
    return module.build()
