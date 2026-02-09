import os
import sys
import json
import requests
from datetime import datetime, timedelta
from collections import OrderedDict


# ─── Configuration ───────────────────────────────────────────────
META_ACCESS_TOKEN = os.environ["META_ACCESS_TOKEN"]
META_AD_ACCOUNT_ID = os.environ["META_AD_ACCOUNT_ID"]
SLACK_WEBHOOK_URL = os.environ["SLACK_WEBHOOK_URL"]
CURRENCY_SYMBOL = os.environ.get("CURRENCY_SYMBOL", "$")

API_VERSION = "v19.0"
BASE_URL = f"https://graph.facebook.com/{API_VERSION}"


def get_yesterday():
    yesterday = datetime.now() - timedelta(days=1)
    return yesterday.strftime("%Y-%m-%d")


def get_yesterday_display():
    yesterday = datetime.now() - timedelta(days=1)
    return yesterday.strftime("%-d %b %Y")


def fetch_all_active_ad_insights(date_str):
    endpoint = f"{BASE_URL}/act_{META_AD_ACCOUNT_ID}/insights"
    params = {
        "access_token": META_ACCESS_TOKEN,
        "time_range": json.dumps({"since": date_str, "until": date_str}),
        "fields": ",".join([
            "ad_id",
            "ad_name",
            "campaign_name",
            "campaign_id",
            "adset_name",
            "adset_id",
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
    if not actions:
        return 0
    for action in actions:
        if action.get("action_type") in ("lead", "offsite_conversion.fb_pixel_lead"):
            return int(action.get("value", 0))
    return 0


def parse_ad_metrics(row):
    spend = float(row.get("spend", 0))
    impressions = int(row.get("impressions", 0))
    clicks = int(row.get("clicks", 0))
    leads = extract_leads(row.get("actions"))

    return {
        "ad_id": row.get("ad_id", ""),
        "ad_name": row.get("ad_name", "Unknown Ad"),
        "campaign_name": row.get("campaign_name", "Unknown Campaign"),
        "campaign_id": row.get("campaign_id", ""),
        "adset_name": row.get("adset_name", "Unknown Ad Set"),
        "adset_id": row.get("adset_id", ""),
        "spend": spend,
        "impressions": impressions,
        "clicks": clicks,
        "ctr": (clicks / impressions) * 100 if impressions > 0 else 0,
        "cpc": spend / clicks if clicks > 0 else 0,
        "leads": leads,
        "cpl": spend / leads if leads > 0 else 0,
    }


def group_by_campaign_adset(ads):
    """Group ads into Campaign → Ad Set → Ads hierarchy."""
    campaigns = OrderedDict()

    for ad in ads:
        cid = ad["campaign_id"]
        asid = ad["adset_id"]

        if cid not in campaigns:
            campaigns[cid] = {
                "name": ad["campaign_name"],
                "adsets": OrderedDict(),
                "spend": 0, "impressions": 0, "clicks": 0, "leads": 0,
            }
        campaigns[cid]["spend"] += ad["spend"]
        campaigns[cid]["impressions"] += ad["impressions"]
        campaigns[cid]["clicks"] += ad["clicks"]
        campaigns[cid]["leads"] += ad["leads"]

        if asid not in campaigns[cid]["adsets"]:
            campaigns[cid]["adsets"][asid] = {
                "name": ad["adset_name"],
                "ads": [],
                "spend": 0, "impressions": 0, "clicks": 0, "leads": 0,
            }
        campaigns[cid]["adsets"][asid]["spend"] += ad["spend"]
        campaigns[cid]["adsets"][asid]["impressions"] += ad["impressions"]
        campaigns[cid]["adsets"][asid]["clicks"] += ad["clicks"]
        campaigns[cid]["adsets"][asid]["leads"] += ad["leads"]
        campaigns[cid]["adsets"][asid]["ads"].append(ad)

    return campaigns


def calc_derived(m):
    """Calculate CTR, CPC, CPL from raw totals."""
    return {
        **m,
        "ctr": (m["clicks"] / m["impressions"]) * 100 if m["impressions"] > 0 else 0,
        "cpc": m["spend"] / m["clicks"] if m["clicks"] > 0 else 0,
        "cpl": m["spend"] / m["leads"] if m["leads"] > 0 else 0,
    }


def format_number(n):
    return f"{n:,}"


def format_currency(amount):
    return f"{CURRENCY_SYMBOL}{amount:,.2f}"


def metrics_fields(m):
    """Return a standard set of Slack fields for any level's metrics."""
    return [
        {"type": "mrkdwn", "text": f"*Spend:* {format_currency(m['spend'])}"},
        {"type": "mrkdwn", "text": f"*Impressions:* {format_number(m['impressions'])}"},
        {"type": "mrkdwn", "text": f"*Clicks:* {format_number(m['clicks'])}"},
        {"type": "mrkdwn", "text": f"*CTR:* {m['ctr']:.2f}%"},
        {"type": "mrkdwn", "text": f"*CPC:* {format_currency(m['cpc'])}"},
        {"type": "mrkdwn", "text": f"*Leads:* {format_number(m['leads'])}"},
    ]


def build_slack_message(campaigns, totals, date_display, ad_count):
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Ads Daily Report ({date_display})"},
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"{len(campaigns)} campaign(s) | {ad_count} active ad(s)"},
            ],
        },
        {"type": "divider"},
    ]

    for cid, campaign in campaigns.items():
        cm = calc_derived(campaign)

        # Campaign header
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*CAMPAIGN: {campaign['name']}*"},
        })
        blocks.append({"type": "section", "fields": metrics_fields(cm)})

        for asid, adset in campaign["adsets"].items():
            asm = calc_derived(adset)

            # Ad Set header (indented with emoji)
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"    *Ad Set: {adset['name']}*"},
            })
            blocks.append({"type": "section", "fields": metrics_fields(asm)})

            # Individual ads
            for ad in adset["ads"]:
                blocks.append({
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": f"*{ad['ad_name']}*  |  Spend: {format_currency(ad['spend'])}  |  Imp: {format_number(ad['impressions'])}  |  Clicks: {format_number(ad['clicks'])}  |  CTR: {ad['ctr']:.2f}%  |  Leads: {ad['leads']}"},
                    ],
                })

        blocks.append({"type": "divider"})

    # Overall totals
    blocks.append({
        "type": "header",
        "text": {"type": "plain_text", "text": "Overall Totals"},
    })
    blocks.append({"type": "section", "fields": metrics_fields(totals)})
    if totals["leads"] > 0:
        blocks.append({
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Cost Per Lead:* {format_currency(totals['cpl'])}"},
            ],
        })

    if totals["spend"] == 0:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": "No ad spend recorded yesterday. Ads may have had no delivery."},
            ],
        })

    return {"blocks": blocks}


def build_no_ads_message(date_display):
    return {
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"Ads Daily Report ({date_display})"},
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "No active ads found yesterday. All campaigns may be paused."},
            },
        ]
    }


def send_to_slack(message):
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
        send_to_slack(build_no_ads_message(date_display))
        return

    ads = [parse_ad_metrics(row) for row in raw_data]
    campaigns = group_by_campaign_adset(ads)

    # Calculate overall totals
    totals = {"spend": 0, "impressions": 0, "clicks": 0, "leads": 0}
    for ad in ads:
        totals["spend"] += ad["spend"]
        totals["impressions"] += ad["impressions"]
        totals["clicks"] += ad["clicks"]
        totals["leads"] += ad["leads"]
    totals = calc_derived(totals)

    print(f"Found {len(ads)} active ad(s) across {len(campaigns)} campaign(s):")
    for cid, c in campaigns.items():
        print(f"  Campaign: {c['name']} ({format_currency(c['spend'])})")
        for asid, a in c["adsets"].items():
            print(f"    Ad Set: {a['name']} ({format_currency(a['spend'])})")
            for ad in a["ads"]:
                print(f"      Ad: {ad['ad_name']} ({format_currency(ad['spend'])})")

    message = build_slack_message(campaigns, totals, date_display, len(ads))
    send_to_slack(message)


if __name__ == "__main__":
    main()
