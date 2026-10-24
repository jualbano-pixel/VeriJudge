"""One web provider, curated Philippine scope adapter, and JSON replay."""
import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Protocol

import httpx
import yaml

from veryjudge.models import SearchResult, SearchScope

logger = logging.getLogger("veryjudge")


class ProviderError(Exception):
    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


def in_ci():
    return os.environ.get("CI", "").lower() not in {"", "0", "false"}


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, language: str, scope: SearchScope) -> list[SearchResult]: ...


class RecordReplay:
    def __init__(self, directory, mode="replay"):
        if mode not in {"record", "replay"}:
            raise ValueError("mode must be record or replay")
        self.directory = Path(directory)
        self.mode = mode

    def path_for(self, operation, request):
        encoded = json.dumps({"operation": operation, "request": request}, sort_keys=True, ensure_ascii=False)
        return self.directory / (hashlib.sha256(encoded.encode()).hexdigest() + ".json")

    def call(self, operation, request, live):
        path = self.path_for(operation, request)
        if self.mode == "replay":
            if not path.is_file():
                raise ProviderError("missing_cassette:" + path.name)
            cassette = json.loads(path.read_text())
            if cassette["operation"] != operation or cassette["request"] != request:
                raise ProviderError("cassette_request_mismatch")
        else:
            if in_ci():
                raise ProviderError("live_calls_forbidden_in_ci")
            try:
                response = live()
                cassette = dict(operation=operation, request=request, response=response)
            except ProviderError as error:
                cassette = dict(operation=operation, request=request,
                                error=dict(message=str(error), retryable=error.retryable))
            self.directory.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(cassette, ensure_ascii=False, indent=2))
            temp.replace(path)
        if "error" in cassette:
            raise ProviderError(cassette["error"]["message"], cassette["error"]["retryable"])
        return cassette["response"]


class WebSearchProvider:
    name = "web"

    def __init__(self, api_key, client=None):
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=20, trust_env=False)

    def search(self, query, language, scope):
        if in_ci():
            raise ProviderError("live_calls_forbidden_in_ci")
        if not self.api_key:
            raise ProviderError("web_provider_key_missing")
        if language not in {"en", "fil"}:
            raise ValueError("unsupported language")
        suffix = " OR ".join("site:" + domain for domain in scope.site_restrict or [])
        scoped_query = query + (" (" + suffix + ")" if suffix else "")
        try:
            response = self.client.get("https://api.search.brave.com/res/v1/web/search",
                params={"q": scoped_query, "count": scope.max_results},
                headers={"Accept": "application/json", "X-Subscription-Token": self.api_key})
            response.raise_for_status()
            rows = response.json().get("web", {}).get("results", [])
            return [SearchResult(url=row["url"], title=row.get("title", ""), snippet=row.get("description", ""),
                                 published_hint=row.get("page_age"), provider=self.name, rank=i+1)
                    for i, row in enumerate(rows[:scope.max_results])]
        except httpx.HTTPStatusError as error:
            _log_upstream_error(self.name, error)
            raise ProviderError(
                "search_http_" + str(error.response.status_code),
                error.response.status_code == 429 or error.response.status_code >= 500,
            ) from error
        except (httpx.RequestError, ValueError, KeyError) as error:
            raise ProviderError("search_provider_failure", isinstance(error, httpx.RequestError)) from error

def _log_upstream_error(provider_name, error):
    """Log a sanitised upstream HTTP error category without secrets or descriptions.

    Reads the upstream JSON error body only for validated ALL_CAPS code strings
    (Brave returns ``{"errors":[{"code":"INVALID_QUERY",...}]}``).
    Description fields are never logged.  Non-JSON bodies are ignored.
    """
    try:
        body = error.response.json()
        errs = body.get("errors", [body]) if isinstance(body, dict) else [body]
        codes = [
            e.get("code", "") for e in (errs if isinstance(errs, list) else [errs])
            if isinstance(e, dict) and re.fullmatch(r"[A-Z][A-Z_]+", str(e.get("code", "")))
        ]
        if codes:
            logger.error(
                "upstream_search_error provider=%s status=%d code=%s",
                provider_name, error.response.status_code, codes[0],
            )
            return
    except (ValueError, KeyError, TypeError):
        pass
    logger.error("upstream_search_error provider=%s status=%d",
                 provider_name, error.response.status_code)


class CuratedPHProvider:
    name = "curated_ph"

    def __init__(self, web, record_key, sources_path="sources.yaml"):
        self.web = web
        self.sources = yaml.safe_load(Path(sources_path).read_text())
        if record_key not in self.sources:
            raise ValueError("unknown curated source key")
        self.record_key = record_key

    def search(self, query, language, scope):
        scoped = scope.model_copy(update={"site_restrict": scope.site_restrict or self.sources[self.record_key]["domains"]})
        return [row.model_copy(update={"provider": self.name}) for row in self.web.search(query, language, scoped)]


class RecordedSearchProvider:
    def __init__(self, provider, recorder):
        self.provider, self.recorder = provider, recorder
        self.name = provider.name
        self.record_key = getattr(provider, "record_key", None)

    def search(self, query, language, scope):
        request = dict(provider=self.name, record_key=self.record_key, query=query,
                       language=language, scope=scope.model_dump(mode="json"))
        response = self.recorder.call("search", request,
                    lambda: [r.model_dump(mode="json") for r in self.provider.search(query, language, scope)])
        return [SearchResult.model_validate(row) for row in response]
