"""Race server: request guards, multipart parsing, exhibit lookup and the spend cap. No network, no paid calls."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

server = pytest.importorskip("race.server")


@pytest.fixture(scope="module")
def base():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    port = httpd.server_address[1]
    old = (server.ALLOWED_HOSTS, server.ALLOWED_ORIGINS)
    server.ALLOWED_HOSTS = {f"127.0.0.1:{port}"}
    server.ALLOWED_ORIGINS = {f"http://127.0.0.1:{port}"}
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()
    server.ALLOWED_HOSTS, server.ALLOWED_ORIGINS = old


def call(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def test_cases_lists_labelled_and_demo(base):
    code, body = call(base + "/api/cases")
    assert code == 200
    ids = {c["id"]: c for c in body["cases"]}
    assert ids["case01"]["labelled"] is True
    assert "demo-run3" not in ids or ids["demo-run3"]["labelled"] is False


def test_exhibit_lookup_and_unknown(base):
    name = sorted(server.CASES["case01"]["exhibits"])[0]
    code, body = call(f"{base}/api/exhibit?case=case01&name={name}")
    assert code == 200 and body["text"] == server.CASES["case01"]["exhibits"][name]
    assert call(f"{base}/api/exhibit?case=case01&name=../../etc/passwd")[0] == 404


def test_post_needs_allowed_origin(base):
    body = json.dumps({"case": "case01", "engine": "drex"}).encode()
    code, _ = call(base + "/api/check", body, {"Content-Type": "application/json", "Origin": "http://evil.example"})
    assert code == 403


def test_unknown_engine_rejected(base):
    body = json.dumps({"case": "case01", "engine": "nope"}).encode()
    code, _ = call(base + "/api/check", body, {"Content-Type": "application/json", "Origin": base})
    assert code == 400


def test_spend_cap_blocks_paid_calls(base, monkeypatch):
    monkeypatch.setattr(server, "spent_usd", server.SPEND_CAP_USD)
    body = json.dumps({"case": "case01", "engine": "rag"}).encode()
    code, out = call(base + "/api/check", body, {"Content-Type": "application/json", "Origin": base})
    assert code == 429 and "cap" in out["error"]


def test_parse_multipart_and_upload_validation(base):
    boundary = "XyZ"
    parts = [("exhibits", "a.txt", b"not a pdf"), ("draft", "d.md", b"The Beneficiary led a team of 6 engineers.")]
    body = b"".join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{f}"; filename="{n}"\r\n\r\n'.encode() + d + b"\r\n"
        for f, n, d in parts) + f"--{boundary}--\r\n".encode()
    ctype = f"multipart/form-data; boundary={boundary}"
    files = server.parse_multipart(ctype, body)
    assert [(f, n) for f, n, _ in files] == [("exhibits", "a.txt"), ("draft", "d.md")]
    code, out = call(base + "/api/upload", body, {"Content-Type": ctype, "Origin": base})
    assert code == 400 and "not a PDF" in out["error"]


def test_safe_name_strips_paths():
    assert server._safe_name("../../x y.pdf") == "x_y.pdf"
    assert server._safe_name("") == "file"


def test_reservations_stop_parallel_lanes_overrunning_the_cap(monkeypatch):
    monkeypatch.setattr(server, "spent_usd", server.SPEND_CAP_USD - 0.15)
    monkeypatch.setattr(server, "reserved_usd", 0.0)
    assert server._reserve(0.10) is True
    assert server._reserve(0.10) is False  # the second lane would pass the cap while the first is still running
    server._settle(0.10, 0.02)
    assert abs(server.spent_usd - (server.SPEND_CAP_USD - 0.13)) < 1e-9 and server.reserved_usd == 0


def test_failed_paid_call_is_charged_its_estimate(base, monkeypatch):
    monkeypatch.setattr(server, "spent_usd", 0.0)
    monkeypatch.setattr(server, "reserved_usd", 0.0)

    async def boom(case):
        raise RuntimeError("judge failed after the embed call was billed")

    monkeypatch.setattr(server, "run_rag", boom)
    body = json.dumps({"case": "case01", "engine": "rag"}).encode()
    code, _ = call(base + "/api/check", body, {"Content-Type": "application/json", "Origin": base})
    assert code == 502 and server.spent_usd == server.ESTIMATE_USD["rag"] and server.reserved_usd == 0


def test_negative_content_length_rejected(base):
    import http.client
    host, port = base.removeprefix("http://").split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=5)
    conn.putrequest("POST", "/api/upload", skip_host=True)
    conn.putheader("Host", f"{host}:{port}")
    conn.putheader("Origin", base)
    conn.putheader("Content-Length", "-1")
    conn.endheaders()
    assert conn.getresponse().status == 400
