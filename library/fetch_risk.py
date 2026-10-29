"""Gather references for estimating risks of death (micromorts) into library.noindex/raw/risk/.

Usage: .venv/bin/python library/fetch_risk.py
Writes the files plus manifest.json for build_library.py: U.S. government mortality statistics (public domain),
downloaded, and Wikipedia's articles on micromorts and risk, copied from the local Wikipedia ZIM. Builds that
include Wikipedia skip those articles, so only the smaller "lite" library gets its own copies.
Sources checked on 2026-10-24.
"""
import glob
import gzip
import json
import os
import re
import subprocess

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "library.noindex")
OUT = os.path.join(ROOT, "raw", "risk")
US_GOV = "U.S. government work, public domain"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"

DOCUMENTS = [  # (file, title, url)
    ("ssa-life-table.html", "Actuarial Life Table: chance of dying within a year at each age (Social Security, 2023)",
     "https://www.ssa.gov/oact/STATS/table4c6.html"),
    ("cdc-mortality-2024.pdf", "Mortality in the United States, 2024 (CDC NCHS Data Brief 548)",
     "https://www.cdc.gov/nchs/data/databriefs/db548.pdf"),
    ("cdc-leading-causes-2024.pdf", "Deaths: Leading Causes for 2024, death rates by cause and age (CDC NCHS)",
     "https://www.cdc.gov/nchs/data/nvsr/nvsr75/nvsr75-07.pdf"),
    ("nhtsa-crashes-2024.pdf", "Overview of Motor Vehicle Traffic Crashes in 2024 (NHTSA), deaths per mile driven",
     "https://crashstats.nhtsa.dot.gov/Api/Public/ViewPublication/813791"),
    ("nws-lightning-odds.html", "How Dangerous is Lightning? Odds of being struck (National Weather Service)",
     "https://www.weather.gov/safety/lightning-odds"),
]
WIKIPEDIA = ["Micromort", "Fermi problem", "Life table", "Relative risk", "Life expectancy", "Aviation safety",
             "Motor vehicle fatality rate in U.S. by year", "Expected value", "Order of magnitude",
             "Gompertz–Makeham law of mortality", "Risk", "Bayes' theorem", "Confidence interval"]


def fetch(url, dest):
    """Download url to dest. CDC and SSA refuse scripted downloads (HTTP 403, checked 2026-10-24), so fall back to
    the Internet Archive's copy. Undo the archive's gzip and NHTSA's form-data wrapper around its PDFs."""
    for src in (url, "https://web.archive.org/web/2026id_/" + url):
        r = subprocess.run(["curl", "-L", "--fail", "-sS", "-A", UA, "--retry", "3", "-o", dest, src],
                           capture_output=True)
        if r.returncode == 0:
            break
    else:
        return False
    data = open(dest, "rb").read()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    if data.startswith(b"------") and b"%PDF" in data:
        data = data[data.index(b"%PDF"):data.rindex(b"%%EOF") + 5]
    open(dest, "wb").write(data)
    return True


def life_table_text(html):
    """The SSA life table as plain lines, with each yearly chance of dying also given in micromorts
    (one-in-a-million chances of death) per year and per day."""
    rows = re.findall(r"<tr[^>]*>\s*<td[^>]*>\s*(\d+)</td>\s*<td>\s*([\d.]+)</td>\s*<td>[^<]*</td>\s*<td>\s*([\d.]+)</td>"
                      r"\s*<td>\s*([\d.]+)</td>\s*<td>[^<]*</td>\s*<td>\s*([\d.]+)</td>", html)
    lines = ["How likely an American is to die within the next year at each age, from the Social Security period "
             "life table for 2023 (2026 Trustees Report). A micromort is a one-in-a-million chance of death; "
             "micromorts per year = yearly chance x 1,000,000, and per day = per year / 365. Life expectancy is the "
             "average number of years left at that age.", ""]
    for age, pm, em, pf, ef in rows:
        m, f = float(pm), float(pf)
        lines.append(f"Age {age}: men {m:.6f} a year ({m * 1e6:,.0f} micromorts a year, {m * 1e6 / 365:,.1f} a day; "
                     f"life expectancy {em} years); women {f:.6f} a year ({f * 1e6:,.0f} micromorts a year, "
                     f"{f * 1e6 / 365:,.1f} a day; life expectancy {ef} years)")
    return "\n\n".join(lines) if rows else ""


def main():
    os.makedirs(OUT, exist_ok=True)
    manifest = []
    for name, title, url in DOCUMENTS:
        dest = os.path.join(OUT, name)
        if not os.path.exists(dest) and not fetch(url, dest):
            print(f"FAILED  {title}", flush=True)
            continue
        entry = {"file": name, "title": title, "url": url, "license": US_GOV, "kind": "web"}
        if name == "ssa-life-table.html":  # a 120-row table reads far better for a model as lines with micromorts
            text = life_table_text(open(dest, encoding="utf-8", errors="replace").read())
            if not text:
                print(f"FAILED  {title} (table not found)", flush=True)
                continue
            with open(os.path.join(OUT, "ssa-life-table.md"), "w") as f:
                f.write(text)
            entry.update(file="ssa-life-table.md", kind="text")
        manifest.append(entry)
        print(f"ok      {title}", flush=True)

    from libzim.reader import Archive
    zims = sorted(glob.glob(os.path.join(ROOT, "zim", "wikipedia_en_top1m_nopic_*.zim")))
    if not zims:
        print("no Wikipedia ZIM found; skipping the Wikipedia articles")
    else:
        archive = Archive(zims[-1])
        for title in WIKIPEDIA:
            path = title.replace(" ", "_")
            try:
                entry = archive.get_entry_by_path(path)
                while entry.is_redirect:
                    entry = entry.get_redirect_entry()
                html = bytes(entry.get_item().content)
            except KeyError:
                print(f"missing {title}", flush=True)
                continue
            name = "wikipedia-" + "".join(c if c.isalnum() else "-" for c in title).strip("-").lower() + ".html"
            with open(os.path.join(OUT, name), "wb") as f:
                f.write(html)
            manifest.append({"file": name, "title": title, "url": "https://en.wikipedia.org/wiki/" + path,
                             "license": "CC BY-SA 4.0, Wikipedia contributors", "kind": "wiki", "wikipedia": True})
            print(f"ok      Wikipedia: {title}", flush=True)
    with open(os.path.join(OUT, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    print(f"{len(manifest)} documents")


if __name__ == "__main__":
    main()
