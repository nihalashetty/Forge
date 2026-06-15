"""Website crawl link extraction + source re-ingest."""

from __future__ import annotations

from forge.db.base import SessionLocal
from forge.knowledge.crawl import extract_links
from forge.services.knowledge import KnowledgeService


def test_extract_links_same_domain_only():
    html = """
      <a href="/about">About</a>
      <a href="https://acme.test/pricing">Pricing</a>
      <a href="https://other.test/x">External</a>
      <a href="/about#team">Fragment dup</a>
      <a href="mailto:hi@acme.test">Mail</a>
    """
    links = extract_links(html, "https://acme.test/")
    assert "https://acme.test/about" in links
    assert "https://acme.test/pricing" in links
    assert "https://other.test/x" not in links  # external domain excluded
    assert "mailto:hi@acme.test" not in links
    assert links.count("https://acme.test/about") == 1  # fragment deduped


async def test_reingest_text_source_reembeds():
    async with SessionLocal() as s:
        src = await KnowledgeService.create_source(s, "t_re", "p_re", kind="text", name="doc",
                                                   text="Forge supports website crawling and re-ingest now.")
        src = await KnowledgeService.ingest(s, src)
        assert src.status == "ready" and src.chunks >= 1
        src2 = await KnowledgeService.reingest(s, src)
    assert src2.status == "ready" and src2.chunks >= 1
