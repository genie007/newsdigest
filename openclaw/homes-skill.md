---
name: homesdigest
description: Run the newsdigest CLI with homes config to fetch Frisco TX home listings and send a daily email digest.
metadata: {"openclaw":{"emoji":"🏠","requires":{"bins":["newsdigest"]}}}
---

# homesdigest skill

## What

Run `newsdigest --config /Users/prasanthmallaya/ai-brief/homes-config.yaml` to fetch active home listings in Frisco, TX from RentCast API and send an HTML email digest.

## When to use

- Daily morning digest (triggered by cron job)
- On-demand when the user asks for home listing updates

## Workflow

1. Run `newsdigest --config /Users/prasanthmallaya/ai-brief/homes-config.yaml` to send the email digest
   - Uses `delivery: gog` so no password needed — gog is already authenticated
   - Fetches listings from RentCast API (requires RENTCAST_API_KEY in .env)
2. If it fails, run with `--dry-run` to diagnose (prints output to stdout without sending)
3. Report success or failure back to the user

## Inputs

None required. The tool reads configuration from `homes-config.yaml` in the project directory.

## Failure handling

- **RENTCAST_API_KEY not set**: Ensure `RENTCAST_API_KEY` is set in `.env`. Sign up at https://www.rentcast.io/ to get a free API key.
- **No new listings found**: Report that no matching listings were found in the last 24 hours. This is normal — not every day has new listings.
- **gog CLI not found**: Ensure gog is installed and on PATH. Run `which gog` to check.
- **gog send failure**: Check `gog gmail send` stderr output. Common causes: gog not authenticated, invalid account.
- **Config missing**: If `homes-config.yaml` is not found, tell the user to create it or copy the example from the project.

## Cron setup

To schedule a daily digest at 8 AM Central:

```bash
openclaw cron add \
  --name "daily-homesdigest" \
  --cron "0 8 * * *" \
  --tz "America/Chicago" \
  --session isolated \
  --message "Run the homesdigest skill to send today's home listings digest email"
```
