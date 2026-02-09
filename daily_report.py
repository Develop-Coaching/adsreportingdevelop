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


def get_date_str(days_ago):
    d = datetime.now() - timedelta(days=days_ago)
    return d.strftime("%Y-%m-%d")


def get_yesterday_display():
    yesterday = datetime.now() - timedelta(days=1)
    return yesterday.strftime("%-d %b %Y")


def fetch_insights_for_date(date_str):
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


def build_prev_lookup(prev_ads):
    """Build lookup dicts keyed by ad_id, adset_id, campaign_id for previous day."""
    by_ad = {}
    by_adset = {}
    by_campaign = {}

    for ad in prev_ads:
        by_ad[ad["ad_id"]] = ad

        asid = ad["adset_id"]
        if asid not in by_adset:
            by_adset[asid] = {"spend": 0, "impressions": 0, "clicks": 0, "leads": 0}
        by_adset[asid]["spend"] += ad["spend"]
        by_adset[asid]["impressions"] += ad["impressions"]
        by_adset[asid]["clicks"] += ad["clicks"]
        by_adset[asid]["leads"] += ad["leads"]

        cid = ad["campaign_id"]
        if cid not in by_campaign:
            by_campaign[cid] = {"spend": 0, "impressions": 0, "clicks": 0, "leads": 0}
        by_campaign[cid]["spend"] += ad["spend"]
        by_campaign[cid]["impressions"] += ad["impressions"]
        by_campaign[cid]["clicks"] += ad["clicks"]
        by_campaign[cid]["leads"] += ad["leads"]

    return by_ad, by_adset, by_campaign


def calc_derived(m):
    return {
        **m,
        "ctr": (m["clicks"] / m["impressions"]) * 100 if m["impressions"] > 0 else 0,
        "cpc": m["spend"] / m["clicks"] if m["clicks"] > 0 else 0,
        "cpl": m["spend"] / m["leads"] if m["leads"] > 0 else 0,
    }


# ─── Trend Emojis ────────────────────────────────────────────────
# For most metrics, higher = better (green up arrow).
# For CPC and CPL, lower = better (green down arrow).

def trend_emoji(current, previous, lower_is_better=False):
    """Return a trend emoji comparing current vs previous value."""
    if previous == 0 and current == 0:
        return "➖"
    if previous == 0:
        return "🆕"

    if current > previous:
        return "🔴 ↑" if lower_is_better else "🟢 ↑"
    elif current < previous:
        return "🟢 ↓" if lower_is_better else "🔴 ↓"
    else:
        return "➖"


def pct_change(current, previous):
    """Return percentage change string."""
    if previous == 0:
        return ""
    change = ((current - previous) / previous) * 100
    sign = "+" if change > 0 else ""
    return f" ({sign}{change:.1f}%)"


def format_number(n):
    return f"{n:,}"


def format_currency(amount):
    return f"{CURRENCY_SYMBOL}{amount:,.2f}"


def metrics_fields_with_trends(m, prev):
    """Return Slack fields with trend emojis comparing to previous day."""
    if prev is None:
        prev = {"spend": 0, "impressions": 0, "clicks": 0, "ctr": 0, "cpc": 0, "leads": 0, "cpl": 0}

    prev = calc_derived(prev) if "ctr" not in prev else prev

    return [
        {"type": "mrkdwn", "text": f"*Spend:* {format_currency(m['spend'])}  {trend_emoji(m['spend'], prev['spend'])}{pct_change(m['spend'], prev['spend'])}"},
        {"type": "mrkdwn", "text": f"*Impressions:* {format_number(m['impressions'])}  {trend_emoji(m['impressions'], prev['impressions'])}{pct_change(m['impressions'], prev['impressions'])}"},
        {"type": "mrkdwn", "text": f"*Clicks:* {format_number(m['clicks'])}  {trend_emoji(m['clicks'], prev['clicks'])}{pct_change(m['clicks'], prev['clicks'])}"},
        {"type": "mrkdwn", "text": f"*CTR:* {m['ctr']:.2f}%  {trend_emoji(m['ctr'], prev['ctr'])}{pct_change(m['ctr'], prev['ctr'])}"},
        {"type": "mrkdwn", "text": f"*CPC:* {format_currency(m['cpc'])}  {trend_emoji(m['cpc'], prev['cpc'], lower_is_better=True)}{pct_change(m['cpc'], prev['cpc'])}"},
        {"type": "mrkdwn", "text": f"*Leads:* {format_number(m['leads'])}  {trend_emoji(m['leads'], prev['leads'])}{pct_change(m['leads'], prev['leads'])}"},
    ]


def ad_line_with_trends(ad, prev_ad):
    """Build a single ad context line with trend emojis."""
    if prev_ad is None:
        prev_ad = {"spend": 0, "impressions": 0, "clicks": 0, "ctr": 0, "leads": 0}

    spend_e = trend_emoji(ad["spend"], prev_ad["spend"])
    clicks_e = trend_emoji(ad["clicks"], prev_ad["clicks"])
    ctr_e = trend_emoji(ad["ctr"], prev_ad["ctr"])
    leads_e = trend_emoji(ad["leads"], prev_ad["leads"])

    return (
        f"*{ad['ad_name']}*  |  "
        f"Spend: {format_currency(ad['spend'])} {spend_e}  |  "
        f"Clicks: {format_number(ad['clicks'])} {clicks_e}  |  "
        f"CTR: {ad['ctr']:.2f}% {ctr_e}  |  "
        f"Leads: {ad['leads']} {leads_e}"
    )


def build_slack_message(campaigns, totals, prev_by_ad, prev_by_adset, prev_by_campaign, prev_totals, date_display, ad_count):
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Ads Daily Report ({date_display})"},
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"{len(campaigns)} campaign(s) | {ad_count} active ad(s) | 🟢 = improving  🔴 = declining  ➖ = no change  🆕 = new"},
            ],
        },
        {"type": "divider"},
    ]

    for cid, campaign in campaigns.items():
        cm = calc_derived(campaign)
        prev_cm = prev_by_campaign.get(cid)

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*📣 CAMPAIGN: {campaign['name']}*"},
        })
        blocks.append({"type": "section", "fields": metrics_fields_with_trends(cm, prev_cm)})

        for asid, adset in campaign["adsets"].items():
            asm = calc_derived(adset)
            prev_asm = prev_by_adset.get(asid)

            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"    *📁 Ad Set: {adset['name']}*"},
            })
            blocks.append({"type": "section", "fields": metrics_fields_with_trends(asm, prev_asm)})

            for ad in adset["ads"]:
                prev_ad = prev_by_ad.get(ad["ad_id"])
                blocks.append({
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": ad_line_with_trends(ad, prev_ad)},
                    ],
                })

        blocks.append({"type": "divider"})

    # Overall totals
    blocks.append({
        "type": "header",
        "text": {"type": "plain_text", "text": "📊 Overall Totals"},
    })
    blocks.append({"type": "section", "fields": metrics_fields_with_trends(totals, prev_totals)})
    if totals["leads"] > 0:
        cpl_e = trend_emoji(totals["cpl"], prev_totals["cpl"], lower_is_better=True)
        cpl_pct = pct_change(totals["cpl"], prev_totals["cpl"])
        blocks.append({
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Cost Per Lead:* {format_currency(totals['cpl'])}  {cpl_e}{cpl_pct}"},
            ],
        })

    if totals["spend"] == 0:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": "⚠️ No ad spend recorded yesterday. Ads may have had no delivery."},
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
                "text": {"type": "mrkdwn", "text": "⚠️ No active ads found yesterday. All campaigns may be paused."},
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
    yesterday = get_date_str(1)
    day_before = get_date_str(2)
    date_display = get_yesterday_display()

    print(f"Fetching ad data for {yesterday} (and {day_before} for comparison)...")

    try:
        raw_yesterday = fetch_insights_for_date(yesterday)
        raw_day_before = fetch_insights_for_date(day_before)
    except requests.exceptions.HTTPError as e:
        print(f"Meta Ads API error: {e}", file=sys.stderr)
        print(f"Response: {e.response.text}", file=sys.stderr)
        sys.exit(1)

    if not raw_yesterday:
        print("No active ads with data found.")
        send_to_slack(build_no_ads_message(date_display))
        return

    # Parse yesterday's data
    ads = [parse_ad_metrics(row) for row in raw_yesterday]
    campaigns = group_by_campaign_adset(ads)

    # Parse previous day's data for comparison
    prev_ads = [parse_ad_metrics(row) for row in raw_day_before]
    prev_by_ad, prev_by_adset, prev_by_campaign = build_prev_lookup(prev_ads)

    # Calculate overall totals for both days
    totals = {"spend": 0, "impressions": 0, "clicks": 0, "leads": 0}
    for ad in ads:
        totals["spend"] += ad["spend"]
        totals["impressions"] += ad["impressions"]
        totals["clicks"] += ad["clicks"]
        totals["leads"] += ad["leads"]
    totals = calc_derived(totals)

    prev_totals = {"spend": 0, "impressions": 0, "clicks": 0, "leads": 0}
    for ad in prev_ads:
        prev_totals["spend"] += ad["spend"]
        prev_totals["impressions"] += ad["impressions"]
        prev_totals["clicks"] += ad["clicks"]
        prev_totals["leads"] += ad["leads"]
    prev_totals = calc_derived(prev_totals)

    print(f"Found {len(ads)} active ad(s) across {len(campaigns)} campaign(s):")
    for cid, c in campaigns.items():
        print(f"  Campaign: {c['name']} ({format_currency(c['spend'])})")
        for asid, a in c["adsets"].items():
            print(f"    Ad Set: {a['name']} ({format_currency(a['spend'])})")
            for ad in a["ads"]:
                print(f"      Ad: {ad['ad_name']} ({format_currency(ad['spend'])})")

    message = build_slack_message(
        campaigns, totals,
        prev_by_ad, prev_by_adset, prev_by_campaign, prev_totals,
        date_display, len(ads),
    )
    send_to_slack(message)


if __name__ == "__main__":
    main()
