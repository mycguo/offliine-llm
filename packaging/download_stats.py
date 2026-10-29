"""Download counts for Survival LLM from GitHub release assets.

Usage: python3 packaging/download_stats.py [--json]

Each run appends a snapshot to ~/.local/share/survival-llm/download-stats.jsonl
and prints the change since the previous one. Needs the `gh` CLI, logged in.

App counts include self-updates: an installed copy that updates downloads the
new zip, so a release's count is new people plus existing users updating.
"""
import json
import os
import subprocess
import sys
from datetime import datetime

REPO = "anonnerdfrenzy/survival-llm"
HISTORY = os.path.expanduser("~/.local/share/survival-llm/download-stats.jsonl")


def releases():
    out = subprocess.run(["gh", "api", f"repos/{REPO}/releases", "--paginate",
                          "--jq", ".[] | {tag: .tag_name, assets: [.assets[] | {name, n: .download_count}]}"],
                         capture_output=True, text=True, check=True).stdout
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def tally(rels):
    s = {"mac": 0, "windows": 0, "by_version": {}, "everything_first": 0, "everything_last": 0, "essentials": 0}
    for r in rels:
        full = sorted((a for a in r["assets"] if a["name"].startswith("library-full.zip.")), key=lambda a: a["name"])
        if full:
            s["everything_first"] += full[0]["n"]
            s["everything_last"] += full[-1]["n"]
        for a in r["assets"]:
            name, n = a["name"], a["n"]
            if name.startswith("library-lite.zip."):
                # Essentials may be split too; count its first part
                if name.endswith(".000"):
                    s["essentials"] += n
            elif name.endswith("-mac.zip") or name.endswith("-windows.zip"):
                key = "mac" if name.endswith("-mac.zip") else "windows"
                s[key] += n
                s["by_version"][r["tag"]] = s["by_version"].get(r["tag"], 0) + n
    s["total"] = s["mac"] + s["windows"]
    return s


def previous():
    try:
        with open(HISTORY) as f:
            lines = [line for line in f if line.strip()]
        return json.loads(lines[-1]) if lines else None
    except OSError:
        return None


def save(s):
    os.makedirs(os.path.dirname(HISTORY), exist_ok=True)
    with open(HISTORY, "a") as f:
        f.write(json.dumps(s) + "\n")


def main():
    s = tally(releases())
    s["at"] = datetime.now().isoformat(timespec="minutes")
    prev = previous()
    save(s)
    if "--json" in sys.argv:
        print(json.dumps(s, indent=2))
        return

    def row(label, key):
        change = f"  (+{s[key] - prev[key]} since {prev['at'].replace('T', ' ')})" if prev and key in prev else ""
        print(f"{label:<26}{s[key]:>7,}{change}")

    row("App downloads", "total")
    row("  Mac", "mac")
    row("  Windows", "windows")
    row("Everything started", "everything_first")
    row("Everything finished", "everything_last")
    row("Essentials", "essentials")
    if s["everything_first"]:
        print(f"Everything finish rate    {s['everything_last'] / s['everything_first']:>7.0%}")
    print("By version: " + ", ".join(f"{t} {n:,}" for t, n in s["by_version"].items()))
    print("App counts include self-updates, so they run ahead of the number of people.")


if __name__ == "__main__":
    main()
