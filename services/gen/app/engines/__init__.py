"""엔진 레지스트리 — env ENGINE으로 구현체를 주입한다 (03-mock-service-spec.md §6).

실엔진 추가 절차 (이 패키지 안에서만 끝난다):
  1. engines/<name>.py 에 GenerationEngine 서브클래스 작성
  2. 아래 _BUILDERS에 한 줄 등록
  3. Railway env ENGINE=<name> 변경 후 재배포
main.py / schemas.py / jobs.py / auth.py 는 수정하지 않는다.

★ 빌더는 **lazy**다. SDK import(google-genai·openai)와 httpx는 빌더 함수 안에서만 일어나므로
`ENGINE=mock` 기동은 SDK가 하나도 설치돼 있지 않아도 성공한다 (06 §13 · R5 완료선 ①).
이 규율을 깨는 가장 쉬운 방법이 "편의를 위해" 이 파일 상단에서 엔진 클래스를 import하는
것이다. 하지 마라 — 그러면 mock 폴백이 SDK 설치 상태에 묶인다.

**롤백은 env `ENGINE=mock` 원복 하나뿐이다** (06 §11-6). 코드 재배포도, `GEN_BASE_URL`
변경도 필요 없다.
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


def _static_dir(samples_dir: Path) -> Path:
    """`main.py`가 넘기는 것은 `static/samples`이고, 라이브 엔진이 필요한 것은 그 부모인
    `static/`이다 (`STORAGE=local`이 `static/generated/`에 쓴다). main.py를 고치지 않고
    (diff 0줄 규율) 여기서 유도한다 — 두 경로의 관계는 main.py 상단에 고정돼 있다."""
    return samples_dir.parent


def _build_gemini_nb(samples_dir: Path) -> GenerationEngine:
    from .gemini_nb import GeminiNanoBananaEngine

    return GeminiNanoBananaEngine(static_dir=_static_dir(samples_dir))


def _build_gpt_image(samples_dir: Path) -> GenerationEngine:
    from .gpt_image import GptImageEngine

    return GptImageEngine(static_dir=_static_dir(samples_dir))


_BUILDERS: Dict[str, _Builder] = {
    "mock": lambda samples_dir: MockEngine(samples_dir=samples_dir),
    # 라이브 (05 부록 A-17 ① · A-18 ⑦). 기본값으로 쓸 엔진.
    "gemini_nb": _build_gemini_nb,
    # 폴백 (A-16 ④). 보유 비용 $0 — 안 쓰면 0원이고, 필요하면 env 한 줄로 전환한다.
    "gpt_image": _build_gpt_image,
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
