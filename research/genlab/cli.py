"""genlab CLI — 06-gen-research-tools-spec.md §10.

서브커맨드 3개: run | gallery | report.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import gallery, report, runner


def _force_utf8_output() -> None:
    """Windows에서 출력이 파이프·리다이렉트로 나가면 파이썬이 로케일 인코딩(cp949)을
    쓴다 — 한국어 로그와 em-dash가 깨진다. summary.md는 항상 utf-8로 쓰지만
    콘솔 출력도 맞춰 준다 (06 §1 N1)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def _load_env() -> None:
    """시크릿은 env만 (06 §1 N6). python-dotenv는 **연구 한정** 편의이며
    없으면 조용히 넘어간다 — 승격 대상 파일은 dotenv를 쓰지 않는다."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(runner.RESEARCH_ROOT / ".env")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m genlab",
        description="생성 엔진 연구 도구 (06-gen-research-tools-spec.md)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="실험 매트릭스 실행")
    run.add_argument("config", type=Path, help="configs/r2_bakeoff.yaml 등")
    run.add_argument("--dry-run", action="store_true", help="견적만 출력하고 생성하지 않는다")
    run.add_argument("--resume", action="store_true", help="완료된 셀은 건너뛴다")
    run.add_argument(
        "--restart",
        action="store_true",
        help="기존 manifest를 무시하고 전량 재생성 (과금 주의)",
    )
    run.add_argument(
        "--fake",
        action="store_true",
        help="모든 어댑터를 fake로 대체해 $0으로 파이프라인 검증 "
        f"(결과는 experiments/{{exp_id}}{runner.FAKE_SUFFIX}/에 격리 저장)",
    )
    run.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="부분 실행 필터. model/selfie/product/pv/rep, 반복 지정 가능",
    )

    gal = sub.add_parser("gallery", help="채점용 정적 HTML 생성")
    gal.add_argument("exp_dir", type=Path, help="experiments/r2_bakeoff 등")
    gal.add_argument("--open", action="store_true", help="생성 후 브라우저로 연다")

    rep = sub.add_parser("report", help="집계·순위 제안 + summary.md")
    rep.add_argument("exp_dir", type=Path)
    rep.add_argument("--scores", type=Path, default=None, help="기본값: <exp_dir>/scores.json")
    rep.add_argument("--gates", type=Path, default=None, help="기본값: <exp_dir>/gates.json")
    rep.add_argument("--out", type=Path, default=None, help="기본값: <exp_dir>/summary.md")
    return parser


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
    args = build_parser().parse_args(argv)
    _load_env()
    try:
        if args.command == "run":
            runner.run(
                args.config,
                runner.RunOptions(
                    dry_run=args.dry_run,
                    resume=args.resume,
                    restart=args.restart,
                    fake=args.fake,
                    only=runner.parse_only(args.only),
                ),
            )
        elif args.command == "gallery":
            gallery.build(args.exp_dir, open_browser=args.open)
        elif args.command == "report":
            report.build(
                args.exp_dir,
                scores_path=args.scores,
                gates_path=args.gates,
                out_path=args.out,
            )
    except (runner.ConfigError, runner.BudgetExceeded) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        # manifest는 레코드마다 flush되므로 여기서 잃는 것은 없다. --resume으로 이어간다.
        print("\ninterrupted — --resume으로 이어서 실행할 수 있다.", file=sys.stderr)
        return 130
    return 0
