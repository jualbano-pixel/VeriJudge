import hashlib
import socket

import httpx
import pytest

from veryjudge.fetcher import DocumentFetcher, RecordedFetcher, destination, normalize_paragraphs
from veryjudge.models import Document
from veryjudge.providers import RecordReplay
from veryjudge.validators import LocatorValidator
from tests.helpers import TEXT, candidate, proposition


def resolve(host, port, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def fetcher(tmp_path, handler):
    return DocumentFetcher(tmp_path, httpx.Client(transport=httpx.MockTransport(handler)), resolve)


@pytest.fixture(autouse=True)
def offline_transport_tests(monkeypatch):
    monkeypatch.delenv("CI", raising=False)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com", "http://169.254.169.254/latest", "http://10.0.0.1", "http://127.0.0.1", "http://[::1]", "http://[fc00::1]", "http://224.0.0.1", "http://0.0.0.0", "http://192.168.1.1", "http://172.16.0.1", "http://[::ffff:127.0.0.1]"])
def test_operator_added_url_private_ip_blocked(tmp_path, url):
    result = fetcher(tmp_path, lambda r: pytest.fail("must not connect")).fetch(url)
    assert result.fetch_status == "failed" and result.failure_reason == "blocked_destination"


def test_redirect_rechecks_destination(tmp_path):
    calls = []
    def handle(request):
        calls.append(request)
        assert request.url.host == "example.test"
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})
    result = fetcher(tmp_path, handle).fetch("https://example.test")
    assert result.failure_reason == "blocked_destination" and len(calls) == 1


def test_dns_private_answer_rejected(tmp_path):
    f = fetcher(tmp_path, lambda r: pytest.fail("must not connect"))
    f.resolver = lambda host, port, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", port))]
    assert f.fetch("https://example.test").failure_reason == "blocked_destination"


def test_locator_offsets_against_stored_text(tmp_path):
    body = "<html><head><title>Census fixture</title></head><body><article><p>" + TEXT.replace(" ", "  ") + "</p><p>Second paragraph about the Philippine population and the census record.</p></article></body></html>"
    result = fetcher(tmp_path, lambda r: httpx.Response(200, text=body, headers={"content-type": "text/html"})).fetch("https://example.test", run_id="run_test", document_id="doc_test")
    assert result.fetch_status == "ok"
    assert " ".join(result.text.split()) == result.text
    assert result.text_sha256 == hashlib.sha256(result.text.encode()).hexdigest()
    doc = Document(**result.model_dump(), id="doc_test", run_id="run_test", operator_added=True)
    index = next(p.index for p in doc.paragraphs if TEXT in doc.text[p.start:p.end])
    locator = LocatorValidator.validate(doc, candidate(paragraph_index=index), proposition())
    assert locator.ok and doc.text[locator.locator_start:locator.locator_end] == TEXT
    assert (tmp_path / "run_test" / "doc_test.html").is_file()


def test_failed_fetch_logged_not_cited(tmp_path):
    result = fetcher(tmp_path, lambda r: httpx.Response(404)).fetch("https://example.test/missing")
    assert result.fetch_status == "failed" and result.failure_reason == "http_404" and result.text is None
    doc = Document(**result.model_dump(), id="doc_test", run_id="run_test", operator_added=True)
    assert not LocatorValidator.validate(doc, candidate(), proposition()).ok


def minimal_pdf():
    stream = b"BT /F1 12 Tf 50 700 Td (Philippine census population fixture record.) Tj ET"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"]
    body, offsets = b"%PDF-1.4\n", [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(body))
        body += str(i).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    xref = len(body)
    body += b"xref\n0 6\n0000000000 65535 f \n"
    for offset in offsets[1:]:
        body += f"{offset:010d} 00000 n \n".encode()
    return body + b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF"


def test_pdf_fixture_yields_paragraphs(tmp_path):
    result = fetcher(tmp_path, lambda r: httpx.Response(200, content=minimal_pdf(), headers={"content-type": "application/pdf"})).fetch("https://example.test/doc.pdf")
    assert result.fetch_status == "ok" and result.paragraphs and "Philippine" in result.text


def test_fetch_record_replay(tmp_path, monkeypatch):
    live = fetcher(tmp_path / "captures", lambda r: httpx.Response(404))
    result = RecordedFetcher(live, RecordReplay(tmp_path / "cassettes", "record")).fetch("https://example.test/missing")
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr(live, "fetch", lambda *a, **kw: pytest.fail("live fetch"))
    assert RecordedFetcher(live, RecordReplay(tmp_path / "cassettes")).fetch("https://example.test/missing") == result
