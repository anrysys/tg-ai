#!/usr/bin/env python3
"""Generate the GitHub Pages site from the repository's own markdown.

The site exists for one reason: a README on github.com cannot carry JSON-LD
structured data, OpenGraph tags or a canonical URL, and those are what make the
project citable by search engines and by generative answer engines.

It is *generated*, never hand-written, because ADR-0007 says a fact lives in
exactly one place. `README.md` and `USE-CASES.md` are that place; this script
renders them. `just site-check` rebuilds and fails if the committed HTML differs,
so the two cannot drift apart silently.

Standard library only, on purpose: a markdown dependency would need an ADR under
AGENTS.md section 4, and the markdown actually used here is a small subset.

Run via `just site-build`.
"""

from __future__ import annotations

import argparse
import filecmp
import html
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"

BASE_URL = "https://anrysys.github.io/tg-ai"
REPO_URL = "https://github.com/anrysys/tg-ai"
BLOB = f"{REPO_URL}/blob/main"

#: Which markdown file becomes which page, with the metadata only a human can
#: write. Order matters: it is also the sitemap order.
PAGES: tuple[dict[str, str], ...] = (
    {
        "source": "README.md",
        "output": "index.html",
        "title": "tg-ai - Telegram MCP Server for AI Agents",
        "description": (
            "Local MCP server that lets Claude Code, Cursor, Codex, Antigravity and any "
            "other AI agent drive your personal Telegram account: send as you, read "
            "replies, full-text search your whole chat history, and draft replies in "
            "your own per-chat writing style."
        ),
        "priority": "1.0",
    },
    {
        "source": "USE-CASES.md",
        "output": "use-cases.html",
        "title": "tg-ai use cases: 40 things you can stop doing by hand",
        "description": (
            "Forty worked scenarios with copy-paste prompts for the tg-ai Telegram MCP "
            "server: instant search across years of chat history, inbox triage without "
            "opening Telegram, reports sent from your terminal, and replies drafted in "
            "your own voice."
        ),
        "priority": "0.9",
    },
)

#: Docs concatenated into llms-full.txt, in reading order. These are the files an
#: answer engine needs to describe the project correctly without guessing.
LLMS_FULL_SOURCES: tuple[str, ...] = (
    "README.md",
    "USE-CASES.md",
    "docs/30-api/mcp-tools.md",
    "docs/10-product/prd.md",
    "docs/20-architecture/sad.md",
    "docs/20-architecture/adr/0005-hard-block-send-to-unknown-peers.md",
    "docs/20-architecture/adr/0008-dialog-persona-hybrid-authorship.md",
    "docs/70-ops/security.md",
)


# --- inline markdown -------------------------------------------------------

_CODE_SPAN_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<![\w*])_([^_]+)_(?![\w*])")
_AUTOLINK_RE = re.compile(r"<(https?://[^>]+)>")


def rewrite_link(target: str) -> str:
    """Point a repo-relative link at something that exists on the web.

    Links between the two rendered pages stay internal; everything else goes to
    the file on github.com, because the site publishes two pages and the docs
    tree stays in the repository where agents read it.
    """
    if target.startswith(("http://", "https://", "mailto:", "#")):
        return target
    # The logo is the one asset copied into the site, so it keeps a local path.
    if target.endswith("assets/logo.svg"):
        return "assets/logo.svg"
    anchor = ""
    if "#" in target:
        target, anchor = target.split("#", 1)
        anchor = "#" + anchor
    if target == "README.md":
        return "index.html" + anchor
    if target == "USE-CASES.md":
        return "use-cases.html" + anchor
    return f"{BLOB}/{target.lstrip('./')}{anchor}"


_RAW_ATTR_RE = re.compile(r'\b(src|href)="([^"]+)"')


def rewrite_raw_attributes(line: str) -> str:
    """Rewrite src/href inside a passthrough HTML line the same way links are."""
    return _RAW_ATTR_RE.sub(
        lambda m: f'{m.group(1)}="{html.escape(rewrite_link(m.group(2)), quote=True)}"', line
    )


def render_inline(text: str) -> str:
    """Render the inline markdown subset, protecting code spans from the rest."""
    spans: list[str] = []

    def stash(match: re.Match[str]) -> str:
        spans.append(html.escape(match.group(1)))
        return f"\x00{len(spans) - 1}\x00"

    text = _CODE_SPAN_RE.sub(stash, text)
    text = html.escape(text, quote=False)
    text = _AUTOLINK_RE.sub(r'<a href="\1">\1</a>', text)

    def link(match: re.Match[str]) -> str:
        label, target = match.group(1), match.group(2)
        href = html.escape(rewrite_link(target), quote=True)
        external = href.startswith("http")
        rel = ' target="_blank" rel="noopener"' if external else ""
        return f'<a href="{href}"{rel}>{label}</a>'

    text = _LINK_RE.sub(link, text)
    text = _BOLD_RE.sub(r"<strong>\1</strong>", text)
    text = _ITALIC_RE.sub(r"<em>\1</em>", text)

    for index, span in enumerate(spans):
        text = text.replace(f"\x00{index}\x00", f"<code>{span}</code>")
    return text


def slugify(heading: str) -> str:
    """Reproduce GitHub's heading anchor, so in-page links survive the move."""
    text = _CODE_SPAN_RE.sub(r"\1", heading)
    text = _LINK_RE.sub(r"\1", text)
    text = text.replace("**", "").replace("_", "")
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    return re.sub(r"[\s]+", "-", text)


# --- block markdown --------------------------------------------------------


def render_table(rows: list[str]) -> str:
    """Render a GFM pipe table. Row 2 is the alignment row and is discarded."""

    def cells(row: str) -> list[str]:
        return [cell.strip() for cell in row.strip().strip("|").split("|")]

    head = cells(rows[0])
    body = [cells(row) for row in rows[2:]]
    out = ['<div class="table-wrap"><table>', "<thead><tr>"]
    out += [f"<th>{render_inline(cell)}</th>" for cell in head]
    out.append("</tr></thead><tbody>")
    for row in body:
        out.append("<tr>" + "".join(f"<td>{render_inline(c)}</td>" for c in row) + "</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def render_markdown(text: str) -> tuple[str, list[dict[str, str]]]:
    """Render the markdown subset used in this repository.

    Returns the HTML and the heading outline, which the caller turns into
    structured data.
    """
    lines = text.split("\n")
    out: list[str] = []
    outline: list[dict[str, str]] = []
    index = 0
    total = len(lines)

    while index < total:
        line = lines[index]
        stripped = line.strip()

        if not stripped:
            index += 1
            continue

        # Front matter, only ever at the very top.
        if index == 0 and stripped == "---":
            index += 1
            while index < total and lines[index].strip() != "---":
                index += 1
            index += 1
            continue

        # Fenced code.
        if stripped.startswith("```"):
            language = stripped[3:].strip() or "text"
            index += 1
            block: list[str] = []
            while index < total and not lines[index].strip().startswith("```"):
                block.append(lines[index])
                index += 1
            index += 1
            code = html.escape("\n".join(block))
            out.append(
                f'<pre><code class="language-{html.escape(language, quote=True)}">'
                f"{code}</code></pre>"
            )
            continue

        # Raw HTML passthrough: <details>, <summary>, <p align>, <img>, <a>.
        # src and href still need rewriting - a repo-relative path is correct on
        # github.com and broken on the site.
        if stripped.startswith("<"):
            out.append(rewrite_raw_attributes(stripped))
            index += 1
            continue

        # Headings.
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            level = len(heading.group(1))
            raw = heading.group(2).strip()
            slug = slugify(raw)
            outline.append({"level": str(level), "text": raw, "slug": slug})
            body = render_inline(raw)
            out.append(
                f'<h{level} id="{html.escape(slug, quote=True)}">'
                f'<a class="anchor" href="#{html.escape(slug, quote=True)}">#</a>'
                f"{body}</h{level}>"
            )
            index += 1
            continue

        # Horizontal rule.
        if re.fullmatch(r"-{3,}|\*{3,}", stripped):
            out.append("<hr>")
            index += 1
            continue

        # Table.
        if (
            stripped.startswith("|")
            and index + 1 < total
            and re.match(r"^\|[\s:|-]+\|$", lines[index + 1].strip())
        ):
            rows = []
            while index < total and lines[index].strip().startswith("|"):
                rows.append(lines[index])
                index += 1
            out.append(render_table(rows))
            continue

        # Blockquote, including GitHub alerts.
        if stripped.startswith(">"):
            block = []
            while index < total and lines[index].strip().startswith(">"):
                block.append(re.sub(r"^\s*>\s?", "", lines[index]))
                index += 1
            joined = "\n".join(block).strip()
            alert = re.match(r"^\[!(\w+)\]\s*(.*)$", joined, re.DOTALL)
            css, label = "quote", ""
            if alert:
                kind = alert.group(1).lower()
                css = f"quote alert alert-{kind}"
                label = f'<p class="alert-label">{kind.title()}</p>'
                joined = alert.group(2).strip()
            inner, _ = render_markdown(joined)
            out.append(f'<blockquote class="{css}">{label}{inner}</blockquote>')
            continue

        # Lists.
        bullet = re.match(r"^(\s*)([-*+]|\d+\.)\s+(.*)$", line)
        if bullet:
            ordered = bool(re.match(r"^\d+\.$", bullet.group(2)))
            tag = "ol" if ordered else "ul"
            items: list[str] = []
            while index < total:
                current = lines[index]
                match = re.match(r"^(\s*)([-*+]|\d+\.)\s+(.*)$", current)
                if match and len(match.group(1)) == len(bullet.group(1)):
                    items.append(match.group(3).strip())
                    index += 1
                elif current.strip() and current.startswith((" ", "\t")):
                    # A wrapped continuation line belongs to the item above it.
                    if items:
                        items[-1] += " " + current.strip()
                    index += 1
                else:
                    break
            body = "".join(f"<li>{render_inline(item)}</li>" for item in items)
            out.append(f"<{tag}>{body}</{tag}>")
            continue

        # Paragraph.
        block = []
        while index < total:
            current = lines[index]
            if not current.strip() or current.strip().startswith(
                ("#", ">", "|", "```", "<", "---")
            ):
                break
            if re.match(r"^(\s*)([-*+]|\d+\.)\s+", current):
                break
            block.append(current.strip())
            index += 1
        if block:
            out.append(f"<p>{render_inline(' '.join(block))}</p>")

    return "\n".join(out), outline


# --- structured data -------------------------------------------------------


def extract_faq(markdown: str) -> list[dict[str, str]]:
    """Pull the README FAQ into schema.org Question/Answer pairs.

    Generated from the page itself so the structured data can never claim an
    answer the page does not give - which is the whole point of marking it up.
    """
    match = re.search(r"^## FAQ\s*$(.*?)(?=^## )", markdown, re.MULTILINE | re.DOTALL)
    if not match:
        return []
    entries: list[dict[str, str]] = []
    for question in re.finditer(
        r"^### (.+?)\s*$(.*?)(?=^### |\Z)", match.group(1), re.MULTILINE | re.DOTALL
    ):
        text = question.group(2)
        text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
        text = _CODE_SPAN_RE.sub(r"\1", text)
        text = _LINK_RE.sub(r"\1", text)
        text = text.replace("**", "").replace("_", "")
        text = " ".join(text.split())
        if text:
            entries.append({"q": question.group(1).strip(), "a": text})
    return entries


def extract_howto(markdown: str) -> list[str]:
    """Turn the quickstart shell block into HowTo steps."""
    match = re.search(r"^## Quickstart\s*$(.*?)(?=^## )", markdown, re.MULTILINE | re.DOTALL)
    if not match:
        return []
    block = re.search(r"```bash\n(.*?)```", match.group(1), re.DOTALL)
    if not block:
        return []
    steps: list[str] = []
    for line in block.group(1).split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        command, _, comment = line.partition("#")
        steps.append(f"{command.strip()} - {comment.strip()}" if comment else command.strip())
    return steps


def json_ld(page: dict[str, str], markdown: str) -> str:
    """Build the @graph for one page."""
    url = f"{BASE_URL}/{'' if page['output'] == 'index.html' else page['output']}"
    graph: list[dict[str, object]] = [
        {
            "@type": "WebSite",
            "@id": f"{BASE_URL}/#website",
            "url": f"{BASE_URL}/",
            "name": "tg-ai",
            "description": PAGES[0]["description"],
            "inLanguage": "en",
        },
        {
            "@type": "WebPage",
            "@id": f"{url}#webpage",
            "url": url,
            "name": page["title"],
            "description": page["description"],
            "isPartOf": {"@id": f"{BASE_URL}/#website"},
            "inLanguage": "en",
        },
    ]

    if page["output"] == "index.html":
        graph.append(
            {
                "@type": "SoftwareApplication",
                "@id": f"{BASE_URL}/#software",
                "name": "tg-ai",
                "alternateName": "tg-ai Telegram MCP server",
                "applicationCategory": "DeveloperApplication",
                "applicationSubCategory": "Model Context Protocol server",
                "operatingSystem": "Linux, macOS, Windows",
                "url": f"{BASE_URL}/",
                "downloadUrl": REPO_URL,
                "codeRepository": REPO_URL,
                "programmingLanguage": "Python",
                "softwareVersion": "0.1.0",
                "license": "https://opensource.org/licenses/MIT",
                "description": page["description"],
                "softwareRequirements": "Python 3.12+, PostgreSQL 16, Docker, uv, just",
                "featureList": [
                    "Send Telegram messages from your own account via an AI agent",
                    "Read replies live without marking chats as read",
                    "Full-text search your entire chat history in local PostgreSQL",
                    "Per-chat writing-style profile (Dialog Persona) for drafted replies",
                    "Non-overridable Send Guard that refuses to message strangers",
                    "Runs fully local; the archive never leaves 127.0.0.1",
                ],
                "keywords": (
                    "Telegram MCP server, Model Context Protocol, MTProto, AI agent, "
                    "Claude Code, Cursor, chat history search, PostgreSQL, local-first"
                ),
                "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
                "author": {
                    "@type": "Person",
                    "name": "anrysys",
                    "url": "https://github.com/anrysys",
                },
            }
        )
        steps = extract_howto(markdown)
        if steps:
            graph.append(
                {
                    "@type": "HowTo",
                    "@id": f"{BASE_URL}/#howto",
                    "name": "Install the tg-ai Telegram MCP server",
                    "description": "Clone, configure, authenticate, sync and register tg-ai with your AI client.",
                    "step": [
                        {"@type": "HowToStep", "position": i, "name": s, "text": s}
                        for i, s in enumerate(steps, start=1)
                    ],
                }
            )
        faq = extract_faq(markdown)
        if faq:
            graph.append(
                {
                    "@type": "FAQPage",
                    "@id": f"{BASE_URL}/#faq",
                    "mainEntity": [
                        {
                            "@type": "Question",
                            "name": entry["q"],
                            "acceptedAnswer": {"@type": "Answer", "text": entry["a"]},
                        }
                        for entry in faq
                    ],
                }
            )
    else:
        graph.append(
            {
                "@type": "BreadcrumbList",
                "itemListElement": [
                    {"@type": "ListItem", "position": 1, "name": "tg-ai", "item": f"{BASE_URL}/"},
                    {"@type": "ListItem", "position": 2, "name": "Use cases", "item": url},
                ],
            }
        )

    return json.dumps({"@context": "https://schema.org", "@graph": graph}, indent=2)


# --- page assembly ---------------------------------------------------------

TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<link rel="canonical" href="{url}">
<meta name="author" content="anrysys">
<meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1">
<meta property="og:type" content="website">
<meta property="og:site_name" content="tg-ai">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:url" content="{url}">
<meta property="og:image" content="{base}/og.png">
<meta property="og:image:width" content="1280">
<meta property="og:image:height" content="640">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{description}">
<meta name="twitter:image" content="{base}/og.png">
<link rel="alternate" type="text/plain" href="{base}/llms.txt" title="llms.txt">
<script type="application/ld+json">
{jsonld}
</script>
<style>{css}</style>
</head>
<body>
<a class="skip" href="#content">Skip to content</a>
<header class="topbar">
  <nav>
    <a class="brand" href="./">tg-ai</a>
    <a href="./">Overview</a>
    <a href="use-cases.html">Use cases</a>
    <a href="{repo}" target="_blank" rel="noopener">GitHub</a>
    <a href="{blob}/docs/README.md" target="_blank" rel="noopener">Docs</a>
  </nav>
</header>
<main id="content">
{body}
</main>
<footer>
  <p>MIT licensed. Built for one personal Telegram account on one machine.
  <a href="{repo}" target="_blank" rel="noopener">Source on GitHub</a> ·
  <a href="{blob}/docs/i18n/ru/README.md" target="_blank" rel="noopener">Russian</a></p>
</footer>
</body>
</html>
"""

CSS = """
:root{color-scheme:light dark;--bg:#ffffff;--fg:#1a1d23;--muted:#5b6472;--line:#e3e7ed;
--accent:#2563eb;--code-bg:#f5f7fa;--card:#f9fafb;--warn-bg:#fff8e6;--warn-line:#e0a800}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#9aa7b6;
--line:#252b33;--accent:#6ea8ff;--code-bg:#161b22;--card:#11161d;--warn-bg:#2a2110;--warn-line:#7a5c00}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Inter,sans-serif;
-webkit-text-size-adjust:100%}
.skip{position:absolute;left:-9999px}.skip:focus{left:8px;top:8px;background:var(--accent);
color:#fff;padding:8px 12px;border-radius:6px;z-index:10}
.topbar{position:sticky;top:0;background:color-mix(in srgb,var(--bg) 92%,transparent);
backdrop-filter:blur(8px);border-bottom:1px solid var(--line);z-index:5}
.topbar nav{max-width:860px;margin:0 auto;padding:12px 20px;display:flex;gap:20px;
align-items:center;flex-wrap:wrap;font-size:14px}
.topbar a{color:var(--muted);text-decoration:none}.topbar a:hover{color:var(--accent)}
.topbar .brand{font-weight:700;color:var(--fg);font-size:16px;letter-spacing:.5px}
main{max-width:860px;margin:0 auto;padding:32px 20px 64px}
h1,h2,h3,h4{line-height:1.25;margin:1.8em 0 .6em;scroll-margin-top:70px}
h1{font-size:2.1rem;margin-top:.4em;letter-spacing:-.02em}
h2{font-size:1.5rem;padding-bottom:.3em;border-bottom:1px solid var(--line)}
h3{font-size:1.17rem}
h1 .anchor,h2 .anchor,h3 .anchor,h4 .anchor{float:left;margin-left:-.8em;padding-right:.3em;
color:var(--line);text-decoration:none;opacity:0}
h1:hover .anchor,h2:hover .anchor,h3:hover .anchor,h4:hover .anchor{opacity:1}
p{margin:0 0 1em}
a{color:var(--accent)}
img{max-width:100%;height:auto}
code{background:var(--code-bg);padding:.15em .4em;border-radius:4px;
font:0.87em/1.5 ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace}
pre{background:var(--code-bg);border:1px solid var(--line);border-radius:8px;
padding:14px 16px;overflow-x:auto;margin:0 0 1.2em}
pre code{background:none;padding:0;font-size:.85rem;line-height:1.55}
.table-wrap{overflow-x:auto;margin:0 0 1.4em;border:1px solid var(--line);border-radius:8px}
table{border-collapse:collapse;width:100%;font-size:.93rem}
th,td{text-align:left;padding:10px 14px;border-bottom:1px solid var(--line);vertical-align:top}
th{background:var(--card);font-weight:600;white-space:nowrap}
tbody tr:last-child td{border-bottom:none}
blockquote.quote{margin:0 0 1.3em;padding:12px 18px;border-left:3px solid var(--line);
background:var(--card);border-radius:0 8px 8px 0;color:var(--muted)}
blockquote.quote p:last-child{margin-bottom:0}
blockquote.alert-warning{background:var(--warn-bg);border-left-color:var(--warn-line);color:var(--fg)}
.alert-label{font-weight:700;text-transform:uppercase;letter-spacing:.06em;font-size:.75rem;
margin:0 0 .4em}
ul,ol{margin:0 0 1.2em;padding-left:1.4em}li{margin:.3em 0}
hr{border:none;border-top:1px solid var(--line);margin:2.4em 0}
details{border:1px solid var(--line);border-radius:8px;padding:10px 16px;margin:0 0 .7em;
background:var(--card)}
details[open]{padding-bottom:4px}
summary{cursor:pointer;font-weight:600;margin:-2px 0}
footer{border-top:1px solid var(--line);padding:24px 20px 48px;text-align:center;
color:var(--muted);font-size:.86rem}
footer a{color:var(--muted)}
@media(max-width:600px){main{padding:20px 16px 48px}h1{font-size:1.7rem}h2{font-size:1.3rem}}
"""


def build_llms_full() -> str:
    """Concatenate the docs an answer engine needs, with provenance headers."""
    parts = [
        "# tg-ai - full documentation for language models",
        "",
        f"Source: {REPO_URL}",
        "Generated by scripts/build_site.py. Do not edit by hand.",
        "",
        "This file concatenates the documents that describe what tg-ai is, what it",
        "does, and what it deliberately refuses to do. Each section names its source",
        "file so a claim can be traced back.",
        "",
    ]
    for relative in LLMS_FULL_SOURCES:
        path = ROOT / relative
        if not path.exists():
            raise SystemExit(f"llms-full source missing: {relative}")
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.DOTALL)
        parts += ["=" * 78, f"# SOURCE: {relative}", "=" * 78, "", text.strip(), ""]
    return "\n".join(parts) + "\n"


def build_sitemap() -> str:
    urls = []
    for page in PAGES:
        loc = f"{BASE_URL}/{'' if page['output'] == 'index.html' else page['output']}"
        urls.append(
            "  <url>\n"
            f"    <loc>{loc}</loc>\n"
            f"    <changefreq>weekly</changefreq>\n"
            f"    <priority>{page['priority']}</priority>\n"
            "  </url>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls)
        + "\n</urlset>\n"
    )


ROBOTS = f"""# Every crawler is welcome, including the ones that train and answer.
User-agent: *
Allow: /

Sitemap: {BASE_URL}/sitemap.xml
"""


def build(site: Path, root_out: Path, *, quiet: bool = False) -> None:
    """Render every generated artefact into `site`, plus llms-full.txt in `root_out`."""

    def say(message: str) -> None:
        if not quiet:
            print(message)

    site.mkdir(parents=True, exist_ok=True)

    for page in PAGES:
        markdown = (ROOT / page["source"]).read_text(encoding="utf-8")
        body, _ = render_markdown(markdown)
        url = f"{BASE_URL}/{'' if page['output'] == 'index.html' else page['output']}"
        rendered = TEMPLATE.format(
            title=html.escape(page["title"], quote=True),
            description=html.escape(page["description"], quote=True),
            url=url,
            base=BASE_URL,
            repo=REPO_URL,
            blob=BLOB,
            jsonld=json_ld(page, markdown),
            css=CSS.strip(),
            body=body,
        )
        (site / page["output"]).write_text(rendered, encoding="utf-8")
        say(f"built site/{page['output']}")

    (site / "robots.txt").write_text(ROBOTS, encoding="utf-8")
    (site / "sitemap.xml").write_text(build_sitemap(), encoding="utf-8")
    (site / ".nojekyll").write_text("", encoding="utf-8")

    full = build_llms_full()
    (root_out / "llms-full.txt").write_text(full, encoding="utf-8")
    (site / "llms-full.txt").write_text(full, encoding="utf-8")
    shutil.copyfile(ROOT / "llms.txt", site / "llms.txt")

    # The logo is the only image the repository has and the site needs it inline.
    (site / "assets").mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "docs" / "assets" / "logo.svg", site / "assets" / "logo.svg")

    say("built site/{robots.txt,sitemap.xml,llms.txt,llms-full.txt,.nojekyll,assets/}")


#: Not generated by this script, so never compared against a fresh build.
#: og.png needs rsvg-convert and ImageMagick; `just site-og` rebuilds it.
UNGENERATED = {"og.png"}


def check() -> int:
    """Rebuild into a temporary directory and diff, without touching the tree.

    Deliberately not a `git status` check: the question is whether `site/` is
    what the markdown currently renders to, which is true or false regardless of
    what has been staged or committed.
    """
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        build(scratch / "site", scratch, quiet=True)

        expected = {
            path.relative_to(scratch).as_posix() for path in scratch.rglob("*") if path.is_file()
        }
        actual = {
            path.relative_to(ROOT).as_posix()
            for path in [*SITE.rglob("*"), ROOT / "llms-full.txt"]
            if path.is_file() and path.name not in UNGENERATED
        }

        problems: list[str] = []
        for name in sorted(expected - actual):
            problems.append(f"  missing:   {name}")
        for name in sorted(actual - expected):
            problems.append(f"  unexpected: {name}")
        for name in sorted(expected & actual):
            if not filecmp.cmp(scratch / name, ROOT / name, shallow=False):
                problems.append(f"  stale:     {name}")

    if problems:
        print("ERROR: generated output does not match the markdown.", file=sys.stderr)
        print("\n".join(problems), file=sys.stderr)
        print("Run `just site-build` and commit the result.", file=sys.stderr)
        return 1
    print("site: up to date")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify site/ matches the markdown instead of rewriting it",
    )
    if parser.parse_args().check:
        return check()
    build(SITE, ROOT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
