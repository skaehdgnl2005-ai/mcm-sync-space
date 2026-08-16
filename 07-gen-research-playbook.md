# Dev A — 생성 엔진 연구 실행 플레이북 (v1)

> `05-gen-research-plan.md`(연구 계획)와 `06-gen-research-tools-spec.md`(툴 스펙)를 **실제로 굴리는 순서 가이드**. "다음 세션에서 무엇을 시키고, 무엇을 확인해야 하는가"를 스텝 단위로 적는다. 판정 규칙·스키마 등 상세는 05/06이 우선하며, 이 문서는 절차만 다룬다.

---

## 0. 전체 그림 — 세션 5개, 그리고 왜 이 순서인가

| 세션 | 내용 | 걸리는 시간 | 선행 조건 | 끝나면 생기는 것 |
|---|---|---|---|---|
| **S1** | 툴 코어 구현 (빌드 오더 B1~B2) | ~4h | 없음 — **API 키·돈 불필요** | fake 프로바이더로 실행→채점→집계가 도는 파이프라인 |
| **S2** | 실입력 연결 (B3~B4) + R0 마감 + R1 스모크 | ~4h | S1, API 키 2종, 셀카·상품컷 | 생존 모델 확정 (게이트 G1) |
| **S3** | R2 본 베이크오프 | 3~5h | S2 | 승자·Go/No-Go·스카프 결정 + **00-common §7 기입 = Dev B 언블록** (G2) |
| **S4** | R3 프롬프트 심화 + R4 안정화 | 4~5h | S3 | pv2 + N/재시도/타임아웃 정책 동결 (G3) |
| **S5** | R5 실엔진 이관 + R6 데모 자산 | 4~6h | S4 | Railway 실엔진 가동 (G4) + p1~p3 납품 |

순서의 논리:

- **S1을 fake로 먼저 완성하는 이유**: 나중에 실제 API 호출이 실패했을 때, 그 원인이 "내 러너 버그"인지 "API 문제"인지 즉시 구분되게 만들기 위해서다. 파이프라인 전체(실행→저장→로깅→채점→집계)를 $0으로 검증해두면, 이후의 모든 실패는 어댑터 아니면 외부 요인으로 좁혀진다. 또한 API 키 발급이 늦어져도 툴 개발이 멈추지 않는다.
- **S3가 팀 크리티컬 패스인 이유**: Dev B의 대량 이미지 수집·업로드 검증 구현이 00-common §7 스펙 확정에 걸려 있다(00-common §7 서두). 승자 모델보다 §7 기입이 1순위 산출물인 이유다(05 §1).
- **S4~S5가 뒤인 이유**: 프롬프트 튜닝과 이관은 승자 모델이 정해져야 의미가 있다. 반대로 데모 자산(R6)은 Go/No-Go와 무관하게 무조건 수행한다(01 §7 — 발표 보험).

---

## 1. S1 — 툴 코어 구현 (B1~B2)

### B1이 무엇이고, 왜 하는가

B1 = **실험 러너와 그 뼈대**. 러너는 이 연구의 척추다 — R1 스모크(4장)부터 R6 데모 자산(30장)까지 모든 생성이 같은 러너를 통과하고, 러너가 남기는 manifest(생성 1회 = 1행 로그)가 재개·비용 추적·리포트·나중의 §7 스펙 근거까지 전부의 원천 데이터가 된다. fake 프로바이더(입력 셀카를 그대로 돌려주는 10줄짜리)를 같이 만드는 이유는 위에서 말한 "인프라 버그와 API 버그의 분리"다.

### B1 스텝 (예상 ~2h)

1. **`.gitignore` 2줄 추가** — `research/assets/`, `research/experiments/` (레포 루트 .gitignore에).
   - *왜 코드보다 먼저*: assets에는 실인물 셀카(개인정보)와 공식몰 이미지(저작권)가, experiments에는 수백 장의 생성물이 들어간다. 실수로 커밋되기 전에 막는다.
   - *확인*: 빈 `research/assets/x.txt`를 만들고 `git status`에 안 뜨는지.
2. **디렉터리 골격 생성** — 06 §2의 트리 그대로: `research/{configs, genlab/providers, assets/{selfies, products}, experiments}` + `genlab/__init__.py`, `genlab/__main__.py`.
3. **파이썬 환경** — `python -m venv research/.venv` → activate → `pip install -r research/requirements.txt` (httpx, pyyaml, pillow, python-dotenv, google-genai, openai — SDK 2종은 B4에서 쓰지만 미리 설치해도 무방). `.env.example`도 이때 작성.
4. **`providers/base.py`** — 06 §4의 `GenCall` / `GenOutcome` / `ProviderClient` 코드블록을 **그대로** 옮긴다.
   - *왜 그대로*: 이 인터페이스가 러너·어댑터 4종·실엔진 승격까지 전부의 계약이다. 여기서 변형하면 이후 전부가 흔들린다. bytes-in/bytes-out(파일·URL I/O는 호출자 책임)이 승격 호환성의 핵심.
5. **`providers/fake.py`** — `user_photo` bytes를 그대로 반환, `latency_ms`는 0.5~2초 랜덤 시뮬레이션. status는 항상 ok.
6. **`prompts.py` 최소 초안** — `WEAR_POSITION_DIRECTIVES` 5종 + `TEMPLATES = {"pv1": <간단한 초안 한 문단>}` + `assemble()`. 완성형 pv1은 B3에서 다듬는다.
   - *왜 지금*: 러너가 `assemble()`을 호출하므로 스텁이라도 있어야 한다. 버전 수정 금지 원칙(06 §8)은 "**실 API 생성에 한 번이라도 쓰인 버전**"에만 적용 — fake 검증 단계에서는 자유롭게 다듬어도 된다.
   - *주의*: 이 파일은 승격 대상 — **import는 stdlib만** (yaml·genlab 내부 모듈 금지).
7. **configs 작성** — `models.yaml`에 실모델 2종(값은 placeholder) **+ B1 검증용 fake 항목**을 추가한다:
   ```yaml
   fake: { adapter: fake, model_id: fake, params: {}, cost_per_image_usd: 0, enabled: true }
   ```
   `products.yaml`·`selfies.yaml`은 placeholder 이미지(아무 jpg)로 시작(실자산은 B3). 검증용 실험 config `r0_faketest.yaml`(models: [fake], 셀카 2 × 상품 2, reps 1 = 4장)도 만든다.
8. **`manifest.py`** — `append_record(path, dict)`(JSONL 1행 append) + `read_manifest(path)`. append-only인 이유: 재개(F1)와 "모든 생성 로깅"(F2)의 기반이자, 사후 감사 가능한 유일한 기록.
9. **`runner.py`** — 06 §5의 6단계 그대로: config 로드 → 곱집합 전개 → cell_id 부여 → **dry-run 견적(장수·Σ비용, budget_cap 초과 시 거부)** → asyncio 세마포어(global 4 / per-model 2) → 셀 실행(자산 로드 → assemble → 어댑터 호출 → 이미지 저장 + manifest append). 재개 = manifest 성공 레코드 + 파일 존재 시 스킵. 재시도 = 네트워크 오류만 1회, refused는 0회(기록 후 진행 — 정책 거부는 재시도가 아니라 데이터다).
10. **`cli.py` + `__main__.py`** — `run` 서브커맨드만 먼저 (`--dry-run`, `--resume`, `--only model=…`).
11. **B1 검증** (06 §13 발췌):
    ```powershell
    python -m genlab run configs/r0_faketest.yaml --dry-run   # 4장·$0.00 견적 출력
    python -m genlab run configs/r0_faketest.yaml             # 이미지 4장 + manifest 4행
    python -m genlab run configs/r0_faketest.yaml --resume    # "0건 생성, 4건 스킵"
    # budget_cap_usd를 0으로 낮춘 사본으로 실행 → 거부되는지
    ```

### B2가 무엇이고, 왜 하는가

B2 = **채점 갤러리와 리포트 집계기**. 러너가 만든 이미지 더미를 "결정"으로 바꾸는 나머지 절반이다. 갤러리가 없으면 ~150장을 폴더에서 하나씩 열어 원본·레퍼런스와 눈으로 대조해야 하고(그 순간 채점 일관성이 무너진다), 리포트가 없으면 05 §5의 부적격 게이트·타이브레이크 사슬을 매번 손으로 계산해야 한다. **B2까지 끝나면 연구 전체 워크플로(실행→채점→순위→결정)를 돈 한 푼 안 쓰고 리허설할 수 있는 상태**가 되며, 그게 S1의 종료 조건이다.

### B2 스텝 (예상 ~2h)

1. **`gallery.py`** — manifest를 읽어 단일 HTML 생성. 구현 요점(06 §6):
   - manifest 데이터를 **HTML 안에 JSON으로 인라인** (*왜*: `file://`로 연 페이지는 fetch가 막힌다 — 서버를 안 띄우는 대가)
   - 이미지는 `images/` 상대경로 참조, 행 = 셀카×상품, 열 = 모델×pv×rep
   - 이미지 클릭 → 라이트박스에서 **원본 셀카 + 상품 레퍼런스 컷과 나란히** (*왜*: c1 "그 로고가 그 로고인가" 판정은 대조 없이는 불가능)
   - 셀별 c1~c6 체크박스 + note 입력 → localStorage 저장(키: exp_id+cell_id) → "Export scores.json" 버튼(Blob 다운로드)
2. **갤러리 검증** — fake 산출물로: 브라우저에서 열기 → 몇 셀 채점 → **새로고침 후 채점 유지 확인** → export로 scores.json 다운로드.
3. **`report.py`** — manifest + scores.json → 모델별: 셀 통과 수(best-of-N: N장 중 ≥1장 전항목 pass), c1~c6 통과율, refused/error 건수, 지연 p50/p95, Σ비용. 그리고 **05 §5의 부적격 게이트와 타이브레이크 사슬을 산식으로 구현**해 순위 "제안" + 근거 라인 → `summary.md`.
   - *왜 산식으로*: R2 당일 판정에서 자의성·실수를 제거하고, 부록 A에 붙일 근거를 자동으로 얻는다. 단, 확정은 항상 사람(05 §4).
4. **리포트 검증** — fake 채점 데이터로 순위표가 나오는지, 게이트 위반(예: 가짜로 refused 5행 주입)이 "부적격"으로 표시되는지.

### S1 완료 기준

06 §13 중: 러너 3종 검증(생성/재개/캡 거부) + 갤러리 3종(열림/유지/export) + 리포트 1종(순위 제안). **여기까지 API 키 0개, 비용 $0.**

---

## 2. S2 — 실입력 연결 (B3~B4) + R1 스모크

### B3: 자산·프롬프트 (예상 ~1h) — "연구의 입력물"

1. **상품컷 확보** — 공식몰에서 베이크오프 3종(No.08/09/10) + **스카프(MEFGAMM12CO001)** 이미지를 직접 저장(01 §2 — B 대기 금지) → `assets/products/{sku}/1.jpg`.
2. **셀카 3장 확보** — casual / smart_daily / formal 각 1장, **동의 확인**, p2(남성 페르소나) 대비 남성 1장 포함 권장 → `assets/selfies/`.
3. **`prep_assets.py` 구현 + 실행** — 리사이즈(긴 변 1536) + EXIF 제거 + 레지스트리 대조.
   - *왜 EXIF 제거*: GPS 등 개인정보가 외부 API로 전송되는 것을 막고, EXIF 회전 메타데이터 때문에 API가 이미지를 눕혀서 처리하는 사고를 방지.
4. **configs 실데이터 기입** — `products.yaml`에 팀원 시트의 name/material/color_hardware **원문 그대로**(계약 필드와 1:1 — 승격 시 프롬프트 조립기가 무수정으로 돌게 하는 장치), `selfies.yaml`에 3장 등록.
5. **`prompts.py` pv1 완성** — 영어 지시 템플릿 + 한국어 DB 원문 임베드, "레퍼런스의 제품을 정확히 그대로, 인물·배경은 보존" **편집 프레이밍** + 부정 지시 블록(로고 변형·신체 변형 금지). wear_position 5종 지시는 01 §4 표의 의도대로.
6. **검증** — 4 SKU × pv1 조립 프롬프트를 출력해 육안 확인: 원문 텍스트가 온전히 들어가는지, cross/shoulder/back/neck 지시가 SKU별로 올바른지.

### B4: 실프로바이더 2종 (예상 1.5~2h) — "돈이 들기 시작하는 유일한 부분"

1. `.env`에 `GOOGLE_API_KEY`, `OPENAI_API_KEY` 세팅.
2. **모델 ID 확인 → `models.yaml` 기입** — 각 프로바이더 문서에서 이미지 **편집**(단순 생성 아님) 최신 모델 ID와 단가를 확인해 `model_id`·`cost_per_image_usd` 갱신. (*왜 설정 문자열인가*: 모델명 개편 주기가 해커톤 주기보다 짧다 — 06 §3.)
3. **`providers/google_genai.py`** — SDK로 contents=[사용자 사진, ref들, 프롬프트] 구성, safety block 응답을 `refused`로 분류, `timeout_s` 적용.
4. **1콜 스모크** — `python -m genlab run configs/r1_smoke.yaml --only model=gemini_nb --dry-run` → 실행 → 갤러리로 결과 확인.
5. **`providers/openai_images.py`** — images.edit 계열 다중 입력, 정책 거부 → `refused`, `params.quality` 반영. 동일하게 1콜 스모크.
6. *어댑터 공통 주의*: `refused`와 `error`의 구분은 어댑터 책임(06 §4) — 이 분류가 "실인물 편집 정책 거부"라는 최대 리스크의 측정 데이터다.

### R1 스모크 실행과 G1 판정 (05 §3 R1)

1. `r1_smoke.yaml` = 셀카 smart_daily × 상품 No.09(비세토스 — 가장 어려운 패턴) × 2모델 × N2 = 4장.
2. 실행 → 갤러리에서 확인하며 **관찰 6항목 기록**(05 R1 표: 정책 거부 / 로고 재현 / 다중 이미지 방식 / 종횡비 / 워터마크 / 지연).
3. **G1 판정 3분기**: 2종 생존 → S3로 / 1종 생존 → 단독 진행 + 백업 부재 리스크 기록 / 0종 → **컨틴전시**: fal.ai 키 발급 → `providers/fal_flux.py`(httpx REST) 작성 → `models.yaml`의 flux_kontext를 `enabled: true` → R1 재실행.
4. 관찰 결과를 05 부록 A 첫 행에 기록.

---

## 3. S3 — R2 본 베이크오프 (팀 크리티컬 패스)

> 이 세션의 진짜 산출물은 승자 모델이 아니라 **00-common §7 기입 커밋**이다. 이게 나가야 Dev B와 팀원의 대량 이미지 수집이 시작된다.

1. **사전 점검**: G1 통과, pv1 동결 선언(이후 R2 끝까지 수정 금지 — 모델 간 공정성 통제), `r2_bakeoff.yaml`(2모델×9셀×N2)·`r2b_scarf.yaml` 준비, `--dry-run`으로 견적(~40장, $2~5) 확인.
2. **실행** — 동시성 덕에 벽시계 30~60분. 도중에 결과가 쌓이는 대로 갤러리를 미리 열어봐도 된다(채점은 전량 완료 후).
3. **채점 45~60분** — 05 §4 규칙: 이미지 단위, **애매하면 fail**, fail마다 note 한 줄(이 note가 §7 스펙과 R4 실패 분류의 원천이다). 원본·레퍼런스 대조를 건너뛰지 않는다.
4. **report 실행** → 순위 제안 확인. 판정 순서: ① 부적격 게이트(거부+오류율 >20% / p95 >40s / 워터마크) ② 주 지표(셀 통과 수) ③ 동점 시 타이브레이크 사슬. 제안을 갤러리로 재확인 후 **A가 확정**. (권장: 확정 전 B의 30분 스팟체크.)
5. **Go/No-Go** — 05 §5 표의 수치 기준. CONDITIONAL GO면 제한 내용(제외할 wear_position 등)을 §7 스펙과 데모 시나리오에 반영. NO-GO여도 R6(데모 자산)는 최선 모델로 계속.
6. **스카프 판정** — 승자 출력 ≥1장이 c1·c3·c4·c5 동시 pass면 포함, **애매하면 제외**.
7. **문서 커밋 (같은 커밋 원칙, 05 §8)** — ① 00-common §7.1/§7.2 기입(부록 B 템플릿 → 확정값) ② 00-common 미결② 해소 + §8 neck 주석 ③ 05 부록 A에 결정 + summary.md 표 붙여넣기 → 커밋 → **B에게 언블록 통지**.

---

## 4. S4 — R3 프롬프트 심화 + R4 안정화

### R3 (승자 모델만, 축별 독립 1:1 — full factorial 금지)

1. 축마다 config 1개(`r3_prompt_refcount.yaml` 등): 대표 셀 2~3개 × 변형 2 × N2.
2. 진행 방법: pv1을 복사해 **한 가지만 바꾼** pv1a/pv1b… 버전을 `TEMPLATES`에 추가(기존 버전 수정 금지) → 실행 → 갤러리에서 1:1 대조 → **축별 결론 한 줄**을 부록 A에.
3. 축 4개(+승자가 gpt 계열이면 품질 티어 mid vs high): ref 장수·순서 / 부정 지시 유무 / 언어 / style_hints(lighting만 vs 미사용 — city 무드 문구는 v2 정의 주의: 밀라노=스트리트).
4. 결론을 합쳐 **pv2 작성 → 동결**.

### R4 (엔진 운영 정책을 데이터로 확정)

1. **분산**: 최고 셀·경계 셀 2개 × N6 → 전항목 pass율 p → **라이브 N=2 성공률 = 1-(1-p)²** 계산. 이 숫자가 "라이브 데모에서 한 번에 좋은 화보가 나올 확률"의 실측치다.
2. **back 엣지**: 정면 셀카 × 백팩 반복 → 스트랩 표현이 계속 어색하면 §7.2에 "백팩은 반측면 이상 권장" 추가 + back 전용 지시 보강.
3. **실패 분류** → 대응 확정: network/timeout → 재시도 1회 / refused → 순화 변형 1회 / 품질 → N=2 병렬 first-ok. 타임아웃 배분 동결(5+40+5+5=55s).
4. G3: 이 정책들을 부록 A에 동결 기록 — **이게 곧 실엔진 구현 스펙이다.**

---

## 5. S5 — R5 실엔진 이관 + R6 데모 자산

### R5 (06 §11의 6단계 그대로)

1. 승자 어댑터 + prompts.py를 `services/gen/app/engines/`로 **복사-이식**(research import 절대 금지 — 배포가 깨진다).
2. `engines/{winner}.py` = GenerationEngine 서브클래스: URL 다운로드 → on_processing → assemble(pv2) → N=2 병렬 first-ok → Supabase Storage REST 업로드 → `EngineResult.ok(url)`.
3. `_BUILDERS`에 lazy 등록 1줄(SDK import는 빌더 내부) → **`ENGINE=mock`으로 기동해 SDK 없이도 부팅되는지 먼저 확인**.
4. `services/gen/requirements.txt`에 httpx + 승자 SDK만 추가.
5. Railway Variables 세팅(`ENGINE={winner}`, 승자 키, `SUPABASE_*`) → 재배포 → services/gen README §3 curl 스위트 + 실생성 E2E 1건.
6. **롤백 리허설**: `ENGINE=mock` 원복만으로 복구되는지 확인 후 다시 실엔진으로. 덤: `static/samples/`를 베이크오프 합격작으로 교체.

### R6 (러너 재사용 — Go/No-Go 무관 무조건 수행)

1. `r6_personas.yaml`: 모델=승자, 셀=p1~p3 × 확정 SKU(No.09/10/08), pv2, N6~8.
2. 실행 → 갤러리에서 **6항목 전부 pass 1장씩** 선별(후보 부족하면 해당 페르소나만 추가 배치).
3. `prep_assets`로 3:4 정리 → `apps/web/public/demo/{p1..p3}/photo.jpg·result.jpg·meta.json`(태그 3종·SKU·추천 이유 1줄) 커밋.
4. p4~p6은 도시 에디션 데이터(미결③) 도착 시 config 추가만으로 동일 반복.

---

## 6. 함정 목록 (자주 틀리는 것)

- **services/gen에서 research/를 import하지 않는다** — Railway 빌드 컨텍스트 밖이라 배포가 깨진다. 승격은 항상 복사-이식.
- **실 API 생성에 쓰인 프롬프트 버전은 수정 금지** — manifest의 `prompt_version`이 재현성을 보증해야 한다. 고치고 싶으면 새 버전 추가.
- **experiments/·assets/ 커밋 금지** — 영구 기록은 05 부록 A의 표 붙여넣기뿐.
- **dry-run 생략 금지** — 모든 `run` 앞에 견적 확인. 하드 가드 $50.
- **city 무드는 v2 정의** — 밀라노=스트리트/트렌디, 도쿄=미니멀. v1과 반대라 프롬프트에 옛 정의를 쓰기 쉽다.
- **R5 이전에 services/gen을 건드리지 않는다** (requirements.txt 포함) — mock 계약(03)이 B의 개발 기준선이다.
- **refused를 재시도로 덮지 않는다** — 정책 거부는 데이터다. 기록하고 넘어간다.

---

## 7. 세션 킥오프 프롬프트 모음 (복붙용)

- **S1**: "06-gen-research-tools-spec.md와 07-gen-research-playbook.md §1을 읽고 빌드 오더 B1~B2를 구현해줘. API 키 없이 fake 프로바이더만으로 진행하고, 완료 기준은 07 §1의 'S1 완료 기준'(06 §13 발췌)이야. R5 이전이니 services/gen은 절대 수정하지 마."
- **S2**: "07 §2대로 B3~B4를 진행하자. 자산은 내가 assets/에 넣어뒀어(또는: 확보부터 같이). 모델 ID·단가를 최신 문서로 확인해서 models.yaml에 기입하고, 어댑터 2종 구현 후 r1_smoke를 돌려서 관찰 6항목을 보고해줘. G1 판정은 내가 한다."
- **S3**: "07 §3대로 R2를 실행하자. dry-run 견적 먼저 보여주고, 실행 후 갤러리를 열어줘. 채점은 내가 하고, scores.json을 주면 report 돌려서 05 §5 규칙 기준의 순위 제안과 근거를 정리해줘. 판정 확정 후 00-common §7 기입 + 미결② 해소 + 05 부록 A 기록을 같은 커밋으로 준비해줘."
- **S4**: "07 §4대로 R3 축별 실험 config를 만들어 순차 실행하자. 축마다 1:1 비교 결과를 정리해주고, 내 결론을 받아 pv2를 작성해. 이어서 R4 분산·엣지·실패 분류까지 돌려서 부록 A에 동결 기록할 정책 초안을 만들어줘."
- **S5**: "06 §11과 07 §5대로 승자 어댑터를 services/gen에 승격하자. ENGINE=mock 부팅 검증 → 배포 → E2E → 롤백 리허설 순서로. 끝나면 r6_personas 배치를 돌려서 갤러리 선별을 준비해줘."
