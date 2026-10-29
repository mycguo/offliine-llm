"""Checks from the 0.3.1 Windows test: the memory step must not save the person's question as a note, and a Sources
line made of tool calls (written instead of looking anything up) must not reach the answer.
No Ollama: a fake model (core._stream) and a fake library.

Usage: .venv/bin/python tests/windows031_test.py"""
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


rounds = []  # what the fake model streams on each call


def fake_stream(model, messages, tools, think, options):
    yield from rounds.pop(0)
    yield {"done": True, "eval_count": 1, "eval_duration": 1}


core._stream = fake_stream


def say(text):
    return {"message": {"content": text}}


def call(name, args):
    return {"message": {"content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}}


# ---- the memory step keeps facts about the person, not their questions ----
def remembered(said, reply, notes=()):
    d = tempfile.mkdtemp()
    m = Memory(os.path.join(d, "m.txt"), os.path.join(d, "s.json"))
    m.save_text("\n".join(notes))
    rounds[:] = [[say(reply)]]
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": said}, {"role": "assistant", "content": "ok"}]
    list(core.memory_check("nemotron", msgs, m, None, {}))
    return m.notes()


GEN_Q = "Where can I safely run a generator?"
check("the question itself isn't saved (Windows: 2 of 2 times)",
      remembered(GEN_Q, '{"forget": [], "change": [], "remember": ["Where can I safely run a generator?"]}') == [])
check("nor the question without its question mark",
      remembered(GEN_Q, '{"forget": [], "change": [], "remember": ["Where can I safely run a generator"]}') == [])
check("a fact said on the way to a question is still saved",
      remembered("I have a Honda generator. Where can I run it?",
                 '{"forget": [], "change": [], "remember": ["Has a Honda generator."]}') == ["Has a Honda generator."])
got = remembered("My son Leo is 8 and has asthma. Can he use a rescue inhaler in the cold?",
                 '{"forget": [], "change": [], "remember": ["Son Leo (8) has asthma.", '
                 '"Can he use a rescue inhaler in the cold?"]}')
check("the fact stays, the question goes", got == ["Son Leo (8) has asthma."], got)
got = remembered("Is my generator safe in the garage?",
                 '{"forget": [], "change": [{"old": "Has a Honda generator.", "new": "Is my generator safe in the garage?"}], '
                 '"remember": []}', notes=["Has a Honda generator."])
check("a note isn't changed into a question", got == ["Has a Honda generator."], got)
check("a statement with no question nearby is kept",
      remembered("My daughter Maya is 7 and allergic to penicillin.",
                 '{"forget": [], "change": [], "remember": ["Daughter Maya (7) is allergic to penicillin."]}')
      == ["Daughter Maya (7) is allergic to penicillin."])
for note, said, want in [("Where can I safely run a generator?", GEN_Q, True),
                         ("“Where can I safely run a generator?”", GEN_Q, True),
                         ("Where can I safely run a generator", GEN_Q, True),
                         ("Has a Honda generator.", "I have a Honda generator. Where can I run it?", False),
                         ("Allergic to penicillin.", "Can you remember that I'm allergic to penicillin?", False),
                         ("Lives in Austin, Texas.", "We live in Austin, Texas. What should I do in a heat wave?", False)]:
    check(f"_is_question({note!r}) == {want}", core._is_question(note, said) == want, core._is_question(note, said))


# ---- Sources lines made of tool calls ----
class Lib:
    has_personal = False

    def labels(self):
        return ["Ready.gov"]

    def search(self, query, topic="any", limit=8):
        return [{"id": 1403312, "title": "Generator safety", "source": "Ready.gov", "official": True,
                 "snippet": "Never run a generator in a garage."}]

    def read(self, pid, max_chars=5000):
        return {"title": "Generator safety", "source": "Ready.gov", "official": True, "url": "",
                "text": "Never run a generator in a garage, even with the door open.", "next_id": None}


def answer(steps):
    rounds[:] = steps
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "How long is fridge food safe in a blackout?"}]
    return next(e for e in core.chat_events("nemotron", Lib(), msgs, caps={"tools"}) if e["type"] == "done")["answer"]


FAKE = ("Sources: search_library (generator carbon monoxide garage), search_library (food safety power outage), "
        "read_article (FEMA power outage food safety)")
BODY = "Keep the fridge door closed. Food stays safe for about 4 hours."
got = answer([[say(f"{FAKE}\n\n{BODY}\n\nSources: none (from my own knowledge)")]])
check("the tool-call Sources line at the top goes (Windows, Think harder off)",
      "search_library" not in got and "read_article" not in got and got.startswith("Keep the fridge"), got)
check("one Sources line is left: the model's own, saying it used none", got.count("Sources:") == 1 and got.endswith(
      "Sources: none (from my own knowledge)"), got)
got = answer([[say(f"{BODY}\n\n{FAKE}")]])
check("a tool-call Sources line at the end is replaced too",
      "search_library" not in got and got.endswith("Sources: none (from its own knowledge)"), got)
got = answer([[call("search_library", {"query": "generator garage"})], [call("read_article", {"id": 1403312})],
              [say("Never run it in a garage.\n\nSources: read_article (Generator safety, Ready.gov)")]])
check("after a real lookup, the line names what it read", got.endswith("Sources: Generator safety (Ready.gov)"), got)
got = answer([[call("search_library", {"query": "generator garage"})], [call("read_article", {"id": 1403312})],
              [say("Never run it in a garage.\n\nSources: Generator safety (Ready.gov)")]])
check("a normal Sources line stays as it was", got == "Never run it in a garage.\n\nSources: Generator safety (Ready.gov)", got)

print(f"\n{len(FAILS)} failed" if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
