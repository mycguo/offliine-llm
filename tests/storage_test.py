"""The memory file and the AI's plans: odd JSON, duplicates, placeholders, questions, today-only facts, a merge with
the person's edits, Windows encodings, a full memory and the off switch. No Ollama.

Usage: .venv/bin/python tests/storage_test.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from memory import Memory, HEADER
import core
d = tempfile.mkdtemp()
def fresh(notes, name):
    m = Memory(os.path.join(d, name + ".txt"), os.path.join(d, name + ".json")); m.save_text("\n".join(notes)); return m
def run(m, reply):
    return [(e["action"], e.get("note") or e.get("old")) for e in m.apply(core.memory_plan(reply))]
checks = []
def check(label, cond, extra=""):
    checks.append(cond)
    if not cond: print("FAIL", label, extra)
m = fresh(["Lives in Austin, Texas.", "Son Leo (9) has type 1 diabetes.", "Has a Honda generator."], "a")
run(m, '{"forget": "Lives in Austin, Texas."}'); check("string forget", m.notes() == ["Son Leo (9) has type 1 diabetes.", "Has a Honda generator."], m.notes())
ev = run(m, '{"remember": "Has a dog."}'); check("string remember", m.notes()[-1] == "Has a dog." and len(ev) == 1, ev)
run(m, '{"forget": ["L", "i", "v"]}'); check("letters", len(m.notes()) == 3)
run(m, '{"remember": true}'); check("bool", len(m.notes()) == 3)
m = fresh(["Daughter Maya is 7."], "b"); run(m, '{"remember": ["Daughter Maya (7) has type 1 diabetes."]}'); check("extend", m.notes() == ["Daughter Maya (7) has type 1 diabetes."], m.notes())
m = fresh(["Daughter Maya (7) is allergic to penicillin."], "c"); run(m, '{"remember": ["Daughter Maya (7) is allergic to peanuts."]}'); check("2nd allergy", len(m.notes()) == 2)
run(m, '{"remember": ["Maya is allergic to penicillin."]}'); check("restate", len(m.notes()) == 2)
m = fresh(["Takes warfarin.", "Has a cat named Rex."], "e")
run(m, '{"change": [{"old": "Takes warfarin.", "new": "No longer takes warfarin; now takes Eliquis."}]}'); check("no longer", "No longer takes warfarin; now takes Eliquis." in m.notes(), m.notes())
run(m, '{"change": [{"old": "Has a cat named Rex.", "new": "No pets; allergic to cats."}]}'); check("no pets", "No pets; allergic to cats." in m.notes())
m = fresh(["Lives at 12 Oak St, Austin."], "f"); run(m, '{"change": [{"old": "Lives in Austin", "new": "No location stored."}]}'); check("placeholder", "No location stored." not in m.notes())
run(m, '{"remember": ["None"]}'); check("None", m.notes() == ["Lives at 12 Oak St, Austin."])
m = fresh(["Has a dog."], "g"); run(m, '{"remember": ["Prefers short answers, with at most one follow-up question."]}'); check("pref", len(m.notes()) == 2)
run(m, '{"remember": ["Asked about food safety in the fridge."]}'); check("question", len(m.notes()) == 2)
run(m, '{"remember": ["Asked to be called Sam."]}'); check("Sam", len(m.notes()) == 3)
run(m, '{"remember": ["Daughter has a rash on her arms now."]}'); check("today", len(m.notes()) == 3)
m = fresh(["#1 rule: insulin lives in the blue cooler", "Has two cats."], "h"); check("#", len(m.notes()) == 2)
m = fresh(["Has a dog.", "Has a cat named Rex."], "i"); ev = run(m, '{"change": [{"old": "Has a cat named Rex.", "new": "has a dog."}]}'); check("dup change", ev == [("delete", "Has a cat named Rex.")], ev)
m.undo("delete", old="Has a cat named Rex."); check("undo", len(m.notes()) == 2)
m = fresh(["Has a Honda EU2200i generator.", "Lives in Austin, Texas."], "j"); base = "\n".join(m.notes())
run(m, '{"forget": ["Lives in Austin, Texas."], "change": [{"old": "Has a Honda EU2200i generator.", "new": "Has a Champion 4500 generator."}]}')
notes, kept, dropped = m.save_text(base + "\nHas a dog.", base); check("merge", notes == ["Has a dog.", "Has a Champion 4500 generator."], notes)
p = os.path.join(d, "k.txt"); open(p, "wb").write(HEADER.encode() + "Lives in K\xf6ln\n".encode("cp1252")); check("cp1252", Memory(p, p + ".json").notes() == ["Lives in Köln"])
open(p, "wb").write((HEADER + "José\n").encode("utf-16")); check("utf16", Memory(p, p + ".json").notes() == ["José"])
m = fresh([f"Note {i}: " + "x" * 280 for i in range(21)], "l"); ev = run(m, '{"remember": ["Has a generator."]}'); check("full", ev == [("full", "Has a generator.")], ev)
m.set_ai_writes(False); check("off", list(m.apply(core.memory_plan('{"remember": ["Has a cat."]}'))) == [])
print(f"{sum(checks)}/{len(checks)} storage checks passed")
sys.exit(0 if all(checks) else 1)
