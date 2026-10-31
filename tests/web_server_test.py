"""Exercise the authenticated app API with real HTTP and a stand-in model/web page."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core
import web_access
from app import server
from memory import Memory

URL = "https://example.org/current"


class WebServerTests(unittest.TestCase):
    def test_authenticated_switch_updates_existing_conversation_and_custom_prompt(self):
        offered = []

        def stream(model, messages, tools, think, options):
            offered.append(tools)
            if tools and messages[-1]["role"] == "user":
                yield {"message": {"tool_calls": [{"function": {"name": "read_webpage", "arguments": {"url": URL}}}]}}
            else:
                yield {"message": {"content": "Here is the answer."}}
            yield {"done": True, "eval_count": 1, "eval_duration": 1}

        with tempfile.TemporaryDirectory() as folder:
            mem = Memory(os.path.join(folder, "memory.txt"), os.path.join(folder, "settings.json"))
            instructions = os.path.join(folder, "instructions.txt")
            with open(instructions, "w") as f:
                f.write("My custom instructions: you cannot go online.")
            httpd = server.make_server(0)
            base = f"http://127.0.0.1:{httpd.server_address[1]}"

            def request(path, body=None, auth=True):
                headers = {"Content-Type": "application/json"}
                if auth:
                    headers["X-Token"] = server.TOKEN
                req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                             headers=headers)
                with urllib.request.urlopen(req, timeout=3) as response:
                    raw = response.read().decode()
                    if response.headers.get_content_type() == "text/event-stream":
                        return [json.loads(line[6:]) for line in raw.splitlines() if line.startswith("data: ")]
                    return json.loads(raw)

            with patch.multiple(server, MEMORY=mem, CUSTOM_PROMPT=instructions), \
                    patch.object(server, "PORT", httpd.server_address[1], create=True), \
                    patch.object(server, "library", return_value=None), \
                    patch.object(server.myfiles, "items", return_value=[]), \
                    patch.object(server, "model_caps", return_value={"tools"}), \
                    patch.object(core, "_stream", stream), \
                    patch.object(web_access, "fetch", return_value=(URL, "<title>Live page</title><p>Fresh facts.</p>", "text/html")) as fetch:
                thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                thread.start()
                try:
                    with self.assertRaises(urllib.error.HTTPError) as blocked:
                        request("/api/web", {"enabled": False}, auth=False)
                    self.assertEqual(blocked.exception.code, 403)
                    blocked.exception.close()
                    with self.assertRaises(urllib.error.HTTPError) as invalid:
                        request("/api/web", {"enabled": "off"})
                    self.assertEqual(invalid.exception.code, 400)
                    invalid.exception.close()
                    conversation = request("/api/new", {})["conversation"]
                    body = {"model": "gemma", "message": "Read this page", "conversation": conversation}
                    events = request("/api/chat", body)
                    self.assertTrue(any(e["type"] == "web_read" for e in events))
                    self.assertEqual(fetch.call_count, 1)
                    self.assertIn("Web access is Automatic", server.conversations[conversation]["messages"][0]["content"])
                    self.assertFalse(request("/api/web", {"enabled": False})["web_enabled"])
                    events = request("/api/chat", body)
                    self.assertEqual(fetch.call_count, 1)
                    self.assertFalse(any(e["type"] == "web_read" for e in events))
                    self.assertIn("Web tools are unavailable", server.conversations[conversation]["messages"][0]["content"])
                    self.assertFalse(offered[-1])
                    self.assertTrue(request("/api/web", {"enabled": True})["web_enabled"])
                    events = request("/api/chat", body)
                    self.assertEqual(fetch.call_count, 2)
                    self.assertTrue(any(e["type"] == "web_read" for e in events))
                    self.assertTrue(Memory(mem.path, mem.settings_path).web_enabled)
                finally:
                    httpd.shutdown()
                    httpd.server_close()
                    thread.join(timeout=3)
                    server.conversations.pop(conversation, None)


if __name__ == "__main__":
    unittest.main()
