"""Checks from the fourth code review, for severe outcomes: notes about different people kept apart, corrections
saved, no similar note hit by mistake, and answer lines under a bare "Sources:" heading kept. No Ollama: a fake
model (core._stream).

Usage: .venv/bin/python tests/review4_test.py"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core  # noqa: E402
from memory import Memory  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"))
    if not ok:
        FAILS.append(name)


REPLY, SCRIPT = [""], []


def fake_stream(model, messages, tools, think, options):
    if "Survival LLM, an offline assistant app" in messages[-1]["content"]:
        yield {"message": {"content": REPLY[0]}}
    elif SCRIPT:
        step = SCRIPT.pop(0)
        if step[0] == "call":
            yield {"message": {"content": "", "tool_calls": [{"function": {"name": step[1], "arguments": step[2]}}]}}
        else:
            yield {"message": {"content": step[1]}}
    else:
        yield {"message": {"content": "OK."}}
    yield {"done": True, "eval_count": 1, "eval_duration": 1}


core._stream = fake_stream
E = {"forget": [], "change": [], "remember": []}


def chat(notes, said, plan):
    d = tempfile.mkdtemp()
    m = Memory(os.path.join(d, "m.txt"), os.path.join(d, "s.json"))
    m.save_text("\n".join(notes))
    REPLY[0] = json.dumps({**E, **plan})
    list(core.chat_events("gemma", None, [{"role": "system", "content": "s"}, {"role": "user", "content": said}],
                          caps=set(), memory=m))
    return m.notes()


CATS = "Has two cats."

# 1. add(): a family member's note must not replace (or block) the person's own health fact
for own, said, rem in [
    ("Allergic to penicillin.", "My daughter Maya is allergic to penicillin too.", "Daughter Maya is allergic to penicillin."),
    ("Allergic to penicillin.", "My daughter Maya isn't allergic to penicillin, unlike me.",
     "Daughter Maya is not allergic to penicillin."),
    ("Takes warfarin.", "My husband takes warfarin as well. Can he have aspirin?", "Husband takes warfarin."),
    ("Has asthma.", "I have asthma but my son Leo doesn't. What goes in his first aid kit?", "Son Leo doesn't have asthma."),
    ("Is 30 weeks pregnant.", "My sister is 30 weeks pregnant too and staying with us.",
     "Sister (30 weeks pregnant) is staying with them."),
]:
    got = chat([own, CATS], said, {"remember": [rem]})
    check(f"1 own note kept, new one saved: {said!r}", own in got and rem in got, got)
for fam, said, rem in [("Daughter Maya (7) is allergic to penicillin.", "I'm allergic to penicillin too.",
                        "Is allergic to penicillin."),
                       ("Wife Ana takes insulin.", "I take insulin too, how long does it keep without a fridge?",
                        "Takes insulin.")]:
    got = chat([fam, CATS], said, {"remember": [rem]})
    check(f"1 own fact saved next to the family one: {said!r}", fam in got and rem in got, got)

# 2. forgets() vaguer: a negated note corrected to the positive is a change, not a deletion
for old, said, new in [
    ("Is not allergic to penicillin.", "Forget that I'm not allergic to penicillin. I had a reaction to it last week.",
     "Is allergic to penicillin."),
    ("Not allergic to penicillin.", "Please forget that I am not allergic to penicillin, I had a reaction yesterday.",
     "Allergic to penicillin."),
    ("Has never had a seizure.", "Forget that I've never had a seizure, I had one last month.", "Has had a seizure."),
]:
    got = chat([old, CATS], said, {"change": [{"old": old, "new": new}]})
    check(f"2 correction saved: {new!r}", got == [new, CATS], got)

# 3. apply's gone filter: a remembered fact isn't dropped for sharing words with what went (other polarity, or the rest)
for notes, said, plan, want in [
    (["Is not allergic to penicillin.", CATS], "Forget that I'm not allergic to penicillin. I had a reaction to it.",
     {"forget": ["Is not allergic to penicillin."], "remember": ["Is allergic to penicillin."]}, "Is allergic to penicillin."),
    (["Allergic to sulfa, not penicillin.", CATS], "Forget what I said about not being allergic to penicillin, I am allergic to it.",
     {"forget": ["Allergic to sulfa, not penicillin."], "remember": ["Allergic to sulfa and penicillin."]},
     "Allergic to sulfa and penicillin."),
    (["Takes warfarin and metformin.", CATS], "My doctor switched me from warfarin to Eliquis.",
     {"change": [{"old": "Takes warfarin and metformin.", "new": "Takes Eliquis."}], "remember": ["Takes metformin."]},
     "Takes metformin."),
]:
    got = chat(notes, said, plan)
    check(f"3 kept: {want!r}", want in got, got)

# 4. _find: a forget/change naming a note that isn't there leaves a different note alone
for notes, said, plan in [
    (["Wife Ana takes insulin.", CATS], "Forget that Ana takes metformin, she stopped.",
     {"forget": ["Wife Ana takes metformin."]}),
    (["Daughter Maya (7) is allergic to penicillin.", CATS], "Forget about Maya's peanut allergy, she outgrew it.",
     {"forget": ["Daughter Maya (7) is allergic to peanuts."]}),
    (["Wife Ana takes insulin.", CATS], "Ana switched from metformin to Ozempic.",
     {"change": [{"old": "Wife Ana takes metformin.", "new": "Wife Ana takes Ozempic."}]}),
    (["Daughter Maya (7) is allergic to penicillin.", CATS], "Maya outgrew her peanut allergy.",
     {"change": [{"old": "Daughter Maya (7) is allergic to peanuts.",
                  "new": "Daughter Maya (7) is no longer allergic to peanuts."}]}),
]:
    got = chat(notes, said, plan)
    check(f"4 other note untouched: {said!r}", notes[0] in got, got)


# 5. Sources: a line of the answer right under a bare "Sources:" list isn't deleted
class Lib:
    has_personal = False

    def search(self, q, topic=None, limit=8):
        return [{"id": 1403312 + i, "title": "Carbon monoxide poisoning" if i == 0 else f"T{1403312 + i}", "source": "CDC",
                 "official": True, "snippet": "x" * 360} for i in range(8)]

    def read(self, pid, max_chars=5000):
        return {"title": "Carbon monoxide poisoning" if pid == 1403312 else f"T{pid}", "source": "CDC",
                "official": True, "url": "", "text": "y" * 5000, "next_id": None}


BODY = "Get everyone out of the house now and leave the generator off."
for tail, keep in [("\n\n**Sources:**\nCarbon monoxide poisoning (CDC)\nIf anyone has a headache, dizziness or confusion, "
                    "call 911 once you are outside.", "call 911 once you are outside"),
                   ("\n\nSources:\nCarbon monoxide poisoning (CDC)\nDon't go back inside until the fire department says "
                    "the air is safe.", "Don't go back inside")]:
    SCRIPT[:] = [("call", "search_library", {"query": "co"}), ("call", "read_article", {"id": 1403312}), ("say", BODY + tail)]
    evs = list(core.chat_events("nemotron", Lib(), [{"role": "system", "content": "s"}, {"role": "user", "content": "Q?"}],
                                caps={"tools"}))
    got = next(e for e in evs if e["type"] == "done")["answer"]
    check(f"5 answer line kept: {keep!r}", keep in got, got)

print(f"\n{len(FAILS)} failed" if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
