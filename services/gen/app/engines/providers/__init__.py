"""승격된 프로바이더 어댑터 코어 — `research/genlab/providers/`에서 복사-이식 (R5, 06 §11-1).

**이 패키지는 import 시 아무 SDK도 끌어오지 않는다.** 어댑터 모듈(`google_genai`·
`openai_images`)은 각자 SDK를 모듈 최상단에서 import하므로, 여기서 그것들을 re-export하면
`ENGINE=mock` 기동이 SDK 없이 실패한다 (06 §13 · 05 R5 완료선 ①). 연구 쪽 `providers/__init__.py`가
`importlib`로 lazy 로딩을 했던 것과 같은 목적이고, 실엔진에서는 레지스트리가 이미
`engines/__init__.py`에 있으므로 **여기는 비워 두는 것**이 가장 단순한 형태다.

어댑터는 쓰는 쪽(`engines/gemini_nb.py` · `engines/gpt_image.py`의 `_build_client()`)이
직접 import한다. 그 두 모듈 자체가 빌더 함수 안에서만 import되므로 SDK도 거기서만 로드된다.
"""
