"""Resolve and download versioned arXiv source documents."""

import hashlib
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from . import __version__

ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z][a-z0-9.-]*/\d{7})(?:v[1-9]\d*)?", re.ASCII)
VERSION = re.compile(r"v[1-9]\d*$")
HOSTS = {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}
MAX_ABSTRACT = 1024 * 1024
MAX_PDF = 25 * 1024 * 1024


class SourceError(ValueError):
    """The paper identity or document could not be verified."""


@dataclass(frozen=True)
class Paper:
    identifier: str
    title: str
    authors: tuple[str, ...]
    abstract_url: str
    pdf_url: str
    pdf_sha256: str
    retrieved_at: str

    def record(self) -> dict:
        return asdict(self)


class Metadata(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.values: dict[str, list[str]] = {}

    def handle_starttag(self, tag, attrs):
        if tag != "meta":
            return
        attrs = dict(attrs)
        key = attrs.get("name") or attrs.get("property")
        value = attrs.get("content")
        if key and value:
            self.values.setdefault(key, []).append(value)


def arxiv_id(reference: str) -> str:
    """Accept a bare identifier or a source link, rejecting other destinations."""
    value = reference.strip()
    if "://" in value:
        parts = urlsplit(value)
        if parts.scheme != "https" or parts.netloc.lower() not in HOSTS:
            raise SourceError("Expected an HTTPS arxiv.org link or arXiv identifier")
        pieces = parts.path.lstrip("/").split("/", 1)
        if len(pieces) != 2 or pieces[0] not in {"abs", "pdf", "html"}:
            raise SourceError("Expected an arXiv abs, pdf or html link")
        value = pieces[1].removesuffix(".pdf")
    elif value.lower().startswith("arxiv:"):
        value = value.split(":", 1)[1]
    if not ID.fullmatch(value):
        raise SourceError(f"Invalid arXiv identifier: {value}")
    return value


class ArxivRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urlsplit(newurl)
        if parts.scheme != "https" or parts.netloc.lower() not in HOSTS:
            raise SourceError("arXiv request redirected outside the allowed HTTPS hosts")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(url: str, maximum: int) -> bytes:
    request = Request(url, headers={"User-Agent": f"PaperFalsifier/{__version__} (interactive research retrieval)"})
    try:
        with build_opener(ArxivRedirects).open(request, timeout=25) as response:
            data = response.read(maximum + 1)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise SourceError(f"Unable to retrieve {url}: {exc}") from exc
    if len(data) > maximum:
        raise SourceError(f"Source exceeds {maximum} bytes")
    return data


def fetch(reference: str) -> tuple[Paper, bytes]:
    requested = arxiv_id(reference)
    metadata = Metadata()
    metadata.feed(_download(f"https://arxiv.org/abs/{requested}", MAX_ABSTRACT).decode("utf-8"))
    if VERSION.search(requested):
        identifier = requested
    else:
        canonical = metadata.values.get("og:url", [])
        if len(canonical) != 1:
            raise SourceError("Expected one canonical arXiv version URL")
        identifier = arxiv_id(canonical[0])
        if not VERSION.search(identifier) or VERSION.sub("", identifier) != requested:
            raise SourceError("Canonical arXiv URL does not pin the requested paper")
    titles = metadata.values.get("citation_title", [])
    authors = tuple(metadata.values.get("citation_author", []))
    if len(titles) != 1 or not authors:
        raise SourceError("arXiv metadata is missing its title or authors")
    pdf_url = f"https://arxiv.org/pdf/{identifier}"
    pdf = _download(pdf_url, MAX_PDF)
    if not pdf.startswith(b"%PDF-"):
        raise SourceError("Source did not return PDF bytes")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    paper = Paper(identifier, titles[0], authors, f"https://arxiv.org/abs/{identifier}",
                  pdf_url, hashlib.sha256(pdf).hexdigest(), now)
    return paper, pdf


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()
