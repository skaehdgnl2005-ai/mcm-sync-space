"""리포트 집계기 — 06-gen-research-tools-spec.md §7.

manifest.jsonl + scores.json → 모델별 표 + **05 §5의 부적격 게이트·타이브레이크
사슬을 산식으로** 구현한 순위 "제안".

도구는 제안하고 결정은 사람이 한다 (05 §4). 그래서 이 파일은 어디서도 "승자"를
확정하지 않는다 — 근거 라인과 함께 순서만 제시하고, summary.md를 05 부록 A에
붙여넣는 것이 유일한 영구 기록이다 (experiments/는 git 미추적).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import manifest
from .gallery import CHECKLIST
from .runner import load_registries

CHECK_IDS = tuple(cid for cid, _, _ in CHECKLIST)
SUMMARY_NAME = "summary.md"

# --- 05 §5 부적격 게이트 -----------------------------------------------------
MAX_FAIL_RATE = 0.20
"""거부+오류율 > 20% (R2 20회 중 4회 초과)."""
MAX_P95_MS = 40_000
"""단건 생성 p95 > 40s — 55s 예산 − I/O ~10s − 여유."""

# --- 05 §5 Go/No-Go 임계 (9셀 기준을 비율로 일반화) -------------------------
GO_CELL_RATIO = 6 / 9
CONDITIONAL_CELL_RATIO = 4 / 9
GO_C1_RATE = 0.70

SCARF_CHECKS = ("c1", "c3", "c4", "c5")
"""스카프 포함 판정 — 05 §5: 승자 출력 중 ≥1장이 이 4항목 동시 pass."""


@dataclass
class Variant:
    """순위 비교 단위. 보통 모델이지만, pv가 여럿인 실험(R3)에서는 모델×pv다."""

    model: str
    pv: str
    label: str
    unit_cost: float = 0.0
    attempts: int = 0
    ok: int = 0
    refused: int = 0
    error: int = 0
    timeout: int = 0
    cost: float = 0.0
    latencies: list[int] = field(default_factory=list)
    scored: int = 0
    check_pass: dict[str, int] = field(default_factory=lambda: {c: 0 for c in CHECK_IDS})
    check_scored: dict[str, int] = field(default_factory=lambda: {c: 0 for c in CHECK_IDS})
    cells: dict[str, bool] = field(default_factory=dict)
    """(selfie__product) → best-of-N 통과 여부 (05 §4: N개 중 ≥1장 전항목 pass)."""
    scarf_pass: bool = False
    scarf_seen: bool = False
    disqualified: list[str] = field(default_factory=list)

    @property
    def fail_rate(self) -> float:
        return (self.refused + self.error) / self.attempts if self.attempts else 0.0

    @property
    def p50(self) -> int:
        return percentile(self.latencies, 0.50)

    @property
    def p95(self) -> int:
        return percentile(self.latencies, 0.95)

    @property
    def cell_pass(self) -> int:
        return sum(1 for passed in self.cells.values() if passed)

    @property
    def cell_total(self) -> int:
        return len(self.cells)

    @property
    def cell_ratio(self) -> float:
        return self.cell_pass / self.cell_total if self.cell_total else 0.0

    def rate(self, check: str) -> Optional[float]:
        total = self.check_scored[check]
        return self.check_pass[check] / total if total else None

    def check_sum(self, *checks: str) -> int:
        return sum(self.check_pass[check] for check in checks)


def percentile(values: list[int], q: float) -> int:
    """nearest-rank. numpy를 쓰지 않는다 (연구 도구 의존 최소화)."""
    if not values:
        return 0
    ordered = sorted(values)
    index = max(1, math.ceil(q * len(ordered))) - 1
    return ordered[index]


def build(
    exp_dir: Path,
    *,
    scores_path: Optional[Path] = None,
    gates_path: Optional[Path] = None,
    out_path: Optional[Path] = None,
    log=print,
) -> str:
    rows = manifest.read(exp_dir / manifest.MANIFEST_NAME)
    if not rows:
        raise FileNotFoundError(f"no manifest rows in {exp_dir} — run first.")

    scores, scorer = _load_scores(exp_dir, scores_path)
    gates = _load_gates(exp_dir, gates_path)
    variants = _aggregate(rows, scores)
    _apply_gates(variants, gates)

    ranked = sorted(
        (v for v in variants.values() if not v.disqualified), key=_tiebreak_key
    )
    text = _render(exp_dir, rows, scores, scorer, variants, ranked, gates)

    target = out_path or (exp_dir / SUMMARY_NAME)
    target.write_text(text, encoding="utf-8")
    log(text)
    log(f"\nsummary: {target}")
    return text


# --- 집계 --------------------------------------------------------------------


def _aggregate(rows: list[dict[str, Any]], scores: dict[str, dict]) -> dict[str, Variant]:
    reg = load_registries()
    pvs = {row.get("prompt_version", "") for row in rows}
    multi_pv = len(pvs) > 1

    variants: dict[str, Variant] = {}
    # 시도 통계는 모든 행(재시도 포함)을, 채점 집계는 cell별 마지막 행을 쓴다.
    for row in rows:
        variant = _variant_for(variants, row, multi_pv, reg)
        variant.attempts += 1
        variant.cost += float(row.get("est_cost_usd") or 0.0)
        status = row.get("status", "error")
        if status in ("ok", "refused", "error", "timeout"):
            setattr(variant, status, getattr(variant, status) + 1)
        if status == "ok":
            variant.latencies.append(int(row.get("latency_ms") or 0))

    for cell_id, row in manifest.latest_by_cell(rows).items():
        if row.get("status") != "ok":
            continue
        variant = _variant_for(variants, row, multi_pv, reg)
        cell_key = f"{row.get('selfie')}__{row.get('product')}"
        variant.cells.setdefault(cell_key, False)

        score = scores.get(cell_id)
        if not score:
            continue
        graded = [score.get(check) for check in CHECK_IDS]
        if any(value is None for value in graded):
            continue  # 부분 채점은 집계에서 제외 — 6항목 전부 있어야 판정이 성립한다
        variant.scored += 1
        for check in CHECK_IDS:
            variant.check_scored[check] += 1
            if score[check] is True:
                variant.check_pass[check] += 1
        if all(graded):
            variant.cells[cell_key] = True  # best-of-N: 한 장이라도 전항목 pass면 셀 통과
        if str(row.get("wear_position")) == "neck":
            variant.scarf_seen = True
            if all(score.get(check) is True for check in SCARF_CHECKS):
                variant.scarf_pass = True
    return variants


def _variant_for(
    variants: dict[str, Variant], row: dict, multi_pv: bool, reg
) -> Variant:
    model = str(row.get("model", "?"))
    pv = str(row.get("prompt_version", "?"))
    key = f"{model}__{pv}" if multi_pv else model
    variant = variants.get(key)
    if variant is None:
        spec = reg.models.get(model)
        variant = Variant(
            model=model,
            pv=pv,
            label=f"{model}/{pv}" if multi_pv else model,
            unit_cost=spec.cost_per_image_usd if spec else 0.0,
        )
        variants[key] = variant
    return variant


def _apply_gates(variants: dict[str, Variant], gates: dict[str, dict]) -> None:
    for variant in variants.values():
        if variant.fail_rate > MAX_FAIL_RATE:
            variant.disqualified.append(
                f"거부+오류율 {variant.fail_rate:.1%} > {MAX_FAIL_RATE:.0%}"
            )
        if variant.p95 > MAX_P95_MS:
            variant.disqualified.append(
                f"생성 p95 {variant.p95 / 1000:.1f}s > {MAX_P95_MS / 1000:.0f}s"
            )
        manual = gates.get(variant.model) or gates.get(variant.label) or {}
        if manual.get("visible_watermark"):
            note = manual.get("note") or ""
            variant.disqualified.append(f"제거 불가한 가시 워터마크 {note}".strip())


def _tiebreak_key(variant: Variant) -> tuple:
    """05 §5 타이브레이커 사슬 — 01 §0 품질 우선순위와 1:1.

    주 지표 셀 통과 수 → ① c1+c2(제품 정합성) → ② c4(인물 보존) → ③ c3+c5(위치+융합)
    → ④ c6(화보 톤) → ⑤ p95 낮은 쪽 → ⑥ 단가 낮은 쪽 → (⑦ 운영성은 사람 판단).
    """
    return (
        -variant.cell_pass,
        -variant.check_sum("c1", "c2"),
        -variant.check_sum("c4"),
        -variant.check_sum("c3", "c5"),
        -variant.check_sum("c6"),
        variant.p95 if variant.p95 else 10**9,
        variant.unit_cost,
        variant.label,
    )


# --- 입력 --------------------------------------------------------------------


def _load_scores(
    exp_dir: Path, scores_path: Optional[Path]
) -> tuple[dict[str, dict], Optional[str]]:
    path = scores_path or (exp_dir / "scores.json")
    if not path.exists():
        return {}, None
    data = json.loads(path.read_text(encoding="utf-8"))
    return dict(data.get("scores") or {}), data.get("scorer")


def _load_gates(exp_dir: Path, gates_path: Optional[Path]) -> dict[str, dict]:
    """사람만 판정할 수 있는 게이트(가시 워터마크)의 입력.

    {"gpt_image": {"visible_watermark": true, "note": "우하단 로고"}}
    """
    path = gates_path or (exp_dir / "gates.json")
    if not path.exists():
        return {}
    return dict(json.loads(path.read_text(encoding="utf-8")))


# --- 출력 --------------------------------------------------------------------


def _render(
    exp_dir: Path,
    rows: list[dict],
    scores: dict,
    scorer: Optional[str],
    variants: dict[str, Variant],
    ranked: list[Variant],
    gates: dict,
) -> str:
    exp_id = rows[-1].get("exp_id", exp_dir.name)
    ordered = sorted(variants.values(), key=_tiebreak_key)
    out: list[str] = [
        f"# {exp_id} — report",
        "",
        f"생성 {datetime.now(timezone.utc).isoformat(timespec='seconds')} · "
        f"manifest {len(rows)}행 · 채점 {len(scores)}건"
        + (f" (scorer: {scorer})" if scorer else " (scores.json 없음)"),
        "",
        "## 실행 통계",
        "",
        "| 모델 | 시도 | ok | refused | error | timeout | 거부+오류율 | p50 | p95 | Σ비용(추정) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for v in ordered:
        out.append(
            f"| {v.label} | {v.attempts} | {v.ok} | {v.refused} | {v.error} | {v.timeout} "
            f"| {v.fail_rate:.1%} | {v.p50 / 1000:.1f}s | {v.p95 / 1000:.1f}s | ${v.cost:.2f} |"
        )

    out += ["", "## 채점 집계 (셀 통과 = best-of-N, N개 중 ≥1장 전항목 pass)", ""]
    if not scores:
        out += [
            "> scores.json이 없다 — `gallery`로 채점 후 Export하고 "
            "`report --scores <경로>`로 다시 돌려라. 아래 순위·판정은 나오지 않는다.",
            "",
        ]
    else:
        out += [
            "| 모델 | 셀 통과 | 채점 이미지 | " + " | ".join(CHECK_IDS) + " |",
            "|---|---:|---:|" + "---:|" * len(CHECK_IDS),
        ]
        for v in ordered:
            rates = " | ".join(
                "—" if v.rate(c) is None else f"{v.rate(c):.0%}" for c in CHECK_IDS
            )
            out.append(
                f"| {v.label} | {v.cell_pass}/{v.cell_total} | {v.scored} | {rates} |"
            )
        out += ["", "항목: " + " · ".join(f"{cid} {label}" for cid, label, _ in CHECKLIST)]

    out += ["", "## 부적격 게이트 (05 §5 — 순위 무관 승자 불가)", ""]
    for v in ordered:
        if v.disqualified:
            out.append(f"- **{v.label}: 부적격** — {' / '.join(v.disqualified)}")
        else:
            out.append(f"- {v.label}: 통과")
    if not gates:
        out += [
            "",
            "> 가시 워터마크 게이트는 자동 판정이 불가능하다. 관찰됐다면 "
            "`gates.json`에 `{\"<모델>\": {\"visible_watermark\": true}}`를 쓰고 다시 돌려라.",
        ]

    out += ["", "## 순위 제안 (타이브레이크 사슬)", ""]
    if not scores:
        out.append("_채점 데이터가 없어 순위를 내지 않는다._")
    elif not ranked:
        out.append("**모든 모델이 부적격이다.** 05 §5 전멸 시나리오를 검토하라.")
    else:
        for index, v in enumerate(ranked, start=1):
            out.append(f"{index}. **{v.label}** — {_evidence(v)}")
        out += [
            "",
            f"→ 승자 제안: **{ranked[0].label}**"
            + (f" / 백업 제안: **{ranked[1].label}**" if len(ranked) > 1 else " / 백업 없음 — 단일 장애점 리스크를 부록 A에 기록하라"),
        ]

    out += ["", "## Go / No-Go 제안 (05 §5)", ""]
    out.append(_go_no_go(ranked, scores))

    scarf_variants = [v for v in ordered if v.scarf_seen]
    if scarf_variants:
        out += ["", "## 스카프 포함 여부 제안 (공통 문서 미결②)", ""]
        for v in scarf_variants:
            verdict = "포함 가능" if v.scarf_pass else "제외"
            out.append(
                f"- {v.label}: **{verdict}** — c1·c3·c4·c5 동시 pass 이미지 "
                f"{'있음' if v.scarf_pass else '없음'}"
            )
        out.append("")
        out.append("> 디폴트 편향은 **제외**다. 애매하면 제외하고, '제외'도 미결②의 유효한 해소다.")

    out += [
        "",
        "---",
        "",
        "> 이 문서는 **제안**이다. 확정은 갤러리 재확인 후 사람이 한다 (05 §4).",
        "> experiments/는 git 미추적이므로, 이 표를 `05-gen-research-plan.md` 부록 A에 "
        "붙여넣는 것이 유일한 영구 기록이다.",
        "",
    ]
    return "\n".join(out)


def _evidence(v: Variant) -> str:
    parts = [
        f"셀 통과 {v.cell_pass}/{v.cell_total}",
        f"c1+c2 {v.check_sum('c1', 'c2')}",
        f"c4 {v.check_sum('c4')}",
        f"c3+c5 {v.check_sum('c3', 'c5')}",
        f"c6 {v.check_sum('c6')}",
        f"p95 {v.p95 / 1000:.1f}s",
        f"단가 ${v.unit_cost:.3f}",
    ]
    return " · ".join(parts)


def _go_no_go(ranked: list[Variant], scores: dict) -> str:
    if not scores or not ranked:
        return "_채점 데이터가 없거나 적격 모델이 없어 판정을 내지 않는다._"
    top = ranked[0]
    c1_rate = top.rate("c1")
    c1_text = "—" if c1_rate is None else f"{c1_rate:.0%}"
    head = (
        f"승자 후보 **{top.label}** — 셀 통과 {top.cell_pass}/{top.cell_total} "
        f"({top.cell_ratio:.0%}), c1 통과율 {c1_text}, 부적격 게이트 통과."
    )
    if top.cell_ratio >= GO_CELL_RATIO and (c1_rate or 0) >= GO_C1_RATE:
        verdict = "**GO** — R3~R6 전체 진행 (기준: 셀 통과 ≥ 6/9 AND c1 ≥ 70%)."
    elif top.cell_ratio >= CONDITIONAL_CELL_RATIO:
        verdict = (
            "**CONDITIONAL GO** — 제한 GO. 약한 wear_position 제외, 00-common §7.2 "
            "사진 조건 강화, 제한 내용을 데모 시나리오에 반영 (기준: 셀 통과 4~5/9)."
        )
    else:
        verdict = (
            "**NO-GO** — 라이브 생성 격하(`ENGINE=mock` 유지), 사전 생성 중심. "
            "R6는 최선 모델로 계속한다. **B의 계약·플로우는 불변 — B를 끌어들이지 않는다** "
            "(기준: 셀 통과 < 4/9)."
        )
    tail = (
        "\n\n기본값을 조정해 판정하는 경우, **조정 사유를 05 부록 A에 기록**해야 한다."
    )
    return f"{head}\n\n{verdict}{tail}"
