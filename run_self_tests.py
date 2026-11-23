# -*- coding: utf-8 -*-
"""One-click self-test runner for SubFlow Studio.

Clearly separates:
  - Static checks (verify_release.py, py_compile, node --check)
  - Offline unit / frontend logic tests (unittest, test_frontend_logic.js)
  - Optional live Windows runtime tests (--live: preflight, app.py --selftest)
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run_step(title: str, cmd: list[str], env: dict[str, str]) -> bool:
    print(f"\n===== {title} =====", flush=True)
    print("$ " + subprocess.list2cmdline(cmd), flush=True)
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env, shell=False)
    ok = proc.returncode == 0
    print(f"[{'PASS' if ok else 'FAIL'}] {title} (rc={proc.returncode})", flush=True)
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SubFlow Studio self-test runner")
    parser.add_argument("--live", action="store_true", help="Also run Windows runtime preflight and app.py --selftest")
    parser.add_argument("--no-pause", action="store_true", help="Ignored; kept for BAT compatibility")
    args = parser.parse_args(argv)

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    py = sys.executable
    venv_py = ROOT / ".build_venv" / "Scripts" / "python.exe"
    if venv_py.is_file() and Path(py).resolve() != venv_py.resolve():
        py = str(venv_py)

    results: list[tuple[str, bool]] = []
    results.append(("Static: verify_release.py", run_step("Static: verify_release.py", [py, "verify_release.py"], env)))
    results.append((
        "Static: py_compile",
        run_step(
            "Static: py_compile",
            [
                py, "-m", "py_compile",
                "app.py", "core_pipeline.py", "runtime_preflight.py",
                "verify_release.py", "prepare_build.py", "diagnose_startup.py",
                "build_release.py", "run_self_tests.py",
            ],
            env,
        ),
    ))
    results.append((
        "Unit: Python unittest",
        run_step("Unit: Python unittest", [py, "-m", "unittest", "discover", "-s", "tests", "-v"], env),
    ))

    node = shutil.which("node")
    if node:
        for js in ("app.js", "bridge.js", "data.js"):
            results.append((f"Static: node --check {js}", run_step(f"Static: node --check {js}", [node, "--check", js], env)))
        results.append((
            "Unit: Frontend JS logic",
            run_step("Unit: Frontend JS logic", [node, "tests/test_frontend_logic.js"], env),
        ))
    else:
        print("\n[WARN] Node.js not found; JavaScript checks skipped.", flush=True)

    if args.live:
        results.append(("Live: runtime_preflight.py", run_step("Live: runtime_preflight.py", [py, "runtime_preflight.py"], env)))
        results.append(("Live: app.py --selftest", run_step("Live: app.py --selftest", [py, "app.py", "--selftest"], env)))

    print("\n================ TEST SUMMARY ================", flush=True)
    failed = 0
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}", flush=True)
        if not ok:
            failed += 1
    if not args.live:
        print("  [INFO] Live Windows GUI/GPU/API tests were NOT run (pass --live to include runtime_preflight & --selftest).", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
