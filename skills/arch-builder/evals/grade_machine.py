"""evals.json の type=machine のアサーションを、成果物から機械的に採点する。

    uv run --project <skill>/tools python <skill>/evals/grade_machine.py <workspace>/iteration-N

各 run ディレクトリ (eval-*/<条件>/run-*/) に machine.json を書き、1 行ずつ結果を出す。
design / legibility は採点しない (evals/README.md のとおり、採点者を分ける)。
"""

import json
import sys
from pathlib import Path

from arch_builder import cli
from arch_builder.rules import lint


def grade(run: Path, lib) -> list[dict]:
    out = run / "outputs"
    drawios = [p for p in out.glob("*.drawio") if p.name != "flawed.drawio"]
    has = {"drawio": bool(drawios), "png": any(out.glob("*.png")), "yaml": any(out.glob("*.arch.yaml"))}
    res = [{"text": "編集可能な .drawio と確認用 PNG と .arch.yaml が出力されている", "passed": all(has.values()),
            "evidence": str(has)}]
    if not drawios:
        return res + [{"text": t, "passed": False, "evidence": ".drawio が無い"} for t in ("error 0", "warn 0")]
    findings = lint(cli.load_drawio(drawios[0], lib))
    errors = [f.code for f in findings if f.severity == "error"]
    warns = [f.code for f in findings if f.severity == "warn" and f.code.startswith(("A-", "N-EDGE"))]
    res.append({"text": "arch lint で error が 0 件", "passed": not errors, "evidence": f"error {len(errors)}: {errors[:8]}"})
    res.append({"text": "arch lint で A-* と N-EDGE-* の warn が 0 件 (残すなら報告に理由がある)", "passed": not warns,
                "evidence": f"warn {len(warns)}: {warns}"})
    return res


def main():
    lib = cli.Library(cli.lib_dir())
    for run in sorted(Path(sys.argv[1]).glob("eval-*/*/run-*")):
        res = grade(run, lib)
        (run / "machine.json").write_text(json.dumps(res, ensure_ascii=False, indent=2))
        print(f"{run.relative_to(sys.argv[1])}: {sum(r['passed'] for r in res)}/{len(res)}")


if __name__ == "__main__":
    main()
