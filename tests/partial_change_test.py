"""A change that names part of a note changes only that part, and a rewrite keeps separate facts it would drop
(an allergy beside a dose). No Ollama.

Usage: .venv/bin/python tests/partial_change_test.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from memory import Memory, forgets  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"))
    if not ok:
        FAILS.append(name)


def run(notes, said, plan):
    d = tempfile.mkdtemp()
    m = Memory(os.path.join(d, "m.txt"), os.path.join(d, "s.json"))
    m.save_text("\n".join(notes))
    list(m.apply(forgets(plan, said, m.notes())))
    return m.notes()


def C(old, new):
    return {"forget": [], "change": [{"old": old, "new": new}], "remember": []}


got = run(["Takes 20 mg lisinopril and is allergic to sulfa drugs."], "My doctor raised my lisinopril to 40 mg.",
          C("Takes 20 mg lisinopril", "Takes 40 mg lisinopril"))
check("dose spliced, allergy kept", got == ["Takes 40 mg lisinopril and is allergic to sulfa drugs."], got)
got = run(["Daughter Maya (7) weighs 50 lb and is allergic to penicillin."], "Maya weighs 55 lb now.",
          C("Maya weighs 50 lb", "Maya weighs 55 lb"))
check("weight: allergy note kept", any("penicillin" in n for n in got) and any("55" in n for n in got), got)
got = run(["Son Leo (8) has asthma and is allergic to peanuts."], "Leo turned 9.", C("Son Leo (8)", "Son Leo (9)"))
check("age spliced", got == ["Son Leo (9) has asthma and is allergic to peanuts."], got)
got = run(["Takes 20 mg lisinopril and is allergic to sulfa drugs."], "My dose went up.",
          C("Takes 20 mg lisinopril", "Takes 40 mg lisinopril and is allergic to sulfa drugs."))
check("new already whole: no doubling", got == ["Takes 40 mg lisinopril and is allergic to sulfa drugs."], got)
got = run(["Takes 20 mg lisinopril and is allergic to sulfa drugs."], "Forget the lisinopril.",
          {"forget": ["Takes 20 mg lisinopril"], "change": [], "remember": []})
check("partial forget doesn't delete the allergy", any("sulfa" in n for n in got), got)
got = run(["Has a Honda EU2200i generator"], "Forget about my generator.",
          {"forget": ["Has a Honda EU2200i generator."], "change": [], "remember": []})
check("whole-note forget still deletes", got == [], got)
got = run(["Has a dog.", "Takes warfarin."], "", C("Takes warfarin.", "Takes Eliquis."))
check("whole-note change still rewrites", got == ["Has a dog.", "Takes Eliquis."], got)
got = run(["Takes 20 mg lisinopril and is allergic to sulfa drugs."], "My doctor raised my lisinopril to 40 mg.",
          C("Takes 20 mg lisinopril and is allergic to sulfa drugs.", "Takes 40 mg lisinopril."))
check("whole rewrite keeps the allergy", got == ["Takes 40 mg lisinopril and is allergic to sulfa drugs."], got)
got = run(["Son Leo (8) has asthma and is allergic to peanuts."], "Leo is 9 now.",
          C("Son Leo (8) has asthma and is allergic to peanuts.", "Son Leo (9) has asthma."))
check("age rewrite keeps the peanut allergy", got == ["Son Leo (9) has asthma and is allergic to peanuts."], got)
got = run(["Has two cats and has a dog named Rex."], "Forget about the cats, they went to my sister.",
          C("Has two cats and has a dog named Rex.", "Has a dog named Rex."))
check("forget request: dropped part stays dropped", got == ["Has a dog named Rex."], got)
got = run(["Lives in Austin, Texas."], "We moved to Denver.", C("Lives in Austin, Texas.", "Lives in Denver, Colorado."))
check("move: nothing re-added", got == ["Lives in Denver, Colorado."], got)
got = run(["Has asthma and no drug allergies."], "Turns out I'm allergic to penicillin.",
          C("Has asthma and no drug allergies.", "Has asthma and is allergic to penicillin."))
check("correction: old 'no allergies' not re-added", got == ["Has asthma and is allergic to penicillin."], got)
print(f"\n{len(FAILS)} failed" if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
