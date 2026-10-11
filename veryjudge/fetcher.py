"""Local document capture with per-hop destination checks and pinned IPs."""
from datetime import datetime, timezone
import base64
import hashlib
from io import BytesIO
import ipaddress
from pathlib import Path
import socket
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
import pdfplumber
import trafilatura

from veryjudge.ids import new_id
from veryjudge.models import FetchResult, Paragraph
from veryjudge.providers import ProviderError, in_ci


class BlockedDestination(ValueError):
    pass


def blocked(address):
    return (not address.is_global or address.is_multicast or address.is_reserved
            or address.is_loopback or address.is_link_local or address.is_unspecified)


def check_url(url):
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            raise BlockedDestination("blocked_destination")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        host = parts.hostname.encode("idna").decode("ascii")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and blocked(address):
            raise BlockedDestination("blocked_destination")
        return parts, host, port
    except (ValueError, UnicodeError) as error:
        raise BlockedDestination("blocked_destination") from error


def destination(url, resolver=socket.getaddrinfo):
    parts, host, port = check_url(url)
    answers = resolver(host, port, type=socket.SOCK_STREAM)
    addresses = [ipaddress.ip_address(answer[4][0]) for answer in answers]
    if not addresses or any(blocked(address) for address in addresses):
        raise BlockedDestination("blocked_destination")
    # Connect to the validated IP rather than resolving the hostname again.
    address = str(addresses[0])
    literal = "[" + address + "]" if ":" in address else address
    pinned = urlunsplit((parts.scheme, literal + ":" + str(port), parts.path or "/", parts.query, ""))
    return pinned, parts.netloc, host


def normalize_paragraphs(extracted):
    paragraphs, chunks, offset = [], [], 0
    for raw in extracted.splitlines():
        paragraph = " ".join(raw.split())
        if not paragraph:
            continue
        if chunks:
            offset += 1
        paragraphs.append(Paragraph(index=len(paragraphs), start=offset, end=offset+len(paragraph)))
        chunks.append(paragraph)
        offset += len(paragraph)
    return " ".join(chunks), paragraphs


class DocumentFetcher:
    def __init__(self, captures="captures", client=None, resolver=socket.getaddrinfo):
        self.captures = Path(captures)
        # Keep certificate validation and honor the host environment's approved
        # outbound transport (for example, its HTTPS proxy). Destination checks
        # still run before every request and redirect.
        self.client = client or httpx.Client(follow_redirects=False, trust_env=True)
        self.resolver = resolver

    def fetch(self, url, timeout_s=20, *, run_id=None, document_id=None):
        fetched_at = datetime.now(timezone.utc)
        current, status = url, None
        deadline = time.monotonic() + timeout_s
        def failed(reason):
            return FetchResult(url=url, final_url=current, http_status=status, fetch_status="failed",
                               failure_reason=reason, fetched_at=fetched_at)
        try:
            check_url(url)
            if in_ci():
                return failed("live_calls_forbidden_in_ci")
            for _ in range(11):
                # Resolve and reject every destination before the connection. The
                # request itself remains addressed to the verified hostname so
                # TLS hostname validation uses the certificate's DNS identity.
                # `httpx` owns the TLS handshake; using an IP URL here would
                # validate the certificate against the IP and reject legitimate
                # HTTPS sites such as the PSA release.
                destination(current, self.resolver)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return failed("timeout")
                response = self.client.get(current, timeout=remaining, follow_redirects=False)
                status = response.status_code
                if status in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        return failed("redirect_without_location")
                    current = urljoin(current, location)
                    continue
                if status >= 400:
                    return failed("http_" + str(status))
                break
            else:
                return failed("too_many_redirects")
            content_type = response.headers.get("content-type", "").split(";")[0].lower()
            title, published_at = None, None
            is_pdf = content_type == "application/pdf" or response.content.startswith(b"%PDF-")
            if is_pdf:
                with pdfplumber.open(BytesIO(response.content)) as pdf:
                    extracted = "\n".join(page.extract_text() or "" for page in pdf.pages)
            else:
                extracted = trafilatura.extract(response.content, output_format="txt", include_links=True,
                                                include_tables=True, favor_recall=True) or ""
                metadata = trafilatura.extract_metadata(response.content)
                if metadata:
                    title = metadata.title
                    if metadata.date:
                        try:
                            published_at = datetime.fromisoformat(metadata.date.replace("Z", "+00:00"))
                            if published_at.tzinfo is None:
                                published_at = published_at.replace(tzinfo=timezone.utc)
                        except ValueError:
                            pass
            stored, paragraphs = normalize_paragraphs(extracted)
            if not stored:
                return failed("no_text")
            if time.monotonic() >= deadline:
                return failed("timeout")
            run_id, document_id = run_id or new_id("run"), document_id or new_id("doc")
            if Path(run_id).name != run_id or Path(document_id).name != document_id or run_id in {".", ".."}:
                raise ValueError("invalid capture id")
            path = self.captures / run_id / (document_id + (".pdf" if is_pdf else ".html"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(response.content)
            return FetchResult(url=url, final_url=current, http_status=status, fetch_status="ok",
                               content_type=content_type, html_path=str(path), text=stored,
                               text_sha256=hashlib.sha256(stored.encode()).hexdigest(), paragraphs=paragraphs,
                               title=title, published_at=published_at, fetched_at=fetched_at)
        except BlockedDestination:
            return failed("blocked_destination")
        except (httpx.TimeoutException, TimeoutError):
            return failed("timeout")
        except (httpx.RequestError, OSError):
            return failed("network_error")
        except Exception:
            return failed("extraction_failed")


class RecordedFetcher:
    def __init__(self, fetcher, recorder):
        self.fetcher, self.recorder = fetcher, recorder

    def fetch(self, url, timeout_s=20, **context):
        try:
            check_url(url)
        except BlockedDestination:
            return FetchResult(url=url, fetch_status="failed", failure_reason="blocked_destination",
                               fetched_at=datetime.now(timezone.utc))
        request = {"url": url}
        def capture():
            value = self.fetcher.fetch(url, timeout_s, **context).model_dump(mode="json")
            if value["html_path"]:
                value["capture_bytes"] = base64.b64encode(Path(value["html_path"]).read_bytes()).decode("ascii")
            return value
        result = dict(self.recorder.call("fetch", request, capture))
        raw = result.pop("capture_bytes", None)
        if raw and self.recorder.mode == "replay":
            run_id = context.get("run_id") or new_id("run")
            doc_id = context.get("document_id") or new_id("doc")
            if Path(run_id).name != run_id or Path(doc_id).name != doc_id or run_id in {".", ".."}:
                raise ValueError("invalid capture id")
            suffix = ".pdf" if result.get("content_type") == "application/pdf" else ".html"
            path = self.fetcher.captures / run_id / (doc_id + suffix)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(raw, validate=True))
            result["html_path"] = str(path)
        return FetchResult.model_validate(result)
