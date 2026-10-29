"""0.3.4: reasoning that runs too long stops at a limit and the model answers from what it has; a model asked to look
something up that answers from memory is asked once more, told to search; an answer it never wrote gets a second try
with the passages its searches found, even when it found none. No Ollama: a fake model (core._stream), a fake library.

Usage: .venv/bin/python tests/limits_test.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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


rounds, calls, closed = [], [], []  # what the fake model streams per call; what each call was given; streams closed


def fake_stream(model, messages, tools, think, options):
    calls.append({"tools": bool(tools), "think": think, "last": messages[-1]["content"],
                  "roles": [m["role"] for m in messages], "predict": options.get("num_predict")})
    try:
        yield from rounds.pop(0)
        yield {"done": True, "eval_count": 3, "eval_duration": 1}
    finally:
        closed.append(True)


core._stream = fake_stream


def thinking(n):
    return [{"message": {"thinking": f"step {i}. "}} for i in range(n)]


def say(text):
    return {"message": {"content": text}}


def call(name, args):
    return {"message": {"content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}}


def ask(q, script, length="normal", reasoning="short", lib=True):
    rounds[:], calls[:], closed[:] = script, [], []
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": q}]
    evs = list(core.chat_events("nemotron", Lib() if lib else None, messages, reasoning=reasoning,
                                caps={"tools", "thinking"}, length=length))
    return evs, messages, next(e for e in evs if e["type"] == "done")


core.REASONING_LIMITS = {"off": 0, "short": 5, "medium": 10, "long": 20, "none": -1}  # small, so a few chunks reach them

# ---- the reasoning limit ----
evs, messages, done = ask("Where can I run a generator?", [thinking(9), [say("Outdoors, 20 feet from the house.")]])
kinds = [e["type"] for e in evs if e["type"] not in ("tick", "token")]
check("reasoning past the limit stops, then it answers", kinds == ["thinking"] * 6 + ["think_limit", "done"], kinds)
check("the event says what the limit was", next(e for e in evs if e["type"] == "think_limit")["limit"] == 5)
check("the length limit is for the answer: reasoning comes on top", calls[0]["predict"] == 8192 + 5, calls[0])
check("the reasoning round's request was closed", closed[0] is True and len(closed) == 2, closed)
check("the answer round: no reasoning, no lookups, told to answer now",
      calls[1]["think"] is False and calls[1]["tools"] is False and calls[1]["last"] == core.THINK_NUDGE, calls[1])
check("the answer it gave is the answer", done["answer"].startswith("Outdoors, 20 feet from the house."), done["answer"])
check("the app's note stays out of the saved chat", core.THINK_NUDGE not in str(messages)
      and messages[-1] == {"role": "assistant", "content": "Outdoors, 20 feet from the house."}, messages)
evs, _, done = ask("Where can I run a generator?", [thinking(9) + [say("Outdoors.")]], reasoning="medium")
check("Medium allows more (9 of 10 is fine)", not any(e["type"] == "think_limit" for e in evs)
      and done["answer"].startswith("Outdoors."), [e["type"] for e in evs])
evs, _, done = ask("Where can I run a generator?", [thinking(15) + [say("Outdoors.")]], reasoning="long")
check("Long allows more still", not any(e["type"] == "think_limit" for e in evs), [e["type"] for e in evs])
evs, _, done = ask("Where can I run a generator?", [thinking(40) + [say("Outdoors.")]], reasoning="none")
check("no limit with No limit", not any(e["type"] == "think_limit" for e in evs) and done["answer"].startswith("Outdoors.")
      and calls[0]["predict"] == -1, calls[0])
evs, _, done = ask("Where can I run a generator?", [thinking(9), [say("Outdoors.")]], reasoning="off")
check("Off: no reasoning asked for, and a model that reasons anyway is held to the short limit",
      calls[0]["think"] is False and any(e["type"] == "think_limit" for e in evs), calls[0])
evs, _, done = ask("Where can I run a generator?", [thinking(3) + [say("Outdoors.")]], length="none")
check("no length limit: no limit on the request either", calls[0]["predict"] == -1, calls[0])
evs, _, done = ask("Where can I run a generator?", [thinking(3) + [call("search_library", {"query": "generator"})],
                                                     thinking(9), [say("Outdoors, as Ready.gov says.")]])
check("the limit counts each round on its own, after a lookup too",
      [e["type"] for e in evs if e["type"] in ("search", "think_limit")] == ["search", "think_limit"]
      and done["answer"].startswith("Outdoors, as Ready.gov says."), [e["type"] for e in evs])
evs, _, done = ask("Where can I run a generator?", [thinking(9), [say("")], [say("Outdoors, 20 feet away.")]])
check("an empty forced answer gets the passages and a second try",
      done["answer"].startswith("Outdoors, 20 feet away.") and not done["unfinished"], done)
evs, _, done = ask("Where can I run a generator?", [thinking(9), [say("")], [say("")]])
check("empty twice: nothing to show, not a lone Sources line", done["unfinished"] and done["answer"] == "", done)

# ---- asked to look it up, it answers from memory: asked once more ----
for q, want in [("Can you look that up?", True), ("Search the library for burn first aid.", True),
                ("What does the library say about tetanus shots?", True), ("Check your sources on that.", True),
                ("Is there anything in the library on water purification?", True),
                ("How do I cook rice without power?", False), ("Where can I run a generator?", False)]:
    check(f"ASKED_SEARCH({q!r}) == {want}", bool(core.ASKED_SEARCH.search(q)) == want)
evs, messages, done = ask("Can you look that up?", [[say("From memory: about 20 feet.")],
                                                    [call("search_library", {"query": "generator distance"})],
                                                    [call("read_article", {"id": 1403312})],
                                                    [say("At least 20 feet from the house.")]])
kinds = [e["type"] for e in evs if e["type"] not in ("tick", "token")]
check("it answered from memory, was asked again, then searched and read", kinds == ["retry", "search", "read", "done"],
      kinds)
check("the retry offered tools and said why", calls[1]["tools"] is True and calls[1]["last"] == core.SEARCH_NUDGE,
      calls[1])
check("the answer from memory and the app's note are not in the saved chat",
      "From memory" not in str(messages) and core.SEARCH_NUDGE not in str(messages), messages)
check("the final answer cites what it read", done["answer"].startswith("At least 20 feet from the house.")
      and done["answer"].endswith("Sources: Generator safety (Ready.gov)"), done["answer"])
evs, _, done = ask("Can you look that up?", [[say("From memory: about 20 feet.")], [say("Still from memory: 20 feet.")]])
check("asked once only: a second answer from memory stands", [e["type"] for e in evs].count("retry") == 1
      and done["answer"].startswith("Still from memory: 20 feet."), done["answer"])
evs, _, done = ask("Where can I run a generator?", [[say("Outdoors, from memory.")]])
check("no retry when they didn't ask for a lookup", not any(e["type"] == "retry" for e in evs))
evs, _, done = ask("Can you look that up?", [[say("From memory.")]], lib=False)
check("no retry without a library to search", not any(e["type"] == "retry" for e in evs) and len(calls) == 1)

# ---- no answer and nothing found: the second try gets a search for the question ----
evs, messages, done = ask("A power line is down in my front yard. What should I do?",
                          [[say('<tool_call>{"name": "search_library"}')],
                           [say("Stay at least 35 feet away and call 911.")]])
check("tool markup on the first round, nothing searched: answered from a search for the question",
      done["answer"].startswith("Stay at least 35 feet away and call 911.")
      and [e["type"] for e in evs if e["type"] in ("read", "retry")] == ["read", "retry"], (done, evs))
check("the passages and the note stay out of the saved chat", "Never run a generator" not in str(messages)
      and core.RESCUE_NOTE not in str(messages), messages)
check("the second try had no tools and no reasoning", calls[1]["tools"] is False and calls[1]["think"] is False,
      calls[1])

# ---- dev copies' test cases (bin/dev --test): ignored anywhere else ----
os.environ["SURVIVAL_LLM_TEST"] = "no-answer"
evs, _, done = ask("Where can I run a generator?", [[say("Outdoors, from memory.")]])
check("--test is ignored outside a dev copy", done["answer"].startswith("Outdoors, from memory.") and len(calls) == 1)
os.environ["SURVIVAL_LLM_DEV"] = "1"
evs, _, done = ask("Where can I run a generator?", [[say("Outdoors, from memory.")], [say("Outdoors, per Ready.gov.")]])
check("no-answer: the answer comes out as a lookup, and the second try answers",
      done["discarded"].startswith("<tool_call>") and done["answer"].startswith("Outdoors, per Ready.gov.")
      and any(e["type"] == "retry" and e["why"] == "excerpts" for e in evs), done)
os.environ["SURVIVAL_LLM_TEST"] = "think-limit=2"
evs, _, done = ask("Where can I run a generator?", [thinking(3), [say("Outdoors.")]])
check("think-limit=2: three chunks of reasoning reach it", any(e["type"] == "think_limit" for e in evs),
      [e["type"] for e in evs])
os.environ["SURVIVAL_LLM_TEST"] = "anything-else"
evs, _, done = ask("Where can I run a generator?", [thinking(3) + [say("Outdoors.")]])
check("an unknown test case is ignored", not any(e["type"] in ("think_limit", "retry") for e in evs))
del os.environ["SURVIVAL_LLM_TEST"], os.environ["SURVIVAL_LLM_DEV"]

print(f"\n{len(FAILS)} failed" if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
