"""Website crawling for knowledge ingestion.

`crawl_site` does a same-domain BFS from a start URL (SSRF-guarded, redirect-safe) up to
`max_pages`, returning {url: text}. `extract_links` (pure, testable) pulls same-domain
links from a page.
"""

from __future__ import annotations

from urllib.parse import urljoin, urlparse


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


async def crawl_site(start_url: str, max_pages: int = 10) -> dict[str, str]:
    from forge.services.knowledge import _strip_html
    from forge.util.http import shared_async_client
    from forge.util.ssrf import guarded_get

    seen: set[str] = set()
    queue: list[str] = [start_url]
    pages: dict[str, str] = {}
    while queue and len(pages) < max_pages:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            r = await guarded_get(shared_async_client(), url, timeout=20, follow_redirects=True)
            html = r.text
        except Exception:  # noqa: BLE001 - skip unreachable pages
            continue
        pages[url] = _strip_html(html)
        for link in extract_links(html, url):
            if link not in seen and link not in queue:
                queue.append(link)
    return pages
