"""Download the Kiwix ZIM files the offline library is built from (resumable).

Usage: python3 library/fetch_zims.py [NAME ...]
Files land in library.noindex/zim/. Re-running resumes partial downloads and skips finished ones.
Give Kiwix names (e.g. usda-2015_en) to fetch only those.
"""
import os
import re
import subprocess
import sys
import urllib.request

# (Kiwix name, flavour). Small, high-value sources first so conversion can start early.
SOURCES = [
    ("zimgit-water_en", ""),
    ("zimgit-food-preparation_en", ""),
    ("zimgit-medicine_en", ""),
    ("zimgit-knots_en", ""),
    ("zimgit-post-disaster_en", ""),
    ("irp.fas.org_en_military-medicine", ""),
    ("nhs.uk_en_medicines", ""),
    ("wikem_en_all", "nopic"),
    ("wwwnc.cdc.gov_en_all", ""),
    ("outdoors.stackexchange.com_en_all", ""),
    ("cooking.stackexchange.com_en_all", ""),
    ("lifehacks.stackexchange.com_en_all", ""),
    ("sustainability.stackexchange.com_en_all", ""),
    ("ham.stackexchange.com_en_all", ""),
    ("medicalsciences.stackexchange.com_en_all", ""),
    ("gardening.stackexchange.com_en_all", ""),
    ("diy.stackexchange.com_en_all", ""),
    ("appropedia_en_all", "maxi"),
    ("wikipedia_en_medicine", "nopic"),
    ("medlineplus.gov_en_all", ""),
    ("ready.gov_en_all", ""),
    ("ifixit_en_all", ""),
    ("usda-2015_en", ""),
    ("wikiciv_en_all", "maxi"),
    ("gardenology.org_en_all", ""),
    ("mechanics.stackexchange.com_en_all", ""),
    ("pets.stackexchange.com_en_all", ""),
    ("energypedia_en_all", "nopic"),
    ("wikipedia_en_top1m", "nopic"),
]

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "library.noindex", "zim")
os.makedirs(ROOT, exist_ok=True)

catalog = urllib.request.urlopen(
    "https://library.kiwix.org/catalog/v2/entries?lang=eng&count=5000", timeout=120).read().decode()
urls = {}
for e in re.findall(r"<entry>(.*?)</entry>", catalog, re.S):
    name = re.search(r"<name>(.*?)</name>", e)
    flavour = re.search(r"<flavour>(.*?)</flavour>", e)
    link = re.search(r'rel="http://opds-spec.org/acquisition/open-access"[^>]*href="([^"]+)"', e) or \
        re.search(r'href="([^"]+\.zim\.meta4)"[^>]*rel="http://opds-spec.org/acquisition/open-access"', e)
    if name and link:
        urls[(name.group(1), flavour.group(1) if flavour else "")] = link.group(1).removesuffix(".meta4")

MIRROR = "https://ftpmirror.your.org/pub/kiwix"  # measured 11.5 MB/s vs ~1 MB/s from Wikimedia (Oct 2026)


def sha256_ok(url, path):
    """Check a download against the .sha256 file Kiwix publishes next to each ZIM."""
    import hashlib
    try:
        expected = urllib.request.urlopen(url + ".sha256", timeout=60).read().decode().split()[0]
    except OSError:
        return True  # no checksum published; trust curl's --fail
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 24):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        print(f"CHECKSUM MISMATCH, deleting {os.path.basename(path)}", flush=True)
        os.remove(path)
        return False
    return True


wanted = set(sys.argv[1:])
for key in SOURCES:
    if wanted and key[0] not in wanted:
        continue
    url = urls.get(key)
    if not url:
        print(f"NOT IN CATALOG: {key}", flush=True)
        continue
    dest = os.path.join(ROOT, url.rsplit("/", 1)[1])
    if os.path.exists(dest + ".done"):
        continue
    print(f"downloading {os.path.basename(dest)}", flush=True)
    # One connection per file: Kiwix mirrors answer parallel range requests with HTTP 429. download.kiwix.org
    # often redirects to dumps.wikimedia.org (about 1 MB/s here); try a fast mirror first, then fall back.
    ok = False
    for candidate in (re.sub(r"^https://([a-z]+\.)?download\.kiwix\.org", MIRROR, url), url):
        if subprocess.run(["curl", "-L", "--fail", "-sS", "-C", "-", "--retry", "5", "-o", dest, candidate]).returncode == 0:
            ok = sha256_ok(url, dest)
            break
    if ok:
        open(dest + ".done", "w").close()
    else:
        print(f"FAILED: {url} (re-run to resume)", flush=True)
print("all downloads finished", flush=True)
