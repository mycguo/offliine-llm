"""Memory test: does a model save what the person tells it about themselves, leave one-off questions alone, keep its
notes tidy, use them in a later chat, and still look things up in the library?

Usage: .venv/bin/python evals/memory_eval.py [--rest SECONDS] [--step] MODEL [MODEL ...]
Each case starts from its own notes in a throwaway memory file, with the app's real instructions (core.py plus
memory.py's section). Answers and notes go to evals/results/memory/. Cases marked "info" are reported, not graded.
--step tests only the memory step after an answer, on fixed answers: a few seconds a case instead of a minute.
"""
import datetime
import json
import os
import re
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core import capabilities, chat_events, memory_check, system_prompt, think_setting  # noqa: E402
from library.search import Library  # noqa: E402
from memory import Memory, prompt_section  # noqa: E402

FILES = [("EU2200i_manual.pdf", "Honda EU2200i Owner's Manual"), ("family-plan.docx", "Our family emergency plan")]


# Ties the allergy to her, or warns off penicillin-type antibiotics: naming penicillin alone would also match an
# answer that recommends it.
USES_ALLERGY = (r"(maya|she|her|daughter)[^.]{0,80}allerg|allerg[^.]{0,80}(maya|she|her|daughter)|"
                r"(avoid|not|n't|never|instead of|alternative)[^.]{0,60}(penicillin|amoxicillin)")


def notes_match(notes, pattern):
    return [n for n in notes if re.search(pattern, n, re.I)]


# turns: questions asked in order; a None starts a new chat (same memory). check(r) -> (passed, why); r has
# "notes" (after), "events" and "answers" (per turn), "markup" (tool calls written out as text).
CASES = [
    {"id": "save-allergy", "turns": ["My daughter Maya is 7 and allergic to penicillin. She has an earache and a mild "
                                     "fever tonight. What can I do at home?"],
     "check": lambda r: (bool(notes_match(r["notes"], r"penicillin")) and r["searched"],
                         "saves the allergy and still searches")},
    {"id": "use-note", "notes": ["Daughter Maya (7) is allergic to penicillin."],
     "turns": ["Maya has a sore throat and a fever. Which antibiotics are usually used for strep throat?"],
     "check": lambda r: (bool(re.search(USES_ALLERGY, r["answers"][-1], re.I)) and r["searched"],
                         "warns about her allergy and still searches")},
    {"id": "no-save-oneoff", "turns": ["How many drops of bleach do I add to a gallon of water to make it safe?"],
     "check": lambda r: (not r["memory_events"] and r["searched"], "saves nothing for a one-off question")},
    {"id": "update", "notes": ["Has a Honda EU2200i generator."],
     "turns": ["I sold the Honda generator and bought a Champion 4500 dual fuel instead. How far from the house "
               "should I run it?"],
     "check": lambda r: (bool(notes_match(r["notes"], r"champion")) and len(notes_match(r["notes"], r"generator")) == 1,
                         "replaces the old generator note instead of adding a second")},
    {"id": "forget", "notes": ["Lives in Oakland, California.", "Has two cats."],
     "turns": ["Please forget where I live."],
     "check": lambda r: (not notes_match(r["notes"], r"oakland") and bool(notes_match(r["notes"], r"cats")),
                         "deletes only that note")},
    {"id": "no-duplicate", "notes": ["Daughter Maya (7) is allergic to penicillin."],
     "turns": ["Remember, Maya can't have penicillin. She has a rash on her arms now. What could it be?"],
     "check": lambda r: (len(notes_match(r["notes"], r"penicillin")) == 1, "doesn't save the allergy twice")},
    {"id": "later-chat", "turns": ["I have type 1 diabetes and take insulin. Is it OK to drink tap water during a "
                                   "boil-water notice if I'm thirsty?", None,
                                   "The power has been out for a day. How do I keep my medicine cold?"],
     "check": lambda r: (bool(notes_match(r["notes"], r"insulin|diabet")) and bool(re.search(r"insulin", r["answers"][-1], re.I)),
                         "saves it in one chat and uses it in the next")},
    {"id": "writing-off", "notes": ["Son Leo (9) has type 1 diabetes."], "ai_writes": False,
     "turns": ["My wife is pregnant. What foods should she avoid if the fridge has been off for a day?"],
     "check": lambda r: (r["notes"] == ["Son Leo (9) has type 1 diabetes."] and not r["markup"]
                         and len(r["answers"][-1]) > 80 and not re.search(r"^\[id \d+\]", r["answers"][-1], re.M),
                         "with saving off: notes unchanged, no stray tool calls or pasted results, still answers")},
    {"id": "asks-about-file", "files": True, "info": True,
     "turns": ["How often should I change the oil in my generator?"],
     "check": lambda r: (bool(re.search(r"(EU2200i|manual|Honda|family-plan|plan)[^?]{0,200}\?", r["answers"][-1], re.I)),
                         "asks what one of their files is (allowed, not required)")},
    {"id": "situational", "info": True,
     "turns": ["The power went out about three hours ago and it's freezing outside. How do I keep my baby warm tonight?"],
     "check": lambda r: (bool(notes_match(r["notes"], r"baby|infant")), "saves that there's a baby (a date is a plus)")},
]


# The memory step alone: (id, what the person said, notes before, the answer it gave, check on the notes after).
STEP_CASES = [
    ("diabetes", "I have type 1 diabetes and take insulin. Is it OK to drink tap water during a boil-water notice?", [],
     "No. Boil it for 1 minute first, or drink bottled water.", lambda n: bool(notes_match(n, r"diabet|insulin"))),
    ("condition-long", "I'm 7 months pregnant and the power has been out since last night. Is the milk in the fridge "
     "still safe to drink?", [],
     "Probably not. Milk kept above 40°F for more than 2 hours should be thrown out, and the USDA says a closed fridge "
     "keeps food safe for about 4 hours. Pregnant women should be extra careful with dairy because of Listeria: when in "
     "doubt, throw it out. Use shelf-stable milk or powdered milk instead.\n\nSources: Keeping Food Safe During an "
     "Emergency (USDA); Listeria and pregnancy (MedlinePlus)", lambda n: bool(notes_match(n, r"pregnan"))),
    ("two-facts", "My son Leo is 9 and has asthma, and we have a dog. What should be in our go-bag?", [],
     "Water, food, his inhaler and spare medicine, the dog's food and leash, copies of documents.",
     lambda n: bool(notes_match(n, r"asthma")) and bool(notes_match(n, r"dog"))),
    ("forget", "Please forget where I live.", ["Lives in Oakland, California.", "Has two cats."],
     "OK, I've removed that.", lambda n: n == ["Has two cats."]),
    ("forget-about", "Forget about my generator.", ["Daughter Maya is allergic to penicillin.", "Has a Honda EU2200i generator."],
     "OK, I'll forget your generator.", lambda n: n == ["Daughter Maya is allergic to penicillin."]),
    ("delete-note", "Please delete the note about my generator from your memory.",
     ["Daughter Maya is allergic to penicillin.", "Has a Honda EU2200i generator."], "OK, I'll delete it.",
     lambda n: n == ["Daughter Maya is allergic to penicillin."]),
    ("forget-sold", "Forget the Honda generator, I sold it. I use a Champion 4500 now.", ["Has a Honda EU2200i generator."],
     "Got it.", lambda n: bool(notes_match(n, r"champion")) and not notes_match(n, r"honda")),
    ("oneoff", "How many drops of bleach do I add to a gallon of water to make it safe?", ["Has two cats."],
     "8 drops of unscented 6% bleach per gallon, then wait 30 minutes.", lambda n: n == ["Has two cats."]),
    ("no-repeat", "Remember, Maya can't have penicillin. She has a rash on her arms now. What could it be?",
     ["Daughter Maya (7) is allergic to penicillin."], "Rashes can come from eczema, hives or a virus.",
     lambda n: n == ["Daughter Maya (7) is allergic to penicillin."]),
    ("update", "I sold my Honda generator and bought a Champion 4500 dual fuel. How far from the house should it go?",
     ["Has a Honda EU2200i generator."], "At least 20 feet away, exhaust pointed away from the house.",
     lambda n: bool(notes_match(n, r"champion")) and not notes_match(n, r"honda eu2200i generator\.?$")),
    ("dose-change", "My doctor raised my lisinopril to 40 mg.", ["Takes 20 mg lisinopril and is allergic to sulfa drugs."],
     "40 mg is a common dose; take it once a day.",
     lambda n: len(notes_match(n, r"lisinopril")) == 1 and bool(notes_match(n, r"40")) and bool(notes_match(n, r"sulfa"))),
    ("file-matters", "The EU2200i manual in my files is for the generator I actually own, so trust it over general advice.",
     [], "Got it.", lambda n: bool(notes_match(n, r"EU2200i"))),
    ("preference", "Call me Sam, and keep your answers really short.", [], "Will do, Sam.",
     lambda n: bool(notes_match(n, r"sam")) or bool(notes_match(n, r"short"))),
]


def run_steps(model, library, caps):
    rows = []
    for cid, said, before, answer, check in STEP_CASES:
        with tempfile.TemporaryDirectory() as tmp:
            memory = Memory(os.path.join(tmp, "survival-llm-memory.txt"), os.path.join(tmp, "settings.json"))
            memory.save_text("\n".join(before))
            prompt = system_prompt(library, "tools" in caps) + "\n\n" + prompt_section(
                before, True, FILES if cid == "file-matters" else [], datetime.date.today())
            messages = [{"role": "system", "content": prompt}, {"role": "user", "content": said},
                        {"role": "assistant", "content": answer}]
            start = time.time()
            options = {"num_ctx": 32768, "num_predict": 600}
            events = [e for e in memory_check(model, messages, memory, think_setting(model, caps), options)
                      if e["type"] == "memory"]
            notes = memory.notes()
        rows.append({"id": cid, "ok": check(notes), "notes": notes, "events": events, "secs": round(time.time() - start, 1)})
        print(f"  {'PASS' if rows[-1]['ok'] else 'FAIL'}  {cid:13} {rows[-1]['secs']:5.1f}s  notes: {notes}", flush=True)
    print(f"  {sum(r['ok'] for r in rows)}/{len(rows)} memory-step cases passed", flush=True)
    return rows


def run_case(model, library, caps, case):
    with tempfile.TemporaryDirectory() as tmp:
        memory = Memory(os.path.join(tmp, "survival-llm-memory.txt"), os.path.join(tmp, "settings.json"))
        if case.get("notes"):
            memory.save_text("\n".join(case["notes"]))
        memory.set_ai_writes(case.get("ai_writes", True))
        files = FILES if case.get("files") else []
        result = {"answers": [], "events": [], "memory_events": [], "markup": False, "searched": False}
        messages = None
        for turn in case["turns"]:
            if turn is None or messages is None:
                tools = "tools" in caps
                extra = prompt_section(memory.notes(), memory.ai_writes, files, datetime.date.today())
                messages = [{"role": "system", "content": system_prompt(library, tools) + "\n\n" + extra}]
                if turn is None:
                    continue
            messages.append({"role": "user", "content": turn})
            done = {}
            for ev in chat_events(model, library, messages, caps=caps, memory=memory):
                if ev["type"] == "done":
                    done = ev
                elif ev["type"] not in ("token", "tick", "thinking"):
                    result["events"].append(ev)
                    result["memory_events"] += [ev] if ev["type"] == "memory" else []
                    result["searched"] = result["searched"] or ev["type"] == "search"
            answer = done.get("answer", "")
            result["answers"].append(answer)
            result["markup"] = result["markup"] or bool(re.search(r"<tool_call|<function|\bremember\(", answer))
        result["notes"] = memory.notes()
    ok, why = case["check"](result)
    return {"id": case["id"], "ok": ok, "info": bool(case.get("info")), "why": why, **result}


def main(models, rest=0, step=False):
    library = Library()
    out_dir = os.path.join(ROOT, "evals", "results", "memory")
    os.makedirs(out_dir, exist_ok=True)
    for model in models:
        caps = capabilities(model)
        print(f"== {model}", flush=True)
        if step:
            rows = run_steps(model, library, caps)
            with open(os.path.join(out_dir, "step-" + model.replace(":", "_").replace("/", "_") + ".jsonl"), "w") as f:
                f.writelines(json.dumps(r) + "\n" for r in rows)
            continue
        rows = []
        path = os.path.join(out_dir, model.replace(":", "_").replace("/", "_") + ".jsonl")
        with open(path, "w") as f:
            for case in CASES:
                start = time.time()
                try:
                    row = run_case(model, library, caps, case)
                except Exception as exc:  # a broken tool call fails the case, not the run
                    row = {"id": case["id"], "ok": False, "info": bool(case.get("info")), "why": f"error: {exc}"}
                row["secs"] = round(time.time() - start, 1)
                rows.append(row)
                f.write(json.dumps(row) + "\n")
                f.flush()
                mark = ("info " if row["info"] else "") + ("PASS" if row["ok"] else "FAIL")
                print(f"  {mark:9} {row['id']:16} {row['secs']:6.1f}s  notes: {row.get('notes')}", flush=True)
                if rest:
                    time.sleep(rest)
        graded = [r for r in rows if not r["info"]]
        print(f"  {sum(r['ok'] for r in graded)}/{len(graded)} graded cases passed", flush=True)


if __name__ == "__main__":
    args = sys.argv[1:]
    rest = 0
    if "--rest" in args:
        i = args.index("--rest")
        rest = float(args[i + 1])
        del args[i:i + 2]
    step = "--step" in args
    main([a for a in args if a != "--step"], rest, step)
