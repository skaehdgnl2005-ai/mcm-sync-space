"""`gemini_nb` — **라이브 생성 엔진** (05 부록 A-17 ① 확정 · A-18 ⑦ 유지 확인).

설정은 `research/configs/models.yaml`의 `gemini_ref2` 항목 그대로다. 이름이 `gemini_nb`인
이유는 그것이 **벤더/모델**의 이름이고, `gemini_nb` vs `gemini_ref2`는 연구에서 ref 장수를
가르기 위한 실험 팔 이름이었기 때문이다. 배포 설정은 ref 2장으로 확정됐으므로(A-17 ②)
여기에는 팔이 하나뿐이고, env 값은 벤더 이름으로 둔다.

**왜 gpt가 아니라 이것이 라이브인가** — 근거는 지연 하나다(A-17 ①④).
p95 **16.4s**로 60초 계약에 **43.6s 여유**가 남는다(gpt는 1.7s). 회선이 3배 열화돼도
계약 안이며, 실제로 열화 세션에서 gpt는 **60초 내 성공 0/23**이었다(A-13 ②).
**발표장 공용 Wi-Fi가 정확히 그 시나리오다.** 품질은 두 벤더가 사실상 무차별이고
(R8 셀 통과 7/9 vs 6/9), 오히려 제품 정합성(c1)은 gemini가 89% vs gpt 61%로 낫다(A-18 ③).
"""

from __future__ import annotations

from typing import Any, ClassVar

from .live import LiveEngine


class GeminiNanoBananaEngine(LiveEngine):
    name: ClassVar[str] = "gemini_nb"

    model_id: ClassVar[str] = "gemini-3.1-flash-image"
    """= Nano Banana 2. models.yaml의 문자열 그대로 (06 §3 — 코드는 모델명을 해석하지 않는다)."""

    max_ref_images: ClassVar[int] = 2
    """A-17 ② 채택값. 지연 페널티 0(p50 16.0 vs 16.5s)이고 로고 플레이트 각인 재현이 낫다."""

    provider_params: ClassVar[dict] = {
        "aspect_ratio": "3:4",  # 05 §2 통제 조건. Interactions가 3:4를 직접 지원한다
        "image_size": "1K",  # ~1MP. 라이브 화면에는 충분하고 단가 최저 구간이다
    }
    """⚠ `input_resolution`은 이 모델이 **미지원**이라 넣지 않는다 (400, models.yaml 주석)."""

    single_call_p50_seconds: ClassVar[float] = 15.0
    """관측 p50: R2 12.7s · R7 16.0~16.5s · R8 14.9s → 15.0. 재시도 판정선으로만 쓴다."""

    def _build_client(self) -> Any:
        # ★ SDK import는 **여기 안에서만** — `ENGINE=mock`이 google-genai 없이 부팅돼야
        #   한다 (06 §13 · R5 완료선 ①). 이 모듈 자체도 빌더에서만 import된다.
        from .providers.google_genai import build

        return build()
