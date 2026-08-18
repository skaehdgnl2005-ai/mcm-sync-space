"""채점 갤러리 — 06-gen-research-tools-spec.md §6.

**단일 정적 HTML**, 서버 없음. manifest 데이터를 HTML에 JSON으로 인라인한다
(file:// 에서는 fetch가 막히므로 외부 JSON 로드는 성립하지 않는다). 이미지는
상대경로 참조 — 브라우저로 파일을 열기만 하면 채점할 수 있다.

채점 단위는 **이미지(rep)**다 (05 §4). 셀 통과(best-of-N) 계산은 report의 일이고,
여기서는 사람이 본 것만 기록한다.
"""

from __future__ import annotations

import json
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import manifest
from .runner import Registries, load_registries, resolve

GALLERY_NAME = "gallery.html"

# 05 §4 — 체크리스트 6항목에 안정 ID를 부여한 표. fail 기준은 툴팁으로 노출한다
# ("애매하면 fail" 규칙을 채점 중에 계속 상기시키는 것이 목적).
CHECKLIST: tuple[tuple[str, str, str], ...] = (
    ("c1", "패턴·로고·하드웨어 톤", "비세토스 반복 단위 왜곡 / 로고 플레이트 문자 뭉개짐 / 골드·실버 뒤바뀜 / 컬러 이탈"),
    ("c2", "크기·착용 비율", "DB 사이즈 대비 명백한 과대·과소 (미디엄 크로스바디가 토트 크기 등)"),
    # c3는 2026-08-16에 개정됐다 (05 §4 / 부록 A-7 ④). 원래 기준이 back의 스트랩 누락만
    # 적고 있어 cross·shoulder를 판정할 규칙이 없었고, 같은 결함이 팔마다 다르게 판정되는
    # 사고가 실제로 났다. 툴팁이 채점 중에 사람이 읽는 유일한 기준이므로 여기가 곧 규칙이다.
    ("c3", "지정 wear_position",
     "지정 위치가 아닌 곳에 합성 / back인데 스트랩 표현조차 없음 / "
     "cross·shoulder인데 ⓐ어깨~가방 스트랩 구간 누락 ⓑ스트랩이 팔·몸통 관통 ⓒ좌우가 지시와 반대"),
    ("c4", "인물 보존", "얼굴 왜곡·동일성 훼손 / 손가락 왜곡 / 의상 임의 변형"),
    ("c5", "조명·그림자 + 배경", "배경 변형 / 그림자 없음·방향 불일치로 제품이 '붙인 티'"),
    ("c6", "화보 톤", "광고 화보로 못 쓸 어색함 (주관 — 보수 판정)"),
)


def build(exp_dir: Path, *, open_browser: bool = False, log=print) -> Path:
    rows = manifest.read(exp_dir / manifest.MANIFEST_NAME)
    if not rows:
        raise FileNotFoundError(
            f"no manifest rows in {exp_dir / manifest.MANIFEST_NAME} — run first."
        )
    payload = _payload(exp_dir, rows, load_registries())
    html = _TEMPLATE.replace("/*__DATA__*/null", _inline_json(payload))
    target = exp_dir / GALLERY_NAME
    target.write_text(html, encoding="utf-8")
    log(f"gallery: {target}  ({payload['stats']['images']} images, {payload['stats']['cells']} cells)")
    if open_browser:
        webbrowser.open(target.resolve().as_uri())
    return target


def _inline_json(payload: dict) -> str:
    """`</script>`로 문서를 깨뜨리지 않도록 '<'를 escape한다. JSON 구조 문자에는
    '<'가 없으므로 문자열 안에서만 \\u003c로 바뀌며, 파싱 결과는 동일하다."""
    return json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")


def _payload(exp_dir: Path, rows: list[dict[str, Any]], reg: Registries) -> dict:
    latest = manifest.latest_by_cell(rows)
    items = []
    for cell_id, row in latest.items():
        items.append(
            {
                "cell_id": cell_id,
                "model": row.get("model", ""),
                "selfie": row.get("selfie", ""),
                "product": row.get("product", ""),
                "wear_position": row.get("wear_position", ""),
                "pv": row.get("prompt_version", ""),
                "rep": row.get("rep", 1),
                "status": row.get("status", "error"),
                "image": row.get("image_path"),
                "latency_ms": row.get("latency_ms", 0),
                "error_message": row.get("error_message"),
                "prompt_text": row.get("prompt_text", ""),
            }
        )
    items.sort(key=lambda item: (item["selfie"], item["product"], item["model"], item["pv"], item["rep"]))

    columns = _ordered({(item["model"], item["pv"], item["rep"]) for item in items})
    row_keys = _ordered({(item["selfie"], item["product"]) for item in items})

    grid_rows = []
    for selfie, product in row_keys:
        product_spec = reg.products.get(product)
        selfie_spec = reg.selfies.get(selfie)
        grid_rows.append(
            {
                "key": f"{selfie}__{product}",
                "selfie": selfie,
                "product": product,
                "wear_position": product_spec.wear_position if product_spec else "",
                "product_name": (product_spec.name if product_spec else "") or product,
                # c2("DB 사이즈 대비 과대·과소") 판정용 참고값. 프롬프트에는 들어가지
                # 않는 계약 밖 정보다 (runner.ProductSpec.size 주석 참고).
                "size": (product_spec.size if product_spec else "") or "",
                "formality": (selfie_spec.formality if selfie_spec else "") or "",
                "selfie_src": _relative(exp_dir, selfie_spec.file) if selfie_spec else None,
                "refs": [_relative(exp_dir, ref) for ref in (product_spec.ref_images if product_spec else [])],
            }
        )

    return {
        # manifest의 exp_id가 아니라 **디렉터리 이름**을 쓴다. --fake 실행은
        # exp_id가 'r2_bakeoff'인 채 experiments/r2_bakeoff_fake/에 저장되므로,
        # manifest 값을 쓰면 fake 갤러리와 실전 갤러리의 localStorage 키(exp_id+cell_id)가
        # 통째로 겹친다 → 연습 채점이 진짜 R2 채점으로 둔갑한다.
        "exp_id": exp_dir.resolve().name,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "checklist": [{"id": cid, "label": label, "fail": fail} for cid, label, fail in CHECKLIST],
        "columns": [{"key": f"{m}__{pv}__r{rep}", "model": m, "pv": pv, "rep": rep} for m, pv, rep in columns],
        "rows": grid_rows,
        "items": items,
        "stats": {"images": sum(1 for i in items if i["status"] == "ok"), "cells": len(row_keys)},
    }


def _ordered(values: set[tuple]) -> list[tuple]:
    return sorted(values, key=lambda value: tuple(str(part) for part in value))


def _relative(exp_dir: Path, asset_path: str) -> str:
    """experiments/{exp}/gallery.html 기준 상대경로 (보통 ../../assets/...).
    file:// 에서 그대로 뜬다."""
    try:
        return resolve(asset_path).resolve().relative_to(exp_dir.resolve(), walk_up=True).as_posix()
    except (ValueError, TypeError):
        return resolve(asset_path).resolve().as_uri()


_TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>genlab gallery</title>
<style>
  :root {
    --bg: #14151a; --panel: #1c1e26; --line: #2c2f3a; --text: #e8e9ee;
    --muted: #9aa0b0; --pass: #3ecf8e; --fail: #ff6b6b; --accent: #7aa2ff;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text);
         font: 13px/1.45 ui-sans-serif, "Segoe UI", "Malgun Gothic", system-ui, sans-serif; }
  header { position: sticky; top: 0; z-index: 30; background: var(--panel);
           border-bottom: 1px solid var(--line); padding: 10px 14px;
           display: flex; gap: 14px; align-items: center; flex-wrap: wrap; }
  h1 { font-size: 15px; margin: 0; font-weight: 600; }
  .muted { color: var(--muted); }
  .grow { flex: 1; }
  button, input[type=text], input[type=file]::file-selector-button {
    background: #262a35; color: var(--text); border: 1px solid var(--line);
    border-radius: 6px; padding: 5px 10px; font: inherit; cursor: pointer; }
  button:hover { border-color: var(--accent); }
  input[type=text] { cursor: text; }
  .wrap { overflow: auto; padding: 12px; }
  table { border-collapse: separate; border-spacing: 8px; }
  th { font-weight: 600; font-size: 12px; text-align: left; color: var(--muted);
       position: sticky; top: 0; background: var(--bg); padding: 4px 2px; }
  td.rowhead { vertical-align: top; width: 168px; position: sticky; left: 0;
               background: var(--bg); z-index: 10; }
  .rowhead .thumbs { display: flex; gap: 4px; margin-top: 6px; }
  .rowhead img { width: 46px; height: 62px; object-fit: cover; border-radius: 4px;
                 border: 1px solid var(--line); background: #000; }
  .card { width: 250px; background: var(--panel); border: 1px solid var(--line);
          border-radius: 8px; padding: 8px; }
  .card.needs-note { border-color: var(--fail); }
  .card.done { border-color: #34694f; }
  /* 미판정(값 없음)과 fail(값 false)은 둘 다 '빈 체크박스'로 보인다. 이 구분이 화면에
     없으면 채점자가 c3를 건너뛴 것과 fail로 찍은 것이 섞인다 — 실제로 S4a/S4b에서
     10장이 그렇게 유실됐다 (05 부록 A-7 ②'). 상태를 눈에 보이게 만든다. */
  .card.partial { border-color: var(--warn); }
  .checks label.unjudged { color: var(--warn); font-weight: 600; }
  .checks label.unjudged::after { content: "?"; }
  .checks label.isfail { color: var(--fail); text-decoration: line-through; }
  .partialbadge { font-size: 10px; color: var(--warn); margin-top: 4px; display: block; }
  .shot { width: 100%; aspect-ratio: 3/4; object-fit: cover; border-radius: 6px;
          background: #000; cursor: zoom-in; display: block; }
  .placeholder { width: 100%; aspect-ratio: 3/4; border-radius: 6px; display: flex;
                 align-items: center; justify-content: center; text-align: center;
                 background: #221c1c; color: var(--fail); font-size: 12px; padding: 8px; }
  .meta { display: flex; justify-content: space-between; color: var(--muted);
          font-size: 11px; margin: 6px 0 4px; }
  .checks { display: grid; grid-template-columns: repeat(3, 1fr); gap: 2px 4px; }
  .checks label { display: flex; gap: 4px; align-items: center; font-size: 11px;
                  cursor: pointer; user-select: none; }
  .checks input { accent-color: var(--pass); margin: 0; }
  .note { width: 100%; margin-top: 5px; background: #23262f; color: var(--text);
          border: 1px solid var(--line); border-radius: 5px; padding: 4px 6px;
          font: inherit; resize: vertical; min-height: 26px; }
  .allpass { margin-top: 5px; width: 100%; font-size: 11px; padding: 3px; }
  .badge { font-size: 10px; padding: 1px 5px; border-radius: 99px; border: 1px solid var(--line); }
  /* 규격은 c2 판정 참고값이다 — 프롬프트에 들어간 정보가 아니라는 걸 시각적으로도
     구분하려고 다른 색을 쓴다 (모델이 못 받은 정보로 모델을 탓하지 않도록). */
  .size { color: #8fb0d9; font-size: 11px; margin-top: 4px; line-height: 1.3; }
  .badge.refused { color: #ffcf70; border-color: #6b5426; }
  .badge.error, .badge.timeout { color: var(--fail); border-color: #6b2626; }
  dialog { border: 1px solid var(--line); background: var(--panel); color: var(--text);
           border-radius: 10px; padding: 0; max-width: 96vw; width: 1180px; }
  dialog::backdrop { background: rgba(0,0,0,.78); }
  .lb { display: grid; grid-template-columns: 1fr 260px; gap: 14px; padding: 14px; }
  .lb-main img { width: 100%; max-height: 74vh; object-fit: contain; background: #000;
                 border-radius: 8px; }
  .lb-side img { width: 100%; border-radius: 6px; border: 1px solid var(--line);
                 margin-bottom: 8px; background: #000; }
  .lb-side h4 { margin: 0 0 4px; font-size: 12px; color: var(--muted); font-weight: 600; }
  .lb-foot { display: flex; gap: 10px; align-items: flex-start; padding: 0 14px 14px; }
  .lb-foot .checks { grid-template-columns: repeat(6, auto); gap: 10px; }
  .lb-foot .checks label { font-size: 12px; }
  details pre { white-space: pre-wrap; font-size: 11px; color: var(--muted);
                max-height: 220px; overflow: auto; background: #16181f; padding: 8px;
                border-radius: 6px; }
  kbd { background: #262a35; border: 1px solid var(--line); border-bottom-width: 2px;
        border-radius: 4px; padding: 0 4px; font-size: 11px; }
</style>
</head>
<body>
<header>
  <h1>genlab <span id="expid"></span></h1>
  <span class="muted" id="progress"></span>
  <span class="muted" id="warn"></span>
  <span class="grow"></span>
  <label class="muted">scorer <input type="text" id="scorer" size="8" value="dev_a"></label>
  <button id="export">Export scores.json</button>
  <label class="muted" title="채점 유실 보험 — 내보낸 파일을 다시 불러온다">
    Import <input type="file" id="import" accept="application/json,.json"></label>
</header>

<div id="vary" style="padding:8px 14px; font-size:12px; border-bottom:1px solid var(--line);
     background:#191b22"></div>
<div class="wrap"><table id="grid"></table></div>

<dialog id="lightbox">
  <div class="lb">
    <div class="lb-main">
      <img id="lb-shot" alt="">
      <div class="muted" id="lb-caption" style="margin-top:8px"></div>
    </div>
    <div class="lb-side">
      <h4>원본 셀카</h4><img id="lb-selfie" alt="">
      <h4>상품 레퍼런스</h4><div id="lb-refs"></div>
    </div>
  </div>
  <div class="lb-foot">
    <div class="checks" id="lb-checks"></div>
    <textarea class="note" id="lb-note" rows="1" placeholder="fail 사유 한 줄 (필수)"></textarea>
    <button id="lb-close">닫기 (Esc)</button>
  </div>
  <div class="muted" style="padding:0 14px 12px">
    <kbd>&larr;</kbd><kbd>&rarr;</kbd> 이동 · <kbd>1</kbd>~<kbd>6</kbd> c1~c6 토글 ·
    <kbd>a</kbd> 전항목 pass · <kbd>Esc</kbd> 닫기
  </div>
</dialog>

<script id="data" type="application/json">/*__DATA__*/null</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
const IDS = DATA.checklist.map(c => c.id);
const key = id => `genlab:${DATA.exp_id}:${id}`;
const byId = Object.fromEntries(DATA.items.map(i => [i.cell_id, i]));
const scorable = DATA.items.filter(i => i.status === 'ok');

function load(id) {
  try { return JSON.parse(localStorage.getItem(key(id))) || {}; } catch (e) { return {}; }
}
function save(id, score) {
  localStorage.setItem(key(id), JSON.stringify(score));
  refreshCard(id); refreshHeader();
}
const isScored = s => IDS.every(c => typeof s[c] === 'boolean');
const hasFail = s => IDS.some(c => s[c] === false);
const needsNote = s => hasFail(s) && !(s.note || '').trim();

document.getElementById('expid').textContent = DATA.exp_id;

function refreshHeader() {
  const scored = scorable.filter(i => isScored(load(i.cell_id))).length;
  const missing = scorable.filter(i => needsNote(load(i.cell_id))).length;
  // '하다 만' 이미지 = 일부만 찍힌 것. report가 통째로 집계에서 빼므로 조용히 유실된다.
  const partial = scorable.filter(i => {
    const s = load(i.cell_id), blanks = IDS.filter(c => s[c] === undefined).length;
    return blanks > 0 && blanks < IDS.length;
  }).length;
  document.getElementById('progress').textContent =
    `채점 ${scored}/${scorable.length}` + (DATA.items.length > scorable.length
      ? ` · 생성 실패 ${DATA.items.length - scorable.length}건` : '');
  // 05 §4: note 한 줄은 의무다 (§7 스펙·R4 실패 분류의 원천 데이터).
  const notes = [];
  if (partial) notes.push(`⚠ 일부만 판정된 이미지 ${partial}건 — 집계에서 제외된다`);
  if (missing) notes.push(`⚠ fail인데 note 없음 ${missing}건`);
  document.getElementById('warn').textContent = notes.join(' · ');
  document.getElementById('warn').style.color = notes.length ? 'var(--warn)' : '';
}

function checkboxes(item, prefix) {
  const score = load(item.cell_id);
  return DATA.checklist.map((c, n) => {
    const v = score[c.id];
    const on = v === true ? 'checked' : '';
    // 미판정(undefined)과 fail(false)을 시각적으로 가른다. 둘 다 체크박스는 비어 있다.
    const cls = v === undefined ? 'unjudged' : (v === false ? 'isfail' : '');
    const hint = v === undefined ? ' — 아직 판정하지 않음 (한 번 클릭=pass, 두 번=fail)' : '';
    return `<label class="${cls}" title="fail 기준: ${c.fail}${hint}"><input type="checkbox" data-cell="${item.cell_id}"
      data-check="${c.id}" id="${prefix}-${item.cell_id}-${c.id}" ${on}>${c.id}
      <span class="muted">${n + 1}</span></label>`;
  }).join('');

const unjudged = item => IDS.filter(c => load(item.cell_id)[c] === undefined);
}

function cardHtml(item) {
  if (!item) return '<div class="card muted" style="opacity:.4">—</div>';
  const score = load(item.cell_id);
  const blanks = item.status === 'ok' ? IDS.filter(c => score[c] === undefined) : [];
  // 한 항목이라도 손댔는데 남은 게 있으면 '하다 만' 상태다 — 아예 안 건드린 카드와 구분한다.
  const partial = blanks.length > 0 && blanks.length < IDS.length;
  const cls = ['card', isScored(score) ? 'done' : '', partial ? 'partial' : '',
               needsNote(score) ? 'needs-note' : ''].join(' ');
  const shot = item.status === 'ok' && item.image
    ? `<img class="shot" src="${item.image}" loading="lazy" alt="${item.cell_id}" data-open="${item.cell_id}">`
    : `<div class="placeholder"><div><b>${item.status}</b><br>${item.error_message || ''}</div></div>`;
  const body = item.status === 'ok' ? `
      <div class="checks">${checkboxes(item, 'g')}</div>
      <textarea class="note" rows="1" data-note="${item.cell_id}"
        placeholder="fail 사유 한 줄">${(score.note || '').replace(/</g, '&lt;')}</textarea>
      <button class="allpass" data-allpass="${item.cell_id}">6/6 전항목 pass</button>
      ${partial ? `<span class="partialbadge">⚠ 미판정 ${blanks.join(', ')} — 집계에서 제외된다</span>` : ''}` : '';
  return `<div class="${cls}" id="card-${item.cell_id}">
      ${shot}
      <div class="meta"><span>${VARIES.pv && !VARIES.model
          ? '<b>' + item.pv + '</b> · r' + item.rep
          : item.model + ' · ' + item.pv + ' · r' + item.rep}</span>
        <span class="badge ${item.status}">${item.status === 'ok' ? item.latency_ms + 'ms' : item.status}</span></div>
      ${body}</div>`;
}

function refreshCard(id) {
  const el = document.getElementById('card-' + id);
  if (el) el.outerHTML = cardHtml(byId[id]);
}

// 이 실험에서 **실제로 변하는** 열 차원을 찾는다.
// 안 변하는 것(R3에서는 model)을 크게 쓰면 채점자에게 "전부 똑같아 보인다"는 인상을 준다.
// 축별 1:1 실험은 원래 한 가지만 빼고 전부 고정하므로, 그 한 가지를 눈에 띄게 해야 한다.
const VARIES = {
  model: new Set(DATA.columns.map(c => c.model)).size > 1,
  pv: new Set(DATA.columns.map(c => c.pv)).size > 1,
};
function colLabel(c) {
  const big = [], small = [];
  (VARIES.model ? big : small).push(c.model);
  (VARIES.pv ? big : small).push(c.pv);
  if (!big.length) big.push(c.model);        // 둘 다 고정이면 model을 표제로
  small.push('r' + c.rep);
  return `${big.join(' · ')}<br><span class="muted">${small.join(' · ')}</span>`;
}
function varyBanner() {
  const dims = Object.keys(VARIES).filter(k => VARIES[k]);
  if (!dims.length) return '';
  const vals = d => [...new Set(DATA.columns.map(c => c[d]))].join(' vs ');
  return '이 실험에서 다른 것 → ' + dims.map(d => `<b>${d}</b>: ${vals(d)}`).join(' · ')
       + ` <span class="muted">(나머지는 전부 고정 — 같은 인물·같은 상품이 반복되는 것이 정상이다)</span>`;
}

function render() {
  document.getElementById('vary').innerHTML = varyBanner();
  const head = ['<tr><th></th>'].concat(
    DATA.columns.map(c => `<th>${colLabel(c)}</th>`)
  ).join('') + '</tr>';
  const body = DATA.rows.map(row => {
    const refs = (row.refs || []).map(src => `<img src="${src}" alt="ref">`).join('');
    const rowHead = `<td class="rowhead">
        <b>${row.selfie}</b> <span class="muted">${row.formality}</span><br>
        <span class="muted">${row.product}</span><br>
        <span class="muted">${row.product_name}</span><br>
        <span class="badge">${row.wear_position}</span>
        ${row.size ? `<div class="size" title="c2 판정 참고 — 프롬프트에는 들어가지 않는 정보">📏 ${row.size}</div>` : ''}
        <div class="thumbs">${row.selfie_src ? `<img src="${row.selfie_src}" alt="selfie">` : ''}${refs}</div>
      </td>`;
    const cells = DATA.columns.map(col => {
      const item = DATA.items.find(i =>
        i.selfie === row.selfie && i.product === row.product &&
        i.model === col.model && i.pv === col.pv && i.rep === col.rep);
      return `<td>${cardHtml(item)}</td>`;
    }).join('');
    return `<tr>${rowHead}${cells}</tr>`;
  }).join('');
  document.getElementById('grid').innerHTML = head + body;
  refreshHeader();
}

// --- 채점 입력 (카드/라이트박스 공통 — 이벤트 위임) ---
document.addEventListener('change', e => {
  const cell = e.target.dataset && e.target.dataset.cell;
  if (!cell) return;
  const score = load(cell);
  score[e.target.dataset.check] = e.target.checked;
  save(cell, score);
  if (current === cell) syncLightbox();
});
document.addEventListener('input', e => {
  const cell = e.target.dataset && (e.target.dataset.note || e.target.dataset.lbnote);
  if (!cell) return;
  const score = load(cell);
  score.note = e.target.value;
  localStorage.setItem(key(cell), JSON.stringify(score));  // 타이핑 중 재렌더 금지
  refreshHeader();
});
document.addEventListener('click', e => {
  const open = e.target.dataset && e.target.dataset.open;
  if (open) { openLightbox(open); return; }
  const all = e.target.dataset && e.target.dataset.allpass;
  if (all) { allPass(all); }
});

function allPass(cell) {
  const score = load(cell);
  IDS.forEach(c => score[c] = true);
  save(cell, score);
  if (current === cell) syncLightbox();
}

// --- 라이트박스: 원본 셀카·상품컷과 나란히 대조 ---
const dlg = document.getElementById('lightbox');
let current = null;
const rowOf = item => DATA.rows.find(r => r.selfie === item.selfie && r.product === item.product) || {};

function openLightbox(cell) {
  current = cell;
  syncLightbox();
  if (!dlg.open) dlg.showModal();
}
function syncLightbox() {
  const item = byId[current], row = rowOf(item);
  document.getElementById('lb-shot').src = item.image || '';
  document.getElementById('lb-selfie').src = row.selfie_src || '';
  document.getElementById('lb-refs').innerHTML = (row.refs || [])
    .map(src => `<img src="${src}" alt="ref">`).join('');
  document.getElementById('lb-caption').innerHTML =
    `<b>${item.cell_id}</b> · ${row.wear_position} · ${item.latency_ms}ms`
    + (row.size ? ` · <span class="size" title="c2 판정 참고 — 프롬프트 미포함">📏 ${row.size}</span>` : '')
    + `
     <details><summary>prompt (${item.pv})</summary><pre>${item.prompt_text.replace(/</g, '&lt;')}</pre></details>`;
  document.getElementById('lb-checks').innerHTML = checkboxes(item, 'lb');
  const note = document.getElementById('lb-note');
  note.value = load(current).note || '';
  note.dataset.lbnote = current;
}
document.getElementById('lb-close').onclick = () => dlg.close();
dlg.addEventListener('close', () => { const c = current; current = null; if (c) refreshCard(c); refreshHeader(); });

function step(delta) {
  const idx = scorable.findIndex(i => i.cell_id === current);
  const next = scorable[idx + delta];
  if (next) openLightbox(next.cell_id);
}
document.addEventListener('keydown', e => {
  if (!dlg.open || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT') return;
  if (e.key === 'ArrowRight') { step(1); e.preventDefault(); }
  else if (e.key === 'ArrowLeft') { step(-1); e.preventDefault(); }
  else if (e.key.toLowerCase() === 'a') { allPass(current); }
  else if (/^[1-6]$/.test(e.key)) {
    const id = IDS[Number(e.key) - 1];
    const score = load(current);
    score[id] = !(score[id] === true);
    save(current, score); syncLightbox();
  }
});

// --- Export / Import ---
document.getElementById('export').onclick = () => {
  const scores = {};
  DATA.items.forEach(item => {
    const s = load(item.cell_id);
    if (Object.keys(s).length) scores[item.cell_id] = s;
  });
  const missing = scorable.filter(i => needsNote(load(i.cell_id))).length;
  if (missing && !confirm(`fail인데 note가 없는 이미지 ${missing}건. 그대로 내보낼까?`)) return;
  const blob = new Blob([JSON.stringify({
    exp_id: DATA.exp_id,
    scorer: document.getElementById('scorer').value.trim() || 'dev_a',
    scored_at: new Date().toISOString(),
    scores,
  }, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'scores.json';
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
};

document.getElementById('import').onchange = e => {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const parsed = JSON.parse(reader.result);
      Object.entries(parsed.scores || {}).forEach(([cell, score]) =>
        localStorage.setItem(key(cell), JSON.stringify(score)));
      if (parsed.scorer) document.getElementById('scorer').value = parsed.scorer;
      render();
      alert(`불러왔다: ${Object.keys(parsed.scores || {}).length}건`);
    } catch (err) { alert('scores.json을 읽지 못했다: ' + err.message); }
  };
  reader.readAsText(file);
};

const stored = localStorage.getItem(`genlab:${DATA.exp_id}:__scorer`);
if (stored) document.getElementById('scorer').value = stored;
document.getElementById('scorer').onchange = e =>
  localStorage.setItem(`genlab:${DATA.exp_id}:__scorer`, e.target.value);

render();
</script>
</body>
</html>
"""
