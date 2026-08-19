"""`gpt_image` — **폴백 생성 엔진** (05 부록 A-16 ④ · A-17 ①).

라이브 기본값은 `gemini_nb`다. 이 엔진은 **env 한 줄로 갈아 끼우는 카드**이며 두 상황을 위해
등록해 둔다:

1. gemini 쪽이 흔들릴 때(키·쿼터·장애) — 품질은 두 벤더가 사실상 무차별이므로(A-17 ④ ·
   A-18 ③) 전환 자체가 품질 손실이 아니다.
2. **OpenAI 크레딧 $100 소진** — 사전 생성(R6 자산) 경로가 이 벤더였고 크레딧이 남아 있다.
   자비 부담인 gemini 대비 실질 $0이다(A-16 ③).

⚠ **대신 60초 계약의 여유가 1.7s로 줄어든다** — p95 58.3s. 회선이 열화되면 이 엔진은
계약을 못 지킨다(열화 세션 60초 내 성공 **0/23**, A-13 ②). 그래서 기본값이 아니다.
그 아래 최종 폴백은 `ENGINE=mock`이다.

설정은 `research/configs/models.yaml`의 `gpt_base` 그대로다 — R6·R8이 이 설정으로 돌았다.
"""

from __future__ import annotations

from typing import Any, ClassVar

from .live import LiveEngine


class GptImageEngine(LiveEngine):
    name: ClassVar[str] = "gpt_image"

    model_id: ClassVar[str] = "gpt-image-2"

    max_ref_images: ClassVar[int] = 2
    """A-3(R2c) 확정. gemini와 같은 값이라 벤더를 바꿔도 B가 보내는 입력은 그대로다."""

    provider_params: ClassVar[dict] = {
        "quality": "mid",  # 레지스트리 어휘. 어댑터가 'medium'으로 번역한다
        "size": "1152x1536",  # 정확히 3:4 · 1.77MP. quality=high는 폐기됐다 (A-13 ⑥)
    }
    """⚠ `input_fidelity`는 gpt-image-2가 **미지원**이다(400). 넣지 마라 — models.yaml의
    `gpt_fid` 항목이 그 반증 기록이다."""

    single_call_p50_seconds: ClassVar[float] = 45.0
    """관측 p50: R6 50.7s · R8 41.6s → 45.0. 이 값이면 재시도 조건(잔여 > p50)이 사실상
    성립하지 않는데, **그게 맞다** — A-15 ③이 "gpt로는 60초 안에 두 번 못 돈다"고 확정했다.
    조건을 코드가 알아서 만족시키지 못하게 두는 것이 상수를 낙관적으로 적는 것보다 안전하다."""

    def _build_client(self) -> Any:
        # ★ SDK import는 **여기 안에서만** (gemini_nb.py와 같은 이유).
        from .providers.openai_images import build

        return build()
