---
name: newsdigest
description: Run the newsdigest CLI to fetch news from HN + RSS feeds and send a daily email digest.
metadata: {"openclaw":{"emoji":"📰","requires":{"bins":["newsdigest"]}}}
---

# newsdigest skill

## What

Run `newsdigest` to fetch stories from configured Hacker News queries and RSS feeds, then send an HTML email digest.

## When to use

- Daily morning digest (triggered by cron job)
- On-demand when the user asks for a news summary

## Workflow

1. Run `newsdigest` to send the email digest
   - newsdigest is configured with `delivery: gog` so it uses the gog CLI directly
   - No password or credentials are needed — gog is already authenticated via OpenClaw
2. If it fails, run `newsdigest --dry-run` to diagnose (prints output to stdout without sending)
3. Report success or failure back to the user

## Inputs

None required. The tool reads configuration from `config.yaml` in the project directory.

## Failure handling

- **gog CLI not found**: Ensure gog is installed and on PATH. Run `which gog` to check.
- **gog send failure**: Check `gog gmail send` stderr output. Common causes: gog not authenticated, invalid account.
- **No stories found**: Report that no matching stories were found in the configured time window.
- **Config missing**: If `config.yaml` is not found, tell the user to copy `config.example.yaml` and edit it.

## Cron setup

To schedule a daily digest at 7 AM Pacific:

```bash
openclaw cron add \
  --name "daily-newsdigest" \
  --cron "0 7 * * *" \
  --tz "America/Los_Angeles" \
  --session isolated \
  --message "Run the newsdigest skill to send today's news digest email"
```
