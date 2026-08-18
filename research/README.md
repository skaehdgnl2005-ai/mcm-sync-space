# genlab — 생성 엔진 연구 도구

`06-gen-research-tools-spec.md`의 구현. `05-gen-research-plan.md`(R0~R6)를 실행하는
CLI다. **배포 안전**: Railway는 Root Directory=`services/gen`으로 빌드하므로
`research/`는 빌드 컨텍스트 밖이다. services/gen은 R5 전까지 건드리지 않는다.

```
run     매트릭스 실행 (곱집합 전개 · dry-run 견적 · 예산 캡 · 재개 · 동시성)
gallery 채점용 정적 HTML 생성 (서버 없음, file://로 열어서 채점)
report  집계 → 부적격 게이트 + 타이브레이크 사슬 → 순위 "제안" + summary.md
```

## 퀵스타트 (PowerShell)

```powershell
cd research
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install pyyaml                      # B1~B2는 이것만 있으면 된다
python -m genlab run configs/r1_smoke.yaml --fake    # $0 E2E — 키 불필요
python -m genlab gallery experiments/r1_smoke_fake --open
python -m genlab report experiments/r1_smoke_fake
```

`--fake`는 모든 어댑터를 fake(입력 셀카를 그대로 반환)로 바꿔 파이프라인 전체를
과금 없이 검증한다. 산출물은 `experiments/{exp_id}_fake/`로 **격리**된다 — fake
이미지가 진짜 실험 디렉터리에 섞이면 나중 실행의 재개 로직이 그걸 완료된 셀로
보고 건너뛰기 때문이다.

자산이 아직 없다면(새 클론) 더미로 파이프라인만 먼저 돌릴 수 있다:

```powershell
pip install pillow
python -c "from PIL import Image; from pathlib import Path; [Path(p).parent.mkdir(parents=True, exist_ok=True) or Image.new('RGB',s,(90,100,130)).save(p,'JPEG') for p,s in [('assets/selfies/s_casual.jpg',(768,1024)),('assets/selfies/s_smart.jpg',(768,1024)),('assets/selfies/s_formal.jpg',(768,1024)),('assets/products/MMRGATA04CO001/1.jpg',(1024,1024)),('assets/products/MMPGADC01BK001/1.jpg',(1024,1024)),('assets/products/MMKGATA03MT001/1.jpg',(1024,1024)),('assets/products/MEFGAMM12CO001/1.jpg',(1024,1024))]]"
```

실자산(동의된 셀카 3장 + 공식몰 팩샷 4종)은 R0에서 `prep_assets`로 정규화해 같은
경로에 놓는다. `assets/`·`experiments/`는 git 미추적이다.

## 실전 실행 (키 발급 후)

```powershell
cp .env.example .env                    # GOOGLE_API_KEY / OPENAI_API_KEY 기입
pip install -r requirements.txt
python -m genlab run configs/r2_bakeoff.yaml --dry-run   # 견적 확인은 의무 (05 §6)
python -m genlab run configs/r2_bakeoff.yaml
python -m genlab run configs/r2_bakeoff.yaml --resume    # 중단 후 이어서
python -m genlab run configs/r2_bakeoff.yaml --only model=gemini_nb --only rep=1
```

- **모델 ID는 코드가 아니라 설정 문자열**이다. R0에서 최신 ID를 확인해
  `configs/models.yaml`에 기입한다. 플레이스홀더(`<...>`)가 남아 있으면 러너가
  실행을 거부한다.
- 이미 manifest가 있는 실험을 그냥 다시 돌리면 거부된다 — `--resume`(완료분 스킵)
  또는 `--restart`(전량 재생성, 과금 주의)를 **명시**해야 한다.
- 견적이 `budget_cap_usd`를 넘으면 실행 자체를 거부한다 (F7).

## 채점 → 리포트

1. `python -m genlab gallery experiments/r2_bakeoff --open`
2. 브라우저에서 c1~c6 체크 + note. 라이트박스(이미지 클릭)에서 원본 셀카·상품컷과
   나란히 대조하고 <kbd>←</kbd><kbd>→</kbd> 이동, <kbd>1</kbd>~<kbd>6</kbd> 토글,
   <kbd>a</kbd> 전항목 pass로 빠르게 넘긴다. 채점은 localStorage에 즉시 저장된다.
   - **애매하면 fail**, fail이면 note 한 줄 의무 (05 §4). 헤더가 누락 건수를 센다.
3. `Export scores.json` → `experiments/r2_bakeoff/scores.json`으로 저장
4. `python -m genlab report experiments/r2_bakeoff`
   - 가시 워터마크처럼 사람만 판정할 수 있는 게이트는
     `experiments/{exp}/gates.json`에 `{"gpt_image": {"visible_watermark": true}}`로
     넣으면 순위에 반영된다.
5. `summary.md`를 **05 부록 A에 붙여넣는다** — `experiments/`는 미추적이라 그게
   유일한 영구 기록이다.

리포트는 **제안만** 한다. 승자·Go/No-Go·스카프 확정은 갤러리 재확인 후 사람이 한다.

> ⚠ `report.py`의 `MAX_P95_MS`는 05 §5의 **기본값 40s를 하드코딩**하고 있다. 부적격 게이트 ②는
> 부록 A-2에서 "N=2 first-ok 총 소요 > 60s"로 재유도됐으므로, **gpt 계열에 대한 리포트의
> 게이트 판정과 순위 제안은 틀린다**(gpt를 부적격 처리하고 gemini를 승자로 제안한다).
> 고치지 않은 것은 의도다 — 코드를 바꾸면 조정된 규칙이 조용히 적용되어 §5 원문과의 차이를
> 추적할 수 없게 된다. 리포트는 실행 통계(시도·ok·지연 원자료)만 신뢰하고 게이트·순위 줄은 무시하라.

## 강제 선택 쌍대 비교 (`compare`)

갤러리(c1~c6)는 **합격선을 넘었는가**를 재는 절대 판정 도구다. R2가 9/9 전항목 pass인
지금 그 답은 전부 '예'라서, **변형 간 우열**은 구조적으로 잴 수 없다(천장 효과 — R2c에서
이미 겪었다). 그 질문 전용 도구가 이것이다. 규칙은 05 부록 A-5에 **실행 전 확정**돼 있다.

```powershell
# 설정 실험 — 모델 엔트리가 갈리는 축
python -m genlab.compare experiments/s4a_settings experiments/s4b_hardcell `
    --baseline gpt_base --exclude gpt_n2 --seed s4 --open

# R3 프롬프트 축 — 모델이 같고 pv가 갈린다. **--axis pv를 빠뜨리면 쌍이 0개**로 나온다
python -m genlab.compare experiments/r3f_cross --axis pv --baseline pv1 --seed r3f --open
```

- 각 변형 팔을 **baseline과만** 짝짓는다(전순위가 아니라 "갈아탈 이유가 있는가"가 질문이므로).
- **블라인드 + 좌우 무작위**(seed로 결정론적 — 무작위인데 재현 가능하다), **동점 금지**,
  사유 태그 1개 필수. 집계는 **전량 판정 후에만** 열린다(중간 점수가 남은 판정을 끌어당긴다).
- `--exclude`는 **지연 전용 팔**을 뺀다. 예: `gpt_n2`는 어댑터가 `data[0]`만 읽어 2번째 장을
  버리므로 품질 비교 대상이 아니다.
- `Export verdicts.json` → 결론 한 줄을 05 부록 A에. (`experiments/`는 미추적이다.)
- **좌우 배치 seed는 결과를 보기 전에 고른다.** 쏠림(예: baseline 좌 2 / 우 6)이 생기면
  균형 잡히는 seed로 바꿔도 되지만 **판정 시작 전에만** 한다 (05 부록 A-5).

**두 층은 함께 써야 한다**: 쌍대 비교는 우열만 본다. 변형이 합격선 **아래로** 떨어졌는지는
갤러리 c1~c6로 따로 확인한다(A-5 층 1). 층 1에서 회귀가 나오면 승률과 무관하게 기각이다.

## 05 단계별 포인터

| 단계 | 명령 | 게이트 |
|---|---|---|
| R0 준비 | `--fake` E2E + models.yaml 기입 + 자산 배치 | fake 왕복 + 키 1콜 |
| R1 스모크 | `run configs/r1_smoke.yaml` | **G1** 진행 모델 확정 |
| R2 베이크오프 | `run configs/r2_bakeoff.yaml` + `r2b_scarf.yaml` → gallery → report | **G2** 승자·백업·Go/No-Go·스카프 + 00-common §7 기입 |
| R3 프롬프트 | 축별 config 추가 (pv2 동결) | 축별 결론 |
| R4 안정화 | N6 반복 config | **G3** N·재시도·타임아웃 동결 |
| R5 이관 | 06 §11 복사-이식 | **G4** 배포 E2E + 롤백 |
| R6 데모 자산 | `r6_personas.yaml` | 페르소나별 전항목 pass 1장 |

R3~R6 config는 각 단계 착수 시 작성한다(승자 모델·pv2가 정해져야 쓸 수 있다).
문법은 곱집합 전개뿐이므로, 품질 티어처럼 파라미터가 다른 비교는 `models.yaml`에
변형 엔트리를 추가하는 방식으로 만든다(예: `gpt_image_high`).

## 구조

```
configs/     모델·상품·셀카 레지스트리 + 실험 config (곱집합 전개만, override 없음)
genlab/      cli · runner · manifest · gallery · report · prompts · providers/
assets/      셀카·상품컷 (git 미추적)
experiments/ {exp_id}/manifest.jsonl · images/ · gallery.html · scores.json · summary.md (git 미추적)
```

**승격 규율**: `genlab/prompts.py`와 `genlab/providers/{winner}.py`·`base.py`는 R5에
services/gen으로 **복사-이식**된다. 그래서 이 세 파일은 stdlib(+해당 SDK)만
import한다. services/gen이 research/를 import하면 빌드 컨텍스트 밖 참조로 배포가
깨진다. `prompts.assemble()`이 받는 product dict는 계약 필드명 그대로라
(`schemas.py`의 `Product`) 연구/실엔진 양쪽에서 같은 코드가 돈다.

## 자산 준비 (`prep_assets`)

레지스트리는 `.jpg`를 가리킨다. 원본이 `.png`면 러너가 실행을 거부하므로 먼저 정규화한다.

```powershell
python genlab/prep_assets.py --all --dry-run   # 계획만
python genlab/prep_assets.py --all             # 긴 변 1536 · EXIF 제거 · 알파→흰색 · jpg 변환
python genlab/prep_assets.py --verify          # 파일을 쓰지 않고 레지스트리 대조만
```

- **업스케일하지 않는다.** 긴 변이 1536보다 작으면 그대로 둔다 — 없는 디테일을
  만들어 내면 c1 판정이 입력 해상도 착시로 오염된다. 규격 미달은 경고로만 알린다.
- 알파 채널은 **흰색**으로 평탄화한다. 공식몰 팩샷은 투명 배경 PNG라 그냥 RGB로
  바꾸면 배경이 검정이 된다.
- 확장자가 바뀌어 대체된 원본은 `assets/_originals/`로 옮긴다(삭제하지 않는다).
- 레지스트리 대조는 양방향이다: 등록됐는데 파일이 없으면 **오류**(러너도 거부한다),
  파일이 있는데 미등록이면 **경고**(R3 축① 여분 컷일 수 있다).

## 채점 중 자주 놓치는 것

- **체크박스는 3상태다**: 한 번 클릭 = pass / **두 번 클릭 = fail** / 안 누르면 **미판정**.
  미판정은 호박색 `?`, fail은 빨강 취소선으로 구분 표시된다. `report`는 6항목이 전부
  채워진 이미지만 집계하므로, 미판정이 섞이면 그 이미지가 통째로 빠진다 —
  헤더의 "일부만 판정된 이미지 N건"이 그걸 알린다. (실제로 10장이 이렇게 유실된 적이 있다.)
- **열 제목은 그 실험에서 실제로 변하는 차원**을 크게 쓴다. 축별 1:1 실험은 하나만 빼고
  전부 고정하므로 같은 인물·같은 상품이 반복되는 것이 정상이다 — 상단 배너가 무엇이
  다른지 알려준다.

## 프롬프트 버전 (`prompts.py`)

`TEMPLATES`는 **추가만, 수정 금지**. 실 API 생성에 한 번이라도 쓰인 버전은 동결이다.

wear_position 지시 한 줄만 바꾸는 축(R3 축⑥ 등)은 템플릿 본문을 복사하지 말고
`DIRECTIVE_OVERRIDES[version]`에 **바뀌는 것만** 적는다. 본문을 옮겨 적으면 그 과정에서
문구가 미끄러진다 — 실제로 `pv1c`가 그렇게 실패했다(05 부록 A-10 ②).
`pv1d`처럼 원문에 덧붙이는 경우는 **문자열 연결로 만든다**(`WEAR_POSITION_DIRECTIVES["cross"] + _CROSS_GAP_RULE`).

| 버전 | 내용 | 상태 |
|---|---|---|
| `pv1` | 초판 (R1~R2 동결) | **동결** |
| `pv1c` | cross 지시 문단 교체 (가림 판정 규칙) | **기각** — 좌우 결함 유발 (A-10) |
| `pv1d` | `pv1` 원문 + 틈 문장 1개 (순수 추가) | 판정 대기 (A-12) |

## 네트워크 사전점검

`run`은 실행 전(`--fake` 제외) 무과금 204 경로의 RTT를 재고 **800ms 초과 시 경고**한다.
**차단하지 않는다** — 판단은 사람이 한다.

어댑터의 `latency_ms`는 **입력 업로드 + 결과 다운로드를 포함한 벽시계**다. 회선이 열화되면
그 값은 생성 시간이 아니라 회선 상태를 잰다. 이 구별이 없어 회선 문제를 프로바이더 지연으로
오인하고 결론까지 쓴 사고가 있었다(05 부록 A-11). **지연이 산출물인 실험은 경고가 뜨면 중단하라.**
품질 채점만 목적이면 진행해도 된다 — 느린 회선은 이미지 내용을 바꾸지 않는다.

## 아직 없는 것

- `providers/fal_flux.py` — 컨틴전시(G1 전멸) 시에만
