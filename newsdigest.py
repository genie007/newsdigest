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
import requests
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


class Listing(TypedDict):
    address: str
    price: int
    bedrooms: int
    bathrooms: float
    square_footage: int
    year_built: int
    days_on_market: int
    listed_date: str       # "YYYY-MM-DD"
    listing_url: str       # Zillow address search URL
    source: str            # "rentcast"


RENTCAST_API_URL = "https://api.rentcast.io/v1/listings/sale"


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

    digest_type = config.get("digest_type", "news")
    if digest_type not in ("news", "homes"):
        log.error("Invalid digest_type %r — must be 'news' or 'homes'", digest_type)
        raise SystemExit(1)
    config["digest_type"] = digest_type

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


def fetch_rentcast(rentcast_params: dict) -> list[Listing]:
    """Fetch active home listings from RentCast API."""
    api_key = os.environ.get("RENTCAST_API_KEY", "")
    if not api_key:
        log.error("RENTCAST_API_KEY not set in environment. Add it to .env")
        return []

    query_params: dict = {
        "city": rentcast_params.get("city"),
        "state": rentcast_params.get("state"),
        "status": "Active",
        "limit": rentcast_params.get("limit", 20),
    }
    if "min_price" in rentcast_params or "max_price" in rentcast_params:
        min_price = rentcast_params.get("min_price", 0)
        max_price = rentcast_params.get("max_price", 999999999)
        query_params["price"] = f"{min_price}-{max_price}"
    if "min_beds" in rentcast_params:
        query_params["bedrooms"] = f'{rentcast_params["min_beds"]}-99'
    if "min_baths" in rentcast_params:
        query_params["bathrooms"] = f'{rentcast_params["min_baths"]}-99'
    if "min_sqft" in rentcast_params:
        query_params["squareFootage"] = f'{rentcast_params["min_sqft"]}-99999'
    if "min_year_built" in rentcast_params:
        query_params["yearBuilt"] = f'{rentcast_params["min_year_built"]}-2030'
    if "days_old" in rentcast_params:
        query_params["daysOld"] = rentcast_params["days_old"]

    try:
        resp = requests.get(
            RENTCAST_API_URL,
            params=query_params,
            headers={"X-Api-Key": api_key, "Accept": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.error("RentCast fetch error: %s", exc)
        return []

    listings: list[Listing] = []
    for item in data:
        address_parts = [
            item.get("formattedAddress")
            or f"{item.get('addressLine1', '')} {item.get('city', '')}, {item.get('state', '')} {item.get('zipCode', '')}"
        ]
        address = address_parts[0].strip()
        encoded_address = urllib.parse.quote(address.replace(" ", "-"))
        listing_url = f"https://www.zillow.com/homes/{encoded_address}_rb/"

        listings.append({
            "address": address,
            "price": item.get("price", 0),
            "bedrooms": item.get("bedrooms", 0),
            "bathrooms": item.get("bathrooms", 0.0),
            "square_footage": item.get("squareFootage", 0),
            "year_built": item.get("yearBuilt", 0),
            "days_on_market": item.get("daysOnMarket", 0),
            "listed_date": item.get("listedDate", "")[:10],
            "listing_url": listing_url,
            "source": "rentcast",
        })

    listings.sort(key=lambda x: x["price"])
    return listings


def fetch_category(
    category: dict, since_ts: int, seen_urls: set[str]
) -> tuple[str, list[Story | Listing]]:
    """Fetch and merge HN + RSS stories (or RentCast listings) for a category."""
    category_name = category["name"]
    limit = category.get("limit", 5)

    # RentCast listings path
    rentcast_params = category.get("rentcast_params")
    if rentcast_params:
        listings = fetch_rentcast(rentcast_params)
        deduped: list[Listing] = []
        for listing in listings:
            if listing["address"] not in seen_urls:
                seen_urls.add(listing["address"])
                deduped.append(listing)
            if len(deduped) == limit:
                break
        return category_name, deduped

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
    deduped_stories: list[Story] = []
    for item in merged:
        if item["url"] not in seen_urls:
            seen_urls.add(item["url"])
            deduped_stories.append(item)
        if len(deduped_stories) == limit:
            break

    return category_name, deduped_stories


# -- Build email --------------------------------------------------------------

def _format_price(price: int) -> str:
    """Format price as $XXX,XXX."""
    return f"${price:,}"


def _extract_domain(url: str) -> str:
    """Return bare domain for display, e.g. 'techcrunch.com'."""
    try:
        parsed = urllib.parse.urlparse(url)
        domain = parsed.netloc.lower()
        return domain.removeprefix("www.")
    except Exception:
        return ""


def _build_news_card_html(item: dict) -> str:
    """Build an Outlook-safe table card for a news story."""
    escaped_title = html.escape(item["title"])
    escaped_url = html.escape(item["url"])
    domain = _extract_domain(item["url"])
    is_hn = item["source"] == "hn"
    stripe_color = "#ff6b35" if is_hn else "#9e9e9e"

    if is_hn:
        meta_cells = (
            f'<td style="padding-right:8px">'
            f'<span style="background:#fff3e0;color:#e65100;padding:3px 8px;'
            f'border-radius:4px;font-family:Arial,Helvetica,sans-serif;'
            f'font-size:11px;font-weight:700">'
            f'&#9650; {item["points"]} pts</span></td>'
            f'<td style="padding-right:8px">'
            f'<span style="background:#e8f4fd;color:#0d47a1;padding:3px 8px;'
            f'border-radius:4px;font-family:Arial,Helvetica,sans-serif;'
            f'font-size:11px;font-weight:600">'
            f'{item["comments"]} comments</span></td>'
        )
    else:
        meta_cells = (
            f'<td style="padding-right:8px">'
            f'<span style="background:#f1f8e9;color:#33691e;padding:3px 8px;'
            f'border-radius:4px;font-family:Arial,Helvetica,sans-serif;'
            f'font-size:11px;font-weight:600">RSS</span></td>'
        )

    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        f'<tr><td style="padding-bottom:12px">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="border:1px solid #e8e8e8;border-radius:8px;background:#ffffff">'
        f'<tr>'
        f'<td width="4" bgcolor="{stripe_color}" style="border-radius:8px 0 0 8px">&nbsp;</td>'
        f'<td class="card-cell" style="padding:14px 18px">'
        f'<a href="{escaped_url}" style="font-family:Georgia,\'Times New Roman\',serif;'
        f'font-size:15px;font-weight:700;color:#1a1a1a;text-decoration:none;'
        f'line-height:1.5;display:block;margin-bottom:8px">{escaped_title}</a>'
        f'<table role="presentation" cellpadding="0" cellspacing="0"><tr>'
        f'{meta_cells}'
        f'<td><span style="color:#999;font-family:Arial,Helvetica,sans-serif;'
        f'font-size:11px">{html.escape(domain)}</span></td>'
        f'</tr></table>'
        f'</td></tr></table>'
        f'</td></tr></table>'
    )


def _build_listing_card_html(item: dict) -> str:
    """Build an Outlook-safe table card for a home listing."""
    price_display = _format_price(item["price"])
    escaped_address = html.escape(item["address"])
    escaped_url = html.escape(item["listing_url"])

    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        f'<tr><td style="padding-bottom:16px">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="border:1px solid #e0ddd8;border-radius:8px;background:#ffffff">'
        f'<tr>'
        f'<td width="4" bgcolor="#2e7d32" style="border-radius:8px 0 0 8px">&nbsp;</td>'
        f'<td class="card-cell" style="padding:16px 20px">'
        # Row 1: Address + Price
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        f'<td><a href="{escaped_url}" style="font-family:Georgia,\'Times New Roman\',serif;'
        f'font-size:15px;font-weight:700;color:#212121;text-decoration:none;line-height:1.4">'
        f'{escaped_address}</a></td>'
        f'<td align="right" style="white-space:nowrap;padding-left:12px">'
        f'<span style="font-family:Georgia,\'Times New Roman\',serif;font-size:20px;'
        f'font-weight:700;color:#1b5e20">{price_display}</span></td>'
        f'</tr></table>'
        # Row 2: Spec pills
        f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:10px"><tr>'
        f'<td style="padding-right:8px">'
        f'<span style="background:#e8f5e9;color:#2e7d32;padding:4px 10px;border-radius:4px;'
        f'font-family:Arial,Helvetica,sans-serif;font-size:12px;font-weight:700">'
        f'{item["bedrooms"]} bd</span></td>'
        f'<td style="padding-right:8px">'
        f'<span style="background:#e3f2fd;color:#1565c0;padding:4px 10px;border-radius:4px;'
        f'font-family:Arial,Helvetica,sans-serif;font-size:12px;font-weight:700">'
        f'{item["bathrooms"]:.0f} ba</span></td>'
        f'<td style="padding-right:8px">'
        f'<span style="background:#fff8e1;color:#f57f17;padding:4px 10px;border-radius:4px;'
        f'font-family:Arial,Helvetica,sans-serif;font-size:12px;font-weight:700">'
        f'{item["square_footage"]:,} sqft</span></td>'
        f'<td>'
        f'<span style="background:#f3e5f5;color:#6a1b9a;padding:4px 10px;border-radius:4px;'
        f'font-family:Arial,Helvetica,sans-serif;font-size:12px;font-weight:700">'
        f'Built {item["year_built"]}</span></td>'
        f'</tr></table>'
        # Row 3: Meta + CTA
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:10px"><tr>'
        f'<td><span style="font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#757575">'
        f'Listed {item["listed_date"]} &middot; {item["days_on_market"]} days on market</span></td>'
        f'<td align="right"><a href="{escaped_url}" style="font-family:Arial,Helvetica,sans-serif;'
        f'font-size:12px;font-weight:700;color:#2e7d32;text-decoration:none">'
        f'View on Zillow &rarr;</a></td>'
        f'</tr></table>'
        f'</td></tr></table>'
        f'</td></tr></table>'
    )


def build_email(
    stories: list[tuple[str, list[Story | Listing]]], digest_type: str = "news"
) -> tuple[str, str]:
    """Return (plain_text, html) for the digest email."""
    today = date.today().strftime("%B %d, %Y")
    total_items = sum(len(items) for _, items in stories)
    is_homes = digest_type == "homes"

    if is_homes:
        heading = "Home Listings Digest"
        subheading = "New listings matching your criteria"
        header_bg = "#1b5e20"
        accent_color = "#2e7d32"
        email_bg = "#f8f6f3"
        count_label = f"{total_items} listing{'s' if total_items != 1 else ''}"
    else:
        heading = "News Digest"
        subheading = "Your daily brief from Hacker News & RSS feeds"
        header_bg = "#1a1a2e"
        accent_color = "#ff6b35"
        email_bg = "#f4f4f4"
        count_label = f"{total_items} stor{'ies' if total_items != 1 else 'y'}"

    # -- Plain text --
    lines = [f"{heading} -- {today}", "=" * 50, ""]
    for category_name, items in stories:
        lines.append(category_name.upper())
        lines.append("-" * len(category_name))
        for item in items:
            if item.get("source") == "rentcast":
                lines.append(f"  {item['address']}")
                lines.append(f"  {_format_price(item['price'])} | {item['bedrooms']} bd | {item['bathrooms']:.0f} ba | {item['square_footage']:,} sqft")
                lines.append(f"  Built {item['year_built']} | Listed {item['listed_date']} | {item['days_on_market']} days on market")
                lines.append(f"  {item['listing_url']}")
            elif item.get("source") == "hn":
                lines.append(f"  {item['title']}")
                lines.append(f"  {item['url']}")
                lines.append(f"  {item['points']} pts | {item['comments']} comments | {_extract_domain(item['url'])}")
            else:
                lines.append(f"  {item['title']}")
                lines.append(f"  {item['url']}")
                lines.append(f"  via {_extract_domain(item['url']) or 'RSS'}")
            lines.append("")
    lines.append("Have a great day!")
    plain_text = "\n".join(lines)

    # -- HTML (table-based, Outlook-safe) --
    category_html_parts = []
    for category_name, items in stories:
        item_blocks = []
        for item in items:
            if item.get("source") == "rentcast":
                item_blocks.append(_build_listing_card_html(item))
            else:
                item_blocks.append(_build_news_card_html(item))

        # Axios-style category header: accent left border, uppercase, small
        category_html_parts.append(
            f'<tr><td style="padding:24px 32px 0 32px">'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
            f'<td style="border-left:3px solid {accent_color};padding:2px 0 2px 12px">'
            f'<span style="font-family:Arial,Helvetica,sans-serif;font-size:11px;font-weight:700;'
            f'color:{accent_color};letter-spacing:1.5px">'
            f'{html.escape(category_name.upper())}</span>'
            f'</td></tr></table>'
            f'</td></tr>'
            # Cards
            f'<tr><td style="padding:12px 32px 0 32px">'
            + "\n".join(item_blocks)
            + '</td></tr>'
        )

    preheader_text = f"{count_label} for {today}"

    html_body = (
        f'<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" '
        f'"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">'
        f'<html xmlns="http://www.w3.org/1999/xhtml" lang="en">'
        f'<head>'
        f'<meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />'
        f'<meta name="viewport" content="width=device-width, initial-scale=1.0" />'
        f'<meta name="color-scheme" content="light dark" />'
        f'<meta name="supported-color-schemes" content="light dark" />'
        f'<title>{heading} - {today}</title>'
        f'<style>'
        f'@media only screen and (max-width:600px) {{'
        f'  .container {{ width:100% !important; }}'
        f'  .card-cell {{ padding:12px 16px !important; }}'
        f'}}'
        f'@media (prefers-color-scheme:dark) {{'
        f'  .email-body {{ background:#1a1a1a !important; }}'
        f'  .email-container {{ background:#2d2d2d !important; }}'
        f'}}'
        f'</style></head>'
        f'<body class="email-body" style="margin:0;padding:0;background:{email_bg}">'
        # Preheader (hidden inbox preview text)
        f'<div style="display:none;font-size:1px;color:{email_bg};line-height:1px;'
        f'max-height:0;max-width:0;opacity:0;overflow:hidden">'
        f'{preheader_text}</div>'
        # Outer wrapper
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="{email_bg}">'
        f'<tr><td align="center" style="padding:20px 0">'
        # MSO conditional for Outlook width control
        f'<!--[if mso]>'
        f'<table role="presentation" width="600" cellpadding="0" cellspacing="0" align="center"><tr><td>'
        f'<![endif]-->'
        # Inner container
        f'<table role="presentation" class="container email-container" width="600" cellpadding="0" cellspacing="0" '
        f'style="background:#ffffff;border-radius:8px">'
        # Header
        f'<tr><td bgcolor="{header_bg}" style="padding:28px 32px">'
        f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:11px;font-weight:700;'
        f'color:{accent_color};letter-spacing:2px;margin:0 0 8px">'
        f'DAILY BRIEF</p>'
        f'<h1 style="font-family:Georgia,\'Times New Roman\',serif;font-size:26px;font-weight:700;'
        f'color:#ffffff;margin:0 0 6px;line-height:1.2">{heading}</h1>'
        f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:13px;'
        f'color:#cccccc;margin:0">'
        f'{today} &nbsp;&middot;&nbsp; {count_label}</p>'
        f'</td></tr>'
        # Subheading bar
        f'<tr><td style="padding:16px 32px 0 32px">'
        f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:13px;'
        f'color:#666;margin:0">{subheading}</p>'
        f'</td></tr>'
        # Categories
        + "\n".join(category_html_parts)
        # Footer
        + '<tr><td style="padding:24px 32px;border-top:1px solid #e0e0e0;text-align:center">'
        + '<p style="font-family:Arial,Helvetica,sans-serif;font-size:11px;'
        + 'color:#aaaaaa;margin:0;line-height:1.6">'
        + f'Sent by newsdigest &nbsp;&middot;&nbsp; {today}</p>'
        + '</td></tr>'
        + '</table>'  # inner container
        + '<!--[if mso]></td></tr></table><![endif]-->'
        + '</td></tr></table>'  # outer wrapper
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

    digest_type = config.get("digest_type", "news")
    is_homes = digest_type == "homes"
    label = "listings" if is_homes else "news"

    log.info("Fetching %s for %s ...", label, date.today())

    seen_urls: set[str] = set()
    stories: list[tuple[str, list[Story | Listing]]] = []
    for category in config["categories"]:
        category_name, items = fetch_category(category, since_ts, seen_urls)
        stories.append((category_name, items))

    total = sum(len(items) for _, items in stories)
    if total == 0:
        log.info("No %s found today.", label)
        return

    today_short = date.today().strftime("%b %d")
    if is_homes:
        subject = f"Home Listings Digest {today_short} -- {total} listings"
    else:
        subject = f"News Digest {today_short} -- {total} stories"
    plain_text, html_body = build_email(stories, digest_type=digest_type)

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
