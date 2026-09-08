# MCM Sync-Space — 셀카 1장으로 시착 화보를 만들고 매장 예약까지 잇는 O2O 데모

> Hackathon demo: selfie + 3 mood tags → AI-generated editorial "try-on" shot on the customer's own photo → private in-store fitting reservation. This repo holds the generation service (FastAPI/Railway) and, more importantly, the **model bake-off harness** that chose the vendor with data.

**한 줄로**: 럭셔리 브랜드 해커톤 무대 데모. 고객이 데일리룩 셀카를 올리고 감성 태그를 고르면 AI가 어울리는 가방을 매칭해 고객 사진 위에 화보급 시착 이미지를 만들고, 확신이 가장 높아진 순간 오프라인 매장 프라이빗 피팅 예약으로 연결한다.

2인 팀 프로젝트. 이 저장소의 저자(Dev A)는 **생성 엔진·베이크오프 연구·데모 자산**을 담당했고, 웹 프론트(Dev B, `apps/web/`)는 별도 관리다.

## 무엇을 만들었나

- **생성 서비스** (`services/gen/`) — FastAPI, Railway 배포. `POST /generate` → 202 + job_id → 폴링, 60초 SLA. 엔진은 env 한 줄로 교체: `gemini_nb`(라이브) · `gpt_image`(폴백) · `mock`(롤백 목적지).
- **모델 베이크오프 하네스** (`research/genlab/`, 약 3,900 LOC) — 실험 매트릭스 곱집합 전개, 실행 전 dry-run 비용 견적과 **예산 캡**, 중단 지점 재개(resume), 동시 실행, `--fake` 무과금 E2E, 채점용 정적 HTML 갤러리(`gallery.py`), 부적격 게이트 + 타이브레이크 사슬 집계(`report.py`). YAML 설정 15종(`research/configs/`)으로 R1 스모크 → R8 라이브 정면 대조까지 재현 가능.
- **결정 기록부** (`05-gen-research-plan.md` 부록 A) — 라운드별 판정·번복·재채점을 전부 기록. "구두 일괄 판정 9/9 → 개별 채점 6/9"처럼 자기 방법론의 결함을 적발한 기록이 그대로 남아 있다.
- **인터페이스 계약 단일 소스** (`00-common.md`) — 계약 변경은 양측 합의 + 같은 커밋 문서 갱신 없이는 금지.

## 왜 이렇게 만들었나 (설계 결정)

- **가장 어려웠던 문제**: 이미지 생성 벤더를 "느낌"이 아니라 데이터로 고르는 것. 두 벤더가 같은 셀카·같은 상품·같은 프롬프트로 만든 결과를 블라인드 채점하고, 지연(p95)·거부율·비용을 같은 러너로 측정했다. 최종 선택 근거는 품질이 아니라 **지연**이었다: gemini p95 16.4초로 60초 계약에 43.6초 여유. R8 라이브 정면 대조에서 두 벤더 모두 c4 17/18로 품질은 동률이었다.
- **기각한 것**: 프롬프트 축 심화(pv2)는 표본 결손으로 "미판정-기각" 처리하고 정지 규칙을 발동했다. 결론을 못 내는 실험을 억지로 끝내지 않았다.
- **차별점**: 자기 회선 열화를 프로바이더 지연으로 오인한 측정(204 응답에 2.5~12.7초)을 철회하고, 러너에 생성 전 무과금 RTT 사전점검을 넣었다. 실패는 refused / fake-product / distortion / position-ignored / timeout / network 6유형으로 분류해 유형별 엔진 대응(재시도·프롬프트 순화·N=2 first-ok)을 확정했다.

## 어떻게 검증했나

- 자동화 유닛 테스트는 없다. 검증은 `--fake` 모드 E2E(무과금)와 라이브 라운드 R1~R8의 채점 결과로 했다.
- R2 본 베이크오프 36장 전면 재채점: gpt 6/9 셀 통과, c1 83% (문턱 70%) → GO.
- R8 라이브 정면 대조: gemini 7/9 vs gpt 6/9 셀 통과, c4 17/18 동률, 에스컬레이션 미발동.
- 채점 프로토콜 개정 이력(§4 c3 기준, 2층 판정, 블라인드 좌우 무작위화)이 문서에 남아 있어 각 숫자의 신뢰 범위를 읽을 수 있다.

## 기술 스택

Python 3.12 · FastAPI · google-genai (`gemini-3.1-flash-image`) · OpenAI Images (`gpt-image-2`) · Supabase Storage · Railway · YAML 실험 설정 · 정적 HTML 갤러리

---

## 구조

| 경로 | 담당 | 내용 |
|---|---|---|
| `services/gen/` | Dev A | 생성 서비스 (FastAPI / Railway). **배포 완료, 현재 `mock` 엔진** → [README](services/gen/README.md) |
| `apps/web/` | Dev B | 모바일 웹 + 매니저 뷰 (Next.js / Vercel). *미생성* |
| `apps/web/public/demo/` | A 자산 + B 구현 | 데모 모드 정적 자산 (페르소나 6종) |

### 배포 현황

| | 값 |
|---|---|
| `GEN_BASE_URL` | `https://mcm-sync-space-production.up.railway.app` (실엔진 교체 후에도 불변) |
| `GEN_API_KEY` | 레포에 두지 않는다. 별도 채널로 공유 (= Railway env `API_KEY`) |
| 엔진 | `mock` — 5초 지연 후 `static/samples`의 세로 3:4 샘플 URL 반환 |
| 계약 | v2 (`00-common.md` §5) — `product.wear_position` 필수, `style_hints.purpose` |

## 문서

| 문서 | 내용 |
|---|---|
| [PROJECT-CONTEXT.md](PROJECT-CONTEXT.md) | 프로젝트 맥락 요약 (AI 세션 투입용) |
| [00-common.md](00-common.md) | **단일 소스** — 인터페이스 계약, 스토리지 규약, 태그 사전, 데모 모드, 환경변수, 완성 기준 |
| [01-dev-a-generation.md](01-dev-a-generation.md) | Dev A 태스크 — 베이크오프, 입력 스펙, 프롬프트, 서비스, 데모 자산 |
| [02-dev-b-product.md](02-dev-b-product.md) | Dev B 태스크 — 웹 플로우, 상품 데이터, VLM, 매칭, 예약/매니저 뷰 |
| [03-mock-service-spec.md](03-mock-service-spec.md) | Mock 생성 서비스 구현 스펙 (Dev A 첫 산출물) |

인터페이스 계약(`00-common.md` §5) 변경은 두 개발자 합의 + 같은 커밋에서 문서 갱신 없이 금지.

## 서비스 흐름

```
[모바일 웹  Next.js/Vercel]
   │ (전부 server-side API Routes)
   ├──> Supabase (Postgres + Storage)
   ├──> VLM API (고객 사진 분석 — B 내부 전용)
   └──> [Gen Service  FastAPI/Railway] ──> 외부 이미지 생성 API
[매니저 태블릿] ── 5초 폴링 ──> 웹의 /manager
```

호출은 B → A 단방향. 비동기 job API (`POST /generate` → 202 + job_id → 2초 폴링, 최대 60초).
