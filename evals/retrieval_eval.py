"""Retrieval test: ask a model 22 blackout / first-aid questions with the library and grade the answers.

Usage: .venv/bin/python evals/retrieval_eval.py [--rest SECONDS] [--memory] MODEL [MODEL ...]
An interrupted run picks up where it stopped. --rest pauses between questions so the fans stay quieter.
--memory runs with the app's memory on (empty notes, saving allowed) and counts notes saved; answers go to
evals/results/with-memory/ instead.
Grades each answer against facts from official guidance (USDA, CDC, FEMA, WHO, NHS), and records
whether the model searched, read a relevant article and cited it. Answers go to evals/results/.
"""
import datetime
import json
import os
import re
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core import HOST, capabilities, chat_events, system_prompt  # noqa: E402
from library.search import Library  # noqa: E402
from memory import Memory, prompt_section  # noqa: E402

# q: question. must: regexes the answer must match (official facts). about: regex a relevant
# source title should match, to check the model actually found the right material.
QUESTIONS = [
    {"q": "The power just went out. How long will the food in my fridge and freezer stay safe?",
     "must": [r"\b4\s*hours?", r"\b48\s*hours?", r"\b24\s*hours?"], "about": r"food|outage|refrigerat|freez"},
    {"q": "Can I run my gas generator in the garage if I leave the big door open?",
     "must": [r"\b(no|never|don'?t|do not)\b", r"\b20\s*(feet|ft|foot)"], "about": r"generator|carbon monoxide"},
    {"q": "The tap water might be contaminated. How many drops of household bleach per gallon make it safe?",
     "must": [r"\b(6|8|16|six|eight|sixteen)\s*drops", r"\b30\s*min"], "about": r"water|bleach|disinfect|purif"},
    {"q": "How long do I need to boil water to make it safe to drink?",
     "must": [r"\b(1|one)\s*(full\s*)?minute"], "about": r"water|boil|purif"},
    {"q": "What are the signs of carbon monoxide poisoning and what should I do?",
     "must": [r"headache", r"dizz|nause|vomit|confus", r"fresh air|outside|get out|leave", r"911|emergency"],
     "about": r"carbon monoxide"},
    {"q": "Someone is shivering hard and acting confused after being out in the cold. What do I do?",
     "must": [r"hypotherm", r"wet", r"warm|blanket", r"911|emergency|medical"], "about": r"hypotherm|cold"},
    {"q": "Someone cut their arm badly and it is bleeding a lot. What do I do?",
     "must": [r"pressure", r"911|emergency"], "about": r"bleed|wound|first aid|hemorrh"},
    {"q": "I burned my hand on the stove. How should I treat it?",
     "must": [r"cool|cold", r"running water|under (cool|cold)? ?water|tap water",
              r"\b(10|15|20|ten|fifteen|twenty)(\s*-\s*\d+)?\s*min"],
     "about": r"burn"},
    {"q": "The power is out. How long can my insulin stay unrefrigerated?",
     "must": [r"\b28\s*days|\b(4|four)\s*weeks|\bmonth"], "about": r"insulin|diabet"},
    {"q": "Is it safe to use candles for light during a blackout?",
     "must": [r"flashlight|battery|lantern", r"unattended|never leave|don'?t leave"], "about": r"candle|outage|fire"},
    {"q": "My phone is at 15% and the power is out. How do I make the battery last?",
     "must": [r"low power|battery saver|power sav", r"airplane|flight mode", r"bright"],
     "about": r"phone|battery|outage"},
    {"q": "Can I charge my phone in my car with the engine running inside the garage?",
     "must": [r"\b(no|never|don'?t|do not|not)\b", r"carbon monoxide|\bco\b"], "about": r"carbon monoxide|car|garage"},
    {"q": "A power line is down in my front yard. What should I do?",
     "must": [r"\b(30|33|35|thirty|thirty-five)\s*(feet|ft)|\b10\s*m", r"911|utility|power company"],
     "about": r"power line|downed|electric"},
    {"q": "The water is off and the toilet won't flush. What can I do for a toilet?",
     "must": [r"bucket", r"bag|liner", r"bleach|disinfect|lime|sawdust|litter|lid"], "about": r"toilet|sanitation|waste"},
    {"q": "How do I keep warm in my house in winter if the power and heat are out?",
     "must": [r"(one|single|small|same) room", r"layer", r"carbon monoxide|\boven\b|\bstove\b|\bgrill"],
     "about": r"cold|winter|warm|heat"},
    {"q": "There has been a nuclear explosion nearby. What should I do?",
     "must": [r"get inside|go inside|shelter|indoors", r"basement|middle|center|interior",
              r"\b24\s*hours|stay inside|stay tuned"], "about": r"nuclear|radiation|fallout"},
    {"q": "Is canned food still safe to eat after it sat in floodwater?",
     "must": [r"label", r"sanitiz|bleach|boil|disinfect", r"home[- ]canned|home canned|discard"],
     "about": r"flood|canned|food"},
    {"q": "Is it safe to take ibuprofen that expired two years ago?",
     "must": [r"less effective|potency|effective|weaker|strength"], "about": r"expir|ibuprofen|medic"},
    {"q": "How do I make oral rehydration solution at home?",
     "must": [r"\b(6|six) (level )?(tea)?spoons?|\b(6|six) teaspoons", r"half|1/2|½",
              r"\b(1|one) (liter|litre|l\b|quart)"], "about": r"rehydrat|diarrh|dehydrat"},
    {"q": "Someone working in the heat is confused, very hot and has stopped sweating. What do I do?",
     "must": [r"heat ?stroke", r"911|emergency", r"cool|ice|cold water|wet"], "about": r"heat"},
    {"q": "How can I tell if home-canned food might have botulism?",
     "must": [r"bulg|swoll|leak|spurt|cloudy|odor|smell|foam", r"(do not|don'?t|never) taste", r"throw|discard"],
     "about": r"botul|canning|canned"},
    {"q": "How much water should I store per person for an emergency?",
     "must": [r"\b(1|one) gallon", r"per (person|day)", r"\b(3|three)[- ]day|\b(2|two)[- ]weeks?"],
     "about": r"water|emergency|supply|kit"},
]


def grade(item, answer, events):
    # Models write "3‑day" and "10–15 minutes" with typographic dashes; compare on plain hyphens.
    text = re.sub(r"[‐-―−]", "-", answer.lower())
    facts = [bool(re.search(p, text)) for p in item["must"]]
    titles = [r["title"] for e in events if e["type"] == "search" for r in e["results"]]
    read = [e["title"] for e in events if e["type"] in ("read", "show")]
    sources_line = re.search(r"sources?:\s*(.+)", text)
    return {
        "facts": sum(facts) / len(facts),
        "missed": [p for p, ok in zip(item["must"], facts) if not ok],
        "searched": any(e["type"] == "search" for e in events),
        "read": bool(read),
        "relevant": any(re.search(item["about"], t, re.I) for t in titles + read),
        "cited": bool(sources_line and "none" not in sources_line.group(1)),
    }


def warm_up(model):
    body = {"model": model, "messages": [{"role": "user", "content": "Say OK."}], "stream": False,
            "keep_alive": "15m", "options": {"num_ctx": 32768}}
    req = urllib.request.Request(HOST + "/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    start = time.time()
    urllib.request.urlopen(req, timeout=900).read()
    return time.time() - start


def run(model):
    library, caps = Library(), capabilities(model)
    load = warm_up(model)
    out_dir = os.path.join(ROOT, "evals", "results", *(["with-memory"] if MEMORY else []))
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, model.replace(":", "_").replace("/", "_") + ".jsonl")
    tmp = tempfile.TemporaryDirectory() if MEMORY else None
    saved = {}
    if os.path.exists(path):  # finish an interrupted run instead of starting over
        saved = {r["q"]: r for r in map(json.loads, open(path))}
        if len(saved) >= len(QUESTIONS):
            saved = {}
    rows = []
    with open(path, "w") as f:
        for item in QUESTIONS:
            if item["q"] in saved:
                row = saved[item["q"]]
                row.update(grade(item, row["answer"], row["events"]))
                rows.append(row)
                f.write(json.dumps(row) + "\n")
                f.flush()  # the old file is already truncated; a kill must not lose these
                continue
            if rows and REST:
                time.sleep(REST)  # let the chip cool between answers (quieter fans, slower run)
            prompt, memory = system_prompt(library, "tools" in caps), None
            if MEMORY:  # every question starts from empty notes
                memory = Memory(os.path.join(tmp.name, f"memory-{len(rows)}.txt"), os.path.join(tmp.name, "settings.json"))
                prompt += "\n\n" + prompt_section([], True, (), datetime.date.today())
            messages = [{"role": "system", "content": prompt}, {"role": "user", "content": item["q"]}]
            events, start, done = [], time.time(), {}
            try:
                for ev in chat_events(model, library, messages, caps=caps, memory=memory):
                    if ev["type"] == "done":
                        done = ev
                    elif ev["type"] not in ("token", "tick", "thinking"):
                        events.append(ev)
            except Exception as exc:  # a broken tool call counts as a failed answer, not a crashed run
                done = {"answer": f"[error: {exc}]", "tokens": 0, "seconds": 0}
            row = {"q": item["q"], "answer": done.get("answer", ""), "secs": round(time.time() - start, 1),
                   "tokens": done.get("tokens", 0), "gen_secs": round(done.get("seconds", 0), 1),
                   "events": events, **grade(item, done.get("answer", ""), events),
                   **({"discarded": done["discarded"]} if done.get("discarded") is not None else {})}  # a no-answer's text
            rows.append(row)
            f.write(json.dumps(row) + "\n")
            f.flush()
            print(f"  {row['facts']:.0%} facts  {row['secs']:5.1f}s  {item['q'][:60]}", flush=True)
    n = len(rows)
    tok = sum(r["tokens"] for r in rows)
    gen = sum(r["gen_secs"] for r in rows) or 1
    summary = {
        "model": model, "tools": "tools" in caps, "load_secs": round(load, 1),
        "facts": round(sum(r["facts"] for r in rows) / n, 3),
        "all_facts": sum(r["facts"] == 1 for r in rows),
        "searched": sum(r["searched"] for r in rows), "read": sum(r["read"] for r in rows),
        "relevant": sum(r["relevant"] for r in rows), "cited": sum(r["cited"] for r in rows),
        "notes_saved": sum(sum(e["type"] == "memory" for e in r["events"]) for r in rows), "memory": MEMORY,
        "avg_secs": round(sum(r["secs"] for r in rows) / n, 1), "tok_s": round(tok / gen, 1), "n": n,
    }
    with open(os.path.join(out_dir, "summary.jsonl"), "a") as f:
        f.write(json.dumps(summary) + "\n")
    print(json.dumps(summary), flush=True)
    try:  # free the memory before the next model
        urllib.request.urlopen(urllib.request.Request(
            HOST + "/api/generate", data=json.dumps({"model": model, "keep_alive": 0}).encode(),
            headers={"Content-Type": "application/json"}), timeout=60).read()
    except OSError:
        pass
    return summary


def regrade():
    """Re-score saved answers with the current grader (no model calls)."""
    out_dir = os.path.join(ROOT, "evals", "results", *(["with-memory"] if MEMORY else []))
    by_q = {item["q"]: item for item in QUESTIONS}
    for name in sorted(os.listdir(out_dir)):
        if not name.endswith(".jsonl") or name == "summary.jsonl":
            continue
        rows = [json.loads(line) for line in open(os.path.join(out_dir, name))]
        if not rows:
            continue
        for r in rows:
            r.update(grade(by_q[r["q"]], r["answer"], r["events"]))
        n = len(rows)
        print(f"{name[:-6]:42} facts {sum(r['facts'] for r in rows) / n:5.1%}  all-correct {sum(r['facts'] == 1 for r in rows):2}/{n}"
              f"  searched {sum(r['searched'] for r in rows):2}  read {sum(r['read'] for r in rows):2}"
              f"  cited {sum(r['cited'] for r in rows):2}  avg {sum(r['secs'] for r in rows) / n:5.1f}s")


REST = 0  # seconds to pause between questions; set with --rest N
MEMORY = False  # --memory


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--rest" in args:
        i = args.index("--rest")
        REST = float(args[i + 1])
        del args[i:i + 2]
    if "--memory" in args:
        MEMORY = True
        args.remove("--memory")
    if args == ["--regrade"]:
        regrade()
    for m in (a for a in args if a != "--regrade"):
        print(f"== {m}", flush=True)
        run(m)
