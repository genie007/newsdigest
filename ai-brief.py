#!/usr/bin/env python3
"""Daily AI Industry Brief - emails a morning digest of AI/supply-chain news."""

import argparse
import html
import json
import logging
import os
import subprocess
import urllib.request
from datetime import date, datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# -- Config -------------------------------------------------------------------
GOG_CMD = os.environ.get("GOG_CMD", "/opt/homebrew/bin/gog")
FROM = os.environ["AI_BRIEF_FROM"]
TO = os.environ["AI_BRIEF_TO"]
GOG_KEYRING_PASSWORD = os.environ["GOG_KEYRING_PASSWORD"]

# Each category: (display_name, search_query, result_limit)
CATEGORIES = [
    ("Supply Chain AI", "AI supply chain logistics planning", 5),
    ("Blue Yonder & Competitors", '"Blue Yonder" OR "Kinaxis" OR "o9 Solutions" OR "Manhattan Associates" OR "SAP IBP"', 5),
    ("Enterprise AI / Agents", "AI agent enterprise automation workflow", 5),
    ("LLM & Language Models", "LLM language model", 5),
]

HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search?tags=story&query={query}&numericFilters=created_at_i>{since}"


# -- Fetch --------------------------------------------------------------------
def fetch_hn(query, since_ts, limit=5):
    """Fetch stories from HN Algolia API, sorted by points descending."""
    try:
        encoded_query = urllib.parse.quote(query)
        full_url = HN_SEARCH_URL.format(query=encoded_query, since=since_ts)
        req = urllib.request.Request(full_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        items = []
        for hit in data.get("hits", []):
            title = hit.get("title", "")
            url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
            points = hit.get("points", 0)
            comments = hit.get("num_comments", 0)
            items.append({"title": title, "url": url, "points": points, "comments": comments})
        items.sort(key=lambda x: x["points"], reverse=True)
        return items[:limit]
    except Exception as exc:
        log.error("Fetch error for %r: %s", query, exc)
        return []


# -- Build email --------------------------------------------------------------
def build_email(stories):
    """Return (plain_text, html) for the digest email."""
    today = date.today().strftime("%B %d, %Y")

    # Plain text
    lines = [f"AI Industry Brief -- {today}", "=" * 50, ""]
    for category_name, items in stories:
        lines.append(category_name)
        lines.append("-" * len(category_name))
        for story in items:
            lines.append(f"  {story['title']}")
            lines.append(f"  {story['url']}")
            lines.append(f"  {story['points']} pts | {story['comments']} comments")
            lines.append("")
    lines.append("Have a great day!")
    plain_text = "\n".join(lines)

    # HTML
    category_html_parts = []
    for category_name, items in stories:
        story_blocks = []
        for story in items:
            escaped_title = html.escape(story["title"])
            story_blocks.append(
                f'<div style="margin-bottom:16px;padding:12px;border-left:3px solid #1a73e8;background:#f8f9fa">'
                f'  <a href="{story["url"]}" style="font-weight:bold;color:#1a73e8;text-decoration:none">{escaped_title}</a>'
                f'  <br><span style="color:#888;font-size:12px">{story["points"]} pts &nbsp;|&nbsp; {story["comments"]} comments</span>'
                f'</div>'
            )
        category_html_parts.append(
            f'<h3 style="color:#333;margin-top:24px">{html.escape(category_name)}</h3>'
            + "\n".join(story_blocks)
        )

    html_body = (
        f'<html><body style="font-family:Arial,sans-serif;max-width:650px;margin:auto;color:#222">'
        f'<h2 style="color:#1a73e8">AI Industry Brief &mdash; {today}</h2>'
        f'<p style="color:#666">Your daily digest of AI &amp; supply-chain intelligence</p>'
        f'<hr style="border:1px solid #eee">'
        + "\n".join(category_html_parts)
        + '<hr style="border:1px solid #eee;margin-top:24px">'
        + '<p style="color:#999;font-size:12px">Sent by ai-brief</p>'
        + '</body></html>'
    )

    return plain_text, html_body


# -- Send ---------------------------------------------------------------------
def send_email(subject, plain_text, html_body):
    """Send via gog CLI using --body and --body-html."""
    env = os.environ.copy()
    env["GOG_KEYRING_PASSWORD"] = GOG_KEYRING_PASSWORD

    result = subprocess.run(
        [
            GOG_CMD, "gmail", "send",
            "--account", FROM,
            "--to", TO,
            "--subject", subject,
            "--body", plain_text,
            "--body-html", html_body,
        ],
        capture_output=True, text=True, env=env,
    )
    if result.returncode == 0:
        log.info("Email sent successfully.")
    else:
        log.error("Email send failed: %s", result.stderr)


# -- Main ---------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Daily AI Industry Brief")
    parser.add_argument("--dry-run", action="store_true", help="Print email to stdout instead of sending")
    args = parser.parse_args()

    since_ts = int((datetime.now() - timedelta(hours=24)).timestamp())
    log.info("Fetching AI news for %s ...", date.today())

    seen_urls = set()
    stories = []
    for category_name, query, limit in CATEGORIES:
        raw_items = fetch_hn(query, since_ts, limit=limit + 5)  # fetch extra to allow for dedup
        deduped = []
        for item in raw_items:
            if item["url"] not in seen_urls:
                seen_urls.add(item["url"])
                deduped.append(item)
            if len(deduped) == limit:
                break
        stories.append((category_name, deduped))

    total = sum(len(items) for _, items in stories)
    if total == 0:
        log.info("No stories found today.")
        return

    today_short = date.today().strftime("%b %d")
    subject = f"AI Brief {today_short} -- {total} stories"
    plain_text, html_body = build_email(stories)

    if args.dry_run:
        print(f"Subject: {subject}\n")
        print(plain_text)
    else:
        send_email(subject, plain_text, html_body)


if __name__ == "__main__":
    main()
