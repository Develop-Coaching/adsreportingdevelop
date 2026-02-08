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


def get_yesterday():
    """Return yesterday's date as a string (YYYY-MM-DD)."""
    yesterday = datetime.now() - timedelta(days=1)
    return yesterday.strftime("%Y-%m-%d")


def get_yesterday_display():
    """Return yesterday's date in a readable format (e.g. 8 Feb 2026)."""
    yesterday = datetime.now() - timedelta(days=1)
    return yesterday.strftime("%-d %b %Y")


def fetch_active_ads():
    """Fetch all currently active ads from the ad account."""
    endpoint = f"{BASE_URL}/act_{META_AD_ACCOUNT_ID}/ads"
    params = {
        "access_token": META_ACCESS_TOKEN,
        "fields": "id,name,campaign.fields(name),adset.fields(name),effective_status",
        "filtering": json.dumps([{
            "field": "effective_status",
            "operator": "IN",
            "value": ["ACTIVE"],
        }]),
        "limit": 200,
    }

    response = requests.get(endpoint, params=params, timeout=30)
    response.raise_for_status()

    return response.json().get("data", [])


def fetch_ad_insights(ad_id, date_str):
    """Pull insights for a single ad for a given day."""
    endpoint = f"{BASE_URL}/{ad_id}/insights"
    params = {
        "access_token": META_ACCESS_TOKEN,
        "time_range": json.dumps({"since": date_str, "until": date_str}),
        "fields": ",".join([
            "ad_name",
            "campaign_name",
            "adset_name",
            "spend",
            "impressions",
            "clicks",
            "ctr",
            "cpc",
            "actions",
            "cost_per_action_type",
        ]),
    }

    response = requests.get(endpoint, params=params, timeout=30)
    response.raise_for_status()

    data = response.json().get("data", [])
    return data[0] if data else None


def fetch_all_active_ad_insights(date_str):
    """Fetch insights for all active ads in one API call at ad level."""
    endpoint = f"{BASE_URL}/act_{META_AD_ACCOUNT_ID}/insights"
    params = {
        "access_token": META_ACCESS_TOKEN,
        "time_range": json.dumps({"since": date_str, "until": date_str}),
        "fields": ",".join([
            "ad_id",
            "ad_name",
            "campaign_name",
            "adset_name",
            "spend",
            "impressions",
            "clicks",
            "ctr",
            "cpc",
            "actions",
            "cost_per_action_type",
        ]),
        "level": "ad",
        "filtering": json.dumps([{
            "field": "ad.effective_status",
            "operator": "IN",
            "value": ["ACTIVE"],
        }]),
        "limit": 200,
    }

    response = requests.get(endpoint, params=params, timeout=30)
    response.raise_for_status()

    return response.json().get("data", [])


def extract_leads(actions):
    """Extract lead count from the actions array."""
    if not actions:
        return 0
    for action in actions:
        if action.get("action_type") in ("lead", "offsite_conversion.fb_pixel_lead"):
            return int(action.get("value", 0))
    return 0


def parse_ad_metrics(row):
    """Parse a single ad's insight row into clean metrics."""
    spend = float(row.get("spend", 0))
    impressions = int(row.get("impressions", 0))
    clicks = int(row.get("clicks", 0))
    leads = extract_leads(row.get("actions"))

    ctr = (clicks / impressions) * 100 if impressions > 0 else 0
    cpc = spend / clicks if clicks > 0 else 0
    cpl = spend / leads if leads > 0 else 0

    return {
        "ad_name": row.get("ad_name", "Unknown Ad"),
        "campaign_name": row.get("campaign_name", "Unknown Campaign"),
        "adset_name": row.get("adset_name", "Unknown Ad Set"),
        "spend": spend,
        "impressions": impressions,
        "clicks": clicks,
        "ctr": ctr,
        "cpc": cpc,
        "leads": leads,
        "cpl": cpl,
    }


def calculate_totals(ads):
    """Calculate totals across all ads."""
    totals = {
        "spend": 0.0,
        "impressions": 0,
        "clicks": 0,
        "leads": 0,
        "ctr": 0.0,
        "cpc": 0.0,
        "cpl": 0.0,
    }

    for ad in ads:
        totals["spend"] += ad["spend"]
        totals["impressions"] += ad["impressions"]
        totals["clicks"] += ad["clicks"]
        totals["leads"] += ad["leads"]

    if totals["impressions"] > 0:
        totals["ctr"] = (totals["clicks"] / totals["impressions"]) * 100
    if totals["clicks"] > 0:
        totals["cpc"] = totals["spend"] / totals["clicks"]
    if totals["leads"] > 0:
        totals["cpl"] = totals["spend"] / totals["leads"]

    return totals


def format_number(n):
    """Format a number with commas (e.g. 12,430)."""
    return f"{n:,}"


def format_currency(amount):
    """Format a currency amount (e.g. $124.50)."""
    return f"{CURRENCY_SYMBOL}{amount:,.2f}"


def build_ad_block(ad):
    """Build Slack blocks for a single ad."""
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{ad['ad_name']}*\n_{ad['campaign_name']} → {ad['adset_name']}_",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Spend:* {format_currency(ad['spend'])}"},
                {"type": "mrkdwn", "text": f"*Impressions:* {format_number(ad['impressions'])}"},
                {"type": "mrkdwn", "text": f"*Clicks:* {format_number(ad['clicks'])}"},
                {"type": "mrkdwn", "text": f"*CTR:* {ad['ctr']:.2f}%"},
                {"type": "mrkdwn", "text": f"*CPC:* {format_currency(ad['cpc'])}"},
                {"type": "mrkdwn", "text": f"*Leads:* {format_number(ad['leads'])}"},
            ],
        },
    ]


def build_slack_message(ads, totals, date_display):
    """Build the full Slack message with every active ad + totals."""
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Cost Guide — Daily Ad Report ({date_display})",
            },
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"*{len(ads)} active ad(s)* reporting yesterday's results"},
            ],
        },
        {"type": "divider"},
    ]

    # Add each individual ad
    for ad in ads:
        blocks.extend(build_ad_block(ad))
        blocks.append({"type": "divider"})

    # Add totals summary
    blocks.append({
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": "Totals",
        },
    })
    blocks.append({
        "type": "section",
        "fields": [
            {"type": "mrkdwn", "text": f"*Total Spend:* {format_currency(totals['spend'])}"},
            {"type": "mrkdwn", "text": f"*Total Impressions:* {format_number(totals['impressions'])}"},
            {"type": "mrkdwn", "text": f"*Total Clicks:* {format_number(totals['clicks'])}"},
            {"type": "mrkdwn", "text": f"*Overall CTR:* {totals['ctr']:.2f}%"},
            {"type": "mrkdwn", "text": f"*Avg CPC:* {format_currency(totals['cpc'])}"},
            {"type": "mrkdwn", "text": f"*Total Leads:* {format_number(totals['leads'])}"},
        ],
    })
    blocks.append({
        "type": "section",
        "fields": [
            {"type": "mrkdwn", "text": f"*Overall Cost Per Lead:* {format_currency(totals['cpl'])}"},
        ],
    })

    # Warning if no active ads had spend
    if totals["spend"] == 0:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": "No ad spend recorded yesterday. Ads may have just been switched on or had no delivery."},
            ],
        })

    return {"blocks": blocks}


def build_no_ads_message(date_display):
    """Build a Slack message when no active ads are found."""
    return {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"Cost Guide — Daily Ad Report ({date_display})",
                },
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "No active ads found in the account yesterday. All campaigns may be paused.",
                },
            },
        ]
    }


def send_to_slack(message):
    """Post the formatted message to Slack via webhook."""
    response = requests.post(
        SLACK_WEBHOOK_URL,
        json=message,
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    if response.status_code != 200:
        print(f"Slack error ({response.status_code}): {response.text}", file=sys.stderr)
        sys.exit(1)
    print("Report sent to Slack successfully.")


def main():
    date_str = get_yesterday()
    date_display = get_yesterday_display()

    print(f"Fetching all active ad data for {date_str}...")

    try:
        raw_data = fetch_all_active_ad_insights(date_str)
    except requests.exceptions.HTTPError as e:
        print(f"Meta Ads API error: {e}", file=sys.stderr)
        print(f"Response: {e.response.text}", file=sys.stderr)
        sys.exit(1)

    if not raw_data:
        print("No active ads with data found.")
        message = build_no_ads_message(date_display)
        send_to_slack(message)
        return

    # Parse each ad's metrics
    ads = [parse_ad_metrics(row) for row in raw_data]

    # Sort by spend (highest first)
    ads.sort(key=lambda a: a["spend"], reverse=True)

    totals = calculate_totals(ads)

    print(f"Found {len(ads)} active ad(s):")
    for ad in ads:
        print(f"  [{ad['campaign_name']}] {ad['ad_name']}: {format_currency(ad['spend'])} spend, {ad['leads']} leads")
    print(f"\n  Total Spend: {format_currency(totals['spend'])}")
    print(f"  Total Leads: {totals['leads']}")
    print(f"  Overall CPL: {format_currency(totals['cpl'])}")

    message = build_slack_message(ads, totals, date_display)
    send_to_slack(message)


if __name__ == "__main__":
    main()
