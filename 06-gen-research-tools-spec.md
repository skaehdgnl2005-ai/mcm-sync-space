# Dev A — 생성 엔진 연구 도구 기획서 (v1)

> `05-gen-research-plan.md`의 실행 도구 스펙. 이 문서만 보고 구현에 착수할 수 있어야 한다.
> 원칙 두 가지 — ① **연구 도구가 곧 실엔진의 첫 초안이다** (승격 대상 파일은 §1 N5 의존 규율 준수) ② **도구는 제안하고, 결정은 사람이 한다** (자동 채점 없음).

---

## 0. 목적 · 배경

베이크오프~데모 자산까지 ~130~150장을 생성·기록·비교·채점해야 한다. 수작업으로는 실행 관리와 기록이 붕괴하므로 최소 도구로 자동화한다: **CLI 서브커맨드 3개(`run` / `gallery` / `report`) + 독립 스크립트 1개(`prep_assets`)**. 프롬프트 뱅크는 도구가 아니라 모듈(`prompts.py`), 프로바이더 클라이언트는 라이브러리다.

연구 코드는 레포 루트 `research/`에 둔다. Railway가 Root Directory=`services/gen`으로 빌드하므로 `research/`는 **배포 빌드 컨텍스트 밖 = 배포 안전**이다.

---

## 1. 요구사항

### 기능

| ID | 요구 |
|---|---|
| F1 | 매트릭스 실행 + 중단 후 재개 (완료 셀 스킵) |
| F2 | **모든 생성 로깅** (append-only manifest — 01 §4 "모든 생성 로깅"의 이행) |
| F3 | 오프라인 인간 채점 UI (6항목 pass/fail + note) |
| F4 | 집계·순위 제안 (05 §5의 부적격 게이트 + 타이브레이크 사슬을 산식으로) |
| F5 | 프롬프트 버전 관리·조립 (버전은 추가만, 수정 금지) |
| F6 | 자산 정규화 (리사이즈·EXIF 제거) |
| F7 | dry-run 견적 + 예산 캡 (초과 시 실행 거부) |
| F8 | fake 프로바이더로 API 키·과금 없이 $0 E2E 검증 |

### 비기능

| ID | 요구 |
|---|---|
| N1 | Windows/PowerShell 네이티브 (순수 파이썬, pathlib) |
| N2 | 외부 인프라 0 — 서버·DB 없음, 파일만 |
| N3 | 총 구현 ≤ 1일 (§12 빌드 오더 합 ~7h) |
| N4 | `assets/`·`experiments/` git 미추적 (실인물 셀카·공식몰 이미지·대량 산출물) |
| N5 | **승격 파일 의존 규율: stdlib + httpx + 해당 프로바이더 SDK만.** yaml·genlab 내부 모듈 import 금지. 근거: 승격은 import가 아니라 **복사-이식**이다 — services/gen이 research/를 import하면 빌드 컨텍스트 밖 참조로 배포가 깨진다 |
| N6 | 시크릿은 env만 (`.env` + python-dotenv는 연구 한정) |

---

## 2. 디렉터리 구조

```
research/                        # 레포 루트 — Railway 빌드 컨텍스트(services/gen) 밖
├── README.md                    # 퀵스타트 + 05 단계 포인터
├── requirements.txt             # httpx, pyyaml, pillow, python-dotenv, google-genai, openai (6개)
├── .env.example                 # GOOGLE_API_KEY / OPENAI_API_KEY (# FAL_KEY — 컨틴전시)
├── configs/
│   ├── models.yaml              # 모델 레지스트리 (§3)
│   ├── products.yaml            # SKU 4종 — 계약 필드명 그대로 (§3)
│   ├── selfies.yaml             # 셀카 레지스트리
│   ├── r1_smoke.yaml            # 단계별 실험 config…
│   ├── r2_bakeoff.yaml
│   ├── r2b_scarf.yaml           # 스카프는 별도 소형 config (러너에 override 문법 없음)
│   ├── r3_prompt_*.yaml         # 축별 1파일
│   ├── r4_variance.yaml
│   └── r6_personas.yaml
├── genlab/                      # 파이썬 패키지 = 실엔진 초안
│   ├── __init__.py  __main__.py
│   ├── cli.py                   # run | gallery | report
│   ├── runner.py                # §5
│   ├── manifest.py              # JSONL append/read
│   ├── gallery.py               # §6
│   ├── report.py                # §7
│   ├── prompts.py               # §8 ★승격 대상 — import는 stdlib만
│   ├── prep_assets.py           # §9 독립 스크립트
│   └── providers/
│       ├── base.py              # GenCall/GenOutcome/ProviderClient (§4)
│       ├── fake.py              # $0 E2E — 입력 셀카를 그대로 반환 (~10줄)
│       ├── google_genai.py      # ★승자면 승격 대상
│       ├── openai_images.py     # ★승자면 승격 대상
│       └── fal_flux.py          # 컨틴전시 발동 시에만 작성 (httpx REST, SDK 불요)
├── assets/                      # gitignore — selfies/, products/{sku}/
└── experiments/                 # gitignore — {exp_id}/manifest.jsonl, images/, gallery.html, scores.json, summary.md
```

구현 1단계 = 루트 `.gitignore`에 2줄 추가: `research/assets/`, `research/experiments/`.

---

## 3. 데이터 · 설정 스키마

### models.yaml — 모델 레지스트리

모델 ID는 **코드가 아니라 설정 문자열** (모델명 개편 주기가 해커톤 주기보다 짧다). R0에서 최신 확인 후 기입.

```yaml
gemini_nb:
  adapter: google_genai          # providers/ 모듈명
  model_id: "<R0에서 이미지 편집 모델 ID 확인 후 기입>"   # 예: gemini-*-image 계열
  params: {}
  cost_per_image_usd: 0.04       # 추정치 — R0에서 갱신
  enabled: true
gpt_image:
  adapter: openai_images
  model_id: "<R0에서 확인>"       # 예: gpt-image-2
  params: { quality: mid }       # R2는 mid 통일, high 비교는 R3 축⑤
  cost_per_image_usd: 0.07
  enabled: true
flux_kontext:                    # 컨틴전시 — 키 미보유. R1 전멸 시에만 enabled 전환
  adapter: fal_flux
  model_id: "<fal 엔드포인트 ID>"
  cost_per_image_usd: 0.06
  enabled: false
```

### products.yaml — **계약 필드명 그대로** (`schemas.py`의 `Product`와 1:1 → 승격 시 프롬프트 조립기 무수정)

```yaml
MMRGATA04CO001:
  name: "나파 가죽 트림 비세토스 모노그램 캔버스 Aren 미디엄 크로스바디 백"
  category: crossbody
  material: "비세토스 모노그램 캔버스, 나파 가죽 트림, 코튼 트윌 안감"
  color_hardware: "꼬냑, 24K 골드 도금 브라스 플레이트"
  wear_position: cross
  ref_images: [assets/products/MMRGATA04CO001/1.jpg]
# + MMPGADC01BK001(shoulder) / MMKGATA03MT001(back) / MEFGAMM12CO001(neck) 동일 형식
```

### selfies.yaml

```yaml
s_smart:
  file: assets/selfies/s_smart.jpg
  formality: smart_daily
  note: "전신, 자연광, 정면"
```

### 실험 config — 곱집합 전개만, override·중첩 문법 없음

```yaml
exp_id: r2_bakeoff
models: [gemini_nb, gpt_image]
selfies: [s_casual, s_smart, s_formal]
products: [MMRGATA04CO001, MMPGADC01BK001, MMKGATA03MT001]
prompt_versions: [pv1]
reps: 2
concurrency: { global: 4, per_model: 2 }
budget_cap_usd: 10
```

### manifest.jsonl — 1행 = 생성 시도 1회 (append-only)

```jsonc
{ "ts": "...", "exp_id": "r2_bakeoff", "cell_id": "gpt_image__s_smart__MMRGATA04CO001__pv1__r1",
  "model": "gpt_image", "model_id": "...", "selfie": "s_smart", "product": "MMRGATA04CO001",
  "wear_position": "cross", "prompt_version": "pv1", "rep": 1,
  "prompt_text": "<조립된 프롬프트 전문>",
  "status": "ok|refused|error|timeout", "image_path": "images/....jpg",
  "latency_ms": 18234, "est_cost_usd": 0.07, "error_message": null, "provider_meta": {} }
```

### scores.json — 갤러리가 내보내는 채점 (이미지 단위, 셀 통과는 report가 계산)

```jsonc
{ "exp_id": "r2_bakeoff", "scorer": "dev_a", "scored_at": "...",
  "scores": { "<cell_id>": { "c1": true, "c2": true, "c3": false, "c4": true, "c5": true, "c6": true,
                             "note": "cross 지정인데 shoulder로 합성" } } }
```

---

## 4. 프로바이더 클라이언트 스펙

### 공통 인터페이스 (확정)

```python
@dataclass(frozen=True)
class GenCall:
    prompt: str
    user_photo: bytes                # 역할 명명 — 순서 리스트 금지 (프로바이더마다 다중 이미지 규약이 달라 역할이 명시돼야 올바르게 조립)
    product_refs: list[bytes]        # 1~3장
    model_id: str                    # models.yaml 문자열 그대로 통과
    params: dict                     # 어댑터가 프로바이더 문법으로 번역
    timeout_s: float = 50.0

@dataclass(frozen=True)
class GenOutcome:
    status: Literal["ok", "refused", "error", "timeout"]
    image: bytes | None
    latency_ms: int
    error_message: str | None = None
    provider_meta: dict = field(default_factory=dict)

class ProviderClient(Protocol):
    name: str
    async def generate(self, call: GenCall) -> GenOutcome: ...
```

**bytes-in / bytes-out** — 파일 읽기(러너)와 URL 다운로드(실엔진)는 호출자 책임. 이것이 승격 호환성의 핵심이다: 같은 코어가 연구와 실서비스에서 그대로 돈다.

### 어댑터별 노트

- **google_genai**: SDK 사용. contents = [사용자 사진, ref 이미지들, 프롬프트] 구성. safety block 응답을 `refused`로 감지·분류.
- **openai_images**: SDK 사용. images.edit 계열 다중 입력. **실인물 편집 정책 거부를 최우선 관찰** 대상으로 — 거부 응답을 `refused`로. 품질 티어는 `params.quality`로.
- **fake**: 입력 셀카 bytes를 그대로 반환 (~10줄). 러너→갤러리→리포트 전 파이프라인을 키·과금 없이 검증(F8). 키 발급 지연이 툴 개발을 블로킹하지 않게 하는 장치.
- **fal_flux (컨틴전시)**: SDK 없이 httpx로 queue REST 직접 호출. 입력 bytes는 어댑터 내부에서 fal storage 업로드 후 URL 전달 — 시그니처는 bytes 유지.

`refused` vs `error`의 구분은 어댑터 책임이다 — 이 분류가 05 리스크 1(정책 거부)의 데이터 원천이다.

---

## 5. 실험 러너 (`run`)

1. config 로드 → models × selfies × products × prompt_versions × reps **곱집합 전개** → cell_id 부여
2. **dry-run**(F7): 총 셀 수·모델별 장수·Σ단가 견적 출력. `budget_cap_usd` 초과 시 실행 거부
3. asyncio 세마포어 동시성 (global 4 / per-model 2)
4. 셀 실행: 자산 파일 로드 → `prompts.assemble()` → 어댑터 호출 → 이미지 저장(`experiments/{exp_id}/images/{cell_id}.jpg`) + manifest append
5. **재개**(F1): manifest에 성공 레코드 존재 + 이미지 파일 존재 → 스킵
6. **재시도**: 네트워크 오류만 1회. `refused`는 0회 — 기록 후 진행(정책 거부는 재시도가 아니라 데이터다)

---

## 6. 채점 갤러리 (`gallery`)

- **단일 정적 HTML** 생성, 서버 없음. manifest 데이터를 **HTML에 JSON 인라인**(file:// fetch 제약 회피), 이미지는 상대경로 참조 → 브라우저로 열기만 하면 됨
- 레이아웃: 행 = 셀카×상품 셀, 열 = 모델×pv×rep. 각 이미지 클릭 → 라이트박스에서 **원본 셀카·상품 레퍼런스 컷과 나란히 대조**
- 채점: 셀별 c1~c6 체크박스 + note 입력 → localStorage 저장(키: exp_id+cell_id) → **"Export scores.json"** 버튼(Blob 다운로드)
- P1(여유 시): scores.json 재로드 input — 채점 유실 보험 (~10줄)

---

## 7. 리포트 집계기 (`report`)

입력 = manifest.jsonl + scores.json → 모델별 표 산출:

- 셀 통과 수 (best-of-N: N개 중 ≥1장 전항목 pass)
- c1~c6 항목별 통과율 / refused·error 건수 / 지연 p50·p95 / Σ비용
- **05 §5의 부적격 게이트 + 타이브레이크 사슬을 산식으로 구현** → 순위 "제안" + 근거 라인 출력 → `summary.md`

확정은 사람이 한다 (05 §4). summary.md 표는 05 부록 A에 붙여넣는 것이 유일한 영구 기록이다 (experiments/는 미추적).

---

## 8. 프롬프트 뱅크 (`prompts.py`) — ★승격 대상, import는 stdlib만

```python
WEAR_POSITION_DIRECTIVES = { "hand": "...", "shoulder": "...", "cross": "...", "back": "...", "neck": "..." }  # 01 §4 표 5종
TEMPLATES = { "pv1": "..." }   # 버전은 추가만, 수정 금지 (manifest 재현성)

def assemble(product: dict, style_hints: dict | None, version: str) -> str: ...
```

- 슬롯: `{name}` `{material}` `{color_hardware}` + wear_position 지시 + style_hints(lighting)
- **product 인자 = 계약 필드명 dict** — 연구에선 products.yaml 항목, 실엔진에선 `payload.product.model_dump()`가 **동일한 형태**로 들어온다 → 승격 시 무수정
- pv1 기본형: 영어 지시 템플릿 + 한국어 DB 원문 필드 그대로 임베드. "레퍼런스의 제품을 정확히 그대로, 인물·배경 보존"의 **편집 프레이밍** + 부정 지시 블록(로고 변형·신체 변형 금지). 언어 축 비교는 R3 축③

---

## 9. 자산 준비 (`prep_assets.py`)

리사이즈(긴 변 1536, 비율 유지) + EXIF 제거(개인정보·회전 사고 방지) + 파일명 규약 적용(selfies: `{id}.jpg` / products: `{sku}/{n}.jpg`) + configs 레지스트리와 대조 검증(레지스트리에 없는 파일·파일 없는 항목 경고).

---

## 10. CLI 커맨드 (PowerShell)

```powershell
python -m genlab run configs/r2_bakeoff.yaml --dry-run          # 견적만
python -m genlab run configs/r2_bakeoff.yaml --resume           # 재개
python -m genlab run configs/r2_bakeoff.yaml --only model=gemini_nb
python -m genlab gallery experiments/r2_bakeoff --open          # HTML 생성 + 브라우저 열기
python -m genlab report experiments/r2_bakeoff --scores experiments/r2_bakeoff/scores.json
python genlab/prep_assets.py --kind selfie raw_in/ assets/selfies/
```

---

## 11. services/gen 승격 경로 (실측 코드 기준 — R5에서 수행)

1. **복사-이식**: `genlab/providers/{winner}.py` 코어 + `genlab/prompts.py` → `services/gen/app/engines/` 아래로 복사, 상대 import만 조정. **research/ import 금지** (근거: §1 N5)
2. `engines/{winner}.py` = `GenerationEngine` 서브클래스 골격:
   `payload.user_photo_url`·`product.ref_image_urls` httpx 다운로드 → `request.on_processing()` → `prompts.assemble(payload.product.model_dump(), style_hints, "pv2")` → **R4 확정 정책(N=2 병렬 first-ok)** → Supabase Storage **REST 직접 업로드**(`POST {SUPABASE_URL}/storage/v1/object/assets/generated/{job_id}.jpg`, service role key — supabase-py 불사용, 다운로드용 httpx 재사용으로 의존 1개 절약) → `EngineResult.ok(공개 URL)`. 실패 매핑: 거부·업스트림 → `fail("UPSTREAM_ERROR")` / 시간 → `fail("TIMEOUT")` (하드컷은 라우트가 `default_timeout_seconds=55.0`으로 보장 — `engines/base.py`)
3. `engines/__init__.py`의 `_BUILDERS`에 **lazy 등록 1줄** — SDK import는 빌더 함수 내부에서. `ENGINE=mock` 기동 시 SDK가 없어도 부팅되어야 한다
4. `services/gen/requirements.txt` 최소 추가: `httpx` + 승자 SDK 1개
5. Railway Variables: `ENGINE={winner}` / 승자 API 키 / `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` → 재배포 → services/gen README §3 curl 스위트 + 실생성 E2E 1건
6. **롤백 = env `ENGINE=mock` 원복만** (코드 재배포 불요, `GEN_BASE_URL` 불변)

---

## 12. 빌드 오더

| 순서 | 내용 | 소요 | 검증 |
|---|---|---|---|
| B1 | gitignore 2줄 + 골격 + configs + manifest + runner(전개·재개·동시성·dry-run) + fake | 2h | fake로 run → manifest·이미지 확인 |
| B2 | gallery + report | 2h | fake 산출물 채점 → export → 순위표 출력 |
| B3 | prompts.py pv1 + prep_assets + 실자산 배치 | 1h | 4 SKU(스카프 포함) 조립 프롬프트 육안 확인 |
| B4 | 실프로바이더 2종 (google_genai → openai_images) | 1.5~2h | 각 1콜 스모크 (= 05 R1 겸용) |
| B5 | (R5 시점) 승격 어댑터 + 배포 | 2~3h | curl 스위트 + E2E + 롤백 리허설 |

B1~B4 ≈ 6.5~7h (N3 충족). **B1~B2는 API 키 없이 진행 가능** (F8).

---

## 13. Definition of Done

- [ ] 러너: r1 config를 fake로 실행 → manifest 4행 + 이미지 4장, 재실행 시 0건 재생성, dry-run이 장수·비용 출력 + 캡 초과 시 거부
- [ ] 갤러리: file://로 열림, 새로고침 후 채점 유지, scores.json 다운로드 동작
- [ ] 리포트: 게이트·타이브레이크 반영 순위 제안 + summary.md 생성
- [ ] 프롬프트: pv1로 4 SKU(스카프 포함) 조립 성공, wear_position 5종 매핑 포함
- [ ] 승격 검증: `ENGINE=mock` 기동이 여전히 SDK 없이 성공(lazy import), `ENGINE={winner}` E2E 1건, env 롤백 확인
- [ ] **전체: 새 클론 + .env 세팅만으로 15분 내 fake E2E 재현**

---

## 14. 안티스코프 (만들지 않을 것)

리뷰용 웹서버·웹앱(정적 HTML만) / DB·sqlite·벡터스토어 / CI·pre-commit·테스트 스위트(fake E2E로 갈음) / pip 패키징 / 자동 품질 지표(05 §9) / 멀티 채점자 병합 / 시드 재현성 보장(프로바이더가 미보장) / 상품컷 스크레이퍼(4 SKU는 수동 저장) / 큐·Celery / 진행률 대시보드 / 이미지 캐시·중복제거 / 정밀 비용 정산(추정만) / **R5 이전 services/gen 일체 수정 금지 (requirements.txt 포함)**
