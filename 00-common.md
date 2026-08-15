# MCM Sync-Space — 공통 문서 (v1)

Dev A / Dev B가 합의한 내용의 **단일 소스(Single Source of Truth)**.
이 문서, 특히 §5 인터페이스 계약을 바꿀 때는 반드시 두 사람이 합의하고, 코드 변경과 같은 커밋에서 이 문서를 함께 갱신한다.

---

## 1. 목표

- 산출물: **무대 발표용 라이브 데모** — 모바일 웹에서 사진 1장 업로드 + 태그 2회 선택 → AI 시착 화보 생성 → 원클릭 매장 예약 → 매니저 태블릿에 예약 정보 표시.
- 품질 우선순위: **① 생성 화보의 제품 정합성 → ② 화보 감성/톤 → ③ UI 완성도 → ④ 속도**.
  속도는 엔진이 아니라 로딩 연출로 커버한다(Dev B 담당).

---

## 2. 확정 스코프

### 포함 (P0)

| 항목 | 담당 |
|---|---|
| 모바일 웹 전체 플로우 (업로드 → 태그 → 로딩 → 화보 → 예약) | B |
| 상품 DB 30~50 SKU + 태그 체계 | B |
| VLM 고객 사진 분석 + 매칭 엔진 | B |
| 생성 엔진 (사진+상품 → 화보) | A |
| 매니저 뷰 (태블릿, 폴링) | B |
| 데모 모드 (§9) | A(자산) + B(구현) |

### 제외 / 축소

- **Pinecone 등 벡터 DB** → 규칙 필터 + LLM 최종 선택으로 대체
- **스마트 미러 / 매장 크로스셀링** → 발표 슬라이드 목업 (개발 안 함)
- **실시간 푸시** → 폴링
- **회원/로그인** → localStorage 세션 ID
- **"3초 생성"** → 목표 60초 내 완료, 체감은 로딩 연출로 커버
- **배경을 도시 풍경으로 바꾸는 연출** → 스트레치(P2). **v1 정책: 원본 사진의 배경·인물 유지, 가방 합성 + 톤 보정까지만.**

---

## 3. 역할 경계

| | Dev A | Dev B |
|---|---|---|
| 소유 | 생성 엔진 그 자체: **(사람 사진 + 확정된 상품 이미지) → 화보** 변환 | 그 외 전부: 웹, 상품 DB, VLM 분석, 매칭, 오케스트레이션, 예약/매니저 뷰, 데모 모드 |

- 호출 방향은 **B → A 단방향**. A는 순수 변환 서비스처럼 동작한다.
- **VLM 분석 결과는 B 내부 전용**이며 계약에 포함하지 않는다. A에게는 `style_hints`의 `lighting` 값 하나만 흘러간다.
- 생성이 막혀도 B를 끌어들이지 않는다. 스코프를 줄이는 방향(사전 생성 피벗)으로만 움직인다.

---

## 4. 아키텍처 & 스택

```
[모바일 웹 (Next.js / Vercel)]
   │  업로드 / 분석 / 생성시작 / 폴링  (API Routes, 전부 server-side 호출)
   ├──> [Supabase Postgres]   products / reservations
   ├──> [Supabase Storage]    uploads / products / generated
   ├──> [VLM API]             고객 사진 분석 (B 내부 전용)
   └──> [Gen Service (FastAPI / Railway)] ──> [이미지 생성 API]
                     └── 생성 결과 업로드 ──> Supabase Storage
[매니저 태블릿] ── 5초 폴링 ──> [모바일 웹 /manager]
```

| 구성 | 선택 | 이유 |
|---|---|---|
| 웹 | Next.js(App Router) + TypeScript + Tailwind + shadcn/ui, Vercel 배포 | AI 코딩 도구가 가장 잘 다루는 조합(학습 데이터 최다), 배포 마찰 최소 |
| 데이터 | Supabase (Postgres + Storage) | DB·스토리지·대시보드 일체형, 시드/디버깅 쉬움 |
| 생성 서비스 | Python 3.11 + FastAPI, Railway 배포 | 이미지 API 생태계가 Python 중심, 컨테이너라 장시간 작업 타임아웃 없음 |
| VLM/LLM | Gemini Flash 계열 또는 GPT-4o 계열 (structured output 지원 최신 모델) | 분석·매칭용, 저렴하고 JSON 출력 안정 |
| 이미지 생성 | **A의 베이크오프로 결정** (후보는 Dev A 문서 §2) | 셀프호스팅(SD+ControlNet) 금지 |

---

## 5. 인터페이스 계약 — Generation API

- Base URL: 환경변수 `GEN_BASE_URL`
- 인증: 모든 요청 헤더 `X-API-Key: {GEN_API_KEY}` (`/health`만 예외). 불일치·누락 시 **401** `{ "error_code": "UNAUTHORIZED", "message": "..." }`
- **브라우저에서 직접 호출 금지.** 항상 B의 Next.js API 라우트가 서버-사이드로 프록시한다 (키 보호, CORS 불필요).
- **이미지는 항상 URL로 전달한다. base64 금지.** URL은 공개 읽기 가능한 Supabase Storage URL.

### POST /generate

```jsonc
// Request
{
  "user_photo_url": "https://.../assets/uploads/{session}/{ts}.jpg",
  "product": {
    "id": "heritage-crossbody-cognac",
    "name": "MCM 헤리티지 비세토스 크로스백",
    "category": "crossbody",            // §8 카테고리 slug
    "pattern": "visetos_cognac",
    "material": "canvas_leather",
    "ref_image_urls": ["https://.../products/heritage-crossbody-cognac/1.jpg"]
    // ref 이미지는 §7의 상품컷 스펙을 따른다
  },
  "style_hints": {                      // 선택. A는 품질에 도움될 때만 사용, 무시 가능
    "city": "milano",                   // §8 slug
    "tpo": "cafe",
    "lighting": "natural"               // VLM 추출값 또는 null
  }
}

// Response 202
{ "job_id": "j_01HXX..." }

// Response 400
{ "error_code": "BAD_INPUT", "message": "..." }
```

### GET /generate/{job_id}

```jsonc
// Response 200
{
  "status": "queued" | "processing" | "done" | "failed",
  "image_url": "https://.../assets/generated/{job_id}.jpg",   // done일 때만
  "error_code": null | "BAD_INPUT" | "UPSTREAM_ERROR" | "TIMEOUT" | "UNKNOWN",
  "elapsed_ms": 18234
}

// Response 404 — 존재하지 않는 job_id
{ "error_code": "UNKNOWN", "message": "job not found" }
```

- 네 키(`status` / `image_url` / `error_code` / `elapsed_ms`)는 **항상 포함**된다. 해당 없으면 `null` (키 생략 없음).
- `elapsed_ms`는 POST 수리 시점부터의 경과 ms이며, `done`/`failed` 이후에는 종결 시점 값으로 고정된다.

### GET /health → `200 "ok"` (인증 불필요)

### 폴링 / 타임아웃 규약

- B: 2초 간격 폴링, 최대 60초 대기. 초과 또는 `failed` 시 → 신규 POST로 1회 재시도 → 그래도 실패 시 데모 모드 폴백(§9).
- A: upstream 이미지 API 호출에 자체 타임아웃을 두고, **60초 내 반드시 done 또는 failed로 종결**시킨다. 영원한 processing 금지.

### Mock 규약 (통합 순서의 핵심)

- A는 실제 엔진과 무관하게 **가장 먼저** 이 계약대로 동작하는 mock을 배포한다 (내부는 5초 지연 후 고정 샘플 이미지 URL 반환).
- B는 mock 기준으로 전체 플로우를 끝까지 연결한다. 이후 A는 내부 구현만 교체하며, 계약이 지켜지는 한 B 코드는 손대지 않는다.
- 실패·지연 경로 테스트용으로 mock은 `POST /generate` 요청 헤더 **`X-Mock-Scenario`**를 지원한다 (구현: `03-mock-service-spec.md` §5).
  `success`(기본) / `fail`→`UPSTREAM_ERROR` / `timeout`→`TIMEOUT` / `slow`→60초 초과까지 `processing` 유지.
  미정의 값은 `success`로 처리하며, 실엔진 교체 후에는 조용히 무시된다(에러 아님). 프로덕션 경로에서는 이 헤더를 보내지 않는다.

---

## 6. 스토리지 규약

Supabase Storage, 버킷 `assets` (public read).

| 경로 | 용도 | 쓰기 |
|---|---|---|
| `/uploads/{session_id}/{ts}.jpg` | 사용자 업로드 원본 | B |
| `/products/{product_id}/{n}.jpg` | 상품 레퍼런스 컷 | B |
| `/generated/{job_id}.jpg` | 생성 결과 | A |

데모 모드 자산은 스토리지가 아니라 **레포 내 정적 파일로 커밋**한다(§9) — 발표장 네트워크와 무관하게 동작해야 하므로.

---

## 7. 입력 스펙 (A 확정 → B 반영, 순서 의존)

아래 두 스펙의 **정의 권한은 A**에게 있다. A의 베이크오프 결과로 확정되며, **확정 전까지 B는 대량 상품 수집과 업로드 검증 구현을 시작하지 않는다.**

### 7.1 상품 레퍼런스 이미지 스펙 — *A 확정 후 기입*

| 항목 | 값 |
|---|---|
| 배경 | (예: 화이트/뉴트럴 팩샷) |
| 필요 각도 / 장수 | (예: 정면 1 + 3/4 1) |
| 최소 해상도 | |
| 기타 금기 | |

### 7.2 사용자 사진 스펙 — *A 확정 후 기입*

| 항목 | 값 |
|---|---|
| 프레이밍 | (예: 무릎 위 이상, 정면~반측면) |
| 인원 | 1명 |
| 최소 해상도 | |
| 조명 권고 / 금기 | (예: 강한 역광 금지) |

---

## 8. 공용 태그 사전

**slug는 프론트, DB, API, 프롬프트에서 동일하게 사용하며 불변이다.** 필터 규칙(우측 열)은 초기값이고, 실제 카탈로그 확보 후 B가 조정한다.

### STEP 1 — 도시/무드 (1개 선택)

| slug | 표시명 | B: DB 필터 의도 | A: 프롬프트/그레이딩 의도 |
|---|---|---|---|
| milano | #밀라노 | 헤리티지·클래식 라인 | 우아한 유러피언 클래식, 웜 톤 |
| tokyo | #도쿄 | 스타크·콜라보·스트릿 | 힙·스트릿, 하이 콘트라스트 |
| newyork | #뉴욕 | 모던·노로고 | 모던 시크, 쿨 뉴트럴 |
| munich | #뮌헨 | 헤리티지·트래블 | 헤리티지 여행 무드 |
| paris | #파리 | 빈티지·로맨틱 | 소프트 로맨틱, 필름 감성 |
| seoul | #서울 | 트렌디 신상 | K-시크, 클린 톤 |
| copenhagen | #코펜하겐 | 미니멀 라인 | 스칸디 미니멀, 밝은 뉴트럴 |
| la | #LA | 애슬레저·나일론 | 캘리포니아 선라이트, 비비드 |
| london | #런던 | 클래식 포멀 | 젠틀 클래식, 딥 톤 |

### STEP 2 — TPO (1개 선택)

| slug | 표시명 | B: 카테고리 필터 (초기 규칙) |
|---|---|---|
| business | #비즈니스 미팅 | tote, briefcase |
| cafe | #주말 카페 투어 | crossbody |
| rooftop | #루프탑 파티 | shoulder, messenger |
| airport | #공항 출국장 | backpack, weekender |
| staycation | #호캉스 | bucket, hobo |
| exhibition | #전시회 관람 | mini, clutch |
| dining | #파인 다이닝 | clutch, mini_tophandle |

### lighting (VLM 추출 → style_hints)

`natural` | `warm` | `night`

---

## 9. 데모 모드 (정식 P0 기능)

목적: 발표장 네트워크 장애, 외부 API 장애, 생성 뽑기 실패를 전부 무력화하는 발표 보험 + 일반 모드 실패 시 폴백 경로.

- 진입: URL `?demo=1` → 랜딩에 페르소나 선택 UI 노출 (발표자용, 숨김 스타일).
- 동작: 선택한 페르소나의 사진·태그·매칭 결과·화보가 전부 정적 자산에서 로드. **외부 API 0회 호출**로 전체 플로우 재생. 로딩 연출은 동일하게 재생(12~15초로 단축).
- 자산 위치: `apps/web/public/demo/{persona_id}/photo.jpg`, `result.jpg`, `meta.json`
- 분담: **자산(페르소나 사진 + 최고 퀄리티 화보 + meta) 납품 = A**, **토글·재생 구현 = B**. 페르소나 사진 선정은 둘이 함께.

### 페르소나 6종 (기획안 시나리오 A~F)

| id | 태그 | 인물/스타일 | 매칭 상품 |
|---|---|---|---|
| p1 | milano + cafe | 여성, 슬림 포멀, 무채색·베이지 | 헤리티지 크로스백 (코냑 비세토스) |
| p2 | tokyo + rooftop | 남성, 오버핏 스트릿 | 블랙 스테이션 메신저백 |
| p3 | paris + exhibition | 여성, 파스텔 슬림 | 파스텔 미니 클러치 |
| p4 | seoul + business | 셋업 수트, 모노톤 | 블랙 가죽 브리프케이스/토트 |
| p5 | copenhagen + staycation | 린넨, 화이트·베이지 | 아이보리 미니멀 버킷백 |
| p6 | la + rooftop | 비비드 컬러, 스포티 | 네온 포인트 숄더백 |

---

## 10. 레포 & 환경변수

모노레포 1개. **main은 항상 데모 가능한 상태를 유지한다.**

```
mcm-sync-space/
├── apps/web/            # Dev B — Next.js
│   └── public/demo/     # 데모 모드 정적 자산 (A 납품 → 커밋)
├── services/gen/        # Dev A — FastAPI
├── docs/                # 이 문서 3개
└── README.md
```

| 위치 | 변수 | 비고 |
|---|---|---|
| apps/web | `NEXT_PUBLIC_SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | |
| apps/web | `VLM_API_KEY` | Gemini 또는 OpenAI |
| apps/web | `GEN_BASE_URL`, `GEN_API_KEY` | A가 배포 후 전달 |
| services/gen | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | B가 세팅 후 전달 |
| services/gen | 이미지 생성 API 키 (베이크오프 결과에 따름) | |
| services/gen | `API_KEY` | 인바운드 검증용, `GEN_API_KEY`와 동일 값 |

---

## 11. 완성 기준 (Demo-Ready)

- [ ] 골든 패스 라이브 1회 관통: QR 접속 → 사진 업로드 → 태그 2회 → 로딩 → 화보 → 예약 → 매니저 뷰 반영
- [ ] 동일 경로가 **데모 모드에서 외부 API 없이 100% 재현**
- [ ] 페르소나 6종 화보가 제품 정합성 체크리스트(Dev A 문서 §2) 전 항목 통과
