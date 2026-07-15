"""Website crawling for knowledge ingestion.

`crawl_site` does a same-domain BFS from a start URL (SSRF-guarded, redirect-safe) up to
`max_pages`/`max_depth`, honoring robots.txt with a small politeness delay between fetches, and
returns {url: text} so each page keeps its own provenance. `extract_links` (pure, testable)
pulls same-domain links from a page.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

# Bounds so a caller-supplied meta can't launch an unbounded crawl (DoS / runaway cost).
MAX_PAGES_CAP = 200
MAX_DEPTH_CAP = 5
DEFAULT_MAX_PAGES = 25
DEFAULT_MAX_DEPTH = 2
DEFAULT_DELAY_SECONDS = 0.3  # politeness gap between fetches (also raised to robots Crawl-delay)
_USER_AGENT = "ForgeKnowledgeBot"


def extract_links(html: str, base_url: str) -> list[str]:
    """Absolute, same-domain http(s) links from a page (deduped, fragments stripped)."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        hrefs = [a.get("href") for a in soup.find_all("a", href=True)]
    except Exception:  # noqa: BLE001 - fall back to a crude regex
        import re
        hrefs = re.findall(r'href=["\']([^"\']+)["\']', html or "")

    base_host = urlparse(base_url).netloc
    out: list[str] = []
    for href in hrefs:
        if not href:
            continue
        u = urljoin(base_url, href).split("#")[0]
        p = urlparse(u)
        if p.scheme in ("http", "https") and p.netloc == base_host and u not in out:
            out.append(u)
    return out


async def _load_robots(client, start_url: str) -> RobotFileParser | None:
    """Fetch + parse the site's robots.txt through the SSRF-guarded client (never urllib's own
    opener, which would bypass the egress guard). None on any failure => treat as allow-all,
    the standard behavior when a site publishes no robots.txt."""
    from forge.util.ssrf import guarded_get

    p = urlparse(start_url)
    robots_url = f"{p.scheme}://{p.netloc}/robots.txt"
    try:
        r = await guarded_get(client, robots_url, timeout=10, follow_redirects=True)
        if r.status_code >= 400:
            return None
        rp = RobotFileParser()
        rp.parse(r.text.splitlines())
        return rp
    except Exception:  # noqa: BLE001 - unreachable / invalid robots -> allow-all
        return None


def _allowed(rp: RobotFileParser | None, url: str) -> bool:
    if rp is None:
        return True
    try:
        return rp.can_fetch(_USER_AGENT, url)
    except Exception:  # noqa: BLE001 - be permissive if the parser chokes on an entry
        return True


async def crawl_site(
    start_url: str, max_pages: int = DEFAULT_MAX_PAGES, *,
    max_depth: int = DEFAULT_MAX_DEPTH, delay: float = DEFAULT_DELAY_SECONDS,
) -> dict[str, str]:
    """Same-domain BFS from ``start_url``, returning {url: extracted_text}.

    Honors robots.txt (skips disallowed URLs, respects any Crawl-delay), waits ``delay`` seconds
    between fetches to stay polite, and stops at ``max_pages`` pages or ``max_depth`` link hops
    from the start (both clamped to hard caps). Unreachable pages are skipped, not fatal.
    """
    from forge.services.knowledge import _strip_html
    from forge.util.http import shared_async_client
    from forge.util.ssrf import guarded_get

    max_pages = max(1, min(int(max_pages or DEFAULT_MAX_PAGES), MAX_PAGES_CAP))
    max_depth = max(0, min(int(max_depth if max_depth is not None else DEFAULT_MAX_DEPTH), MAX_DEPTH_CAP))
    client = shared_async_client()
    rp = await _load_robots(client, start_url)
    robots_delay = rp.crawl_delay(_USER_AGENT) if rp else None
    delay = max(float(delay or 0.0), float(robots_delay or 0.0))

    seen: set[str] = set()
    queue: list[tuple[str, int]] = [(start_url, 0)]
    pages: dict[str, str] = {}
    first = True
    while queue and len(pages) < max_pages:
        url, depth = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        if not _allowed(rp, url):
            continue
        if not first and delay > 0:
            await asyncio.sleep(delay)
        first = False
        try:
            r = await guarded_get(client, url, timeout=20, follow_redirects=True)
            html = r.text
        except Exception:  # noqa: BLE001 - skip unreachable pages
            continue
        pages[url] = _strip_html(html)
        if depth < max_depth:
            for link in extract_links(html, url):
                if link not in seen:
                    queue.append((link, depth + 1))
    return pages
