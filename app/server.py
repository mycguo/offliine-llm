"""Survival LLM: a local web app for chatting with a local model that searches the offline library.

Run: python app/server.py   (opens your browser; listens on 127.0.0.1 only; works with Wi-Fi off)
Runs models with Ollama (https://ollama.com): the app's own copy (inside the Mac app, or downloaded on first use
on Windows), or one the person installed. Everything else is the Python standard library.
"""
import atexit
import base64
import bisect
import ctypes
import datetime
import errno
import filecmp
import hashlib
import io
import json
import os
import platform
import re
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
import weakref
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core import HOST, capabilities, chat_events, installed_models, system_prompt, trim  # noqa: E402
from library import myfiles  # noqa: E402
from memory import MAX_CHARS as MEMORY_MAX, Memory, parse as parse_notes, prompt_section, size as notes_size  # noqa: E402
from library.search import DB_PATH, LIB, TOPICS, Library  # noqa: E402

# Check web certificates the way the computer itself does, with the Mac's Keychain or the Windows certificate store.
# The bundled Python only knows a fixed list, so behind a company's or an antivirus's own certificate the library,
# engine and update downloads failed with CERTIFICATE_VERIFY_FAILED while models (Ollama) downloaded fine.
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:  # a copy run from source without it
    pass

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
TOKEN = secrets.token_urlsafe(16)
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png"}

# Models the app offers. mac/win are Ollama names; min_ram is total memory in GB; gb is the download size.
# Picks and blurbs come from evals/retrieval_eval.py (22 blackout and first-aid questions answered with the library).
CATALOG = [
    {"key": "nemotron-super", "name": "Nemotron 3.5 Super", "maker": "NVIDIA", "min_ram": 128,
     "mac": "nemotron-3.5-super:120b-a12b-nvfp4", "win": "nemotron-3.5-super:120b-a12b", "gb": {"mac": 80, "win": 90},
     "blurb": {"mac": "The smartest option: knows the most and is the least likely to make things up. Quick on Apple Silicon.",
               "win": "The smartest option: knows the most and is the least likely to make things up. Needs a powerful PC."}},
    {"key": "qwen-27b", "name": "Qwen3.8 27B", "maker": "Alibaba", "min_ram": 32,
     "mac": "qwen3.8:27b-mlx", "win": "qwen3.8:27b", "gb": {"mac": 18, "win": 18},
     "blurb": {"mac": "Careful and thorough: looks things up for every question. A great fit for 32-64 GB Macs.",
               "win": "Careful and thorough, but slow unless your PC has a graphics card with 24 GB of memory."}},
    {"key": "nemotron-lightning", "name": "Nemotron 3.5 Lightning", "maker": "NVIDIA", "min_ram": 32,
     "mac": "nemotron-3.5-lightning:30b-a3b-nvfp4", "win": "nemotron-3.5-lightning:30b-a3b", "gb": {"mac": 19, "win": 25},
     "blurb": {"mac": "The fastest: answers in a few seconds and saves battery. Sometimes misses details.",
               "win": "The best choice for most PCs: accurate and careful. Expect up to a minute per answer "
                      "on a typical PC."}},
    {"key": "granite-8b", "name": "Granite 4.2 8B", "maker": "IBM", "min_ram": 12,
     "mac": "granite4.2:8b", "win": "granite4.2:8b", "gb": {"mac": 6, "win": 6},
     "blurb": {"mac": "For 16 GB computers. Always looks things up and shows its sources, but makes more mistakes "
                      "than the bigger models, so check the source.",
               "win": "For 16 GB computers. Always looks things up and shows its sources, but makes more mistakes "
                      "than the bigger models, so check the source."}},
    {"key": "gemma-e4b", "name": "Gemma 4 E4B", "maker": "Google", "min_ram": 12,
     "mac": "gemma4:latest", "win": "gemma4:latest", "gb": {"mac": 10, "win": 10},
     "blurb": {"mac": "Small and very fast, for 16 GB computers. Often answers from memory instead of the library, "
                      "so check what it says.",
               "win": "Small and very fast, for 16 GB computers. Often answers from memory instead of the library, "
                      "so check what it says."}},
]
# Order = what to recommend first, from the blind-judged tests in evals/ (README "How the models did").
# Granite 4.2 3B was dropped: 5 of its 22 test answers could hurt someone, 2 of them fatally.
# Nemotron Super needs 128 GB: macOS lets the GPU use about three quarters of memory, and the model is 80 GB.
# Windows doesn't get it: its 90 GB build would run mostly on the processor, far too slowly.
PREFERENCE = {"mac": ["nemotron-super", "qwen-27b", "nemotron-lightning", "granite-8b", "gemma-e4b"],
              "win": ["nemotron-lightning", "qwen-27b", "granite-8b", "gemma-e4b"]}
ALIASES = {"granite4.2": "granite-8b"}  # granite4.2:latest is the same 8B model
NOT_CHAT = ("embed", "bge-", "nomic", "minilm")
DISK_MARGIN_GB = 5  # keep this much free after a download

# The library packs on offer are listed in a small JSON file published with each library release
# (packaging/make_packs.py writes it): {"packs": [{"id", "label", "gb", "parts": [{"url", "bytes", "sha256"}]}]}.
# Packs are zips of index.sqlite + text/, split into parts because GitHub caps release files at 2 GB.
# It lives on the project's web page (not "latest release"), so app releases and library releases stay independent.
MANIFEST_URL = os.environ.get("SURVIVAL_LLM_MANIFEST") or \
    "https://anonnerdfrenzy.github.io/survival-llm/library-packs.json"

if os.environ.get("SURVIVAL_LLM_APP_DIR"):  # another settings folder, such as the dev copy's (bin/dev)
    APP_DIR = os.environ["SURVIVAL_LLM_APP_DIR"]
elif sys.platform == "darwin":
    APP_DIR = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Survival LLM")
elif os.name == "nt":
    APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Survival LLM")
else:
    APP_DIR = os.path.join(os.path.expanduser("~"), ".survival-llm")
STATE = os.path.join(APP_DIR, "server.json")
CUSTOM_PROMPT = os.path.join(APP_DIR, "instructions.txt")  # the person's own edit of the AI's instructions
# The person's own files: a folder they can see, beside the library folder rather than in it (installing a library
# pack replaces that folder), and its index, kept with the settings.
MY_DIR = os.environ.get("SURVIVAL_LLM_FILES") or os.path.join(os.path.expanduser("~"), "Survival LLM Files")
MY_DB = os.path.join(APP_DIR, "my-files.sqlite")
# The AI's notes about the person (memory.py), a plain text file they edit in the app, and the app's settings.
MEMORY = Memory(os.path.join(APP_DIR, "survival-llm-memory.txt"), os.path.join(APP_DIR, "settings.json"))
def save_failed(exc, path):
    """What to tell the person when the memory or settings file couldn't be written (it goes to app.log too)."""
    print(f"Couldn't save {path}: {exc}", file=sys.stderr)
    if getattr(exc, "errno", None) == errno.ENOSPC:
        return "Couldn't save: the disk is full. Free some space and try again."
    if os.name == "nt" and isinstance(exc, PermissionError):  # Windows refuses while another program has it open
        return f"Couldn't save: another program is using {path}. Close it and try again."
    return f"Couldn't save {path}: {getattr(exc, 'strerror', None) or exc}."
MAX_UPLOAD = 4 * 1024 ** 3
my_files_checked = [0.0]  # when the folder was last looked at

# Updates. Each release publishes update.json on the project's web page (packaging/make_update.py writes it):
# {"version", "date", "notes": [...], "mac": {"url", "bytes", "sha256"}, "windows": {...}}. The app checks it once a
# day while running (a plain request for a small file, nothing about the person) and when they press Check.
UPDATES_URL = os.environ.get("SURVIVAL_LLM_UPDATES") or "https://anonnerdfrenzy.github.io/survival-llm/update.json"
DOWNLOAD_PAGE = "https://www.brangus.io/survival-llm"
FIRST_PACKS = "2026-10-24"  # the library packs' version from before packs said which version they are
UPDATE_FAILED = os.path.join(APP_DIR, "update-failed.txt")  # left by the Windows updater when it had to roll back
updates = {"data": None, "checked": 0.0, "error": None}

# The app's own Ollama. The Mac app carries it (Resources/ollama, next to the app code). On Windows it is 1.6 GB
# with its graphics-card libraries, so it is downloaded on first use instead; build.sh writes engine.json with its
# address and checksum. An Ollama that is already running is used as is.
if sys.platform == "darwin":
    ENGINE = os.path.join(os.path.dirname(ROOT), "ollama", "ollama")
elif os.name == "nt":
    ENGINE = os.path.join(os.environ.get("LOCALAPPDATA") or APP_DIR, "Survival LLM", "ollama", "ollama.exe")
else:
    ENGINE = None
ENGINE_INFO = os.path.join(ROOT, "engine.json")


def _read_version():
    """This copy's version: version.txt (written by packaging/build.sh), or packaging/VERSION when run from source."""
    for path in (os.path.join(ROOT, "version.txt"), os.path.join(ROOT, "packaging", "VERSION")):
        try:
            with open(path) as f:
                return f.read().strip()
        except OSError:
            pass
    return "0"


VERSION = _read_version()


def _dev_label(value):
    """A copy for trying changes before they ship (bin/dev) gets SURVIVAL_LLM_DEV: what it runs, or "1" to read
    that from git ("reasoning-view @ 13806f4, with uncommitted changes"). It says so on screen and never updates."""
    if value != "1":
        return value or None
    def git(*args):
        return subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True, timeout=10).stdout.strip()
    try:
        branch, commit = git("rev-parse", "--abbrev-ref", "HEAD"), git("rev-parse", "--short", "HEAD")
        changed = git("status", "--porcelain", "--untracked-files=no")
    except (OSError, subprocess.SubprocessError):
        return "dev copy"
    return f"{branch} @ {commit}" + (", with uncommitted changes" if changed else "") if commit else "dev copy"


DEV = _dev_label(os.environ.get("SURVIVAL_LLM_DEV"))
engine_procs = []   # Ollama processes this app started, stopped when it quits

conversations = {}
caps_cache = {}
local = threading.local()
lib_version = [0]   # bumped when a new library is installed, so threads reopen it
lib_lock = threading.Lock()  # held while a new library is swapped in, so no thread opens the old one meanwhile
open_libs = weakref.WeakSet()  # every open library, so a swap can close them (Windows won't move open files)
downloads = {}      # key -> {key, label, status, completed, total, error}; runs in background threads
dl_lock = threading.Lock()
warming = {}        # model -> "loading" | "error"
manifest = {"packs": None, "failed_at": 0.0, "error": None}
ollama_started_at = [0.0]
quit_hooks = []     # run when someone presses Quit (closes the app window)
quit_event = threading.Event()
httpd = None        # the web server, once it is listening


# ---------- helpers ----------

def running_instance():
    """URL of an Survival LLM server this user already has running, if any."""
    try:
        with open(STATE) as f:
            port = json.load(f)["port"]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/ping", timeout=2) as resp:
            if json.load(resp).get("app") == "survival-llm":
                return f"http://127.0.0.1:{port}/"
    except (OSError, ValueError, KeyError):
        return None


def library():
    """The library plus the person's own files (None when there are neither). One set of SQLite connections per
    server thread, reopened after a new library is installed or their files change."""
    if getattr(local, "version", None) != lib_version[0] or getattr(local, "lib", None) is None:
        with lib_lock:
            local.version = lib_version[0]
            main = Library() if os.path.exists(DB_PATH) else None
            if main:
                open_libs.add(main)
            mine = myfiles.MyFiles.open(MY_DB)
            local.lib = myfiles.Combined(main, mine) if (main or mine) else None
    return local.lib


def sync_my_files():
    """Bring the index of their files up to date with the folder; if it changed, open chats see it from the next
    question (the instructions list what the AI can search)."""
    if not myfiles.sync(MY_DIR, MY_DB):
        return
    lib_version[0] += 1
    refresh_prompts()


def refresh_prompts():
    """Rebuild the instructions of open chats after the person changes them, their memory or their files; they apply
    from the next question. (Notes the AI saves itself don't do this: it knows them already, and changing the start
    of a chat makes the model read the whole chat again.)"""
    for conv in list(conversations.values()):
        if conv.get("messages") and conv.get("model"):
            conv["messages"][0]["content"] = prompt_for(model_caps(conv["model"]))


def check_my_files():
    my_files_checked[0] = time.monotonic()
    threading.Thread(target=sync_my_files, daemon=True).start()


def safe_name(name):
    """A file name that is safe to save on Mac and Windows: no folders, no reserved characters or names."""
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", name.replace("\\", "/").rsplit("/", 1)[-1]).strip(" .")
    stem, ext = os.path.splitext(name)
    ext = ext[:12]
    if re.fullmatch(r"(?i)(con|prn|aux|nul|com\d|lpt\d)", stem.split(".")[0].strip(" .")):  # "con.notes.txt" too
        stem = "_" + stem
    while len((stem + ext).encode("utf-8")) > 200:  # disks allow 255 bytes; leave room for " (2)"
        stem = stem[:-1]
    return stem.rstrip(" .") + ext if stem.strip(" .") else ""


def my_file_path(name):
    """The full path of one of their files, from its name in the list; None if it would point outside the folder."""
    path = os.path.realpath(os.path.join(MY_DIR, *str(name).split("/")))
    folder = os.path.realpath(MY_DIR)
    return path if path.startswith(folder + os.sep) and os.path.isfile(path) else None


def my_name(path):
    """A file's name in the list: its path inside their folder."""
    return os.path.relpath(os.path.realpath(path), os.path.realpath(MY_DIR)).replace(os.sep, "/")


def to_trash(path):
    """Move a file to the Trash (Mac) or Recycle Bin (Windows), so removing one of their files can be undone."""
    if sys.platform == "darwin":
        try:
            from Foundation import NSURL, NSFileManager  # part of pywebview's macOS dependencies
        except ImportError:  # run from source without pywebview: ask Finder (macOS asks permission the first time)
            subprocess.run(["osascript", "-e", "on run argv", "-e",
                            'tell application "Finder" to delete (POSIX file (item 1 of argv) as alias)', "-e", "end run",
                            path], check=True, capture_output=True, timeout=60)
            return
        ok, _, err = NSFileManager.defaultManager().trashItemAtURL_resultingItemURL_error_(
            NSURL.fileURLWithPath_(path), None, None)
        if not ok:
            raise OSError(str(err))
    elif os.name == "nt":
        script = ("Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("
                  "$env:SURVIVAL_LLM_TRASH, 'AllDialogs', 'SendToRecycleBin')")  # asks before deleting for good
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], check=True, timeout=60,
                       env={**os.environ, "SURVIVAL_LLM_TRASH": path}, creationflags=0x08000000)  # no console window
    else:
        os.remove(path)


def open_folder(path):
    os.makedirs(path, exist_ok=True)
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
    elif os.name == "nt":
        os.startfile(path)
    else:
        subprocess.Popen(["xdg-open", path])


def ram_gb():
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return round(status.ullTotalPhys / 2**30)
    return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30)


def disk_free_bytes(path=None):
    """Free space on the disk holding path (default: the home folder), as Finder and System Settings count it on a
    Mac: that includes space macOS frees when something needs it (caches, local snapshots, iCloud copies of files),
    which can make it tens of GB more than the plain figure."""
    path = os.path.abspath(path or os.path.expanduser("~"))
    while not os.path.exists(path):  # a folder that isn't made yet: its disk is its parent's
        path = os.path.dirname(path)
    free = shutil.disk_usage(path).free
    if sys.platform == "darwin":
        try:
            import objc
            from Foundation import NSURL, NSURLVolumeAvailableCapacityForImportantUsageKey  # pywebview depends on these
            with objc.autorelease_pool():
                ok, value, _ = NSURL.fileURLWithPath_(path).getResourceValue_forKey_error_(
                    None, NSURLVolumeAvailableCapacityForImportantUsageKey, None)
            if ok and value is not None:
                free = max(free, int(value))
        except Exception:  # run from source without pywebview: the plain figure
            pass
    return free


def disk_free_gb(path=None):
    return round(disk_free_bytes(path) / 1e9)


def system_key():
    return "mac" if sys.platform == "darwin" else "win"


def catalog_entry(model):
    """The catalog entry for an Ollama model name (or one of the author's aliases), if any."""
    model = model.removesuffix(":latest")
    if model in ALIASES:
        return next(c for c in CATALOG if c["key"] == ALIASES[model])
    return next((c for c in CATALOG if model in (c["mac"].removesuffix(":latest"), c["win"].removesuffix(":latest"))), None)


def machine_catalog(installed):
    """Catalog entries for this computer, marked with what fits its memory and disk, and what is installed."""
    ram, disk, sys_key = ram_gb(), disk_free_gb(), system_key()
    rows = []
    for key in PREFERENCE[sys_key]:
        c = next(c for c in CATALOG if c["key"] == key)
        local_names = [m for m in installed if catalog_entry(m) and catalog_entry(m)["key"] == key]
        gb = c["gb"][sys_key]
        rows.append({"key": key, "name": c["name"], "maker": c["maker"], "blurb": c["blurb"][sys_key],
                     "model": local_names[0] if local_names else c[sys_key], "gb": gb, "min_ram": c["min_ram"],
                     "fits": c["min_ram"] <= ram, "fits_disk": bool(local_names) or gb + DISK_MARGIN_GB <= disk,
                     "installed": bool(local_names), "recommended": False})
    best = next((r for r in rows if r["fits"] and r["fits_disk"]), None)
    if best:
        best["recommended"] = True
    return rows


def friendly_name(model):
    entry = catalog_entry(model) if model else None
    return entry["name"] if entry else model


def model_caps(model):
    if not caps_cache.get(model):  # never cache an empty answer from a stopped Ollama
        caps_cache[model] = capabilities(model)
    return caps_cache[model]


def library_packs():
    """Packs from the published manifest; empty when offline or unpublished (checked again after a minute)."""
    if manifest["packs"] is None and time.time() - manifest["failed_at"] > 60:
        try:
            packs = fetch_json(MANIFEST_URL).get("packs", [])
            if not valid_packs(packs):
                raise ValueError("library-packs.json")
            manifest.update(packs=packs, error=None)
        except (urllib.error.HTTPError, ValueError, AttributeError):  # the site answered, but without a usable list
            manifest.update(failed_at=time.time(), error="unavailable")
        except OSError:
            manifest.update(failed_at=time.time(), error="offline")
    return manifest["packs"] or []


# ---------- Ollama: find it, start it, explain its errors ----------

def ollama_locations():
    """(app to open, command-line binary) for the app's own Ollama or an installed one, either may be None."""
    if sys.platform == "darwin":
        apps = ["/Applications/Ollama.app", os.path.expanduser("~/Applications/Ollama.app")]
        bins = [ENGINE, "/opt/homebrew/bin/ollama", "/usr/local/bin/ollama", shutil.which("ollama")]
    elif os.name == "nt":
        base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama")
        apps = [os.path.join(base, "ollama app.exe")]
        bins = [ENGINE, os.path.join(base, "ollama.exe"), shutil.which("ollama")]
    else:
        apps, bins = [], [shutil.which("ollama")]
    app = next((a for a in apps if a and os.path.exists(a)), None)
    binary = next((b for b in bins if b and os.path.exists(b)), None)
    return app, binary


def ollama_starting():
    return time.time() - ollama_started_at[0] < 60


def start_ollama():
    """Start the app's own Ollama, or an installed one, in the background (at most every 30 seconds)."""
    if time.time() - ollama_started_at[0] < 30:
        return
    app, binary = ollama_locations()
    if not (app or binary):
        return
    ollama_started_at[0] = time.time()
    quiet = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    hidden = {"creationflags": 0x08000000} if os.name == "nt" else {}  # CREATE_NO_WINDOW
    try:
        if binary == ENGINE:  # ours runs only while the app does
            os.makedirs(APP_DIR, exist_ok=True)
            with open(os.path.join(APP_DIR, "ollama.log"), "wb") as log:
                engine_procs.append(subprocess.Popen([ENGINE, "serve"], stdout=log, stderr=log, **hidden))
            if os.name != "nt":  # Cmd+Q ends the Mac app without its cleanup: stop the engine once the app is gone
                watch = ('while kill -0 "$1" 2>/dev/null && kill -0 "$2" 2>/dev/null; do sleep 2; done; '
                         'kill -0 "$1" 2>/dev/null || kill "$2" 2>/dev/null')  # from issue #7, by wigginthethird
                subprocess.Popen(["/bin/sh", "-c", watch, "survival-llm-engine-watch", str(os.getpid()),
                                  str(engine_procs[-1].pid)], **quiet, start_new_session=True)
        elif sys.platform == "darwin" and app:
            subprocess.Popen(["open", "-g", "-a", app], **quiet)
        elif os.name == "nt" and app:
            subprocess.Popen([app], **quiet, **hidden)
        else:
            subprocess.Popen([binary, "serve"], **quiet, **(hidden or {"start_new_session": True}))
    except OSError:
        pass


def stop_engine():
    """Stop the Ollama this app started, so it doesn't keep running (and holding memory) after the app quits."""
    for proc in engine_procs:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()


def engine_info():
    """{"url", "bytes", "sha256", "version"} for downloading the app's own Ollama, when this build offers one."""
    try:
        with open(ENGINE_INFO) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def loaded_models():
    """Models Ollama currently has in memory (ready to answer without loading)."""
    try:
        with urllib.request.urlopen(HOST + "/api/ps", timeout=3) as resp:
            return [m["name"].removesuffix(":latest") for m in json.load(resp).get("models", [])]
    except (OSError, ValueError):
        return []


def friendly_error(exc):
    """Plain-language explanation of an Ollama or network failure."""
    if isinstance(exc, urllib.error.HTTPError):
        try:
            detail = json.loads(exc.read().decode("utf-8", "replace")).get("error", "")
        except (ValueError, OSError):
            detail = ""
        low = detail.lower()
        if exc.code == 404 or "not found" in low:
            return "This model isn't installed. Open Model at the top to download it or pick another."
        if "newer version" in low or "requires" in low and "ollama" in low:
            return "This model needs a newer AI engine. Update Ollama (ollama.com/download), then try again."
        if "memory" in low:
            return "There isn't enough free memory for this model. Close other apps, or pick a smaller model under Model."
        return f"The AI engine reported a problem: {detail or exc.reason}."
    if getattr(exc, "errno", None) == errno.ENOSPC:
        return "The disk is full. Free up some space, then try again."
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, ConnectionRefusedError) or "refused" in str(reason).lower():
        start_ollama()
        return "The AI engine (Ollama) isn't running. Starting it now; try again in a few seconds."
    if "timed out" in str(reason).lower():
        return "The AI engine took too long to answer. Try again, or pick a faster model under Model."
    return f"Something went wrong: {exc}"


# ---------- background jobs: model downloads, library installs, warm-up ----------

def _update(key, **fields):
    with dl_lock:
        downloads[key].update(fields)


def start_job(key, label, worker, *args):
    """Run a download in the background so it survives page reloads. False if it is already running."""
    with dl_lock:
        current = downloads.get(key)
        if current and current["status"] in ("starting", "downloading", "unpacking"):
            return False
        downloads[key] = {"key": key, "label": label, "status": "starting", "completed": 0, "total": 0, "error": None}

    def run():
        try:
            worker(key, *args)
            _update(key, status="done")
        except Exception as exc:  # shown to the user until they retry
            _update(key, status="error", error=friendly_error(exc) if isinstance(exc, OSError) else str(exc))
    threading.Thread(target=run, daemon=True).start()
    return True


def pull_worker(key, model):
    req = urllib.request.Request(HOST + "/api/pull", data=json.dumps({"model": model}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        for line in resp:
            event = json.loads(line)
            if event.get("error"):
                raise RuntimeError(f"Download failed: {event['error']}")
            if event.get("total"):
                _update(key, status="downloading", completed=event.get("completed", 0), total=event["total"])
    caps_cache.pop(model, None)


def _download(key, url, path, size, done_before=0, total=None):
    """Fetch url into path, resuming a partial file; reports progress to job key."""
    have = os.path.getsize(path) if os.path.exists(path) else 0
    if have < size:
        req = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
        with urllib.request.urlopen(req, timeout=120) as resp:
            if have and resp.status != 206:  # server ignored the range: start over
                have = 0
            with open(path, "ab" if have else "wb") as f:
                while chunk := resp.read(1 << 20):
                    f.write(chunk)
                    have += len(chunk)
                    _update(key, status="downloading", completed=done_before + have, total=total or size)


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 24):
            digest.update(chunk)
    return digest.hexdigest()


def engine_worker(key):
    """Download and unpack the app's own Ollama (Windows), then start it."""
    info = engine_info()
    if not info or not ENGINE:
        raise RuntimeError("This version of the app can't download the AI engine. Install Ollama from ollama.com.")
    need = info["bytes"] * 2.5 / 1e9 + DISK_MARGIN_GB  # the zip plus its unpacked copy, briefly at the same time
    if need > disk_free_gb():
        raise RuntimeError(f"Not enough free disk space: installing the AI engine needs about {need:.0f} GB free, "
                           f"and this computer has {disk_free_gb()} GB.")
    folder = os.path.dirname(ENGINE)
    zip_path, partial = folder + ".zip", folder + ".partial"
    os.makedirs(os.path.dirname(folder), exist_ok=True)
    _download(key, info["url"], zip_path, info["bytes"])
    if _sha256(zip_path) != info["sha256"]:
        os.remove(zip_path)
        raise RuntimeError("The AI engine download was damaged in transit. Try again.")
    _update(key, status="unpacking", completed=info["bytes"], total=info["bytes"])
    shutil.rmtree(partial, ignore_errors=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(partial)
    shutil.rmtree(folder, ignore_errors=True)
    os.replace(partial, folder)
    os.remove(zip_path)
    ollama_started_at[0] = 0
    start_ollama()


class PackParts(io.RawIOBase):
    """A pack's downloaded parts, read as the one zip they were cut from, so it unpacks without a joined copy."""

    def __init__(self, paths):
        self.paths = paths
        self.sizes = [os.path.getsize(p) for p in paths]
        self.starts = [sum(self.sizes[:n]) for n in range(len(paths))]
        self.size, self.pos, self.part, self.file = sum(self.sizes), 0, None, None

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=io.SEEK_SET):
        self.pos = offset + (0, self.pos, self.size)[whence]
        return self.pos

    def readinto(self, buffer):
        """Fill the whole buffer, across parts if need be (zipfile takes a short read for a damaged file)."""
        view, done = memoryview(buffer).cast("B"), 0
        while done < len(view) and self.pos < self.size:
            n = bisect.bisect_right(self.starts, self.pos) - 1
            if n != self.part:
                self.close_part()
                self.file, self.part = open(self.paths[n], "rb"), n
            self.file.seek(self.pos - self.starts[n])
            got = self.file.readinto(view[done:done + self.starts[n] + self.sizes[n] - self.pos])
            if not got:
                raise OSError(f"{self.paths[n]} is shorter than it was")
            done += got
            self.pos += got
        return done

    def free_before(self, offset):
        """Delete the parts that end before offset: nothing from there on is read from them."""
        for n, path in enumerate(self.paths):
            if self.starts[n] + self.sizes[n] <= offset and os.path.exists(path):
                if n == self.part:
                    self.close_part()
                os.remove(path)

    def close_part(self):
        if self.file:
            self.file.close()
        self.file, self.part = None, None

    def close(self):
        self.close_part()
        super().close()


def library_worker(key, pack_id):
    """Download a pack's parts (resumable, checksummed), then unzip straight from them into the library folder."""
    pack = next((p for p in library_packs() if p["id"] == pack_id), None)
    if not pack:
        raise RuntimeError("That library pack isn't available. Check the internet connection.")
    work, partial = LIB + ".download", LIB + ".partial"
    shutil.rmtree(partial, ignore_errors=True)  # left by an install that stopped while unpacking
    os.makedirs(work, exist_ok=True)
    paths = [os.path.join(work, f"{pack_id}.{n:03d}") for n in range(len(pack["parts"]))]
    total = sum(part["bytes"] for part in pack["parts"])
    # the most on disk at once: the unpacked library plus the parts not unpacked yet (the manifest has the exact
    # figure; unpacked is about 2.4 times the zip); parts downloaded by an earlier try are already on the disk
    peak = pack.get("install_bytes") or total * 3.5
    have = sum(os.path.getsize(p) for p in paths if os.path.exists(p))
    need = (peak - have) / 1e9 + DISK_MARGIN_GB
    if need > disk_free_gb(os.path.dirname(LIB)):
        raise RuntimeError(f"Not enough free disk space: installing this pack needs about {need:.0f} GB free, "
                           f"and this computer has {disk_free_gb(os.path.dirname(LIB))} GB.")
    done_before = 0
    for n, (part, path) in enumerate(zip(pack["parts"], paths)):
        _download(key, part["url"], path, part["bytes"], done_before, total)
        if _sha256(path) != part["sha256"]:
            os.remove(path)
            raise RuntimeError(f"Part {n + 1} of {len(pack['parts'])} was damaged in transit. Try again to re-download it.")
        done_before += part["bytes"]
    _update(key, status="unpacking", completed=total, total=total)
    parts = PackParts(paths)
    try:
        with zipfile.ZipFile(parts) as z:
            for member in sorted(z.infolist(), key=lambda m: m.header_offset):
                parts.free_before(member.header_offset)  # each part goes as soon as everything in it is unpacked
                z.extract(member, partial)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)  # don't leave a half-unpacked library taking up the disk
        raise
    finally:
        parts.close()
    with open(os.path.join(partial, "pack.json"), "w") as f:  # which pack this is, for update checks
        json.dump({"id": pack_id, "version": pack.get("version", FIRST_PACKS)}, f)
    old = LIB + ".old"
    shutil.rmtree(old, ignore_errors=True)
    with lib_lock:  # close every open copy first: Windows won't rename a folder with open files in it
        lib_version[0] += 1
        for lib in list(open_libs):
            try:
                lib.db.close()
            except Exception:
                pass
        try:
            if os.path.islink(LIB):
                os.remove(LIB)
            elif os.path.isdir(LIB):
                os.rename(LIB, old)  # the old library stays whole until the new one is in place
            os.replace(partial, LIB)
        except OSError:
            if os.path.isdir(old) and not os.path.exists(LIB):
                os.rename(old, LIB)
            raise RuntimeError("Couldn't swap in the new library. Close any window showing the library folder, "
                               "then try again.") from None
        lib_version[0] += 1
    shutil.rmtree(old, ignore_errors=True)
    shutil.rmtree(work, ignore_errors=True)


def warm_model(model):
    """Load a model into memory ahead of the first question, so that question isn't slow."""
    if warming.get(model) == "loading" or model.removesuffix(":latest") in loaded_models():
        return
    if DEV:  # restarted after every change, a dev copy would load the model each time (80 GB for Nemotron Super)
        return

    def run():
        warming[model] = "loading"
        try:
            req = urllib.request.Request(HOST + "/api/generate", headers={"Content-Type": "application/json"},
                                         data=json.dumps({"model": model, "keep_alive": "15m"}).encode())
            urllib.request.urlopen(req, timeout=600).read()
            warming.pop(model, None)
        except OSError:
            warming[model] = "error"
    threading.Thread(target=run, daemon=True).start()


# ---------- HTTP ----------

# ---------- updates: the library pack, and the app itself (it replaces itself, then restarts) ----------

def version_tuple(version):
    return tuple(int(n) for n in re.findall(r"\d+", version or "0"))


def valid_update(data):
    """Whether update.json has the shape the app relies on (a broken file must not break running copies)."""
    if not isinstance(data, dict) or not isinstance(data.get("version"), str) \
            or not re.fullmatch(r"\d+(\.\d+){0,3}", data["version"]):
        return False
    for key in ("mac", "windows"):
        pkg = data.get(key)
        if pkg is not None and not (isinstance(pkg, dict) and isinstance(pkg.get("bytes"), int)
                                    and re.fullmatch(r"(https://|http://127\.0\.0\.1:)\S+", str(pkg.get("url", "")))
                                    and re.fullmatch(r"[0-9a-f]{64}", str(pkg.get("sha256", "")))):
            return False
    notes = data.get("notes", [])
    return isinstance(notes, list) and all(isinstance(n, str) for n in notes) and isinstance(data.get("date", ""), str)


def valid_packs(packs):
    return isinstance(packs, list) and all(
        isinstance(p, dict) and isinstance(p.get("id"), str) and isinstance(p.get("label"), str)
        and isinstance(p.get("gb"), (int, float)) and isinstance(p.get("version", ""), str)
        and isinstance(p.get("install_bytes", 0), int)
        and isinstance(p.get("parts"), list) and all(isinstance(x, dict) and isinstance(x.get("bytes"), int)
                                                     and isinstance(x.get("url"), str) for x in p["parts"])
        for p in packs)


def fetch_json(url):
    with urllib.request.urlopen(url, timeout=8) as resp:
        return json.load(resp)


def check_updates(force=False):
    """Fetch what's published: the latest app version and the library packs (at most every 6 hours unless forced)."""
    if not force and time.time() - updates["checked"] < 6 * 3600:
        return
    try:
        data = fetch_json(UPDATES_URL)
        if not valid_update(data):
            raise ValueError("update.json")
        updates.update(data=data, error=None)
        packs = fetch_json(MANIFEST_URL)
        if isinstance(packs, dict) and valid_packs(packs.get("packs")):
            manifest.update(packs=packs["packs"], error=None)
    except urllib.error.HTTPError:  # the site answered, but without the file
        updates["error"] = "unavailable"
    except OSError:
        updates["error"] = "offline"
    except Exception:  # a file that isn't what the app expects
        updates["error"] = "unavailable"
    updates["checked"] = time.time()


def auto_check_updates():
    delay = 20  # a little after starting, then once a day
    while not quit_event.wait(delay):
        try:
            check_updates(force=True)
        except Exception:
            pass
        delay = 24 * 3600


def installed_pack():
    """The library pack that is installed ({"id", "version"}), or None: no library, or one built by hand."""
    try:
        with open(os.path.join(LIB, "pack.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        pass
    if not os.path.exists(DB_PATH) or os.path.islink(LIB):
        return None
    try:
        db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        keys = {k for (k,) in db.execute("SELECT key FROM sources")}
        db.close()
    except sqlite3.Error:
        return None
    if keys & {"post-disaster", "water", "food-prep", "medical-library", "knots"}:  # sources no pack has
        return None
    return {"id": "full" if "wikipedia" in keys else "lite", "version": FIRST_PACKS}  # from before pack.json


def platform_key():
    return "mac" if sys.platform == "darwin" else "windows" if os.name == "nt" else None


def newer_app():
    """The published version if it is newer than this one and has a download for this computer, else None."""
    data = updates["data"] or {}
    if data.get(platform_key()) and version_tuple(data.get("version")) > version_tuple(VERSION):
        return data
    return None


def newer_library():
    pack = installed_pack()
    offer = next((p for p in manifest["packs"] or [] if pack and p["id"] == pack.get("id")), None)
    if offer and offer.get("version", FIRST_PACKS) > str(pack.get("version", FIRST_PACKS)):  # ISO dates sort as text
        return {k: offer.get(k) for k in ("id", "label", "gb", "version")}
    return None


def original_path(path):
    """macOS runs a downloaded app that was never moved from a temporary, read-only copy (App Translocation).
    The path of the real one, from the system's Security framework; None if it can't say."""
    if "/AppTranslocation/" not in path:
        return path
    try:
        cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        sec = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Security.framework/Security")
        cf.CFURLCreateFromFileSystemRepresentation.restype = ctypes.c_void_p
        cf.CFURLCreateFromFileSystemRepresentation.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long,
                                                               ctypes.c_bool]
        cf.CFURLGetFileSystemRepresentation.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_char_p, ctypes.c_long]
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        sec.SecTranslocateCreateOriginalPathForURL.restype = ctypes.c_void_p
        sec.SecTranslocateCreateOriginalPathForURL.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        raw = path.encode()
        url = cf.CFURLCreateFromFileSystemRepresentation(None, raw, len(raw), True)
        real = sec.SecTranslocateCreateOriginalPathForURL(url, None)
        cf.CFRelease(url)
        if not real:
            return None
        buf = ctypes.create_string_buffer(4096)
        ok = cf.CFURLGetFileSystemRepresentation(real, True, buf, len(buf))
        cf.CFRelease(real)
        return buf.value.decode() if ok else None
    except Exception:
        return None


def install_location():
    """Where this copy of the app lives when it came from a download: the Mac app, or the Windows folder."""
    if sys.platform == "darwin" and ROOT.endswith(os.path.join("Contents", "Resources", "app")):
        return original_path(os.path.dirname(os.path.dirname(os.path.dirname(ROOT))))
    if os.name == "nt" and os.path.isfile(os.path.join(os.path.dirname(ROOT), "Survival LLM.bat")):
        return os.path.dirname(ROOT)
    return None


def writable(folder):
    if os.name != "nt":
        return os.access(folder, os.W_OK)
    try:  # Windows folder permissions only show when you try
        probe = os.path.join(folder, f".survival-llm-{uuid.uuid4().hex[:8]}")
        open(probe, "w").close()
        os.remove(probe)
        return True
    except OSError:
        return False


def update_blocker():
    """Why this copy can't update itself, as a sentence for the person; None if it can."""
    if DEV:
        return "This is a dev copy of Survival LLM (started with bin/dev), so it doesn't update itself."
    where = install_location()
    if where is None:
        if "/AppTranslocation/" in ROOT:
            return ("macOS is running Survival LLM from a temporary copy. Move it to your Applications folder (drag it "
                    "there in Finder), open it from there, and check again.")
        return "This copy of Survival LLM runs from its source code, so it can't update itself. Update it with git pull."
    if not writable(os.path.dirname(where)):
        return "Survival LLM can't replace itself in the folder it's in. Download the new version from the website."
    return None


def update_report():
    """Everything the Updates window shows."""
    app, lib = newer_app(), newer_library()
    report = {"version": VERSION, "checked": updates["checked"] or None, "error": updates["error"],
              "download_page": DOWNLOAD_PAGE, "app": None, "library": lib}
    if app:
        report["app"] = {"version": app["version"], "date": app.get("date"), "notes": app.get("notes", []),
                         "gb": round(app[platform_key()]["bytes"] / 1e9, 3), "blocker": update_blocker()}
    try:
        with open(UPDATE_FAILED, encoding="utf-8-sig") as f:
            report["failed"] = f.read().strip() or "no reason given"
        os.remove(UPDATE_FAILED)  # shown once
    except OSError:
        pass
    return report


def app_update_worker(key):
    """Download the new version, check it, put it in place of this copy, then restart into it."""
    app, blocker = newer_app(), update_blocker()
    if blocker or not app:
        raise RuntimeError(blocker or "There's no newer version to install.")
    pkg = app[platform_key()]
    need = pkg["bytes"] * 3 / 1e9 + 1
    if need > disk_free_gb():
        raise RuntimeError(f"Not enough free disk space for the update: it needs about {need:.0f} GB free, "
                           f"and this computer has {disk_free_gb()} GB.")
    work = os.path.join(APP_DIR, "update")
    os.makedirs(work, exist_ok=True)
    zip_path = os.path.join(work, f"Survival-LLM-{re.sub(r'[^0-9.]', '', app['version'])}-{platform_key()}.zip")
    for name in os.listdir(work):  # a download of an older version
        if os.path.join(work, name) != zip_path:
            os.remove(os.path.join(work, name))
    _download(key, pkg["url"], zip_path, pkg["bytes"])
    if os.path.getsize(zip_path) != pkg["bytes"] or _sha256(zip_path) != pkg["sha256"]:
        os.remove(zip_path)
        raise RuntimeError("The update was damaged in transit. Try again.")
    _update(key, status="unpacking", completed=pkg["bytes"], total=pkg["bytes"])
    where = install_location()
    helper = install_mac(zip_path, where) if sys.platform == "darwin" else install_windows(zip_path, where)
    os.remove(zip_path)
    try:
        os.rmdir(work)
    except OSError:
        pass
    threading.Timer(1.5, restart_into, args=(helper,)).start()  # time for the window to say "Restarting"


def install_mac(zip_path, bundle):
    """Unpack the new app beside this one, check its signature, and swap them (the old one goes to the Trash).
    A running app's files can be replaced on macOS; the new one starts once this one has quit."""
    if sys.platform == "darwin":
        from Foundation import NSURL, NSFileManager  # noqa: F401  load before the swap: to_trash needs it after
    parent = os.path.dirname(bundle)
    staging = os.path.join(parent, f".Survival LLM update {uuid.uuid4().hex[:8]}")
    try:
        subprocess.run(["ditto", "-x", "-k", zip_path, staging], check=True, capture_output=True, timeout=900)
        new = os.path.join(staging, "Survival LLM.app")
        if subprocess.run(["codesign", "--verify", "--deep", new], capture_output=True, timeout=600).returncode:
            raise RuntimeError("The update didn't pass macOS's signature check, so it wasn't installed.")
        old = os.path.join(staging, "Survival LLM (old version).app")
        os.rename(bundle, old)
        try:
            os.rename(new, bundle)
        except OSError:
            os.rename(old, bundle)
            raise
        try:
            to_trash(old)  # can be put back from the Trash
        except Exception:
            shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    wait = ('n=0; while kill -0 "$1" 2>/dev/null; do n=$((n+1)); [ $n -gt 300 ] && kill -9 "$1"; sleep 0.2; done; '
            'sleep 0.5; open "$2"')  # a minute at most, then it stops the old copy
    return {"cmd": ["/bin/sh", "-c", wait, "survival-llm-update", str(os.getpid()), bundle]}


WINDOWS_SWAP = """# Run by a copy of Survival LLM that is updating itself on Windows: wait for it to quit, swap in the new folder
# (retrying for a minute, since something may still be using a file in it), put the old one back if that fails
# and leave a note saying why, and in every case start whichever version is in place.
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $env:TEMP
Wait-Process -Id ([int]$env:SLLM_PID) -Timeout 120 -ErrorAction SilentlyContinue
$dir = $env:SLLM_DIR
try {
  if (Test-Path -LiteralPath $env:SLLM_OLD) { Remove-Item -LiteralPath $env:SLLM_OLD -Recurse -Force }
  $moved = $false
  for ($i = 0; $i -lt 30 -and -not $moved; $i++) {
    try { Move-Item -LiteralPath $dir -Destination $env:SLLM_OLD; $moved = $true } catch { Start-Sleep -Seconds 2 }
  }
  if (-not $moved) { throw "Something is still using a file in the Survival LLM folder." }
  try { Move-Item -LiteralPath $env:SLLM_NEW -Destination $dir }
  catch { Move-Item -LiteralPath $env:SLLM_OLD -Destination $dir; throw }
  Remove-Item -LiteralPath $env:SLLM_OLD -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $env:SLLM_STAGING -Recurse -Force -ErrorAction SilentlyContinue
} catch {
  try { Set-Content -LiteralPath $env:SLLM_FAILED -Value $_.Exception.Message -Encoding UTF8 } catch {}
} finally {
  Start-Process -FilePath (Join-Path $dir 'python\\pythonw.exe') -WorkingDirectory $env:USERPROFILE `
    -ArgumentList ('"' + (Join-Path $dir 'app\\app\\server.py') + '"')
}"""


def install_windows(zip_path, folder):
    """Unpack the new version beside this one. Windows won't replace a running program's files, so a helper swaps
    the folders once this copy has quit, puts the old one back if that fails, and starts the app again."""
    staging = folder + ".update"
    shutil.rmtree(staging, ignore_errors=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(staging)
    new = os.path.join(staging, "Survival LLM")
    if not os.path.isfile(os.path.join(new, "python", "pythonw.exe")):
        shutil.rmtree(staging, ignore_errors=True)
        raise RuntimeError("The update didn't contain the app, so it wasn't installed.")
    script = base64.b64encode(WINDOWS_SWAP.encode("utf-16-le")).decode()
    return {"cmd": ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-EncodedCommand", script],
            "env": {**os.environ, "SLLM_PID": str(os.getpid()), "SLLM_DIR": folder, "SLLM_NEW": new,
                    "SLLM_OLD": folder + ".old", "SLLM_STAGING": staging, "SLLM_FAILED": UPDATE_FAILED}}


def restart_into(helper):
    """Start the helper that opens the new version once this copy has quit, then quit."""
    flags = {"creationflags": 0x08000000 | 0x00000200} if os.name == "nt" else {"start_new_session": True}
    subprocess.Popen(helper["cmd"], env=helper.get("env"), cwd=os.path.expanduser("~"), stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **flags)
    quit_app()


def quit_app():
    """Stop serving and close the window; the app then exits, stopping the AI engine it started."""
    if httpd:
        threading.Thread(target=httpd.shutdown, daemon=True).start()
    quit_event.set()
    for hook in quit_hooks:
        threading.Thread(target=hook, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _allowed(self, need_token=True):
        # Host check blocks DNS-rebinding pages; the token blocks other local web pages.
        host = self.headers.get("Host", "")
        if host not in (f"127.0.0.1:{PORT}", f"localhost:{PORT}"):
            return False
        if not need_token:
            return True
        query = parse_qs(urlparse(self.path).query)
        return secrets.compare_digest(self.headers.get("X-Token", query.get("t", [""])[0]), TOKEN)

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _stream_start(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def _event(self, event):
        self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
        self.wfile.flush()

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/ping":
            return self._send(200 if self._allowed(need_token=False) else 403, {"app": "survival-llm"})
        if url.path == "/" or url.path.startswith("/static/"):
            if not self._allowed(need_token=False):
                return self._send(403, {"error": "forbidden"})
            name = "index.html" if url.path == "/" else os.path.basename(url.path)
            path = os.path.join(STATIC, name)
            if not os.path.isfile(path):
                return self._send(404, {"error": "not found"})
            with open(path, "rb") as f:
                data = f.read()
            if name == "index.html":
                # Other websites cannot read this page (no CORS headers), so the token stays private.
                data = data.replace(b"{{TOKEN}}", TOKEN.encode())
            return self._send(200, data, MIME.get(os.path.splitext(name)[1], "application/octet-stream"))
        if not self._allowed():
            return self._send(403, {"error": "forbidden"})
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/api/status":
            return self._send(200, self._status())
        if url.path == "/api/article":
            doc = library().document(int(query.get("id", 0))) if library() else None
            return self._send(200 if doc else 404, doc or {"error": "not found"})
        if url.path == "/api/prompt":
            return self._send(200, {"default": system_prompt(library(), True), "custom": custom_prompt()})
        if url.path == "/api/memory":
            return self._send(200, {"text": "\n".join(MEMORY.notes()), "ai_writes": MEMORY.ai_writes,
                                    "max_chars": MEMORY_MAX})
        if url.path == "/api/myfiles":  # their files, and whether any are being read (?check=1: look for changes)
            if query.get("check"):
                check_my_files()
            return self._send(200, {"folder": MY_DIR, "items": myfiles.items(MY_DB), **myfiles.progress})
        if url.path == "/api/updates":  # ?check=1: look now instead of using the last check
            check_updates(force=bool(query.get("check")))
            return self._send(200, update_report())
        if url.path == "/api/search":
            if not library():
                return self._send(200, {"results": []})
            return self._send(200, {"results": library().search(query.get("q", ""), topic=query.get("topic"), limit=25)})
        return self._send(404, {"error": "not found"})

    def _status(self):
        if time.monotonic() - my_files_checked[0] > 30:  # picks up files changed or added in the folder itself
            check_my_files()
        models = installed_models()
        if models is None:
            start_ollama()
        app, binary = ollama_locations()
        info = engine_info()
        lib = library()
        docs, sources = lib.stats() if lib else (0, 0)
        mine = lib.mine.stats()[0] if lib and lib.mine else 0
        chat_models = [m.removesuffix(":latest") for m in models or [] if not any(x in m for x in NOT_CHAT)]
        catalog = machine_catalog(chat_models)
        best = next((c["model"] for c in catalog if c["installed"] and c["fits"]), None)
        with dl_lock:
            jobs = [dict(j) for j in downloads.values()]
        return {
            "ollama": models is not None, "ollama_installed": bool(app or binary), "ollama_starting": ollama_starting(),
            "engine": {"downloadable": bool(info and ENGINE and not (app or binary)),
                       "gb": round(info["bytes"] / 1e9, 1) if info else None},
            "models": [{"model": m, "name": friendly_name(m), "tested": bool(catalog_entry(m))} for m in chat_models],
            "default_model": best or (chat_models or [None])[0],
            "loaded": loaded_models() if models is not None else [],
            "warming": [m for m, s in warming.items() if s == "loading"],
            "catalog": catalog, "downloads": jobs,
            "library": {"articles": docs, "sources": sources} if docs else None,
            "packs": [] if docs else [{k: p[k] for k in ("id", "label", "gb")} for p in library_packs()],
            "packs_error": None if docs else manifest["error"],
            "library_dir": LIB, "disk_free_gb": disk_free_gb(),
            "my_files": {"ready": mine, "busy": myfiles.progress["busy"]},
            "version": VERSION, "dev": DEV,
            "update": {"app": (newer_app() or {}).get("version"), "library": bool(newer_library())},
            "machine": {"os": "macOS" if sys.platform == "darwin" else platform.system(), "ram_gb": ram_gb()},
            "topics": TOPICS}

    def do_POST(self):
        if not self._allowed():
            return self._send(403, {"error": "forbidden"})
        url = urlparse(self.path)
        if url.path == "/api/myfiles/add":  # the body is the file itself
            return self._add_file(url)
        body = self._body()
        model = str(body.get("model") or "")
        if model and not re.fullmatch(r"[\w.:/-]{1,120}", model):
            return self._send(400, {"error": "bad model name"})
        if url.path == "/api/prompt":  # save the person's instructions ("" or reset: back to the default)
            text = "" if body.get("reset") else str(body.get("text") or "").strip()
            os.makedirs(APP_DIR, exist_ok=True)
            if text:
                with open(CUSTOM_PROMPT, "w", encoding="utf-8") as f:
                    f.write(text + "\n")
            elif os.path.exists(CUSTOM_PROMPT):
                os.remove(CUSTOM_PROMPT)
            refresh_prompts()  # takes effect in open chats too, from the next question
            return self._send(200, {"ok": True, "custom": custom_prompt()})
        if url.path == "/api/memory":  # save the person's version of the notes; base: the text they started from
            text = str(body.get("text") or "")
            if notes_size(parse_notes(text)) > MEMORY_MAX:
                return self._send(400, {"error": f"That's more than memory holds ({MEMORY_MAX:,} characters). Shorten "
                                                 "some notes, or put longer things in Your files."})
            base = body.get("base")
            try:
                notes, kept, dropped = MEMORY.save_text(text, base if isinstance(base, str) else None)
            except OSError as exc:
                return self._send(500, {"error": save_failed(exc, MEMORY.path)})
            refresh_prompts()
            return self._send(200, {"ok": True, "text": "\n".join(notes), "kept": kept, "dropped": dropped})
        if url.path == "/api/memory/ai-writes":  # the switch: may the AI add and change notes?
            try:
                MEMORY.set_ai_writes(bool(body.get("on")))
            except OSError as exc:
                return self._send(500, {"error": save_failed(exc, MEMORY.settings_path)})
            refresh_prompts()
            return self._send(200, {"ok": True, "ai_writes": MEMORY.ai_writes})
        if url.path == "/api/memory/undo":  # take back a note the AI just saved, changed or deleted
            note, old = str(body.get("note") or ""), str(body.get("old") or "")
            at = body.get("at") if isinstance(body.get("at"), int) and not isinstance(body.get("at"), bool) else None
            try:
                near = [[str(x) for x in v][:500] if isinstance(v, list) else []
                        for v in (body.get("before"), body.get("after"))]  # the notes around a deleted one
                done = MEMORY.undo(str(body.get("action") or ""), note, old, at, *near)
            except OSError:
                done = False
            if done and any(n and n in (c.get("messages") or [{}])[0].get("content", "") for n in (note, old)
                            for c in list(conversations.values())):
                refresh_prompts()  # a chat whose instructions already list the note learns it was taken back
            return self._send(200 if done else 409, {"ok": done})
        if url.path == "/api/myfiles/remove":
            path = my_file_path(body.get("name", ""))
            if not path:
                return self._send(404, {"error": "That file isn't in the folder any more."})
            try:
                to_trash(path)
            except Exception:
                return self._send(500, {"error": "Couldn't move it to the " + ("Recycle Bin." if os.name == "nt" else "Trash.")})
            check_my_files()
            return self._send(200, {"ok": True})
        if url.path == "/api/myfiles/open":
            open_folder(MY_DIR)
            return self._send(200, {"ok": True})
        if url.path == "/api/new":
            check_my_files()  # picks up files put straight into the folder
            conversations.pop(body.get("conversation"), None)
            return self._send(200, {"conversation": uuid.uuid4().hex})
        if url.path == "/api/chat":
            return self._chat(body)
        if url.path == "/api/pull":
            started = start_job(f"model:{model}", friendly_name(model), pull_worker, model)
            return self._send(200 if started else 409, {"ok": started})
        if url.path == "/api/library":
            pack = str(body.get("pack", ""))
            label = next((p["label"] for p in library_packs() if p["id"] == pack), pack)
            started = start_job(f"library:{pack}", label, library_worker, pack)
            return self._send(200 if started else 409, {"ok": started})
        if url.path == "/api/warm":
            warm_model(model)
            return self._send(200, {"ok": True})
        if url.path == "/api/engine":
            started = start_job("engine", "AI engine (Ollama)", engine_worker)
            return self._send(200 if started else 409, {"ok": started})
        if url.path == "/api/start-ollama":
            ollama_started_at[0] = 0
            start_ollama()
            return self._send(200, {"ok": True})
        if url.path == "/api/update/app":
            app = newer_app()
            with dl_lock:
                busy = [j["label"] for j in downloads.values()
                        if j["status"] in ("starting", "downloading", "unpacking") and j["key"] != "app-update"]
            if busy:  # updating restarts the app, which would cut it short
                return self._send(409, {"error": f"Wait until {busy[0]} has finished, then update."})
            started = bool(app) and start_job("app-update", f"Survival LLM {app['version']}", app_update_worker)
            return self._send(200 if started else 409, {"ok": started})
        if url.path == "/api/quit":
            self._send(200, {"ok": True})
            quit_app()
            return None
        return self._send(404, {"error": "not found"})

    def _add_file(self, url):
        try:
            return self._save_file(url)
        except ConnectionError:  # the page went away mid-upload
            return None
        except OSError as exc:
            return self._send(500, {"error": f"Couldn't save this file ({exc.strerror or exc})."})

    def _save_file(self, url):
        """Save an uploaded file into their folder. ?replace=<name> makes it the new version of that file (the old one
        goes to the Trash); ?keep=1 keeps both when a different file already has this name ("name (2)"); with
        neither, that case answers 409 so the app can ask which they want."""
        self.close_connection = True  # a refused upload leaves its body unread
        query = parse_qs(url.query)
        name = safe_name(query.get("name", [""])[0])
        old = my_file_path(query["replace"][0]) if query.get("replace") else None
        if query.get("replace") and not old:
            return self._send(404, {"error": "The file you're updating isn't in the folder any more. Add this one instead."})
        size = int(self.headers.get("Content-Length") or 0)
        if os.path.splitext(name)[1].lower() not in myfiles.KINDS:
            return self._send(400, {"error": myfiles.CANT_READ})
        if size > MAX_UPLOAD:
            return self._send(413, {"error": "This file is too big (over 4 GB)."})
        if size + 1e9 > disk_free_bytes(MY_DIR):
            return self._send(507, {"error": "There isn't enough free disk space for this file."})
        os.makedirs(MY_DIR, exist_ok=True)
        part = os.path.join(MY_DIR, f".{uuid.uuid4().hex}.part")
        try:
            with open(part, "wb") as f:
                left = size
                while left:
                    chunk = self.rfile.read(min(left, 1 << 20))
                    if not chunk:
                        raise ConnectionError("upload cut short")
                    f.write(chunk)
                    left -= len(chunk)
            dest = os.path.join(MY_DIR, name)
            if old and os.path.getsize(old) == size and filecmp.cmp(old, part, shallow=False):
                return self._send(200, {"ok": True, "name": my_name(old), "unchanged": True})
            if os.path.exists(dest) and os.path.realpath(dest) != old:
                if os.path.getsize(dest) == size and filecmp.cmp(dest, part, shallow=False):
                    return self._send(200, {"ok": True, "name": name, "duplicate": True})  # the same file again
                if not old and query.get("keep", [""])[0] != "1":
                    return self._send(409, {"error": f"You already have a file called {name}.", "name": name})
                stem, ext = os.path.splitext(name)
                n = 2
                while os.path.exists(dest):
                    dest = os.path.join(MY_DIR, f"{stem} ({n}){ext}")
                    n += 1
            if old:
                try:
                    to_trash(old)
                except Exception:
                    return self._send(500, {"error": "Couldn't move the old version to the "
                                                     + ("Recycle Bin." if os.name == "nt" else "Trash.")})
            os.replace(part, dest)
        finally:
            if os.path.exists(part):
                os.remove(part)
        check_my_files()
        reply = {"ok": True, "name": my_name(dest)}
        if old:
            reply["replaced"] = my_name(old)
        return self._send(200, reply)

    def _chat(self, body):
        model, question = body.get("model"), (body.get("message") or "").strip()
        if not model or not question:
            return self._send(400, {"error": "model and message are required"})
        conv = conversations.setdefault(body.get("conversation") or uuid.uuid4().hex, {"model": None, "run": 0})
        caps = model_caps(model)
        if conv.get("model") != model:  # new chat, or the model changed mid-chat
            conv.update(model=model, messages=[{"role": "system", "content": prompt_for(caps)}])
        conv["run"] += 1  # a newer question on this chat stops any older one still running
        run = conv["run"]
        messages = conv["messages"]
        keep = len(messages)
        messages.append({"role": "user", "content": question})
        trim(messages)
        self._stream_start()
        events = chat_events(model, library(), messages, reasoning=str(body.get("reasoning") or "off"), caps=caps,
                             length=str(body.get("length") or "normal"), memory=MEMORY)
        answered, last_tick = False, 0.0  # answered: the answer went out (a memory step may follow it)
        try:
            for event in events:
                if conv["run"] != run:
                    break
                if event["type"] == "tick":  # heartbeat: writing it is how we notice a closed or stopped page
                    if time.monotonic() - last_tick > 0.5:
                        last_tick = time.monotonic()
                        self.wfile.write(b": tick\n\n")
                        self.wfile.flush()
                    continue
                self._event(event)
                answered = answered or (event["type"] == "done" and conv["run"] == run)
        except (BrokenPipeError, ConnectionResetError):
            pass  # stopped, or the page was closed
        except Exception as exc:
            try:
                self._event({"type": "error", "message": friendly_error(exc)})
            except OSError:
                pass
        finally:
            events.close()  # closes the request to Ollama, which stops generating
            if not answered and conv["run"] == run:
                del messages[keep:]  # forget the unanswered question so the next one starts clean


def custom_prompt():
    try:
        with open(CUSTOM_PROMPT, encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def prompt_for(caps):
    """The instructions a conversation starts with: the person's edited version, or the default for this model, then
    today's date, the names of their files and the AI's memory notes (memory.py)."""
    tools = "tools" in caps
    files = [(it["name"], it["title"]) for it in myfiles.items(MY_DB) if it["status"] == "ready"]
    extra = prompt_section(MEMORY.notes(), MEMORY.ai_writes, files, datetime.date.today())
    return (custom_prompt() or system_prompt(library(), tools)) + (f"\n\n{extra}" if extra else "")


def open_window(url):
    """Show the app in its own window (the system's built-in web view) until it is closed. False when this
    computer can't (pywebview or the web view missing), so the caller falls back to the browser."""
    try:
        import webview
    except ImportError:
        return False
    try:
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True  # source links go to the browser, not this window
        window = webview.create_window(f"Survival LLM (dev: {DEV})" if DEV else "Survival LLM", url, width=1280,
                                       height=860, min_size=(420, 520), text_select=True)
        quit_hooks.append(window.destroy)
        webview.start(gui="edgechromium" if os.name == "nt" else None, private_mode=False,
                      storage_path=os.path.join(APP_DIR, "window"))
        return True
    except Exception as exc:  # e.g. no WebView2 on an old Windows 10
        print(f"Couldn't open a window ({exc}); using the browser instead.", flush=True)
        return False


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return  # the window closed a connection it kept open; nothing went wrong
        super().handle_error(request, client_address)


def make_server(preferred):
    """Listen on the preferred port, or any free one if something else holds it."""
    try:
        return QuietServer(("127.0.0.1", preferred), Handler)
    except OSError:
        return QuietServer(("127.0.0.1", 0), Handler)


if __name__ == "__main__":
    if sys.stdout is None:  # Windows pythonw (no console): keep the log instead of losing it
        os.makedirs(APP_DIR, exist_ok=True)
        sys.stdout = sys.stderr = open(os.path.join(APP_DIR, "app.log"), "a", buffering=1)
    os.chdir(os.path.expanduser("~"))  # nor can anything started from here, like the person's own Ollama, hold it
    headless, use_browser = "--no-browser" in sys.argv, "--browser" in sys.argv
    existing = None if "--port" in sys.argv else running_instance()  # an explicit port means "start another"
    if existing:  # opened twice: just bring up the one already running
        print(f"Survival LLM is already running at {existing}")
        if not headless and (use_browser or not open_window(existing)):
            webbrowser.open(existing)
        sys.exit(0)
    atexit.register(stop_engine)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # so a plain kill still stops the engine
    if installed_models() is None:
        start_ollama()
    port_arg = sys.argv[sys.argv.index("--port") + 1] if "--port" in sys.argv else "8765"
    httpd = server = make_server(int(port_arg))
    PORT = server.server_address[1]
    url = f"http://127.0.0.1:{PORT}/"
    os.makedirs(APP_DIR, exist_ok=True)
    with open(STATE, "w") as f:
        json.dump({"port": PORT, "pid": os.getpid()}, f)
    try:  # an update's download folder, left behind when Windows doesn't let go of it in time (0.3.1 Windows test)
        os.rmdir(os.path.join(APP_DIR, "update"))
    except OSError:
        pass  # not there, or holding a download
    print(f"{datetime.datetime.now():%Y-%m-%d %H:%M} Survival LLM {VERSION}{f' (dev: {DEV})' if DEV else ''} is running at "
          f"{url}", flush=True)  # the date tells one run's lines in app.log from the next
    check_my_files()  # files put in the folder while the app was closed
    threading.Thread(target=auto_check_updates, daemon=True).start()
    try:
        if headless or use_browser:
            if use_browser:
                threading.Timer(0.5, webbrowser.open, args=(url,)).start()
            server.serve_forever()
        else:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            if not open_window(url):  # no window support on this computer: use the browser after all
                webbrowser.open(url)
                quit_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            os.remove(STATE)
        except OSError:
            pass
