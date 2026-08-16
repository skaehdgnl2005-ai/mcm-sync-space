"""genlab — 생성 엔진 연구 도구 (06-gen-research-tools-spec.md).

이 패키지는 **실엔진의 첫 초안**이다. R5에서 `providers/{winner}.py`와 `prompts.py`가
services/gen으로 복사-이식된다(import가 아니라 복사 — research/는 Railway 빌드
컨텍스트 밖이라 참조하면 배포가 깨진다, 06 §1 N5).
"""

__all__ = ["cli", "gallery", "manifest", "prompts", "providers", "report", "runner"]
