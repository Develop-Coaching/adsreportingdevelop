# Cost Guide — Daily Slack Ad Report

Automated daily report that finds every **active** ad in your Meta/Facebook Ads account, pulls yesterday's performance data for each one, and posts a full breakdown to Slack.

## What it reports

For **each active ad** individually:
- Ad name (with campaign and ad set context)
- Spend, Impressions, Clicks, CTR, CPC, Leads

Plus an **overall totals** section:
- Total Spend, Impressions, Clicks, CTR, CPC, Leads, Cost Per Lead

Ads are sorted by spend (highest first). If no active ads are found, it posts a notice that all campaigns may be paused.

## Setup

### 1. Create a Slack Incoming Webhook

1. Go to https://api.slack.com/apps
2. Click **Create New App** → **From scratch**
3. Name it something like `Cost Guide Reports`, select your workspace
4. Go to **Incoming Webhooks** → toggle it **On**
5. Click **Add New Webhook to Workspace**
6. Select the channel you want reports in (e.g. `#cost-guide-ad-reports`)
7. Copy the webhook URL

### 2. Get your Meta Ads API credentials

| Credential | Where to find it |
|---|---|
| **Access Token** | [Meta Business Suite](https://business.facebook.com/settings) → System Users → Generate Token (needs `ads_read` permission) |
| **Ad Account ID** | In Ads Manager, the number in the URL after `act_` (e.g. `act_123456789` → use `123456789`) |

**Important:** Generate a long-lived token (60 days) or set up a System User for a token that doesn't expire.

### 3. Add secrets to your GitHub repo

Go to your repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret name | Value |
|---|---|
| `META_ACCESS_TOKEN` | Your Meta access token |
| `META_AD_ACCOUNT_ID` | Your ad account ID (numbers only, no `act_` prefix) |
| `SLACK_WEBHOOK_URL` | The Slack webhook URL from step 1 |

### 4. Adjust the schedule (optional)

The workflow runs daily at **8:00 AM AEST** (10:00 PM UTC).

To change the time, edit `.github/workflows/daily-report.yml` and update the cron:

```yaml
- cron: "0 22 * * *"  # 10 PM UTC = 8 AM AEST
```

### 5. Test it

Trigger the report manually:

1. Go to your repo → **Actions** tab
2. Select **Daily Cost Guide Ad Report**
3. Click **Run workflow**

## Running locally

```bash
cp .env.example .env
# Fill in your real values in .env

export $(cat .env | xargs) && python daily_report.py
```
