"""Генерує звіти для захисту: покриття (C3) та статичний аналіз (C4) у каталог reports/.

Запуск: python scripts/quality_report.py
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "reports"


def run(cmd, outfile):
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False)
    text = result.stdout + result.stderr
    (OUT / outfile).write_text(text, encoding="utf-8")
    print(f"[{'OK' if result.returncode == 0 else 'FAIL'}] {' '.join(cmd[2:5])} -> reports/{outfile}")
    return result.returncode


def main() -> int:
    OUT.mkdir(exist_ok=True)
    py = sys.executable
    code = run([py, "-m", "pytest", "--cov=airedge", "--cov-report=term-missing",
                "--cov-report=html:reports/htmlcov", "--cov-fail-under=80"], "coverage.txt")
    code |= run([py, "-m", "pylint", "airedge"], "pylint.txt")
    return code


if __name__ == "__main__":
    sys.exit(main())
