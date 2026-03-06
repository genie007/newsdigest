# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

**newsdigest** is a configurable Python CLI tool that fetches news from Hacker News (Algolia API) and RSS feeds, then emails a formatted daily digest. It supports two delivery modes: SMTP (standalone) and gog CLI (no password needed). Configuration is YAML-based with deduplication across categories. Includes an OpenClaw skill for automated scheduling.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

Configuration:
- Copy `config.example.yaml` to `config.yaml` and edit with your email settings
- Set `delivery: smtp` or `delivery: gog` depending on your setup
- For SMTP mode: copy `.env.example` to `.env` and add your `SMTP_PASSWORD`
- For gog mode: no credentials needed (gog must be authenticated)

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
- **Config**: `config.yaml` (YAML) for categories, delivery mode, email addresses; `.env` for SMTP_PASSWORD
- **Delivery modes**: `smtp` (smtplib.SMTP_SSL, needs SMTP_PASSWORD) or `gog` (shells out to gog CLI)
- **Categories**: Defined in config.yaml with optional `hn_query`, `rss_feeds`, and `limit`
- **Deduplication**: Stories seen in earlier categories are skipped in later ones
- **Sorting**: HN stories by points desc, RSS by publish date desc; merged: HN first then RSS
- **Email delivery**: SMTP via `smtplib.SMTP_SSL` or gog via `subprocess.run`
- **Dry-run**: `--dry-run` flag prints the email to stdout instead of sending
- **OpenClaw**: Skill at `openclaw/SKILL.md` for automated daily digest via cron
