"""Status and removed endpoints never request release metadata; initial library installs still work."""
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import weakref
import zipfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import server
from memory import Memory


class NoUpdatesTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.folder = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.httpd = server.make_server(0)
        self.addCleanup(self.httpd.server_close)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.stack.enter_context(patch.object(server, "PORT", self.port, create=True))
        self.stack.enter_context(patch.object(server, "MEMORY", Memory(
            os.path.join(self.folder, "memory.txt"), os.path.join(self.folder, "settings.json"))))
        self.stack.enter_context(patch.object(server, "pack_catalog", {"packs": None, "error": None}))
        self.stack.enter_context(patch.object(server, "check_my_files"))
        self.stack.enter_context(patch.object(server, "library", return_value=None))
        self.requests = []
        original = urllib.request.urlopen

        def local_only(request, *args, **kwargs):
            url = request.full_url if isinstance(request, urllib.request.Request) else request
            self.requests.append(url)
            if url.startswith(self.base + "/"):
                return original(request, *args, **kwargs)
            if url in (server.HOST + "/api/tags", server.HOST + "/api/ps"):
                return io.BytesIO(b'{"models":[{"name":"gemma4:latest"}]}')
            raise AssertionError(f"Unexpected external request: {url}")

        self.stack.enter_context(patch.object(server.urllib.request, "urlopen", local_only))
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 3)
        self.addCleanup(self.httpd.shutdown)

    def request(self, path, body=None):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-Token": server.TOKEN, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as response:
            return json.load(response)

    def test_fresh_setup_and_repeated_status_use_only_local_catalog(self):
        for _ in range(3):
            status = self.request("/api/status")
            self.assertEqual({p["id"] for p in status["packs"]}, {"lite", "full"})
            self.assertNotIn("update", status)
            self.assertTrue(status["web_enabled"])
            self.assertIsNone(status["packs_error"])
        self.assertTrue(all(url.startswith(self.base + "/") or url.startswith(server.HOST + "/")
                            for url in self.requests))

    def test_installed_library_status_makes_no_release_requests(self):
        class Library:
            mine = None

            def stats(self):
                return 100, 2

        with patch.object(server, "library", return_value=Library()):
            status = self.request("/api/status")
        self.assertEqual(status["library"], {"articles": 100, "sources": 2})
        self.assertNotIn("update", status)
        self.assertEqual(status["packs"], [])

    def test_old_update_endpoints_are_unavailable(self):
        for path, body in (("/api/updates", None), ("/api/updates?check=1", None), ("/api/update/app", {})):
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as failed:
                self.request(path, body)
            self.assertEqual(failed.exception.code, 404)
            failed.exception.close()
        self.assertEqual(len(self.requests), 3)

    def test_missing_or_invalid_catalog_never_falls_back_to_network(self):
        path = Path(self.folder, "library-packs.json")
        for content in (None, "broken JSON", '{"packs":"invalid"}'):
            if content is not None:
                path.write_text(content)
            with patch.object(server, "PACKS_FILE", str(path)), \
                    patch.object(server, "pack_catalog", {"packs": None, "error": None}):
                status = self.request("/api/status")
            self.assertEqual(status["packs"], [])
            self.assertEqual(status["packs_error"], "unavailable")

    def test_initial_library_install_uses_fixed_download_and_checksums(self):
        archive = Path(self.folder, "fixture.zip")
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("index.sqlite", b"initial-library-fixture")
            zipped.writestr("text/article.md", "Local reference text.")
        payload = archive.read_bytes()
        part = {"url": "https://example.org/fixed-library.zip.000", "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest()}
        catalog = Path(self.folder, "library-packs.json")
        catalog.write_text(json.dumps({"packs": [{"id": "lite", "label": "Essentials", "gb": 0.1,
                                                "parts": [part], "install_bytes": 1000}]}))
        target = Path(self.folder, "library")

        def download(key, url, path, size, done_before, total):
            self.assertEqual(url, part["url"])
            self.assertEqual(size, len(payload))
            shutil.copyfile(archive, path)

        with patch.object(server, "PACKS_FILE", str(catalog)), patch.object(server, "LIB", str(target)), \
                patch.object(server, "open_libs", weakref.WeakSet()), \
                patch.object(server, "disk_free_gb", return_value=100), \
                patch.dict(server.downloads, {"library:lite": {}}, clear=True), \
                patch.object(server, "_download", download):
            server.library_worker("library:lite", "lite")
        self.assertEqual((target / "index.sqlite").read_bytes(), b"initial-library-fixture")
        self.assertEqual((target / "text/article.md").read_text(), "Local reference text.")
        self.assertFalse((target / "pack.json").exists())
        self.assertEqual(self.requests, [])


if __name__ == "__main__":
    unittest.main()
