"""Memory checks from the third code review: negative safety facts kept, what's left of a forgotten note kept,
forget phrasings, and the memory step for follow-ups that name someone in the notes. No Ollama.

Usage: .venv/bin/python tests/review3_test.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core  # noqa: E402
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


GEN = "Has a Honda EU2200i generator."
CATS = "Has two cats."

# 1/10: a partial-forget rewrite that keeps a negative fact keeps it
for notes, said, old, new in [
    (["Son Leo (8) has asthma and is not vaccinated against tetanus."], "Forget about Leo's asthma, he grew out of it.",
     None, "Son Leo (8) is not vaccinated against tetanus."),
    (["Son Leo (8) has asthma and no drug allergies."], "Forget about Leo's asthma.", None,
     "Son Leo (8) has no drug allergies."),
    (["Son Leo (8) has asthma and has never had chickenpox."], "Please forget Leo's asthma.", None,
     "Son Leo (8) has never had chickenpox."),
    (["Takes ibuprofen for back pain."], "Forget the ibuprofen, my doctor says I can't take it anymore.", None,
     "Can't take ibuprofen anymore."),
    (["Maya can have ibuprofen."], "Forget that Maya can have ibuprofen, she can't.", None, "Maya can't have ibuprofen."),
    (["Takes warfarin and can't take aspirin."], "Forget the warfarin, I stopped it.", None, "Can't take aspirin."),
    (["Daughter Maya (7) is allergic to penicillin and can't have peanuts."], "Forget the penicillin allergy.", None,
     "Daughter Maya (7) can't have peanuts."),
]:
    got = run(notes + [CATS], said, P(change=[(notes[0], new)]))
    check(f"1 kept: {new!r}", got == [new, CATS], got)

# 2/11/13: negated safety facts under remember are saved
for notes, said, forget, rem in [
    (["Daughter Maya (7) goes to Lincoln school."], "Forget about Maya's school. She can't have peanuts, though.",
     ["Daughter Maya (7) goes to Lincoln school."], "Maya can't have peanuts."),
    (["Takes warfarin."], "Forget that I take warfarin. I can't take aspirin either.", ["Takes warfarin."],
     "Can't take aspirin."),
    (["Son Leo (8) goes to Lincoln Elementary."], "Forget my son's school. He can't take ibuprofen, it makes his asthma worse.",
     ["Son Leo (8) goes to Lincoln Elementary."], "Son Leo can't take ibuprofen (worsens his asthma)."),
    (["Daughter Maya (7) is allergic to penicillin and can't have peanuts."], "Forget the penicillin allergy.",
     ["Daughter Maya (7) is allergic to penicillin and can't have peanuts."], "Daughter Maya (7) can't have peanuts."),
    (["Takes warfarin and can't take aspirin."], "Forget the warfarin, I stopped it.",
     ["Takes warfarin and can't take aspirin."], "Can't take aspirin."),
    (["Takes ibuprofen for back pain."], "Forget the ibuprofen, my doctor says I can't take it anymore.",
     ["Takes ibuprofen for back pain."], "Can't take ibuprofen."),
]:
    got = run(notes + [CATS], said, P(forget=forget, remember=[rem]))
    check(f"2 saved: {rem!r}", rem in got and not any(n == notes[0] for n in got), got)

# 3/12: forget the whole note + remember what's left keeps what's left
for note, said, rem in [
    ("Maya takes amoxicillin and is allergic to penicillin.", "Forget that Maya takes amoxicillin.",
     "Maya is allergic to penicillin."),
    ("Takes warfarin and metformin.", "Forget that I take warfarin.", "Takes metformin."),
    ("Son Leo (8) goes to Lincoln Elementary and has asthma.", "Forget what I said about my son's school.",
     "Son Leo (8) has asthma."),
    ("Daughter Maya (7) is allergic to penicillin.", "Forget Maya's age.", "Daughter Maya is allergic to penicillin."),
    ("Wife Ana takes insulin.", "Please forget my wife's name.", "Wife takes insulin."),
]:
    got = run([note, CATS], said, P(forget=[note], remember=[rem]))
    check(f"3 rest kept: {rem!r}", got == [rem, CATS], got)
got = run([GEN, CATS], "Forget about my generator.", P(forget=[GEN], remember=[GEN]))
check("3 echo of the forgotten note isn't saved back", got == [CATS], got)

# 4/15: a change that won't replace the note doesn't cancel the forget
got = run([GEN, CATS], "Forget the Honda generator, I sold it. I use a Champion 4500 now.",
          P(forget=[GEN], change=[(GEN, "Has a Honda EU2200i generator. Uses a Champion 4500 generator.")]))
check("4 tacked-on change: Honda gone, Champion saved", GEN not in got and "Uses a Champion 4500 generator." in got, got)
got = run(["Takes warfarin.", CATS], "Forget the warfarin, I stopped it. I'm on Eliquis now.",
          P(forget=["Takes warfarin."], change=[("Takes warfarin.", "Takes Eliquis now.")]))
check("4 unusable rewrite: warfarin still forgotten", "Takes warfarin." not in got, got)
got = run(["Lives in Austin, Texas.", CATS], "Please forget my old address.",
          P(forget=["Lives in Austin, Texas."], change=[("Lives in Austin, Texas.", "Lives in Austin, Texas.")]))
check("4 echo change: address still forgotten", got == [CATS], got)

# 7/14: the "Has a generator." rewrite with wider or unpunctuated requests
for said in ["Forget about my old generator.", "Forget my Honda generator, it's gone.",
             "Forget about my generator since I sold it.", "Forget my generator I sold it",
             "forget about my generator I sold it last week", "Forget my generator we dont have it anymore"]:
    got = run([GEN, CATS], said, P(change=[(GEN, "Has a generator.")]))
    check(f"7 'Has a generator.' deleted: {said!r}", got == [CATS], got)
got = run(["Takes warfarin and metformin.", CATS], "forget the warfarin I take metformin now",
          P(change=[("Takes warfarin and metformin.", "Takes metformin.")]))
check("14 unpunctuated: metformin kept", got == ["Takes metformin.", CATS], got)
got = run([GEN, CATS], "Please forget the generator and the cats.",
          P(change=[(GEN, "Has a generator.")], forget=[CATS]))
check("7 two items: both gone", got == [], got)
got = run([GEN, CATS], "Forget the brand of my generator.", P(change=[(GEN, "Has a generator.")]))
check("7 brand only: 'Has a generator.' kept", got == ["Has a generator.", CATS], got)

# the three Windows shapes still delete
for new in ["Does not have a Honda EU2200i generator.", "Has no generator mentioned.", "Has a generator.",
            "Forget about my generator."]:
    got = run([GEN, CATS], "Forget about my generator.", P(change=[(GEN, new)]))
    check(f"Windows shape deletes: {new!r}", got == [CATS], got)
got = run([GEN, CATS], "Forget about my generator.", P(remember=["Does not have a generator anymore."]))
check("remember 'Does not have a generator anymore.': generator forgotten", got == [CATS], got)

# 5/6/9/16: phrasing
for said, want in [("Make a note that Maya is allergic to penicillin.", True), ("Please note Maya is allergic to penicillin.", True),
                   ("Add to your notes that Leo has asthma.", True), ("Write down that Leo has asthma.", True),
                   ("Keep a note that Ana takes insulin.", True), ("Take note: Ana takes insulin.", True),
                   ("Write down the steps for CPR for me.", False), ("Jot down a packing list for a 72 hour kit.", False),
                   ("Take note of what symptoms to watch for?", False),
                   ("Delete the generator note.", True), ("Can you delete the generator note?", True),
                   ("Remove the cat note.", True), ("Get rid of the generator note.", True), ("Delete the last note.", True),
                   ("Clear my notes about the generator.", True), ("Clear your memory.", True), ("Erase your memory.", True),
                   ("Please clear your memory.", True), ("Wipe your memory.", True), ("Clear all your notes.", True),
                   ("Erase all notes.", True), ("Reset your memory.", True), ("Erase everything about my generator.", True),
                   ("Clear the memory foam mattress of mold?", False), ("How do I clear the memory on my camera?", False),
                   ("Note the color of the smoke.", False)]:
    check(f"asked_memory({said!r}) == {want}", asked_memory(said) == want, asked_memory(said))

# 8: undo puts a deleted note back next to its nearest neighbour
d = tempfile.mkdtemp()
m = Memory(os.path.join(d, "m.txt"), os.path.join(d, "s.json"))
m.save_text("A one\nB two\nC three\nD four")
evs = list(m.apply({"forget": ["B two"], "change": [{"old": "C three", "new": "C three hundred"}], "remember": []}))
e = next(e for e in evs if e["action"] == "delete")
m.undo("delete", "", e["old"], e.get("at"), e.get("before", []), e.get("after", []))
check("8 undo next to its nearest neighbour", m.notes() == ["A one", "B two", "C three hundred", "D four"], m.notes())


# 17: follow-ups that name someone from the notes, or he/she after a family message, get the memory step
class Mem:
    ai_writes = True

    def __init__(self, notes):
        self._notes, self.ran = notes, 0

    def notes(self):
        return self._notes

    def apply(self, plan):
        self.ran += 1
        return iter(())


def fake_stream(model, messages, tools, think, options):
    yield {"message": {"content": "OK." if tools is not None or "Survival LLM" not in messages[-1]["content"]
                       else '{"forget": [], "change": [], "remember": []}'}}
    yield {"done": True, "eval_count": 1, "eval_duration": 1}


core._stream = fake_stream
for history, q, notes, want in [
    ([], "Leo is allergic to peanuts too. Is that a problem for the MREs?", ["Son Leo (8) has asthma."], True),
    (["My son Leo is 8 and has asthma."], "Actually he's 9, not 8. How much ibuprofen can he have?", [], True),
    ([], "Ana is 30 weeks pregnant. Can she take Tylenol?", ["Wife Ana takes insulin."], True),
    ([], "How long does water need to boil?", ["Son Leo (8) has asthma."], False),
    ([], "What does the Red Cross say about generators?", ["Has a Honda EU2200i generator."], False),
]:
    mm = Mem(notes)
    msgs = [{"role": "system", "content": "sys"}]
    for h in history:
        msgs += [{"role": "user", "content": h}, {"role": "assistant", "content": "ok"}]
    msgs.append({"role": "user", "content": q})
    list(core.chat_events("nemotron", None, msgs, caps=set(), memory=mm))
    check(f"17 memory step for {q!r} == {want}", (mm.ran == 1) == want, mm.ran)

print(f"\n{len(FAILS)} failed" if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
