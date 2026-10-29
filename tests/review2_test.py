"""Memory checks from the second code review: number corrections, partial forgets, negated rewrites, forget and
remember phrasings. No Ollama.

Usage: .venv/bin/python tests/review2_test.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from memory import Memory, asked_memory, forgets  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"))
    if not ok:
        FAILS.append(name)


def P(forget=(), change=(), remember=()):
    return {"forget": list(forget), "change": [{"old": o, "new": n} for o, n in change], "remember": list(remember)}


def run(notes, said, plan):
    d = tempfile.mkdtemp()
    m = Memory(os.path.join(d, "m.txt"), os.path.join(d, "s.json"))
    m.save_text("\n".join(notes))
    list(m.apply(forgets(plan, said, m.notes())))
    return m.notes()


GEN = "Has a Honda EU2200i generator"
CATS = "Has two cats."

# 1. numbers under 3 characters are invisible to _stems: a corrected number "says less" and becomes a deletion
for notes, said, old, new in [
    (["Takes 5 mg of warfarin daily.", CATS], "Forget the 5 mg, my dose is 10 mg now.", "Takes 5 mg of warfarin daily.",
     "Takes 10 mg of warfarin daily."),
    (["Daughter Maya (8) is allergic to penicillin.", CATS], "Forget what I said about Maya's age, she's 7.",
     "Daughter Maya (8) is allergic to penicillin.", "Daughter Maya (7) is allergic to penicillin."),
    (["Son Leo (9) has asthma.", CATS], "Forget I said that, he's 8.", "Son Leo (9) has asthma.", "Son Leo (8) has asthma."),
]:
    got = run(notes, said, P(change=[(old, new)]))
    check(f"1 number correction kept: {said!r}", got == [new, CATS], got)

# 2. the request names whose fact it is ("my son's school"): a change that keeps the rest is deleted whole
for notes, said, old, new in [
    (["Son Leo (8) goes to Lincoln Elementary and has asthma.", CATS], "Forget what I said about my son's school.",
     "Son Leo (8) goes to Lincoln Elementary and has asthma.", "Son Leo (8) has asthma."),
    (["Daughter Maya (7) is allergic to penicillin.", CATS], "Forget my daughter's age.",
     "Daughter Maya (7) is allergic to penicillin.", "Daughter Maya is allergic to penicillin."),
    (["Wife Ana takes insulin.", CATS], "Remove my wife's name from your notes.", "Wife Ana takes insulin.",
     "Wife takes insulin."),
]:
    got = run(notes, said, P(change=[(old, new)]))
    check(f"2 partial forget keeps the rest: {said!r}", got == [new, CATS], got)

# 3. a negated rewrite that still holds another fact is deleted whole
for notes, said, old, new in [
    (["Takes warfarin and metformin."], "Forget the warfarin.", "Takes warfarin and metformin.",
     "Takes metformin, no longer warfarin."),
    (["Has two cats and a dog named Rex."], "Forget about the dog, he died last month.",
     "Has two cats and a dog named Rex.", "Has two cats and no dog."),
    (["Maya is allergic to penicillin and amoxicillin."],
     "Forget about it, Maya isn't allergic to amoxicillin after all, just penicillin.",
     "Maya is allergic to penicillin and amoxicillin.", "Maya is allergic to penicillin, not amoxicillin."),
]:
    got = run(notes, said, P(change=[(old, new)]))
    check(f"3 negated rewrite keeps the other fact: {said!r}", got == [new], got)

# 4. the empty-plan fallback
got = run(["Last tetanus shot was in 2019.", CATS], "Forget the last thing I told you.", P())
check("4 fallback: 'last thing' doesn't delete the tetanus note", got == ["Last tetanus shot was in 2019.", CATS], got)
got = run(["First aid kit is in the hall closet.", CATS], "Forget the first thing I said.", P())
check("4 fallback: 'first thing' doesn't delete the first aid note", got == ["First aid kit is in the hall closet.", CATS],
      got)
got = run([GEN, "Daughter Maya (7) is allergic to penicillin."],
          "Forget the generator for now, it's out of gas. How long will my fridge stay cold?", P())
check("4 fallback: 'forget X for now' + question deletes nothing", GEN in got, got)
got = run(["Daughter Maya (7) goes to Lincoln school and is allergic to penicillin.", CATS],
          "Forget my daughter's school.", P())
check("4 fallback: doesn't delete a note that also holds an allergy", len(got) == 2, got)
got = run(["Has two cats and a dog named Rex.", "Lives in Austin, Texas."], "Forget that I have a dog.", P())
check("4 fallback: doesn't delete the cats with the dog", any("cats" in n for n in got), got)

# 5. Memory.apply's handled set
got = run(["Takes warfarin and metformin.", CATS], "Forget the warfarin.",
          P(forget=["Takes warfarin and metformin."], change=[("Takes warfarin and metformin.", "Takes metformin.")]))
check("5 forget + real change of the same note keeps the new fact", "Takes metformin." in got, got)
notes = [GEN, "Honda EU2200i generator manual is in the garage drawer."]
got = run(notes, "Forget about my generator.",
          P(forget=[GEN + "."], change=[(GEN, "Does not have a Honda EU2200i generator.")]))
check("5 same note named with and without a period: manual note stays", got == notes[1:], got)
got = run(["Wife Ana takes insulin.", "Wife Ana takes lisinopril."], "Forget that Ana takes insulin.",
          P(forget=["Wife Ana takes insulin.", "Wife Ana takes insulin"]))
check("5 forget listed twice (period differs): lisinopril stays", got == ["Wife Ana takes lisinopril."], got)

# 6. forget requests FORGET_ASK misses: the Windows rewrite is saved, and the page says nothing
for said in ["Just forget about my generator.", "Please, forget about my generator.", "I'd like you to forget my generator.",
             "You can forget about my generator now.", "Can you also forget my generator?",
             "Thanks, and please forget about my generator.", "Erase my generator from memory.", "Delete my generator."]:
    got = run([GEN + ".", CATS], said, P(change=[(GEN + ".", "Does not have a Honda EU2200i generator.")]))
    check(f"6 forget rewrite deletes: {said!r}", got == [CATS], got)
    check(f"6 asked_memory({said!r})", asked_memory(said), False)

# 7. remember requests REMEMBER_ASK misses (e30dad4's ASKED caught them): no "Memory didn't change" when nothing saved
for said in ["Make sure you remember Maya can't have penicillin.", "I'd like you to remember that I'm diabetic.",
             "Just remember that I'm diabetic.", "Please, remember that I'm diabetic."]:
    check(f"7 asked_memory({said!r})", asked_memory(said), False)

# 8. "Save" at a sentence start: a food question gets "Memory didn't change"
for said in ["Save the milk or toss it? Our power was out 8 hours.", "Save or toss: my fridge was off 6 hours, is the chicken ok?"]:
    check(f"8 not a memory request: {said!r}", not asked_memory(said), True)

# 9. a forgotten note restated as gone only under "remember"
got = run([GEN, CATS], "Forget about my generator.", P(remember=["Does not have a generator anymore."]))
check("9 remember-only 'Does not have a generator anymore.': generator forgotten", got == [CATS], got)

# 10. forget the whole note (as the new prompt says) + remember what's left: apply's gone filter drops the rest
got = run(["Takes warfarin and metformin.", CATS], "Forget the warfarin.",
          P(forget=["Takes warfarin and metformin."], remember=["Takes metformin."]))
check("10 remaining fact re-added after forgetting a combined note", "Takes metformin." in got, got)

print(f"\n{len(FAILS)} failed" if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
