#!/usr/bin/env python3
"""newsdigest - Configurable news digest via email from Hacker News and RSS feeds."""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import smtplib
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import TypedDict

import feedparser
import yaml
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search?tags=story&query={query}&numericFilters=created_at_i>{since}"


class Story(TypedDict):
    title: str
    url: str
    points: int
    comments: int
    source: str  # "hn" or "rss"
    published: int  # unix timestamp


# -- Config -------------------------------------------------------------------

def load_config(path: str) -> dict:
    """Parse and validate config.yaml."""
    if not os.path.exists(path):
        log.error("Config file not found: %s", path)
        log.error("Copy config.example.yaml to config.yaml and edit it.")
        raise SystemExit(1)
    with open(path) as fh:
        config = yaml.safe_load(fh)

    if "categories" not in config:
        log.error("Missing required config key: categories")
        raise SystemExit(1)

    delivery_mode = config.get("delivery", "smtp")
    if delivery_mode not in ("smtp", "gog"):
        log.error("Invalid delivery mode %r — must be 'smtp' or 'gog'", delivery_mode)
        raise SystemExit(1)
    config["delivery"] = delivery_mode

    # Resolve from/to addresses
    email_section = config.get("email", {})
    smtp_section = config.get("smtp", {})

    if delivery_mode == "smtp":
        # smtp section is required
        if "smtp" not in config:
            log.error("Missing required config key: smtp (needed for delivery: smtp)")
            raise SystemExit(1)
        for key in ("host", "port"):
            if key not in smtp_section:
                log.error("Missing smtp.%s in config", key)
                raise SystemExit(1)
        # from/to can come from email section or smtp section
        from_addr = email_section.get("from") or smtp_section.get("from")
        to_addr = email_section.get("to") or smtp_section.get("to")
        if not from_addr or not to_addr:
            log.error("Missing from/to address — set in email or smtp section")
            raise SystemExit(1)
    else:
        # gog mode: from/to from email section (or smtp fallback)
        from_addr = email_section.get("from") or smtp_section.get("from")
        to_addr = email_section.get("to") or smtp_section.get("to")
        if not from_addr or not to_addr:
            log.error("Missing email.from / email.to in config (needed for delivery: gog)")
            raise SystemExit(1)

    config["_from"] = from_addr
    config["_to"] = to_addr
    return config


# -- Fetch --------------------------------------------------------------------

def fetch_hn(query: str, since_ts: int, limit: int = 5) -> list[Story]:
    """Fetch stories from HN Algolia API, sorted by points descending."""
    try:
        encoded_query = urllib.parse.quote(query)
        full_url = HN_SEARCH_URL.format(query=encoded_query, since=since_ts)
        req = urllib.request.Request(full_url, headers={"User-Agent": "newsdigest/0.1"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        items: list[Story] = []
        for hit in data.get("hits", []):
            title = hit.get("title", "")
            url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
            points = hit.get("points", 0)
            comments = hit.get("num_comments", 0)
            created_at_i = hit.get("created_at_i", 0)
            items.append({
                "title": title,
                "url": url,
                "points": points,
                "comments": comments,
                "source": "hn",
                "published": created_at_i,
            })
        items.sort(key=lambda x: x["points"], reverse=True)
        return items[:limit]
    except Exception as exc:
        log.error("HN fetch error for %r: %s", query, exc)
        return []


def fetch_rss(feed_url: str, since_ts: int, limit: int = 5) -> list[Story]:
    """Fetch stories from an RSS feed, filtered by time window."""
    try:
        req = urllib.request.Request(feed_url, headers={"User-Agent": "newsdigest/0.1"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw_feed = resp.read()
        feed = feedparser.parse(raw_feed)
        items: list[Story] = []
        for entry in feed.entries:
            published_struct = entry.get("published_parsed") or entry.get("updated_parsed")
            if published_struct:
                published_ts = int(time.mktime(published_struct))
            else:
                published_ts = int(time.time())

            if published_ts < since_ts:
                continue

            title = entry.get("title", "")
            url = entry.get("link", "")
            if not url:
                continue
            items.append({
                "title": title,
                "url": url,
                "points": 0,
                "comments": 0,
                "source": "rss",
                "published": published_ts,
            })
        items.sort(key=lambda x: x["published"], reverse=True)
        return items[:limit]
    except Exception as exc:
        log.error("RSS fetch error for %r: %s", feed_url, exc)
        return []


def fetch_category(
    category: dict, since_ts: int, seen_urls: set[str]
) -> tuple[str, list[Story]]:
    """Fetch and merge HN + RSS stories for a category, deduplicating across categories."""
    category_name = category["name"]
    limit = category.get("limit", 5)

    # Fetch HN stories
    hn_items: list[Story] = []
    hn_query = category.get("hn_query")
    if hn_query:
        hn_items = fetch_hn(hn_query, since_ts, limit=limit + 5)

    # Fetch RSS stories
    rss_items: list[Story] = []
    for feed_url in category.get("rss_feeds", []):
        rss_items.extend(fetch_rss(feed_url, since_ts, limit=limit + 5))

    # Merge: HN first (sorted by points), then RSS (sorted by published)
    rss_items.sort(key=lambda x: x["published"], reverse=True)
    merged = hn_items + rss_items

    # Deduplicate
    deduped: list[Story] = []
    for item in merged:
        if item["url"] not in seen_urls:
            seen_urls.add(item["url"])
            deduped.append(item)
        if len(deduped) == limit:
            break

    return category_name, deduped


# -- Build email --------------------------------------------------------------

def build_email(stories: list[tuple[str, list[Story]]]) -> tuple[str, str]:
    """Return (plain_text, html) for the digest email."""
    today = date.today().strftime("%B %d, %Y")

    # Plain text
    lines = [f"News Digest -- {today}", "=" * 50, ""]
    for category_name, items in stories:
        lines.append(category_name)
        lines.append("-" * len(category_name))
        for story in items:
            lines.append(f"  {story['title']}")
            lines.append(f"  {story['url']}")
            if story["source"] == "hn":
                lines.append(f"  {story['points']} pts | {story['comments']} comments")
            else:
                lines.append("  via RSS")
            lines.append("")
    lines.append("Have a great day!")
    plain_text = "\n".join(lines)

    # HTML
    category_html_parts = []
    for category_name, items in stories:
        story_blocks = []
        for story in items:
            escaped_title = html.escape(story["title"])
            if story["source"] == "hn":
                meta = f'{story["points"]} pts &nbsp;|&nbsp; {story["comments"]} comments'
            else:
                meta = "via RSS"
            story_blocks.append(
                f'<div style="margin-bottom:16px;padding:12px;border-left:3px solid #1a73e8;background:#f8f9fa">'
                f'  <a href="{html.escape(story["url"])}" style="font-weight:bold;color:#1a73e8;text-decoration:none">{escaped_title}</a>'
                f'  <br><span style="color:#888;font-size:12px">{meta}</span>'
                f'</div>'
            )
        category_html_parts.append(
            f'<h3 style="color:#333;margin-top:24px">{html.escape(category_name)}</h3>'
            + "\n".join(story_blocks)
        )

    html_body = (
        f'<html><body style="font-family:Arial,sans-serif;max-width:650px;margin:auto;color:#222">'
        f'<h2 style="color:#1a73e8">News Digest &mdash; {today}</h2>'
        f'<p style="color:#666">Your daily digest of news from Hacker News &amp; RSS feeds</p>'
        f'<hr style="border:1px solid #eee">'
        + "\n".join(category_html_parts)
        + '<hr style="border:1px solid #eee;margin-top:24px">'
        + '<p style="color:#999;font-size:12px">Sent by newsdigest</p>'
        + '</body></html>'
    )

    return plain_text, html_body


# -- Send ---------------------------------------------------------------------

def send_email(
    host: str,
    port: int,
    from_addr: str,
    to_addr: str,
    password: str,
    subject: str,
    plain_text: str,
    html_body: str,
) -> None:
    """Send email via SMTP_SSL."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL(host, port) as server:
        server.login(from_addr, password)
        server.sendmail(from_addr, [to_addr], msg.as_string())
    log.info("Email sent successfully.")


def send_email_gog(
    from_addr: str,
    to_addr: str,
    subject: str,
    plain_text: str,
    html_body: str,
) -> None:
    """Send email using the gog CLI."""
    cmd = [
        "gog", "gmail", "send",
        "--to", to_addr,
        "--account", from_addr,
        "--subject", subject,
        "--body", plain_text,
        "--body-html", html_body,
        "--force",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("gog send failed (exit %d): %s", result.returncode, result.stderr.strip())
        raise SystemExit(1)
    log.info("Email sent via gog successfully.")


# -- Main ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Configurable news digest via email")
    parser.add_argument("--dry-run", action="store_true", help="Print email to stdout instead of sending")
    parser.add_argument("--config", default="config.yaml", help="Path to config file (default: config.yaml)")
    args = parser.parse_args()

    config = load_config(args.config)
    delivery_mode = config["delivery"]
    time_window_hours = config.get("time_window", 24)
    since_ts = int((datetime.now() - timedelta(hours=time_window_hours)).timestamp())

    log.info("Fetching news for %s ...", date.today())

    seen_urls: set[str] = set()
    stories: list[tuple[str, list[Story]]] = []
    for category in config["categories"]:
        category_name, items = fetch_category(category, since_ts, seen_urls)
        stories.append((category_name, items))

    total = sum(len(items) for _, items in stories)
    if total == 0:
        log.info("No stories found today.")
        return

    today_short = date.today().strftime("%b %d")
    subject = f"News Digest {today_short} -- {total} stories"
    plain_text, html_body = build_email(stories)

    if args.dry_run:
        print(f"Subject: {subject}\n")
        print(plain_text)
    elif delivery_mode == "gog":
        send_email_gog(
            from_addr=config["_from"],
            to_addr=config["_to"],
            subject=subject,
            plain_text=plain_text,
            html_body=html_body,
        )
    else:
        smtp_config = config["smtp"]
        smtp_password = os.environ.get("SMTP_PASSWORD", "")
        if not smtp_password:
            log.error("SMTP_PASSWORD not set in environment. Add it to .env")
            raise SystemExit(1)
        send_email(
            host=smtp_config["host"],
            port=smtp_config["port"],
            from_addr=config["_from"],
            to_addr=config["_to"],
            password=smtp_password,
            subject=subject,
            plain_text=plain_text,
            html_body=html_body,
        )


if __name__ == "__main__":
    main()
