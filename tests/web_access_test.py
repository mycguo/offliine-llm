"""Web transport, model tool loop, source attribution and saved settings. No internet or model required."""
import copy
import http.client
import io
import json
import os
import socket
import sys
import tempfile
import unittest
from email.message import Message
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core
import web_access as web
from memory import Memory

URL = "https://example.org/current"
PAGE = "<title>Current conditions</title><nav>Ignore navigation</nav><p>Verified live information.</p><script>secret()</script>"
RESULTS = '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fcurrent">Current <b>conditions</b></a><a class="result__snippet">A current report.</a>'


def call(name, args):
    return {"message": {"tool_calls": [{"function": {"name": name, "arguments": args}}]}}


def stream_script(script, offered):
    def stream(model, messages, tools, think, options):
        offered.append(copy.deepcopy(tools))
        yield script.pop(0)
        yield {"done": True, "eval_count": 5, "eval_duration": 100}
    return stream


class WebTests(unittest.TestCase):
    def turn(self, script, access, library=None, caps=None, question="Search online for current conditions"):
        offered = []
        messages = [{"role": "system", "content": core.system_prompt(library, True, access is not None)},
                    {"role": "user", "content": question}]
        with patch.object(core, "_stream", stream_script(script, offered)):
            events = list(core.chat_events("gemma", library, messages, caps={"tools"} if caps is None else caps, web=access))
        return events, messages, offered

    def test_search_and_read_without_library(self):
        replies = [(web.SEARCH_URL, RESULTS, "text/html"), (URL, PAGE, "text/html")]
        script = [call("search_web", {"query": "conditions"}), call("read_webpage", {"url": URL}),
                  {"message": {"content": "Verified live information.\n\nSources: Invented publisher"}}]
        with patch.object(web, "fetch", side_effect=replies) as fetch:
            events, messages, offered = self.turn(script, web.WebAccess())
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual({t["function"]["name"] for t in offered[0]}, {"search_web", "read_webpage"})
        done = events[-1]
        self.assertEqual(done["sources"][0]["url"], URL)
        self.assertTrue(done["sources"][0]["web"])
        self.assertNotIn("Invented publisher", done["answer"])
        self.assertIn("Current conditions (example.org)", done["answer"])
        self.assertNotIn("secret()", access_text(messages))
        self.assertNotIn("Ignore navigation", access_text(messages))
        self.assertIn("Current conditions", core._read_earlier(messages)[0]["title"])

    def test_off_never_requests_web_even_when_model_calls_it(self):
        with patch.object(web, "fetch") as fetch:
            events, _, offered = self.turn([call("read_webpage", {"url": URL})], None)
        fetch.assert_not_called()
        self.assertFalse(offered[0])

    def test_switch_off_during_answer(self):
        with patch.object(web, "fetch") as fetch:
            text, event = web.WebAccess(lambda: False).run("read_webpage", {"url": URL})
        fetch.assert_not_called()
        self.assertEqual(event["type"], "web_error")
        self.assertIn("off", text)

    def test_disconnect_answers_locally_and_next_turn_retries(self):
        access = web.WebAccess()
        script = [call("search_web", {"query": "conditions"}),
                  {"message": {"content": "I couldn't verify live information. Here is general guidance."}}]
        with patch.object(web, "fetch", side_effect=socket.timeout("offline")) as fetch:
            events, _, _ = self.turn(script, access)
            access.run("read_webpage", {"url": URL})
        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(events[-1]["web_error"])
        self.assertFalse(events[-1]["unfinished"])
        self.assertEqual(events[-1]["sources"], [])
        with patch.object(web, "fetch", return_value=(URL, PAGE, "text/html")) as fetch:
            _, event = web.WebAccess().run("read_webpage", {"url": URL})
        self.assertEqual(event["type"], "web_read")

    def test_model_without_tools_does_not_request_web(self):
        with patch.object(web, "fetch") as fetch:
            events, _, offered = self.turn([{"message": {"content": "Local answer."}}], web.WebAccess(), caps=set())
        fetch.assert_not_called()
        self.assertFalse(offered[0])
        self.assertIn("does not support", events[-1]["web_error"])

    def test_explicit_web_request_is_nudged_to_web(self):
        script = [{"message": {"content": "An answer from memory."}}, call("read_webpage", {"url": URL}),
                  {"message": {"content": "Verified live information."}}]
        with patch.object(web, "fetch", return_value=(URL, PAGE, "text/html")):
            events, _, _ = self.turn(script, web.WebAccess())
        self.assertTrue(any(e["type"] == "retry" for e in events))
        self.assertEqual(events[-1]["sources"][0]["url"], URL)

    def test_empty_answer_rescued_from_web_without_library(self):
        script = [call("read_webpage", {"url": URL}), {"message": {"content": ""}},
                  {"message": {"content": "Verified live information."}}]
        with patch.object(web, "fetch", return_value=(URL, PAGE, "text/html")):
            events, _, _ = self.turn(script, web.WebAccess())
        self.assertFalse(events[-1]["unfinished"])
        self.assertEqual(events[-1]["sources"][0]["url"], URL)

    def test_local_library_and_web_sources_work_together(self):
        class Library:
            def labels(self):
                return ["Ready.gov"]

            def search(self, query, topic="any", limit=8):
                return [{"id": 42, "title": "Local guidance", "source": "Ready.gov", "official": True, "snippet": "Local facts."}]

            def read(self, pid, max_chars=5000):
                return {"title": "Local guidance", "source": "Ready.gov", "official": True, "url": "", "text": "Local facts.", "next_id": None}

        script = [call("search_library", {"query": "conditions"}), call("read_article", {"id": 42}),
                  call("read_webpage", {"url": URL}), {"message": {"content": "Local and current guidance."}}]
        with patch.object(web, "fetch", return_value=(URL, PAGE, "text/html")):
            events, _, offered = self.turn(script, web.WebAccess(), library=Library())
        self.assertEqual(len(offered[0]), 5)
        self.assertEqual([s["title"] for s in events[-1]["sources"]], ["Local guidance", "Current conditions"])
        self.assertEqual(events[-1]["seen"], [])

    def test_search_snippets_not_reported_as_read(self):
        script = [call("search_web", {"query": "conditions"}), {"message": {"content": "Only a snippet was available."}}]
        with patch.object(web, "fetch", return_value=(URL, RESULTS, "text/html")):
            events, _, _ = self.turn(script, web.WebAccess())
        self.assertEqual(events[-1]["sources"], [])
        self.assertEqual(events[-1]["seen"][0]["url"], URL)

    def test_challenge_and_request_budget_are_visible_failures(self):
        access = web.WebAccess()
        with patch.object(web, "fetch", return_value=(URL, "<h1>Verify you are human</h1>", "text/html")) as fetch:
            text, event = access.run("search_web", {"query": "conditions"})
            for _ in range(9):
                access.run("search_web", {"query": "conditions"})
        self.assertEqual(event["type"], "web_error")
        self.assertIn("verification", text)
        self.assertEqual(fetch.call_count, 6)

    def test_saved_off_survives_reload_and_memory_setting_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            mem = Memory(os.path.join(folder, "memory.txt"), os.path.join(folder, "settings.json"))
            self.assertTrue(mem.web_enabled)
            mem.set_web_enabled(False)
            mem.set_ai_writes(False)
            again = Memory(mem.path, mem.settings_path)
            self.assertFalse(again.web_enabled)
            self.assertFalse(again.ai_writes)
            again.set_web_enabled(True)
            self.assertFalse(mem.ai_writes)

    def test_prompt_off_and_on_match_capabilities(self):
        self.assertIn("no internet access", core.system_prompt(None))
        self.assertNotIn("no internet access", core.system_prompt(None, web_enabled=True))
        self.assertIn("no internet access", core.system_prompt(None, tools=False, web_enabled=True))

    def test_blocks_unsafe_urls(self):
        for url in ["file:///etc/passwd", "http://localhost/", "http://127.0.0.1/", "http://[::1]/",
                    "http://169.254.169.254/", "https://example.org:8765/", "https://user:pass@example.org/",
                    "http://10.0.0.1/", "http://224.0.0.1/", "http://[::ffff:127.0.0.1]/", "https://example.org/\nHeader:x"]:
            with self.subTest(url=url), self.assertRaises(web.WebError):
                web.public_url(url)

    def test_blocks_private_dns_and_redirects(self):
        conn = MagicMock()
        response = Mock()
        response.status = 302
        response.getheader.return_value = "http://127.0.0.1/private"
        conn.getresponse.return_value.__enter__.return_value = response
        with patch.object(web.socket, "getaddrinfo", return_value=dns("93.184.216.34")), \
                patch.object(web.http.client, "HTTPSConnection", return_value=conn):
            with self.assertRaises(web.WebError):
                web.fetch(URL)
        self.assertEqual(conn.request.call_count, 1)
        with patch.object(web.socket, "getaddrinfo", return_value=dns("10.1.2.3")), \
                patch.object(web.http.client, "HTTPSConnection") as connection:
            with self.assertRaises(web.WebError):
                web.fetch(URL)
            connection.assert_not_called()

    def test_transport_pins_dns_limits_body_and_keeps_tls_hostname(self):
        # Use real HTTPResponse parsing over an in-memory socket; the connection itself is a stand-in.
        def response(payload, headers="Content-Type: text/html; charset=utf-8\r\n"):
            fake_socket = Mock()
            fake_socket.makefile.return_value = io.BytesIO(b"HTTP/1.1 200 OK\r\n" + headers.encode() + b"\r\n" + payload)
            result = http.client.HTTPResponse(fake_socket)
            result.begin()
            return result
        conn = Mock()
        with patch.object(web.socket, "getaddrinfo", return_value=dns("93.184.216.34")), \
                patch.object(web.http.client, "HTTPSConnection", return_value=conn) as factory:
            conn.getresponse.return_value = response(PAGE.encode())
            # BytesIO has no raw socket; the transport also supports response streams without one.
            with patch.object(web, "TIMEOUT", 1):
                url, text, kind = web.fetch(URL)
            factory.assert_called_with("example.org", 443, timeout=unittest.mock.ANY)
            self.assertEqual(url, URL)
            self.assertEqual(kind, "text/html")
            self.assertIn("Verified live", text)
            with patch.object(web.socket, "create_connection") as connect:
                conn._create_connection(("example.org", 443), 1)
                connect.assert_called_once_with(("93.184.216.34", 443), 1)
            conn.getresponse.return_value = response(b"x" * 40)
            with patch.object(web, "MAX_BYTES", 32), self.assertRaises(web.WebError):
                web.fetch(URL)
            conn.getresponse.return_value = response(b"%PDF", "Content-Type: application/pdf\r\n")
            with self.assertRaises(web.WebError):
                web.fetch(URL)


def access_text(messages):
    return "\n".join(m["content"] for m in messages if m.get("tool_name") == "read_webpage")


def dns(address):
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443))]


if __name__ == "__main__":
    unittest.main()
