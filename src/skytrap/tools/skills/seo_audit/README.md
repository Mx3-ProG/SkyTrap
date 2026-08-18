# seo_audit

Technical SEO checks that complement `lighthouse_audit` (which already reports
a general SEO score plus performance/accessibility/best-practices) rather than
duplicating it: title/meta description length, `<link rel="canonical">`, Open
Graph tags (`og:title`, `og:description`, `og:image`), heading hierarchy (h1
count), `robots.txt` and `sitemap.xml` presence.

Read-only, no confirmation needed — same local-only restriction as
`lighthouse_audit`/`accessibility_check` (only `localhost`/`127.0.0.1` URLs).

## Arguments

```json
{"url": "http://localhost:3000/"}
```

## Dependencies

None new — `httpx` (already a project dependency) for fetching, Python's
stdlib `html.parser` for extraction.
