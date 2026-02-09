import os
import sys
import json
import requests
from datetime import datetime, timedelta


# ─── Configuration ───────────────────────────────────────────────
META_ACCESS_TOKEN = os.environ["META_ACCESS_TOKEN"]
META_AD_ACCOUNT_ID = os.environ["META_AD_ACCOUNT_ID"]
SLACK_WEBHOOK_URL = os.environ["SLACK_WEBHOOK_URL"]
CURRENCY_SYMBOL = os.environ.get("CURRENCY_SYMBOL", "$")

API_VERSION = "v19.0"
BASE_URL = f"https://graph.facebook.com/{API_VERSION}"


def get_date_str(days_ago):
    d = datetime.now() - timedelta(days=days_ago)
    return d.strftime("%Y-%m-%d")


def get_yesterday_display():
    yesterday = datetime.now() - timedelta(days=1)
    return yesterday.strftime("%-d %b %Y")


def fetch_campaign_insights(date_str):
    endpoint = f"{BASE_URL}/act_{META_AD_ACCOUNT_ID}/insights"
    params = {
        "access_token": META_ACCESS_TOKEN,
        "time_range": json.dumps({"since": date_str, "until": date_str}),
        "fields": ",".join([
            "campaign_name",
            "campaign_id",
            "spend",
            "impressions",
            "clicks",
            "actions",
        ]),
        "level": "campaign",
        "filtering": json.dumps([{
            "field": "campaign.effective_status",
            "operator": "IN",
            "value": ["ACTIVE"],
        }]),
        "limit": 200,
    }

    response = requests.get(endpoint, params=params, timeout=30)
    response.raise_for_status()
    return response.json().get("data", [])


def extract_leads(actions):
    if not actions:
        return 0
    for action in actions:
        if action.get("action_type") in ("lead", "offsite_conversion.fb_pixel_lead"):
            return int(action.get("value", 0))
    return 0


def parse_campaign(row):
    spend = float(row.get("spend", 0))
    clicks = int(row.get("clicks", 0))
    impressions = int(row.get("impressions", 0))
    leads = extract_leads(row.get("actions"))

    return {
        "campaign_id": row.get("campaign_id", ""),
        "campaign_name": row.get("campaign_name", "Unknown"),
        "spend": spend,
        "impressions": impressions,
        "clicks": clicks,
        "leads": leads,
        "ctr": (clicks / impressions) * 100 if impressions > 0 else 0,
        "cpc": spend / clicks if clicks > 0 else 0,
        "cpl": spend / leads if leads > 0 else 0,
    }


def trend(current, previous, lower_is_better=False):
    if previous == 0 and current == 0:
        return "➖"
    if previous == 0:
        return "🆕"
    if current > previous:
        return "🔴 ↑" if lower_is_better else "🟢 ↑"
    elif current < previous:
        return "🟢 ↓" if lower_is_better else "🔴 ↓"
    return "➖"


def pct(current, previous):
    if previous == 0:
        return ""
    change = ((current - previous) / previous) * 100
    sign = "+" if change > 0 else ""
    return f" ({sign}{change:.0f}%)"


def fmt_num(n):
    return f"{n:,}"


def fmt_money(amount):
    return f"{CURRENCY_SYMBOL}{amount:,.2f}"


def build_slack_message(campaigns, prev_lookup, date_display):
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Ads Daily Report ({date_display})"},
        },
        {"type": "divider"},
    ]

    total_spend = 0
    total_leads = 0
    prev_total_spend = 0
    prev_total_leads = 0

    for c in campaigns:
        prev = prev_lookup.get(c["campaign_id"], {})
        ps = prev.get("spend", 0)
        pl = prev.get("leads", 0)

        total_spend += c["spend"]
        total_leads += c["leads"]
        prev_total_spend += ps
        prev_total_leads += pl

        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*📣 {c['campaign_name']}*\n"
                    f"Spend: *{fmt_money(c['spend'])}* {trend(c['spend'], ps)}{pct(c['spend'], ps)}  |  "
                    f"Clicks: *{fmt_num(c['clicks'])}* {trend(c['clicks'], prev.get('clicks', 0))}{pct(c['clicks'], prev.get('clicks', 0))}  |  "
                    f"Leads: *{c['leads']}* {trend(c['leads'], pl)}{pct(c['leads'], pl)}  |  "
                    f"CPL: *{fmt_money(c['cpl'])}* {trend(c['cpl'], prev.get('cpl', 0), lower_is_better=True)}{pct(c['cpl'], prev.get('cpl', 0))}"
                ),
            },
        })

    blocks.append({"type": "divider"})

    total_cpl = total_spend / total_leads if total_leads > 0 else 0
    prev_total_cpl = prev_total_spend / prev_total_leads if prev_total_leads > 0 else 0

    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                f"*📊 Totals*\n"
                f"Spend: *{fmt_money(total_spend)}* {trend(total_spend, prev_total_spend)}{pct(total_spend, prev_total_spend)}  |  "
                f"Leads: *{total_leads}* {trend(total_leads, prev_total_leads)}{pct(total_leads, prev_total_leads)}  |  "
                f"CPL: *{fmt_money(total_cpl)}* {trend(total_cpl, prev_total_cpl, lower_is_better=True)}{pct(total_cpl, prev_total_cpl)}"
            ),
        },
    })

    return {"blocks": blocks}


def build_no_ads_message(date_display):
    return {
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": f"Ads Daily Report ({date_display})"}},
            {"type": "divider"},
            {"type": "section", "text": {"type": "mrkdwn", "text": "⚠️ No active campaigns found yesterday."}},
        ]
    }


def send_to_slack(message):
    response = requests.post(
        SLACK_WEBHOOK_URL, json=message,
        headers={"Content-Type": "application/json"}, timeout=10,
    )
    if response.status_code != 200:
        print(f"Slack error ({response.status_code}): {response.text}", file=sys.stderr)
        sys.exit(1)
    print("Report sent to Slack successfully.")


def main():
    yesterday = get_date_str(1)
    day_before = get_date_str(2)
    date_display = get_yesterday_display()

    print(f"Fetching campaign data for {yesterday}...")

    try:
        raw_yesterday = fetch_campaign_insights(yesterday)
        raw_day_before = fetch_campaign_insights(day_before)
    except requests.exceptions.HTTPError as e:
        print(f"Meta Ads API error: {e}", file=sys.stderr)
        print(f"Response: {e.response.text}", file=sys.stderr)
        sys.exit(1)

    if not raw_yesterday:
        send_to_slack(build_no_ads_message(date_display))
        return

    campaigns = [parse_campaign(row) for row in raw_yesterday]
    campaigns.sort(key=lambda c: c["spend"], reverse=True)

    prev_campaigns = [parse_campaign(row) for row in raw_day_before]
    prev_lookup = {c["campaign_id"]: c for c in prev_campaigns}

    message = build_slack_message(campaigns, prev_lookup, date_display)
    send_to_slack(message)


if __name__ == "__main__":
    main()
