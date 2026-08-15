# MCM Sync-Space

해커톤 무대 발표용 O2O 데모. 고객이 데일리룩 셀카 1장을 올리고 감성 태그 2개를 고르면,
AI가 어울리는 MCM 가방을 매칭해 **고객 본인의 사진 위에 화보급 시착 이미지를 생성**하고,
확신이 가장 높아진 순간에 **오프라인 플래그십 매장 프라이빗 피팅 예약**으로 연결한다.

**main 브랜치는 항상 데모 가능한 상태를 유지한다.**

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
