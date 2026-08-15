"""엔진 레지스트리 — env ENGINE으로 구현체를 주입한다 (03-mock-service-spec.md §6).

실엔진 추가 절차 (이 패키지 안에서만 끝난다):
  1. engines/<name>.py 에 GenerationEngine 서브클래스 작성
  2. 아래 _BUILDERS에 한 줄 등록
  3. Railway env ENGINE=<name> 변경 후 재배포
main.py / schemas.py / jobs.py / auth.py 는 수정하지 않는다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict

from .base import EngineRequest, EngineResult, GenerationEngine
from .mock import MockEngine

__all__ = [
    "EngineRequest",
    "EngineResult",
    "GenerationEngine",
    "MockEngine",
    "available_engines",
    "create_engine",
]

_Builder = Callable[[Path], GenerationEngine]

_BUILDERS: Dict[str, _Builder] = {
    "mock": lambda samples_dir: MockEngine(samples_dir=samples_dir),
}


def available_engines() -> list[str]:
    return sorted(_BUILDERS)


def create_engine(name: str | None, *, samples_dir: Path) -> GenerationEngine:
    key = (name or "mock").strip().lower()
    builder = _BUILDERS.get(key)
    if builder is None:
        raise RuntimeError(
            f"unknown ENGINE={name!r}. Available engines: {available_engines()}"
        )
    return builder(samples_dir)
