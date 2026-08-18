"""강제 선택 쌍대 비교 — 05 부록 A-5의 **층 2** 구현.

갤러리(c1~c6)가 답하지 못하는 질문을 답한다. 갤러리는 "합격선을 넘었는가"라는 절대
판정 도구이고, R2가 9/9 전항목 pass인 지금 그 질문의 답은 전부 '예'다(천장 효과).
남은 질문은 **"합격선 위쪽에서 어느 쪽이 나은가"**이며, 그건 이진 체크리스트로는
구조적으로 잴 수 없다 — 그래서 별도 도구다.

설계 근거 (전부 A-5에 사전 확정된 규칙의 구현이다):

- **블라인드 + 좌우 무작위**: 팔 이름을 감춘다. "912는 화소가 적으니 나쁠 것"
  같은 사전 기대가 판정에 새는 것을 막는다. 배치는 seed로 결정론적이라
  사후에 정확히 복원된다(무작위인데 재현 가능해야 감사가 된다).
- **동점 금지**: 좌 또는 우만 있다. 천장을 뚫는 장치가 이것이다.
- **사유 태그 필수**: 이긴 이유가 없으면 R3 프롬프트 축으로 이어지지 않는다.
  "갈랐다고 보기 어려움"이 무차별의 정직한 출구이며, 강제 선택은 유지하되
  집계에서 무차별로 빠진다.
- **집계는 전량 판정 후에만 열린다**: 중간 점수를 보면서 판정하면 남은 판정이
  거기에 끌려간다. 버튼 뒤에 숨겨 둔다.
- **원본 셀카·상품컷 상시 노출**: c1("그 로고가 정확히 그 로고인가")은 대조 없이
  판정 불가다 (06 §6이 갤러리 라이트박스에 같은 요구를 건 것과 같은 이유).

실행:
    python -m genlab.compare experiments/s4a_settings experiments/s4b_hardcell \\
        --baseline gpt_base --exclude gpt_n2 --open

R3의 프롬프트 축은 모델이 같고 pv가 갈리므로 ``--axis pv``를 쓴다::

    python -m genlab.compare experiments/r3f_cross --axis pv --baseline pv1 --open

`--exclude`가 필요한 이유: gpt_n2는 어댑터가 `data[0]`만 읽어 2번째 장을 버리므로
**품질 팔이 아니라 지연 팔**이다. 쌍대 비교에 넣으면 의미 없는 판정을 시킨다(A-5).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import manifest
from .runner import EXPERIMENTS_DIR, RESEARCH_ROOT, Registries, load_registries, resolve

DEFAULT_OUT = EXPERIMENTS_DIR / "compare.html"

REASONS: tuple[tuple[str, str], ...] = (
    ("product", "제품 동일성 (패턴·로고·하드웨어)"),
    ("person", "인물 보존 (얼굴·손·체형)"),
    ("blend", "융합 (조명·그림자·배경)"),
    ("editorial", "화보 톤"),
    ("tie", "갈랐다고 보기 어려움"),
)
"""A-5의 사유 태그 5종. 앞 4개는 05 §4의 c1/c4/c5/c6과 대응하고, 마지막은 무차별 출구다.
c2(크기·비율)와 c3(위치)은 뺐다 — 설정 축(size·n)이 그것들을 바꿀 경로가 없고,
선택지가 많아지면 태그가 대충 찍힌다."""

TIE_REASON = "tie"


@dataclass(frozen=True)
class Item:
    exp: str
    cell_id: str
    model: str
    selfie: str
    product: str
    pv: str
    rep: int
    image: str
    latency_ms: int


def _collect(exp_dirs: list[Path], out_dir: Path) -> tuple[list[Item], list[str]]:
    """여러 실험 디렉터리의 ok 이미지를 한 목록으로. 경로는 out 기준 상대경로로 바꾼다."""
    items: list[Item] = []
    problems: list[str] = []
    for exp_dir in exp_dirs:
        path = exp_dir / manifest.MANIFEST_NAME
        if not path.exists():
            problems.append(f"{path} 가 없다 — run 먼저.")
            continue
        rows = manifest.read(path)
        for cell_id, row in manifest.latest_by_cell(rows).items():
            if row.get("status") != "ok" or not row.get("image_path"):
                continue
            abs_image = (exp_dir / str(row["image_path"])).resolve()
            if not abs_image.exists():
                problems.append(f"{cell_id}: 이미지 파일이 없다 — {abs_image}")
                continue
            items.append(
                Item(
                    exp=exp_dir.resolve().name,
                    cell_id=cell_id,
                    model=str(row.get("model", "")),
                    selfie=str(row.get("selfie", "")),
                    product=str(row.get("product", "")),
                    pv=str(row.get("prompt_version", "")),
                    rep=int(row.get("rep", 1)),
                    image=_rel(abs_image, out_dir),
                    latency_ms=int(row.get("latency_ms") or 0),
                )
            )
    return items, problems


def _rel(target: Path, base_dir: Path) -> str:
    try:
        return target.relative_to(base_dir.resolve(), walk_up=True).as_posix()
    except (ValueError, TypeError):
        return target.as_uri()


def _side_is_baseline_left(seed: str, pair_id: str) -> bool:
    """좌우 배치를 seed로 결정론적으로 정한다.

    난수 생성기 대신 해시를 쓰는 이유: 쌍의 순서·개수가 바뀌어도 **각 쌍의 배치가
    변하지 않는다.** 판정을 중간에 저장해 뒀다가 나중에 쌍이 추가되면, RNG 방식은
    기존 쌍의 좌우가 통째로 흔들려 이미 내린 판정이 뒤집힌다.
    """
    digest = hashlib.sha256(f"{seed}:{pair_id}".encode("utf-8")).digest()
    return digest[0] % 2 == 0


def build_pairs(
    items: list[Item],
    baseline: str,
    exclude: set[str],
    seed: str,
    axis: str = "model",
) -> tuple[list[dict], list[str]]:
    """비교 축(model 또는 pv)에서 baseline과 변형을 1:1로 짝짓는다.

    **축이 두 종류인 이유**: S4a 같은 설정 실험은 모델 엔트리가 갈리지만(`gpt_base` vs
    `gpt_s912`), R3의 프롬프트 축은 **모델이 같고 pv가 갈린다**(`pv1` vs `pv1c`).
    축이 아닌 나머지 차원은 **그룹 키에 넣어 통제한다** — 그래야 비교가 1:1로 남는다.

    rep끼리 같은 번호로 짝짓는 것은 임의지만 **편향은 없다** — 두 팔 모두 독립 표본이라
    r1↔r1이든 r1↔r2든 기대값이 같다. 임의 짝을 늘리면 판정 부담만 커진다.
    """
    if axis not in ("model", "pv"):
        raise ValueError(f"axis must be 'model' or 'pv' (got {axis!r})")
    held = "pv" if axis == "model" else "model"

    groups: dict[tuple, dict[str, Item]] = {}
    for item in items:
        key = (item.exp, item.selfie, item.product, getattr(item, held), item.rep)
        groups.setdefault(key, {})[getattr(item, axis)] = item

    pairs: list[dict] = []
    notes: list[str] = []
    for gkey in sorted(groups):
        arms = groups[gkey]
        base = arms.get(baseline)
        if base is None:
            notes.append(f"{'/'.join(str(k) for k in gkey)}: baseline({baseline}) 이미지가 없어 건너뜀")
            continue
        for model in sorted(arms):
            if model == baseline or model in exclude:
                continue
            variant = arms[model]
            # axis=model일 때의 pair_id 형식은 **바꾸지 않는다** — 이미 판정이 끝난
            # S4a/S4b의 seed→좌우 배치를 재생성 시 그대로 복원하기 위해서다.
            stem = f"{variant.exp}__{variant.selfie}__{variant.product}"
            pair_id = (
                f"{stem}__r{variant.rep}__{model}"
                if axis == "model"
                else f"{stem}__{getattr(variant, held)}__r{variant.rep}__{model}"
            )
            base_left = _side_is_baseline_left(seed, pair_id)
            left, right = (base, variant) if base_left else (variant, base)
            pairs.append(
                {
                    "pair_id": pair_id,
                    "exp": variant.exp,
                    "selfie": variant.selfie,
                    "product": variant.product,
                    "rep": variant.rep,
                    "variant": model,
                    "baseline": baseline,
                    "baseline_side": "left" if base_left else "right",
                    # ★ `arm`이 승자 기록의 유일한 근거다. 예전에는 `model`만 실었는데,
                    #   axis=pv에서는 양쪽 model이 같아(gpt_base) 승자가 "gpt_base"로 뭉개졌다
                    #   — pv1인지 pv1c인지가 통째로 유실된다. 축이 무엇이든 팔 이름을 싣는다.
                    "left": {
                        "image": left.image,
                        "arm": getattr(left, axis),
                        "model": left.model,
                        "pv": left.pv,
                        "latency_ms": left.latency_ms,
                    },
                    "right": {
                        "image": right.image,
                        "arm": getattr(right, axis),
                        "model": right.model,
                        "pv": right.pv,
                        "latency_ms": right.latency_ms,
                    },
                }
            )
    return pairs, notes


def _context(pairs: list[dict], reg: Registries, out_dir: Path) -> dict:
    """쌍마다 필요한 원본 셀카 + 상품 레퍼런스 경로. c1 판정에 대조가 필수다."""
    context: dict[str, dict] = {}
    for pair in pairs:
        key = f"{pair['selfie']}__{pair['product']}"
        if key in context:
            continue
        selfie = reg.selfies.get(pair["selfie"])
        product = reg.products.get(pair["product"])
        context[key] = {
            "selfie_src": _rel(resolve(selfie.file).resolve(), out_dir) if selfie else None,
            "refs": [
                _rel(resolve(ref).resolve(), out_dir)
                for ref in (product.ref_images if product else [])
            ],
            "product_name": (product.name if product else "") or pair["product"],
            "wear_position": product.wear_position if product else "",
            "size": (product.size if product else "") or "",
        }
    return context


def build(
    exp_dirs: list[Path],
    *,
    baseline: str,
    exclude: set[str],
    seed: str,
    out: Path,
    axis: str = "model",
    open_browser: bool = False,
    log=print,
) -> Path:
    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    items, problems = _collect(exp_dirs, out.parent)
    for message in problems:
        log(f"warn: {message}")
    if not items:
        raise FileNotFoundError("비교할 ok 이미지가 없다.")

    pairs, notes = build_pairs(items, baseline, exclude, seed, axis)
    # 가드: 양쪽 팔 이름이 같으면 승자를 기록할 수 없다(과거 axis=pv에서 실제로 난 사고).
    # 조용히 판정을 날리느니 여기서 죽는다.
    collapsed = [p["pair_id"] for p in pairs if p["left"]["arm"] == p["right"]["arm"]]
    if collapsed:
        raise RuntimeError(
            f"축 {axis!r}에서 좌우 팔 이름이 같다 — 승자 기록이 불가능하다: {collapsed[:3]}"
        )
    for message in notes:
        log(f"warn: {message}")
    if not pairs:
        raise FileNotFoundError(
            f"쌍이 만들어지지 않았다 — baseline={baseline!r}가 맞는지, "
            f"제외({sorted(exclude)}) 후 변형이 남는지 확인하라."
        )

    payload = {
        "title": " + ".join(sorted({p["exp"] for p in pairs})),
        "baseline": baseline,
        "axis": axis,
        "seed": seed,
        "excluded": sorted(exclude),
        "reasons": [{"id": rid, "label": label} for rid, label in REASONS],
        "tie_reason": TIE_REASON,
        "pairs": pairs,
        "context": _context(pairs, load_registries(), out.parent),
    }
    html = _TEMPLATE.replace(
        "/*__DATA__*/null", json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    )
    out.write_text(html, encoding="utf-8")

    per_variant: dict[str, int] = {}
    for pair in pairs:
        per_variant[pair["variant"]] = per_variant.get(pair["variant"], 0) + 1
    log(f"compare: {out}")
    log(f"  축={axis}  baseline={baseline}  seed={seed}  쌍 {len(pairs)}개")
    for model in sorted(per_variant):
        log(f"    vs {model:<12} {per_variant[model]:>3} 쌍")
    if exclude:
        log(f"  제외된 팔: {', '.join(sorted(exclude))} (A-5: 지연 팔은 쌍대 비교 대상이 아니다)")
    if open_browser:
        webbrowser.open(out.as_uri())
    return out


_TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>genlab compare — 강제 선택</title>
<style>
  :root { --bg:#14151a; --panel:#1c1e26; --line:#2c2f3a; --text:#e8e9ee; --muted:#9aa0b0;
          --pass:#3ecf8e; --fail:#ff6b6b; --accent:#7aa2ff; --warn:#ffcf70; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
         font:13px/1.45 ui-sans-serif,"Segoe UI","Malgun Gothic",system-ui,sans-serif; }
  header { position:sticky; top:0; z-index:30; background:var(--panel);
           border-bottom:1px solid var(--line); padding:10px 14px;
           display:flex; gap:14px; align-items:center; flex-wrap:wrap; }
  h1 { font-size:15px; margin:0; font-weight:600; }
  .muted { color:var(--muted); } .grow { flex:1; }
  button, input[type=text], input[type=file]::file-selector-button {
    background:#262a35; color:var(--text); border:1px solid var(--line);
    border-radius:6px; padding:5px 10px; font:inherit; cursor:pointer; }
  button:hover { border-color:var(--accent); }
  button:disabled { opacity:.4; cursor:not-allowed; }
  input[type=text] { cursor:text; }
  main { padding:14px; max-width:1500px; margin:0 auto; }
  .stage { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
  .side { background:var(--panel); border:2px solid var(--line); border-radius:10px;
          padding:10px; display:flex; flex-direction:column; gap:8px; }
  .side.picked { border-color:var(--pass); }
  .side img { width:100%; aspect-ratio:3/4; object-fit:contain; background:#000;
              border-radius:6px; cursor:zoom-in; display:block; }
  .side .hd { display:flex; justify-content:space-between; align-items:center; }
  .sidename { font-weight:600; font-size:15px; }
  .pick { width:100%; padding:9px; font-weight:600; }
  .ctx { margin-top:14px; background:var(--panel); border:1px solid var(--line);
         border-radius:10px; padding:10px; display:flex; gap:14px; align-items:flex-start;
         flex-wrap:wrap; }
  .ctx .block { display:flex; gap:6px; align-items:flex-start; }
  .ctx img { height:132px; border-radius:6px; border:1px solid var(--line); background:#000;
             cursor:zoom-in; }
  .ctx h4 { margin:0 0 4px; font-size:11px; color:var(--muted); font-weight:600; }
  .reasons { display:flex; gap:6px; flex-wrap:wrap; margin-top:10px; }
  .reasons button.on { border-color:var(--accent); background:#313a52; }
  .bar { display:flex; gap:10px; align-items:center; margin-top:12px; flex-wrap:wrap; }
  .note { flex:1; min-width:220px; background:#23262f; color:var(--text);
          border:1px solid var(--line); border-radius:5px; padding:6px 8px; font:inherit; }
  .badge { font-size:10px; padding:1px 6px; border-radius:99px; border:1px solid var(--line);
           color:var(--muted); }
  table { border-collapse:collapse; margin-top:10px; width:100%; }
  th,td { border:1px solid var(--line); padding:5px 8px; text-align:left; font-size:12px; }
  th { color:var(--muted); font-weight:600; }
  dialog { border:1px solid var(--line); background:var(--panel); color:var(--text);
           border-radius:10px; padding:14px; max-width:96vw; }
  dialog::backdrop { background:rgba(0,0,0,.85); }
  dialog img { max-width:92vw; max-height:86vh; object-fit:contain; display:block; }
  kbd { background:#262a35; border:1px solid var(--line); border-bottom-width:2px;
        border-radius:4px; padding:0 4px; font-size:11px; }
  .warnbox { background:#2a2320; border:1px solid #6b5426; color:var(--warn);
             border-radius:8px; padding:8px 10px; margin-bottom:12px; font-size:12px; }
</style>
</head>
<body>
<header>
  <h1>강제 선택 <span class="muted" id="title"></span></h1>
  <span class="muted" id="progress"></span>
  <span class="grow"></span>
  <label class="muted">judge <input type="text" id="judge" size="8" value="dev_a"></label>
  <button id="reveal" disabled>결과 보기</button>
  <button id="export">Export verdicts.json</button>
  <label class="muted" title="판정 유실 보험">
    Import <input type="file" id="import" accept="application/json,.json"></label>
</header>

<main>
  <div class="warnbox">
    <b>규칙 (05 부록 A-5 — 실행 전 확정)</b> · 반드시 좌 또는 우를 고른다(<b>동점 없음</b>) ·
    사유 태그 1개 필수 · 어느 쪽이 어느 설정인지는 <b>전량 판정 후에만</b> 열린다 ·
    정말 못 가르겠으면 고르되 사유를 <b>“갈랐다고 보기 어려움”</b>으로 — 그게 무차별의 기록이다.
  </div>

  <div id="meta" class="muted" style="margin-bottom:8px"></div>
  <div class="stage">
    <div class="side" id="side-left">
      <div class="hd"><span class="sidename">A (좌)</span><span class="badge">1</span></div>
      <img id="img-left" alt="A">
      <button class="pick" data-pick="left">이쪽이 낫다 &nbsp;<kbd>1</kbd></button>
    </div>
    <div class="side" id="side-right">
      <div class="hd"><span class="sidename">B (우)</span><span class="badge">2</span></div>
      <img id="img-right" alt="B">
      <button class="pick" data-pick="right">이쪽이 낫다 &nbsp;<kbd>2</kbd></button>
    </div>
  </div>

  <div class="reasons" id="reasons"></div>
  <div class="bar">
    <button id="prev">← 이전</button>
    <button id="next">다음 →</button>
    <input class="note" id="note" placeholder="한 줄 메모 (선택)">
  </div>

  <div class="ctx" id="ctx"></div>

  <div id="results" style="display:none"></div>
</main>

<dialog id="zoom"><img id="zoom-img" alt=""></dialog>

<script id="data" type="application/json">/*__DATA__*/null</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
const PAIRS = DATA.pairs;
const SKEY = `genlab:compare:${DATA.title}:${DATA.seed}`;
const key = id => `${SKEY}:${id}`;
let idx = 0;

document.getElementById('title').textContent =
  `${DATA.title} · baseline ${DATA.baseline} · seed ${DATA.seed}`;

const load = id => { try { return JSON.parse(localStorage.getItem(key(id))) || {}; }
                     catch (e) { return {}; } };
const save = (id, v) => localStorage.setItem(key(id), JSON.stringify(v));
const done = v => !!(v.choice && v.reason);
const allDone = () => PAIRS.every(p => done(load(p.pair_id)));

function refreshHeader() {
  const n = PAIRS.filter(p => done(load(p.pair_id))).length;
  document.getElementById('progress').textContent = `판정 ${n}/${PAIRS.length}`;
  document.getElementById('reveal').disabled = n < PAIRS.length;
}

function render() {
  const pair = PAIRS[idx];
  const v = load(pair.pair_id);
  const ctx = DATA.context[`${pair.selfie}__${pair.product}`] || {};

  document.getElementById('meta').innerHTML =
    `<b>${idx + 1} / ${PAIRS.length}</b> · ${pair.exp} · ${pair.selfie} · ` +
    `${ctx.product_name || pair.product} <span class="badge">${ctx.wear_position || ''}</span> · rep ${pair.rep}` +
    (ctx.size ? ` · <span class="muted">📏 ${ctx.size}</span>` : '');

  document.getElementById('img-left').src = pair.left.image;
  document.getElementById('img-right').src = pair.right.image;
  document.getElementById('side-left').classList.toggle('picked', v.choice === 'left');
  document.getElementById('side-right').classList.toggle('picked', v.choice === 'right');

  document.getElementById('reasons').innerHTML = DATA.reasons.map(r =>
    `<button data-reason="${r.id}" class="${v.reason === r.id ? 'on' : ''}">${r.label}</button>`
  ).join('');

  document.getElementById('note').value = v.note || '';

  document.getElementById('ctx').innerHTML =
    `<div class="block"><div><h4>원본 셀카</h4>` +
    (ctx.selfie_src ? `<img src="${ctx.selfie_src}" data-zoom="${ctx.selfie_src}" alt="selfie">` : '—') +
    `</div></div><div class="block"><div><h4>상품 레퍼런스 (c1 대조용)</h4>` +
    (ctx.refs || []).map(s => `<img src="${s}" data-zoom="${s}" alt="ref">`).join('') +
    `</div></div>`;

  document.getElementById('prev').disabled = idx === 0;
  document.getElementById('next').disabled = idx === PAIRS.length - 1;
  refreshHeader();
}

function pick(side) {
  const pair = PAIRS[idx];
  const v = load(pair.pair_id);
  v.choice = side;
  v.winner = pair[side].arm;            // 블라인드는 화면 한정 — 집계는 정답을 안다
  save(pair.pair_id, v);
  render();
  // 사유까지 찍혀야 완료다. 사유가 이미 있으면 자동으로 다음 쌍으로 넘어간다.
  if (v.reason && idx < PAIRS.length - 1) setTimeout(() => { idx++; render(); }, 160);
}

document.addEventListener('click', e => {
  const zoom = e.target.dataset && (e.target.dataset.zoom || (e.target.id === 'img-left'
    ? PAIRS[idx].left.image : e.target.id === 'img-right' ? PAIRS[idx].right.image : null));
  if (zoom) { document.getElementById('zoom-img').src = zoom;
              document.getElementById('zoom').showModal(); return; }
  const p = e.target.dataset && e.target.dataset.pick;
  if (p) { pick(p); return; }
  const r = e.target.dataset && e.target.dataset.reason;
  if (r) {
    const pair = PAIRS[idx]; const v = load(pair.pair_id);
    v.reason = r; save(pair.pair_id, v); render();
    if (v.choice && idx < PAIRS.length - 1) setTimeout(() => { idx++; render(); }, 160);
  }
});
document.getElementById('zoom').addEventListener('click', () =>
  document.getElementById('zoom').close());
document.getElementById('prev').onclick = () => { if (idx > 0) { idx--; render(); } };
document.getElementById('next').onclick = () => { if (idx < PAIRS.length - 1) { idx++; render(); } };
document.getElementById('note').oninput = e => {
  const pair = PAIRS[idx]; const v = load(pair.pair_id);
  v.note = e.target.value; save(pair.pair_id, v); refreshHeader();
};
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (e.key === '1') pick('left');
  else if (e.key === '2') pick('right');
  else if (e.key === 'ArrowLeft' && idx > 0) { idx--; render(); }
  else if (e.key === 'ArrowRight' && idx < PAIRS.length - 1) { idx++; render(); }
});

// --- 집계: 전량 판정 후에만. 중간 점수를 보면 남은 판정이 거기 끌려간다 (A-5) ---
document.getElementById('reveal').onclick = () => {
  const tally = {};
  PAIRS.forEach(p => {
    const v = load(p.pair_id);
    const t = tally[p.variant] || (tally[p.variant] = { win: 0, loss: 0, tie: 0, n: 0, reasons: {} });
    t.n++;
    if (v.reason === DATA.tie_reason) t.tie++;
    else if (v.winner === p.variant) t.win++;
    else t.loss++;
    if (v.reason) t.reasons[v.reason] = (t.reasons[v.reason] || 0) + 1;
  });
  const label = id => (DATA.reasons.find(r => r.id === id) || {}).label || id;
  const rows = Object.keys(tally).sort().map(m => {
    const t = tally[m];
    // A-5 채택 규칙: 변형 승 ≥ 6/8 이면 채택, 그 외 '차이 없음' → 타이브레이커.
    const verdict = t.win >= Math.ceil(t.n * 0.75) && t.tie * 2 <= t.n
      ? '<b style="color:var(--pass)">변형 우세</b>'
      : (t.tie * 2 > t.n ? '<b style="color:var(--warn)">사실상 무차별</b>' : '차이 없음 → 타이브레이커');
    const rs = Object.keys(t.reasons).sort((a, b) => t.reasons[b] - t.reasons[a])
      .map(r => `${label(r)} ${t.reasons[r]}`).join(', ');
    return `<tr><td><b>${m}</b> vs ${DATA.baseline}</td><td>${t.win}/${t.n}</td>` +
           `<td>${t.loss}</td><td>${t.tie}</td><td>${verdict}</td><td class="muted">${rs}</td></tr>`;
  }).join('');
  document.getElementById('results').style.display = 'block';
  document.getElementById('results').innerHTML =
    `<h3 style="margin:18px 0 4px">집계</h3>
     <div class="muted">A-5 채택 규칙: 변형 승 ≥ 6/8 AND 층 1(갤러리 c1~c6) 회귀 없음 → 채택.
     그 외에는 <b>차이 없음</b>으로 판정하고 타이브레이커(① first-ok p95 ② 단가 ③ baseline 유지)로 간다.
     이 표는 층 2만 본다 — 층 1 회귀 확인은 갤러리에서 별도로 해야 한다.</div>
     <table><tr><th>비교</th><th>변형 승</th><th>패</th><th>무차별</th><th>판정</th><th>사유 분포</th></tr>
     ${rows}</table>`;
};

document.getElementById('export').onclick = () => {
  const verdicts = {};
  PAIRS.forEach(p => {
    const v = load(p.pair_id);
    if (Object.keys(v).length) verdicts[p.pair_id] = {
      ...v, variant: p.variant, baseline: p.baseline, baseline_side: p.baseline_side,
      exp: p.exp, selfie: p.selfie, product: p.product, rep: p.rep,
    };
  });
  const missing = PAIRS.filter(p => !done(load(p.pair_id))).length;
  if (missing && !confirm(`미완 ${missing}건(선택 또는 사유 누락). 그대로 내보낼까?`)) return;
  const blob = new Blob([JSON.stringify({
    title: DATA.title, baseline: DATA.baseline, seed: DATA.seed, excluded: DATA.excluded,
    judge: document.getElementById('judge').value.trim() || 'dev_a',
    judged_at: new Date().toISOString(), verdicts,
  }, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = 'verdicts.json'; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
};

document.getElementById('import').onchange = e => {
  const file = e.target.files[0]; if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const parsed = JSON.parse(reader.result);
      Object.entries(parsed.verdicts || {}).forEach(([id, v]) => save(id, v));
      if (parsed.judge) document.getElementById('judge').value = parsed.judge;
      render(); alert(`불러왔다: ${Object.keys(parsed.verdicts || {}).length}건`);
    } catch (err) { alert('verdicts.json을 읽지 못했다: ' + err.message); }
  };
  reader.readAsText(file);
};

render();
</script>
</body>
</html>
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m genlab.compare",
        description="강제 선택 쌍대 비교 페이지 생성 (05 부록 A-5 층 2)",
    )
    parser.add_argument("exp_dirs", nargs="+", type=Path, help="experiments/s4a_settings ...")
    parser.add_argument("--baseline", required=True, help="대조군 키 (예: gpt_base 또는 pv1)")
    parser.add_argument(
        "--axis",
        choices=("model", "pv"),
        default="model",
        help="비교 축. 설정 실험은 model, R3 프롬프트 축은 pv (기본: model)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="MODEL",
        help="쌍대 비교에서 뺄 팔 (지연 전용 팔 등). 반복 지정 가능",
    )
    parser.add_argument(
        "--seed",
        default="s4",
        help="좌우 배치 seed. 같은 seed = 같은 배치 (재현 가능한 무작위)",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--open", action="store_true", help="생성 후 브라우저로 연다")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        build(
            args.exp_dirs,
            baseline=args.baseline,
            exclude=set(args.exclude),
            seed=args.seed,
            out=args.out,
            axis=args.axis,
            open_browser=args.open,
        )
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
