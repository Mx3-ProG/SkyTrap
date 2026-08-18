from html.parser import HTMLParser

HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


class SeoHTMLParser(HTMLParser):
    """Extracts just what a technical SEO audit needs from a page's HTML: the
    <title>, relevant <meta>/<link> tags, and a count of each heading level.
    Deliberately not a general-purpose HTML parser — narrow and specific to this
    skill's checks."""

    def __init__(self) -> None:
        super().__init__()
        self.title: str | None = None
        self.meta: dict[str, str] = {}
        self.heading_counts: dict[str, int] = dict.fromkeys(HEADING_TAGS, 0)
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)

        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = attrs_dict.get("name") or attrs_dict.get("property")
            content = attrs_dict.get("content")
            if key and content is not None:
                self.meta[key.lower()] = content
        elif tag in HEADING_TAGS:
            self.heading_counts[tag] += 1
        elif tag == "link" and (attrs_dict.get("rel") or "").lower() == "canonical":
            href = attrs_dict.get("href")
            if href:
                self.meta["canonical_href"] = href

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title = (self.title or "") + data
