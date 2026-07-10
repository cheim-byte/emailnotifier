# Ironwood Email Monitor

AI-powered email monitoring app for Ironwood Solutions LLC.

## Features
- Monitors info@ironwood-solutions-llc.com via IMAP every 60 seconds
- AI analyzes each email for importance (HIGH/MEDIUM/LOW)
- Drafts replies using Ironwood business context
- Sends push notifications via ntfy.sh to your Android phone
- Web UI to review and send draft replies

## Environment Variables (set in Railway)

| Variable | Value |
|----------|-------|
| `EMAIL` | info@ironwood-solutions-llc.com |
| `EMAIL_PASSWORD` | your email password |
| `ANTHROPIC_API_KEY` | your Anthropic API key |
| `NTFY_TOPIC` | ironwood-email-alerts1337 |
| `CHECK_INTERVAL` | 60 (seconds between checks) |

## Deploy to Railway

1. Push this repo to GitHub
2. Connect to Railway.app
3. Set environment variables above
4. Deploy!
