# newsdigest

Configurable news digest via email. Pulls stories from **Hacker News** and **RSS feeds**, deduplicates and sorts them, then sends a formatted HTML + plain-text email digest.

## Features

- **YAML config** — define any number of categories with custom HN queries and RSS feeds
- **Deduplication** — stories seen in earlier categories are skipped in later ones
- **Sort by engagement** — HN stories sorted by points, RSS by publish date
- **HTML + plain text** — rich email with plain-text fallback
- **Dry-run mode** — preview the digest in your terminal without sending
- **OpenClaw integration** — skill + cron job for automated daily digests

## Quickstart

```bash
# Clone and install
git clone <repo-url> && cd newsdigest
python3 -m venv venv && source venv/bin/activate
pip install -e .

# Configure
cp config.example.yaml config.yaml
# Edit config.yaml with your email addresses

cp .env.example .env
# Add your Gmail App Password to .env
# (Generate at https://myaccount.google.com/apppasswords)

# Test
newsdigest --dry-run

# Send
newsdigest
```

## Configuration

### config.yaml

```yaml
smtp:
  host: smtp.gmail.com
  port: 465
  from: you@gmail.com
  to: recipient@gmail.com

time_window: 24  # hours to look back

categories:
  - name: "Category Name"
    hn_query: "search terms"       # optional
    rss_feeds:                      # optional
      - https://example.com/feed/
    limit: 5                        # max stories per category
```

### Environment Variables

| Variable | Description |
|---|---|
| `SMTP_PASSWORD` | Gmail App Password (required for sending) |

## OpenClaw Integration

newsdigest includes an OpenClaw skill for automated daily digests.

### Setup

```bash
# Schedule daily digest at 7 AM Pacific
openclaw cron add \
  --name "daily-newsdigest" \
  --cron "0 7 * * *" \
  --tz "America/Los_Angeles" \
  --session isolated \
  --message "Run the newsdigest skill to send today's news digest email"
```

The skill file is at `openclaw/SKILL.md`. It teaches the OpenClaw agent how to run the digest, handle errors, and report results.

## CLI Usage

```
newsdigest [--dry-run] [--config PATH]
```

| Flag | Description |
|---|---|
| `--dry-run` | Print digest to stdout without sending email |
| `--config PATH` | Config file path (default: `config.yaml`) |

## License

MIT
