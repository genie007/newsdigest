# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

**newsdigest** is a configurable Python CLI tool that fetches news from Hacker News (Algolia API) and RSS feeds, then emails a formatted daily digest using SMTP. It supports YAML-based configuration, deduplication across categories, and includes an OpenClaw skill for automated scheduling.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

Configuration:
- Copy `config.example.yaml` to `config.yaml` and edit with your email settings
- Copy `.env.example` to `.env` and add your `SMTP_PASSWORD` (Gmail App Password)

## Running

```bash
source venv/bin/activate

# Dry-run (prints to stdout)
newsdigest --dry-run

# Send email
newsdigest

# Custom config path
newsdigest --config /path/to/config.yaml
```

## Architecture

- **Single module**: `newsdigest.py` — installed as CLI entry point via pyproject.toml
- **Data sources**: HN Algolia search API + RSS feeds via feedparser
- **Config**: `config.yaml` (YAML) for categories, SMTP settings; `.env` for SMTP_PASSWORD
- **Categories**: Defined in config.yaml with optional `hn_query`, `rss_feeds`, and `limit`
- **Deduplication**: Stories seen in earlier categories are skipped in later ones
- **Sorting**: HN stories by points desc, RSS by publish date desc; merged: HN first then RSS
- **Email delivery**: `smtplib.SMTP_SSL` with `MIMEMultipart("alternative")` for HTML + plain text
- **Dry-run**: `--dry-run` flag prints the email to stdout instead of sending
- **OpenClaw**: Skill at `openclaw/SKILL.md` for automated daily digest via cron
