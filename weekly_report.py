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

LEAD_ACTION_TYPES = {
    "lead",
    "offsite_conversion.fb_pixel_lead",
    "complete_registration",
    "offsite_conversion.fb_pixel_complete_registration",
    "offsite_complete_registration_add_meta_leads",
    "onsite_conversion.lead_grouped",
}


def get_last_week_range():
    today = datetime.now().date()
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_sunday = last_monday + timedelta(days=6)
    return last_monday.strftime("%Y-%m-%d"), last_sunday.strftime("%Y-%m-%d")


def get_prev_week_range():
    today = datetime.now().date()
    prev_monday = today - timedelta(days=today.weekday() + 14)
    prev_sunday = prev_monday + timedelta(days=6)
    return prev_monday.strftime("%Y-%m-%d"), prev_sunday.strftime("%Y-%m-%d")


def get_week_display(start, end):
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    return f"{s.strftime('%-d %b')} – {e.strftime('%-d %b %Y')}"


def fetch_adset_insights(since, until):
    endpoint = f"{BASE_URL}/act_{META_AD_ACCOUNT_ID}/insights"
    params = {
        "access_token": META_ACCESS_TOKEN,
        "time_range": json.dumps({"since": since, "until": until}),
        "fields": ",".join([
            "campaign_name",
            "campaign_id",
            "adset_name",
            "adset_id",
            "spend",
            "impressions",
            "clicks",
            "actions",
        ]),
        "level": "adset",
        "limit": 200,
    }

    response = requests.get(endpoint, params=params, timeout=30)
    response.raise_for_status()
    return response.json().get("data", [])


def extract_leads(actions):
    if not actions:
        return 0
    best = 0
    for action in actions:
        if action.get("action_type") in LEAD_ACTION_TYPES:
            best = max(best, int(action.get("value", 0)))
    return best


def parse_adset(row):
    spend = float(row.get("spend", 0))
    clicks = int(row.get("clicks", 0))
    impressions = int(row.get("impressions", 0))
    leads = extract_leads(row.get("actions"))

    return {
        "campaign_id": row.get("campaign_id", ""),
        "campaign_name": row.get("campaign_name", "Unknown"),
        "adset_id": row.get("adset_id", ""),
        "adset_name": row.get("adset_name", "Unknown"),
        "spend": spend,
        "impressions": impressions,
        "clicks": clicks,
        "leads": leads,
        "ctr": (clicks / impressions) * 100 if impressions > 0 else 0,
        "cpc": spend / clicks if clicks > 0 else 0,
        "cpl": spend / leads if leads > 0 else 0,
    }


def group_by_campaign(adsets):
    campaigns = OrderedDict()
    for a in adsets:
        cid = a["campaign_id"]
        if cid not in campaigns:
            campaigns[cid] = {
                "name": a["campaign_name"],
                "adsets": [],
                "spend": 0, "impressions": 0, "clicks": 0, "leads": 0,
            }
        campaigns[cid]["adsets"].append(a)
        campaigns[cid]["spend"] += a["spend"]
        campaigns[cid]["impressions"] += a["impressions"]
        campaigns[cid]["clicks"] += a["clicks"]
        campaigns[cid]["leads"] += a["leads"]
    return campaigns


def build_prev_lookups(prev_adsets):
    by_campaign = {}
    by_adset = {}
    for a in prev_adsets:
        by_adset[a["adset_id"]] = a

        cid = a["campaign_id"]
        if cid not in by_campaign:
            by_campaign[cid] = {"spend": 0, "impressions": 0, "clicks": 0, "leads": 0}
        by_campaign[cid]["spend"] += a["spend"]
        by_campaign[cid]["impressions"] += a["impressions"]
        by_campaign[cid]["clicks"] += a["clicks"]
        by_campaign[cid]["leads"] += a["leads"]

    # Add derived metrics
    for v in by_campaign.values():
        v["cpl"] = v["spend"] / v["leads"] if v["leads"] > 0 else 0
    return by_campaign, by_adset


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


def build_slack_message(campaigns, prev_by_campaign, prev_by_adset, week_display):
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Ads Weekly Report ({week_display})"},
        },
        {"type": "divider"},
    ]

    total_spend = 0
    total_leads = 0
    total_clicks = 0
    prev_total_spend = 0
    prev_total_leads = 0
    prev_total_clicks = 0

    for cid, c in campaigns.items():
        prev_c = prev_by_campaign.get(cid, {})
        ps = prev_c.get("spend", 0)
        pl = prev_c.get("leads", 0)
        pc = prev_c.get("clicks", 0)
        c_cpl = c["spend"] / c["leads"] if c["leads"] > 0 else 0
        prev_cpl = prev_c.get("cpl", 0)

        total_spend += c["spend"]
        total_leads += c["leads"]
        total_clicks += c["clicks"]
        prev_total_spend += ps
        prev_total_leads += pl
        prev_total_clicks += pc

        # Campaign header with metrics
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*📣 {c['name']}*\n"
                    f"Spend: *{fmt_money(c['spend'])}* {trend(c['spend'], ps)}{pct(c['spend'], ps)}  |  "
                    f"Clicks: *{fmt_num(c['clicks'])}* {trend(c['clicks'], pc)}{pct(c['clicks'], pc)}  |  "
                    f"Leads: *{c['leads']}* {trend(c['leads'], pl)}{pct(c['leads'], pl)}  |  "
                    f"CPL: *{fmt_money(c_cpl)}* {trend(c_cpl, prev_cpl, lower_is_better=True)}{pct(c_cpl, prev_cpl)}"
                ),
            },
        })

        # Ad sets within this campaign
        for a in sorted(c["adsets"], key=lambda x: x["spend"], reverse=True):
            prev_a = prev_by_adset.get(a["adset_id"], {})
            pa_spend = prev_a.get("spend", 0)
            pa_clicks = prev_a.get("clicks", 0)
            pa_leads = prev_a.get("leads", 0)
            pa_cpl = prev_a.get("cpl", 0)

            blocks.append({
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"📁 *{a['adset_name']}*  |  "
                            f"Spend: {fmt_money(a['spend'])} {trend(a['spend'], pa_spend)}{pct(a['spend'], pa_spend)}  |  "
                            f"Clicks: {fmt_num(a['clicks'])} {trend(a['clicks'], pa_clicks)}{pct(a['clicks'], pa_clicks)}  |  "
                            f"Leads: {a['leads']} {trend(a['leads'], pa_leads)}{pct(a['leads'], pa_leads)}  |  "
                            f"CPL: {fmt_money(a['cpl'])} {trend(a['cpl'], pa_cpl, lower_is_better=True)}{pct(a['cpl'], pa_cpl)}"
                        ),
                    },
                ],
            })

        blocks.append({"type": "divider"})

    # Overall totals
    total_cpl = total_spend / total_leads if total_leads > 0 else 0
    prev_total_cpl = prev_total_spend / prev_total_leads if prev_total_leads > 0 else 0

    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                f"*📊 Weekly Totals*\n"
                f"Spend: *{fmt_money(total_spend)}* {trend(total_spend, prev_total_spend)}{pct(total_spend, prev_total_spend)}  |  "
                f"Clicks: *{fmt_num(total_clicks)}* {trend(total_clicks, prev_total_clicks)}{pct(total_clicks, prev_total_clicks)}  |  "
                f"Leads: *{total_leads}* {trend(total_leads, prev_total_leads)}{pct(total_leads, prev_total_leads)}  |  "
                f"CPL: *{fmt_money(total_cpl)}* {trend(total_cpl, prev_total_cpl, lower_is_better=True)}{pct(total_cpl, prev_total_cpl)}"
            ),
        },
    })

    return {"blocks": blocks}


def build_no_data_message(week_display):
    return {
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": f"Ads Weekly Report ({week_display})"}},
            {"type": "divider"},
            {"type": "section", "text": {"type": "mrkdwn", "text": "⚠️ No campaign data found for last week."}},
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
    print("Weekly report sent to Slack successfully.")


def main():
    last_start, last_end = get_last_week_range()
    prev_start, prev_end = get_prev_week_range()
    week_display = get_week_display(last_start, last_end)

    print(f"Fetching weekly data for {last_start} to {last_end}...")
    print(f"Comparing against {prev_start} to {prev_end}...")

    try:
        raw_last = fetch_adset_insights(last_start, last_end)
        raw_prev = fetch_adset_insights(prev_start, prev_end)
    except requests.exceptions.HTTPError as e:
        print(f"Meta Ads API error: {e}", file=sys.stderr)
        print(f"Response: {e.response.text}", file=sys.stderr)
        sys.exit(1)

    if not raw_last:
        send_to_slack(build_no_data_message(week_display))
        return

    adsets = [parse_adset(row) for row in raw_last]
    campaigns = group_by_campaign(adsets)

    prev_adsets = [parse_adset(row) for row in raw_prev]
    prev_by_campaign, prev_by_adset = build_prev_lookups(prev_adsets)

    print(f"Found {len(campaigns)} campaign(s) with {len(adsets)} ad set(s):")
    for cid, c in campaigns.items():
        print(f"  {c['name']}: {fmt_money(c['spend'])} | {c['leads']} leads")
        for a in c["adsets"]:
            print(f"    {a['adset_name']}: {fmt_money(a['spend'])} | {a['leads']} leads")

    message = build_slack_message(campaigns, prev_by_campaign, prev_by_adset, week_display)
    send_to_slack(message)


if __name__ == "__main__":
    main()
