# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Single-file Python script (`ai-brief.py`) that fetches AI/LLM stories from the Hacker News Algolia API (last 24 hours) and emails a formatted daily digest using the `gog` CLI tool for Gmail sending.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Configuration lives in `.env` (not committed — see `.gitignore`):

```
GOG_CMD=/opt/homebrew/bin/gog
AI_BRIEF_FROM=sender@gmail.com
AI_BRIEF_TO=recipient@gmail.com
GOG_KEYRING_PASSWORD=...
```

## Running

```bash
source venv/bin/activate
python3 ai-brief.py
```

## Running

Dry-run mode (prints email to stdout without sending):

```bash
source venv/bin/activate
python3 ai-brief.py --dry-run
```

## Architecture

- **Data source**: HN Algolia search API, queried with a Unix timestamp filter for the last 24h
- **Search categories**: Supply Chain AI, Blue Yonder & Competitors, Enterprise AI / Agents, LLM & Language Models — defined in the `CATEGORIES` list
- **Deduplication**: Stories seen in earlier categories are skipped in later ones
- **Sorting**: Stories within each category are sorted by points (most engaging first)
- **Email delivery**: Shells out to `gog gmail send --body` (plain text) and `--body-html` (rich HTML) — no temp files
- **Output format**: Both plain-text and HTML versions are built and passed inline to `gog`
- **Dry-run**: `--dry-run` flag prints the email to stdout instead of sending
