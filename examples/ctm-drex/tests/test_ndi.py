import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from ctm_drex import ndi
from ctm_drex.check import load_exhibits

MARKDOWN = "# Offer Letter\nSalary $245,000. Team of 12."


class FakeNDI:
    """Upload grant -> upload -> parse job that is still running -> poll returns succeeded."""

    def __init__(self):
        self.calls = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        if path.endswith("/documents/upload-grants"):
            assert request.headers["Authorization"] == "Bearer test-key"
            return httpx.Response(200, json={"workspace_id": "ws1", "upload_url": "https://upload.test/v1/workspaces/ws1/files", "token": "tok"})
        if request.url.host == "upload.test":
            assert request.headers["X-Upload-Token"] == "tok"
            assert b'name="metadata"' in request.content
            return httpx.Response(200, json={"status": "succeeded", "result": {"file": {"workspace_id": "ws1", "file_id": "f1"}}})
        if path.endswith("/documents/parse"):
            body = json.loads(request.content)
            assert body["source"] == {"type": "workspace_file", "workspace_id": "ws1", "file_id": "f1"}
            return httpx.Response(202, json={"job_id": "j1", "status": "running"})
        if path.endswith("/documents/jobs/j1"):
            return httpx.Response(200, json={"job_id": "j1", "status": "succeeded", "result": {"document": {"markdown": MARKDOWN}}, "cost": {"total_usd": "0.0035"}})
        return httpx.Response(404, json={"error": "unexpected " + path})


class ParsePdfTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeNDI()
        real = httpx.AsyncClient
        patches = [
            mock.patch.object(ndi.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(self.fake), **kw)),
            mock.patch.object(ndi, "_key", lambda: "test-key"),
            mock.patch.object(ndi.asyncio, "sleep", mock.AsyncMock()),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.tmp = Path(tempfile.mkdtemp())
        self.pdf = self.tmp / "E1.pdf"
        self.pdf.write_bytes(b"%PDF-1.3 fake")

    def test_parse_uploads_polls_and_caches(self):
        text = asyncio.run(ndi.parse_pdf(self.pdf))
        self.assertEqual(text, MARKDOWN)
        self.assertEqual((self.tmp / "E1.ndi.md").read_text(), MARKDOWN)
        self.assertEqual(ndi.last_cost, {"total_usd": "0.0035"})
        self.assertIn(("GET", "/v1/documents/jobs/j1"), self.fake.calls)
        # A second call reads the cache and makes no request.
        n = len(self.fake.calls)
        self.assertEqual(asyncio.run(ndi.parse_pdf(self.pdf)), MARKDOWN)
        self.assertEqual(len(self.fake.calls), n)

    def test_load_exhibits_reads_pdf_via_ndi_and_skips_cache_file(self):
        (self.tmp / "E2.md").write_text("Award letter.")
        exhibits = load_exhibits(self.tmp)
        self.assertEqual(exhibits, {"E1.pdf": MARKDOWN, "E2.md": "Award letter."})
        # The cache file now exists but is not loaded as a separate exhibit.
        self.assertEqual(set(load_exhibits(self.tmp)), {"E1.pdf", "E2.md"})

    def test_failed_job_raises(self):
        def failing(request):
            if request.url.path.endswith("/documents/jobs/j1"):
                return httpx.Response(200, json={"job_id": "j1", "status": "failed", "error": {"code": "bad_pdf"}})
            return FakeNDI()(request)

        self.fake = failing
        with self.assertRaises(ndi.NDIError):
            asyncio.run(ndi.parse_pdf(self.pdf))
        self.assertFalse((self.tmp / "E1.ndi.md").exists())


class NoPdfNoNdiTest(unittest.TestCase):
    def test_text_exhibits_need_no_key(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "E1.md").write_text("x")
        with mock.patch.object(ndi, "_key", side_effect=AssertionError("NDI must not be called")):
            self.assertEqual(load_exhibits(tmp), {"E1.md": "x"})


if __name__ == "__main__":
    unittest.main()
