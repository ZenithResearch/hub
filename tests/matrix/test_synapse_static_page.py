import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "infra/matrix/synapse/static/index.html"


class PageContractParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data: str) -> None:
        self.text.append(data)


def parse_page() -> tuple[str, PageContractParser]:
    source = PAGE.read_text()
    parser = PageContractParser()
    parser.feed(source)
    return source, parser


def test_static_page_has_locked_copy_and_semantic_structure():
    source, parser = parse_page()
    text = " ".join(" ".join(parser.text).split())
    tags = [tag for tag, _ in parser.tags]

    assert "Zenith Matrix is running" in text
    assert "synapse.zenith-research.ca" in text
    assert "Get ZenithOS" in text
    assert "Built on Matrix" in text
    assert tags.count("h1") == 1
    assert "main" in tags
    assert "footer" in tags
    assert "lang=\"en\"" in source


def test_static_page_links_are_exact_and_matrix_link_is_isolated():
    _, parser = parse_page()
    links = [attrs for tag, attrs in parser.tags if tag == "a"]

    assert {link.get("href") for link in links} == {
        "https://github.com/ZenithResearch/ZenithOS",
        "https://matrix.org",
    }
    matrix_link = next(link for link in links if link.get("href") == "https://matrix.org")
    assert matrix_link.get("target") == "_blank"
    assert set((matrix_link.get("rel") or "").split()) == {"noopener", "noreferrer"}


def test_static_page_is_self_contained_and_tracks_canonical_mark_provenance():
    source, parser = parse_page()
    tags = [tag for tag, _ in parser.tags]

    assert "<svg" in source
    assert "ZenithResearch/ZenithOS/Resources/ZenithOSIcon.svg" in source
    assert "6d85132c52f1dc9021e124093f20164a64a89dff" in source
    assert "script" not in tags
    assert "img" not in tags
    assert "iframe" not in tags
    assert "@import" not in source
    assert not re.search(r"\bsrc=[\"']https?://", source, re.IGNORECASE)
    assert not re.search(r"url\s*\(\s*[\"']?https?://", source, re.IGNORECASE)
    assert "analytics" not in source.lower()
    assert "cookie" not in source.lower()


def test_static_page_declares_mobile_and_keyboard_accessibility_contracts():
    source, parser = parse_page()
    meta = [attrs for tag, attrs in parser.tags if tag == "meta"]

    assert any(
        item.get("name") == "viewport"
        and item.get("content") == "width=device-width, initial-scale=1"
        for item in meta
    )
    assert ":focus-visible" in source
    assert "@media (max-width: 320px)" in source
    assert "prefers-reduced-motion" not in source or "animation: none" in source
