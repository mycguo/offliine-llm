"""Ask a local model the standard blackout questions and report speed.

Usage: python3 evals/no_library_check.py MODEL   (e.g. granite4.2:8b)
Talks to the Ollama server on OLLAMA_HOST (default 127.0.0.1:11434); stdlib only.
"""
import json
import os
import sys
import time
import urllib.request

QUESTIONS = [
    "The power just went out. How long will the food in my fridge and freezer stay safe?",
    "Can I run my gas generator in the garage if I leave the garage door open?",
    "The tap water might be contaminated. How do I make it safe to drink using household bleach?",
    "A power line is down in my front yard. What should I do?",
    "My phone is at 15% and the power is out. How do I make the battery last?",
    "What number do I call to report the outage to my power company?",
]

host = os.environ.get("OLLAMA_HOST", "127.0.0.1:11434")
model = sys.argv[1]
for q in QUESTIONS:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": q}],
        "think": False,
        "stream": False,
    }).encode()
    req = urllib.request.Request(f"http://{host}/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    start = time.time()
    r = json.load(urllib.request.urlopen(req, timeout=900))
    tps = r["eval_count"] / (r["eval_duration"] / 1e9)
    wait = (r.get("load_duration", 0) + r.get("prompt_eval_duration", 0)) / 1e9
    thinking = "yes" if r["message"].get("thinking") else "no"
    print(f"### {q}\n{r['message']['content'].strip()}\n")
    print(f"--- {r['eval_count']} tokens at {tps:.1f} tok/s; first token after {wait:.1f}s; "
          f"total {time.time() - start:.1f}s; thinking: {thinking}\n")
