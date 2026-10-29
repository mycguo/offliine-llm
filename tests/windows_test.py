"""Memory and answer checks from the 0.3.0 Windows test and the first code review: forget requests, the forget
guard's false triggers, undo, locked and unreadable files, unseen article ids, last-round tool calls and Sources lines.
No Ollama: a fake model (core._stream) and a fake library.

Usage: .venv/bin/python tests/windows_test.py"""
import errno
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core  # noqa: E402
import memory as mem  # noqa: E402
from memory import Memory, asked_memory, forgets  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"))
    if not ok:
        FAILS.append(name)


def P(forget=(), change=(), remember=()):
    return {"forget": list(forget), "change": [{"old": o, "new": n} for o, n in change], "remember": list(remember)}


def run(notes, said, plan):
    """Apply a model plan the way core.memory_check does, on a temp memory; return the notes after."""
    d = tempfile.mkdtemp()
    m = Memory(os.path.join(d, "m.txt"), os.path.join(d, "s.json"))
    m.save_text("\n".join(notes))
    list(m.apply(forgets(plan, said, m.notes())))
    return m.notes()


GEN = "Has a Honda EU2200i generator"
BASE = ["Daughter Maya is allergic to penicillin.", "The family emergency plan is the most important of their files.", GEN]

# ---- the Windows failure: forget rewrites ----
for new in ["Does not have a Honda EU2200i generator.", "Has no generator mentioned.", "Has a generator.",
            "Forget about my generator."]:
    after = run(BASE, "Forget about my generator.", P(change=[(GEN + ".", new)]))
    check(f"forget rewrite {new!r} deletes", after == BASE[:2], after)
check("forget, empty plan: the one generator note goes", run(BASE, "Forget about my generator.", P()) == BASE[:2])
check("delete-note request, empty plan: the generator note goes",
      run(BASE, "Please delete the note about my generator from your memory.", P()) == BASE[:2])
check("Delete the generator note., empty plan", run(BASE, "Delete the generator note.", P()) == BASE[:2])
check("delete-note request, subset change", run(BASE, "Please delete the note about my generator from your memory.",
                                                 P(change=[(GEN, "Has a generator.")])) == BASE[:2])
check("forget + champion stays a change",
      run([GEN], "Forget the Honda generator, I sold it. I use a Champion 4500 now.",
          P(change=[(GEN, "Has a Champion 4500 generator.")])) == ["Has a Champion 4500 generator."])

# ---- review 13/14/20/22: false forget triggers ----
for said, notes in [
    ("Don't forget I'm allergic to penicillin.", ["Allergic to penicillin.", "Has two cats."]),
    ("I always forget how much Tylenol to give my daughter. She weighs 50 pounds.",
     ["Daughter Maya (7) is allergic to penicillin.", "Has two cats."]),
    ("How do I remove the memory card from my camera?", ["Has a Canon camera.", "Has two cats."]),
    ("How do I remove mold from my memory foam mattress?", ["Sleeps on a memory foam mattress in the basement."]),
    ("Please forget what I said about my son's school.", ["Son Leo (8) has asthma.", "Has two cats."]),
    ("Please forget my old address, I don't live there anymore.", ["Can't eat gluten (celiac).", "Has two cats."]),
    ("Don't forget Maya is allergic to penicillin.", ["Daughter Maya (7) is allergic to penicillin.",
                                                     "Keeps 20 gallons of water in the garage.", GEN]),
    ("I forget whether we have a fire extinguisher.", ["Has a fire extinguisher in the kitchen.", "Has two cats."]),
    ("Forget it.", ["Has two cats."]),
]:
    check(f"no deletion: {said!r}", run(notes, said, P()) == notes, run(notes, said, P()))

# ---- review 15/21: negated facts in a (non-)forget message ----
check("don't forget + can't take ibuprofen saved, asthma kept",
      run(["Son Leo (8) has asthma.", "Has two cats."], "Please don't forget that my son can't take ibuprofen.",
          P(remember=["Son can't take ibuprofen."])) == ["Son Leo (8) has asthma.", "Has two cats.",
                                                         "Son can't take ibuprofen."])
check("forget dairy, keep the new gluten fact",
      run(["Can't have dairy.", "Has two cats."], "Forget the note about dairy. I can't eat gluten though, I have celiac.",
          P(forget=["Can't have dairy."], remember=["Can't eat gluten (celiac)."])) ==
      ["Has two cats.", "Can't eat gluten (celiac)."])
check("don't forget NSAIDs saved",
      run([], "Don't forget that I can't take NSAIDs, I have an ulcer.",
          P(remember=["Is not able to take NSAIDs (stomach ulcer)."])) == ["Is not able to take NSAIDs (stomach ulcer)."])
check("don't forget cephalexin change applied",
      run(["Daughter Maya takes amoxicillin."], "Don't forget Maya is on cephalexin now, not amoxicillin.",
          P(change=[("Daughter Maya takes amoxicillin.", "Daughter Maya takes cephalexin, not amoxicillin.")])) ==
      ["Daughter Maya takes cephalexin, not amoxicillin."])

# ---- review 16/17: real updates inside a forget request ----
check("forget warfarin -> Eliquis kept",
      run(["Takes warfarin."], "Forget the warfarin, I stopped it. I'm on Eliquis now.",
          P(change=[("Takes warfarin.", "No longer takes warfarin; now takes Eliquis.")])) ==
      ["No longer takes warfarin; now takes Eliquis."])
check("forget Austin -> Denver kept",
      run(["Lives in Austin, Texas."], "Forget Austin, we moved to Denver.",
          P(change=[("Lives in Austin, Texas.", "Lives in Denver, no longer in Austin.")])) ==
      ["Lives in Denver, no longer in Austin."])
check("forget cats keeps the dog",
      run(["Has two cats and a dog named Rex."], "Forget about the cats, they went to my sister.",
          P(change=[("Has two cats and a dog named Rex.", "Has a dog named Rex.")])) == ["Has a dog named Rex."])
check("forget warfarin keeps metformin",
      run(["Takes warfarin and metformin."], "Forget the warfarin.",
          P(change=[("Takes warfarin and metformin.", "Takes metformin.")])) == ["Takes metformin."])

# ---- review 18: one note named twice ----
notes = ["Wife Ana takes insulin.", "Wife Ana takes lisinopril.", "Has two cats."]
check("forget+change of one note deletes only it",
      run(notes, "Forget that Ana takes insulin, she stopped.",
          P(forget=["Wife Ana takes insulin."], change=[("Wife Ana takes insulin.", "Wife Ana no longer takes insulin.")]))
      == ["Wife Ana takes lisinopril.", "Has two cats."])
notes = [GEN, "Honda EU2200i generator manual is in the garage drawer."]
check("generator forget doesn't take the manual note",
      run(notes, "Forget about my generator.", P(forget=[GEN], change=[(GEN, "Does not have a Honda EU2200i generator.")]))
      == notes[1:], run(notes, "Forget about my generator.",
                         P(forget=[GEN], change=[(GEN, "Does not have a Honda EU2200i generator.")])))

# ---- review 19: undo order after deleting several ----
d = tempfile.mkdtemp()
m = Memory(os.path.join(d, "m.txt"), os.path.join(d, "s.json"))
m.save_text("A one\nB two\nC three\nD four\nE five")
evs = list(m.apply({"forget": ["B two", "D four"], "change": [], "remember": []}))
for e in evs:
    m.undo("delete", "", e["old"], e.get("at"), e.get("before", []), e.get("after", []))
check("undo B then D restores order", m.notes() == ["A one", "B two", "C three", "D four", "E five"], m.notes())
m.save_text("A one\nB two\nC three\nD four\nE five")
evs = list(m.apply({"forget": ["B two", "D four"], "change": [], "remember": []}))
for e in reversed(evs):
    m.undo("delete", "", e["old"], e.get("at"), e.get("before", []), e.get("after", []))
check("undo D then B restores order", m.notes() == ["A one", "B two", "C three", "D four", "E five"], m.notes())
m.save_text("A one\nB two")
e = m.change("B two", "")
m.undo("delete", "", e["old"], e.get("at"), e.get("before", []), e.get("after", []))
check("undo last note goes back at the end", m.notes() == ["A one", "B two"], m.notes())

# ---- asked_memory ----
for said, want in [("Remember, Maya can't have penicillin.", True), ("I don't remember the dose.", False),
                   ("Forget about my generator.", True), ("Please delete the note about my generator from your memory.", True),
                   ("Don't forget I'm diabetic.", True), ("I always forget my keys.", False),
                   ("How do I remove the memory card?", False), ("Can you forget where I live?", True),
                   ("OK, forget the cats.", True)]:
    check(f"asked_memory({said!r}) == {want}", asked_memory(said) == want, asked_memory(said))

# ---- review 24: a writer must not save over notes it couldn't read ----
d = tempfile.mkdtemp()
m = Memory(os.path.join(d, "m.txt"), os.path.join(d, "s.json"))
m.save_text("Keeps 20 gallons of water.\nHas two cats.")
os.chmod(m.path, 0)
try:
    m.add("Has a dog.")
    check("add with unreadable file raises", False, "no exception")
except PermissionError:
    check("add with unreadable file raises", True)
os.chmod(m.path, 0o600)
check("notes intact after failed add", m.notes() == ["Keeps 20 gallons of water.", "Has two cats."], m.notes())

# ---- _write_file removes .part on a failed write ----
real_fsync = os.fsync
os.fsync = lambda fd: (_ for _ in ()).throw(OSError(errno.ENOSPC, "No space left on device"))
try:
    mem._write_file(m.path, "x")
except OSError:
    pass
os.fsync = real_fsync
check("no .part left after a full disk", not os.path.exists(m.path + ".part"))
check("notes intact after full disk", m.notes() == ["Keeps 20 gallons of water.", "Has two cats."], m.notes())


# ---- core: fake model and library ----
class Lib:
    has_personal = False

    def search(self, q, topic=None, limit=8):
        base = 1403312 if "water" in q else 1500000 if "generator" in q else 2000000 + abs(hash(q)) % 100000
        return [{"id": base + i, "title": "Water storage" if base + i == 1403312 else
                 "Generator Safety" if base + i == 1500000 else f"T{base + i}", "source": "Ready.gov", "official": True,
                 "snippet": "x" * 360} for i in range(8)]

    def read(self, pid, max_chars=5000):
        title = {1403312: "Water storage", 1500000: "Generator Safety"}.get(pid, f"T{pid}")
        return {"title": title, "source": "Ready.gov", "official": True, "url": "", "text": "y" * 5000,
                "next_id": None}


script = []


def fake_stream(model, messages, tools, think, options):
    step = script.pop(0)
    if step[0] == "call":
        yield {"message": {"content": step[3] if len(step) > 3 else "",
                           "tool_calls": [{"function": {"name": step[1], "arguments": step[2]}}]}}
    else:
        for piece in [step[1][:10], step[1][10:]]:
            yield {"message": {"content": piece}}
    yield {"done": True, "eval_count": 1, "eval_duration": 1}


core._stream = fake_stream


def ask(messages, q, steps):
    script[:] = list(steps)
    messages.append({"role": "user", "content": q})
    core.trim(messages)
    return list(core.chat_events("nemotron", Lib(), messages, caps={"tools"}))


def done(evs):
    return next(e for e in evs if e["type"] == "done")


# review 1/11: ids from earlier questions after trim
msgs = [{"role": "system", "content": "sys"}]
ask(msgs, "How do I store water?", [("call", "search_library", {"query": "water storage"}),
                                    ("call", "read_article", {"id": 1403312}),
                                    ("say", "Store a gallon a day.\n\nSources: Water storage (Ready.gov)")])
for i in range(8):
    base = 2000000 + abs(hash(f"topic{i}")) % 100000
    ask(msgs, f"Q{i}", [("call", "search_library", {"query": f"topic{i}"}), ("call", "read_article", {"id": base}),
                        ("say", f"answer {i}\n\nSources: T{base} (Ready.gov)")])
check("trim blanked the first question", any(m.get("content") == "Water storage (Ready.gov)\n[old result removed]"
                                              for m in msgs))
evs = ask(msgs, "Show me that water article.", [("call", "show_article", {"id": 1403312}), ("say", "Opened it.")])
check("show_article of an article read before trim opens", any(e["type"] == "show" for e in evs),
      [e["type"] for e in evs])

# the original Windows bug: an unseen id is refused
msgs = [{"role": "system", "content": "sys"}]
evs = ask(msgs, "My plan?", [("call", "read_article", {"id": 1777777}), ("call", "search_library", {"query": "water"}),
                             ("call", "read_article", {"id": 1403312}),
                             ("say", "Store water.\n\nSources: Water storage (Ready.gov)")])
check("unseen id refused", [e["title"] for e in evs if e["type"] == "read"] == ["Water storage"],
      [e.get("title") for e in evs if e["type"] == "read"])

# last-round calls are not run; an echoed error isn't shown. Since 0.3.4 (issue #2) such an answer gets one more try,
# with the best passages its searches found; only if that fails too is there nothing to show.
msgs = [{"role": "system", "content": "sys"}]
steps = [("call", "search_library", {"query": f"water {i}"}) for i in range(core.MAX_TOOL_ROUNDS)]
steps.append(("call", "read_article", {"id": "abc"}))
evs = ask(msgs, "Water?", steps + [("say", "Boil it for one minute.")])
check("last-round call dropped: answered from the passages it found",
      not done(evs)["unfinished"] and done(evs)["answer"].startswith("Boil it for one minute.")
      and msgs[-1] == {"role": "assistant", "content": "Boil it for one minute."}, (done(evs), msgs[-1]))
check("the passages it was given show as read, before the retry",
      [e["type"] for e in evs if e["type"] in ("read", "retry")] == ["read"] * 4 + ["retry"],
      [e["type"] for e in evs])
check("the discarded text is kept for the logs", done(evs)["discarded"] == "", done(evs)["discarded"])
evs = ask([{"role": "system", "content": "sys"}], "Water?",
          steps + [("say", '<tool_call>{"name": "search_library"}')])
check("last-round call dropped and the second try fails too: unfinished",
      done(evs)["unfinished"] and done(evs)["answer"] == "", done(evs))
steps = [("call", "search_library", {"query": f"water {i}"}) for i in range(core.MAX_TOOL_ROUNDS)]
steps.append(("call", "search_library", {"query": "more"}, "Let me check one more source on boiling times."))
evs = ask([{"role": "system", "content": "sys"}], "Water?", steps + [("say", "Let me check one more source.")])
check("review 5: preamble before a dropped call, twice, is unfinished", done(evs)["unfinished"], done(evs)["answer"])
evs = ask([{"role": "system", "content": "sys"}], "Water?",
          [("call", "search_library", {"query": "water"}), ("say", core.NO_PASSAGE), ("say", core.NO_PASSAGE)])
check("echoed tool error, twice, is unfinished, answer empty", done(evs)["unfinished"] and done(evs)["answer"] == "",
      done(evs))
evs = ask([{"role": "system", "content": "sys"}], "Water?",
          [("call", "search_library", {"query": "water"}), ("say", core.NO_PASSAGE), ("say", "Store a gallon a day.")])
check("echoed tool error, then an answer from the passages", done(evs)["answer"].startswith("Store a gallon a day."),
      done(evs)["answer"])


def answer_for(text, read_ids=(1403312,), seen_q="water"):
    steps = [("call", "search_library", {"query": seen_q})]
    steps += [("call", "read_article", {"id": i}) for i in read_ids]
    steps.append(("say", text))
    return done(ask([{"role": "system", "content": "sys"}], "Q?", steps))["answer"]


BODY = "Store one gallon per person per day."
cases = [
    # review 2/7: id-only citations
    (BODY + "\n\nSources: [id 1403312]; [id 1403313]", "Sources: Water storage (Ready.gov); T1403313 (Ready.gov)"),
    (BODY + "\n\n**Sources:** (id 1403312)", "Sources: Water storage (Ready.gov)"),
    (BODY + "\n\nSources:\n- [id 1403312]\n- [id 1403313]", "Sources: Water storage (Ready.gov); T1403313 (Ready.gov)"),
    (BODY + "\n\nSources: 1403312 (Ready.gov)", "Sources: Water storage (Ready.gov)"),
    # review 3/10: titles not doubled
    (BODY + "\n\nSources: Water storage [1403312]", "Sources: Water storage"),
    (BODY + "\n\nSources: Water storage (Ready.gov) [1403312]", "Sources: Water storage (Ready.gov)"),
    (BODY + "\n\nSources: Water storage (1403312)", "Sources: Water storage"),
    (BODY + "\n\nSources: Water storage (passage 1403312)", "Sources: Water storage"),
    (BODY + "\n\nSources: Water storage (Ready.gov, id 1403312)", "Sources: Water storage (Ready.gov)"),
    (BODY + "\n\nSources: Water storage, id: 1403312", "Sources: Water storage"),
    (BODY + "\n\nSources: passage 1403312 (Water storage)", "Sources: Water storage (Ready.gov)"),
    (BODY + "\n\nSources: Ready.gov water storage (ids 1403312, 1403313, etc.)", "Sources: Ready.gov water storage"),
    # review 6: an id proves the paraphrased title was read
    (BODY + "\n\nSources: Storing drinking water safely (id 1403312)", "Sources: Storing drinking water safely"),
    # review 8: one per line in the search-result format
    (BODY + "\n\nSources:\n[id 1403312] Water storage (Ready.gov)\n[id 1403313] T1403313 (Ready.gov)",
     "Sources: Water storage (Ready.gov); T1403313 (Ready.gov)"),
    # plain correct line unchanged
    (BODY + "\n\nSources: Water storage (Ready.gov)", "Sources: Water storage (Ready.gov)"),
]
for text, want in cases:
    got = answer_for(text)
    check(f"sources {text.split(chr(10)*2, 1)[1][:60]!r}", got.endswith(want) and got.count("Sources") == 1
          and got.startswith(BODY), got)

# body id remarks go; numbers in text stay
got = answer_for("The Ready.gov articles (ids 1403312, 1403313, etc.) cover it. Boil water (1 minute).\n\n"
                 "Sources: Water storage (Ready.gov)")
check("body id remark removed, (1 minute) kept", "(ids" not in got and "(1 minute)" in got, got)
got = answer_for("Call 1-800-222-1222 (Poison Control). ZIP 94110.\n\nSources: Water storage (Ready.gov)")
check("phone and ZIP untouched", "1-800-222-1222" in got and "94110" in got, got)

# review 9: an ordinary "Source:" label on the last line isn't a citation
for text, keep in [("1. Close the doors.\n2. Heat one room. Source: a wood stove or fireplace, never a grill or camp "
                    "stove indoors.", "never a grill or camp stove indoors"),
                   ("- Drinking water. Sources: the water heater tank, ice cubes, and canned fruit juice.",
                    "the water heater tank, ice cubes")]:
    got = answer_for(text)
    check(f"inline label kept: {keep!r}", keep in got and got.rstrip().endswith("Sources: Water storage (Ready.gov)"), got)
got = answer_for("Store water in clean jugs. Sources: Water storage (Ready.gov)")
check("inline real citation moved to its own line", got == "Store water in clean jugs.\n\nSources: Water storage (Ready.gov)",
      got)

# ---- round 2 ----
# 26: undo trailing notes in order
d = tempfile.mkdtemp()
m = Memory(os.path.join(d, "m.txt"), os.path.join(d, "s.json"))
for order in ("forward", "reverse"):
    m.save_text("A one\nB two\nC three\nD four")
    evs = list(m.apply({"forget": ["C three", "D four"], "change": [], "remember": []}))
    for e in (evs if order == "forward" else list(reversed(evs))):
        m.undo("delete", "", e["old"], e.get("at"), e.get("before", []), e.get("after", []))
    check(f"undo trailing C,D {order}", m.notes() == ["A one", "B two", "C three", "D four"], m.notes())
    m.save_text("A one\nB two\nC three\nD four")
    evs = list(m.apply({"forget": ["B two", "C three", "D four"], "change": [], "remember": []}))
    for e in (evs if order == "forward" else list(reversed(evs))):
        m.undo("delete", "", e["old"], e.get("at"), e.get("before", []), e.get("after", []))
    check(f"undo B,C,D {order}", m.notes() == ["A one", "B two", "C three", "D four"], m.notes())

# 13: a bare Sources heading with plain rows / other bullets
for text in [BODY + "\n\n**Sources:**\nWater storage (Ready.gov)\nT1403313 (Ready.gov)",
             BODY + "\n\nSources:\n– Water storage (Ready.gov)\n– T1403313 (Ready.gov)",
             BODY + "\n\nSources:\n+ Water storage (Ready.gov)\n+ T1403313 (Ready.gov)"]:
    got = answer_for(text)
    check(f"bare heading rows kept as the model's list: {text[len(BODY):][:40]!r}", got == text and got.count("Sources") == 1, got)
got = answer_for(BODY + "\n\nSources:\nMade Up Article (CDC)\nAnother Fake (Mayo)")
check("bare heading with made-up rows replaced whole", got == BODY + "\n\nSources: Water storage (Ready.gov)", got)

# 16/20: real ID numbers in the text stay
got = answer_for("Your State Farm policy (ID 4471203) covers generator damage. Keep your FEMA number [ID: 123456789] "
                 "handy.\n\nSources: Water storage (Ready.gov)")
check("person's own ID numbers kept", "(ID 4471203)" in got and "[ID: 123456789]" in got, got)
got = answer_for("Champion 100891 manual says 20 feet.\n\nSources: Water storage (Ready.gov); T1403313 (Ready.gov)")
check("model number in text kept", "Champion 100891" in got, got)

# 17/19/25: the memory step runs for memory requests without "my", and after an unfinished answer
class Mem:
    ai_writes = True
    def __init__(self): self.ran = 0
    def notes(self): return ["Has a Honda EU2200i generator."]
    def apply(self, plan):
        self.ran += 1
        return iter(())
for q, steps in [("Delete the note about the generator.", [("say", "OK.")]),
                 ("Stop remembering the cats.", [("say", "OK.")]),
                 ("Remember that I have type 1 diabetes.", [("call", "search_library", {"query": "diabetes"}), ("say", ""),
                                                            ("say", "")])]:  # the empty answer gets a second try
    mm = Mem()
    script[:] = list(steps) + [("say", '{"forget": [], "change": [], "remember": []}')]
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": q}]
    evs = list(core.chat_events("nemotron", Lib(), msgs, caps={"tools"}, memory=mm))
    check(f"memory step runs: {q!r}", mm.ran == 1, [e["type"] for e in evs])
mm = Mem()
script[:] = [("say", "Boil it for a minute.")]
list(core.chat_events("nemotron", Lib(), [{"role": "system", "content": "s"}, {"role": "user", "content": "How long to boil water?"}],
                      caps={"tools"}, memory=mm))
check("no memory step for an impersonal question", mm.ran == 0)

# 18: a refused id stays refused after trim
msgs = [{"role": "system", "content": "sys"}]
ask(msgs, "Q1", [("call", "read_article", {"id": 1777777}), ("say", "answer\n\nSources: none (from my own knowledge)")])
for i in range(8):
    base = 2000000 + abs(hash(f"topic{i}")) % 100000
    ask(msgs, f"Q{i}", [("call", "search_library", {"query": f"topic{i}"}), ("call", "read_article", {"id": base}),
                        ("say", f"answer {i}\n\nSources: T{base} (Ready.gov)")])
evs = ask(msgs, "Again?", [("call", "read_article", {"id": 1777777}), ("say", "ok")])
check("refused id still refused after trim", not any(e["type"] == "read" for e in evs), [e["type"] for e in evs])

print(f"\n{len(FAILS)} failed" if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
