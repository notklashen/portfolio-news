"""Credible-source policy and URL canonicalization."""

from __future__ import annotations

import re
from typing import Optional, Union
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


DEFAULT_ALLOWED_DOMAINS: tuple[str, ...] = (
    "reuters.com",
    "apnews.com",
    "bloomberg.com",
    "ft.com",
    "wsj.com",
    "bbc.com",
    "bbc.co.uk",
    "cnbc.com",
    "sec.gov",
    "cftc.gov",
    "finra.org",
    "justice.gov",
    "ftc.gov",
    "fda.gov",
    "fca.org.uk",
    "amf-france.org",
    "esma.europa.eu",
    "federalreserve.gov",
    "ecb.europa.eu",
    "bankofengland.co.uk",
    "banque-france.fr",
    "bundesbank.de",
    "bafin.de",
    "boj.or.jp",
    "bankofcanada.ca",
    "rba.gov.au",
    "snb.ch",
    "imf.org",
    "worldbank.org",
    "oecd.org",
    "bis.org",
    "europa.eu",
    "consilium.europa.eu",
    "un.org",
    "nato.int",
    "wto.org",
)

_TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "referrer",
    "source",
}
_DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


def normalize_domain(raw: str) -> str:
    value = raw.strip().casefold().rstrip(".")
    if "://" in value:
        parsed = urlsplit(value)
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("domain entries cannot include a path, query, or fragment")
        value = parsed.hostname or ""
    if value.startswith("www."):
        value = value[4:]
    if not _DOMAIN_PATTERN.fullmatch(value):
        raise ValueError("invalid domain")
    return value


def hostname_for_url(url: str) -> Optional[str]:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme.casefold() != "https" or parsed.username or parsed.password:
        return None
    if not parsed.hostname:
        return None
    return parsed.hostname.casefold().rstrip(".")


def is_url_allowed(
    url: str,
    allowed_domains: Union[tuple[str, ...], list[str], set[str]],
) -> bool:
    hostname = hostname_for_url(url)
    if hostname is None:
        return False
    for raw_domain in allowed_domains:
        try:
            domain = normalize_domain(raw_domain)
        except ValueError:
            continue
        if hostname == domain or hostname.endswith(f".{domain}"):
            return True
    return False


def canonicalize_url(url: str) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in url):
        raise ValueError("URL cannot contain control characters")
    parsed = urlsplit(url.strip())
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must be absolute HTTP(S)")
    scheme = "https" if parsed.scheme.casefold() == "https" else "http"
    hostname = parsed.hostname.casefold().rstrip(".")
    if hostname.startswith("www."):
        hostname = hostname[4:]
    port = parsed.port
    netloc = hostname
    if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        netloc = f"{hostname}:{port}"
    path = parsed.path or "/"
    while "//" in path:
        path = path.replace("//", "/")
    if path != "/":
        path = path.rstrip("/")
    filtered_query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.casefold()
        if lowered.startswith("utm_") or lowered in _TRACKING_QUERY_KEYS:
            continue
        filtered_query.append((key, value))
    query = urlencode(sorted(filtered_query))
    return urlunsplit((scheme, netloc, path, query, ""))
