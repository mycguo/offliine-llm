"""Run every test in this folder, each in its own process (they stand in for the model in different ways).

Usage: .venv/bin/python tests/run_all.py
No Ollama or library needed: these test memory.py and core.py's handling of what a model writes.
"""
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
failed = []
for path in sorted(glob.glob(os.path.join(HERE, "*_test.py"))):
    result = subprocess.run([sys.executable, path], capture_output=True, text=True)
    lines = (result.stdout + result.stderr).strip().splitlines()
    print(f"{'ok  ' if result.returncode == 0 else 'FAIL'} {os.path.basename(path)}: {lines[-1] if lines else ''}")
    if result.returncode:
        failed.append(path)
        print("\n".join(line for line in lines if not line.startswith("PASS")))
sys.exit(1 if failed else 0)
