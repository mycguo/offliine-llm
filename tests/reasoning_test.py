"""The model's reasoning: core.chat_events passes it on as it streams, keeps it out of the answer, the saved chat and
the memory step, and says whether the model was asked to reason; the terminal chat keeps it for /reasoning instead of
printing it. No Ollama: a fake model (core._stream) and a fake library.

Usage: .venv/bin/python tests/reasoning_test.py"""
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import chat  # noqa: E402
import core  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"))
    if not ok:
        FAILS.append(name)


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


class Mem:
    ai_writes = True

    def notes(self):
        return []

    def apply(self, plan):
        return iter(())


rounds, thinks = [], []  # what the fake model streams on each call; the think setting each call got


def fake_stream(model, messages, tools, think, options):
    thinks.append(think)
    yield from rounds.pop(0)
    yield {"done": True, "eval_count": 5, "eval_duration": 1}


core._stream = fake_stream


def thinking(text):
    return {"message": {"thinking": text}}


def call(name, args):
    return {"message": {"content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}}


def say(text):
    return {"message": {"content": text}}


ANSWER = "No. Never run it in a garage, even with the door open.\n\nSources: Generator safety (Ready.gov)"


def script():
    return [[thinking("\n"), thinking("The person asks about a generator."), thinking(" I should search the library.\n\n"),
             call("search_library", {"query": "generator garage"})],
            [thinking("Open the Ready.gov result."), call("read_article", {"id": 1403312})],
            [thinking("It says never in a garage."), say(ANSWER[:20]), say(ANSWER[20:])]]


def ask(q="Can I run a generator in the garage?", model="nemotron", caps=frozenset({"tools", "thinking"}), reasoning="off",
        memory=None):
    rounds[:] = script() + ([[thinking("Secret memory thoughts."), say('{"forget": [], "change": [], "remember": []}')]]
                            if memory else [])
    thinks.clear()
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": q}]
    return list(core.chat_events(model, Lib(), messages, reasoning=reasoning, caps=set(caps), memory=memory)), messages


evs, messages = ask()
first_thinks = list(thinks)
kinds = [e["type"] for e in evs if e["type"] not in ("tick", "token")]
check("reasoning streams in order, between the lookups",
      kinds == ["thinking"] * 3 + ["search", "thinking", "read", "thinking", "done"], kinds)
said = [e["text"] for e in evs if e["type"] == "thinking"]
check("each thinking event carries its text as it came",
      said == ["\n", "The person asks about a generator.", " I should search the library.\n\n",
               "Open the Ready.gov result.", "It says never in a garage."], said)
done = next(e for e in evs if e["type"] == "done")
check("the answer is only what it wrote, not its reasoning", done["answer"] == ANSWER, done["answer"])
check("the answer's tokens carry no reasoning", "".join(e["text"] for e in evs if e["type"] == "token") == ANSWER)
saved = " ".join(m.get("content") or "" for m in messages if m["role"] == "assistant")
check("the saved chat keeps no reasoning", "search the library" not in saved and "thinking" not in str(messages), saved)

# What it was asked: no reasoning (False), reasoning (True or a level), or a model that can't (None)
check("think: False by default for Nemotron", done["think"] is False, done["think"])
check("think: True with Reasoning above Off", next(e for e in ask(reasoning="medium")[0] if e["type"] == "done")["think"] is True)
for model, reasoning, want in [("qwen3.8:27b", "short", "low"), ("qwen3.8:27b", "long", "xhigh"), ("qwen3.8:27b", "none", "xhigh"),
                               ("nemotron", "long", True), ("nemotron", "none", True), ("nemotron", "short", True)]:
    got = core.think_setting(model, {"thinking"}, reasoning)
    check(f"think_setting({model}, {reasoning}) == {want!r}", got == want, got)
check("think: Qwen's brief think by default", next(e for e in ask(model="qwen3.8:27b")[0] if e["type"] == "done")["think"] == "low")
check("think: None for a model that can't reason",
      next(e for e in ask(caps={"tools"})[0] if e["type"] == "done")["think"] is None)
check("each round asked with the same think setting", first_thinks == [False, False, False], first_thinks)

# A chunk with both reasoning and answer text: both come through
rounds[:] = [[{"message": {"thinking": "Short.", "content": "Boil it for one minute."}}]]
evs = list(core.chat_events("nemotron", None, [{"role": "system", "content": "s"}, {"role": "user", "content": "Water?"}],
                            caps={"thinking"}))
check("reasoning and text in one chunk: both kept",
      [(e["type"], e.get("text")) for e in evs if e["type"] in ("thinking", "token")]
      == [("thinking", "Short."), ("token", "Boil it for one minute.")], evs)

# The memory step's reasoning (a JSON side step) is never shown
evs, _ = ask(q="Can I run my generator in the garage?", memory=Mem())
check("the memory step ran", any(e["type"] == "memory_check" for e in evs), [e["type"] for e in evs])
check("the memory step's reasoning isn't passed on", not any("Secret memory" in str(e) for e in evs))

# Terminal: reasoning is kept for /reasoning, with the lookups in between, not printed
rounds[:] = script()
out = io.StringIO()
with contextlib.redirect_stdout(out):
    used, reasoning = chat.run_turn("nemotron", Lib(), [{"role": "system", "content": "s"},
                                                        {"role": "user", "content": "Generator in the garage?"}],
                                    "off", {"tools", "thinking"})
printed = out.getvalue()
check("terminal: the reasoning isn't printed", "search the library" not in printed and "Ready.gov result" not in printed,
      printed)
check("terminal: one '· thinking' line per stretch, the first with the /reasoning hint",
      printed.count("· thinking") == 3 and printed.count("/reasoning") == 1, printed)
check("terminal: /reasoning text, in order with the lookups",
      reasoning == "The person asks about a generator. I should search the library.\n\n· searching: generator garage"
                   "\n\nOpen the Ready.gov result.\n\n· reading: Generator safety (Ready.gov)\n\nIt says never in a garage.",
      reasoning)
check("terminal: still returns the sources", [s["title"] for s in used] == ["Generator safety"], used)
rounds[:] = [[say("Boil it for one minute.")]]
with contextlib.redirect_stdout(io.StringIO()):
    _, reasoning = chat.run_turn("nemotron", None, [{"role": "system", "content": "s"},
                                                    {"role": "user", "content": "Water?"}], "off", set())
check("terminal: no reasoning, nothing for /reasoning", reasoning == "", reasoning)

print(f"\n{len(FAILS)} failed" if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
