"""Direct Meta Marketing API client. ASCII only. Pulls ads with creative copy."""
import os, sys, json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

API_VERSION = "v21.0"
API = "https://graph.facebook.com/" + API_VERSION
TOKEN = os.environ["META_ACCESS_TOKEN"]
ACCOUNT_IDS = os.environ.get("META_AD_ACCOUNTS", "act_2047856822417350,act_929449929417505").split(",")
ACCOUNT_NAMES = {
    "act_2047856822417350": "Larroude US",
    "act_929449929417505": "PRE-ORDER US",
}

def get(path, params=None):
    params = dict(params or {})
    params["access_token"] = TOKEN
    url = API + "/" + path + "?" + urlencode(params)
    rows = []
    while url:
        try:
            with urlopen(Request(url), timeout=30) as resp:
                data = json.loads(resp.read())
        except HTTPError as e:
            err = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError("Meta API " + str(e.code) + " on " + url[:100] + ": " + err[:300])
        rows.extend(data.get("data", []))
        url = data.get("paging", {}).get("next")
    return rows

def fetch_ads_with_insights(account_id):
    rows = get(account_id + "/insights", {
        "level": "ad",
        "fields": "campaign_id,campaign_name,adset_id,adset_name,ad_id,ad_name,spend,impressions,clicks,inline_link_clicks,ctr,inline_link_click_ctr,actions,action_values,website_purchase_roas,objective",
        "date_preset": "last_7d",
        "filtering": json.dumps([
            {"field": "campaign.effective_status", "operator": "IN", "value": ["ACTIVE"]},
            {"field": "adset.effective_status", "operator": "IN", "value": ["ACTIVE"]},
            {"field": "ad.effective_status", "operator": "IN", "value": ["ACTIVE"]},
        ]),
        "limit": 500,
    })
    sales = {"OUTCOME_SALES", "CONVERSIONS"}
    return [r for r in rows if r.get("objective") in sales]

def fetch_ad_metadata(ad_ids):
    """Fetch creative incl. body, title, object_story_spec for copy analysis."""
    out = {}
    for i in range(0, len(ad_ids), 50):
        batch = ad_ids[i:i+50]
        params = {"ids": ",".join(batch),
                  "fields": "name,status,effective_status,creative{id,name,object_type,thumbnail_url,image_url,body,title,object_story_spec,asset_feed_spec,effective_object_story_id},adset_id,campaign_id",
                  "access_token": TOKEN}
        url = API + "/?" + urlencode(params)
        with urlopen(Request(url), timeout=30) as r:
            data = json.loads(r.read())
        for ad_id, info in data.items():
            out[ad_id] = info
    return out

def fetch_ad_per_day_spend(account_id):
    return get(account_id + "/insights", {
        "level": "ad",
        "fields": "ad_id,spend,impressions",
        "date_preset": "last_7d",
        "time_increment": "1",
        "filtering": json.dumps([
            {"field": "campaign.effective_status", "operator": "IN", "value": ["ACTIVE"]},
            {"field": "adset.effective_status", "operator": "IN", "value": ["ACTIVE"]},
            {"field": "ad.effective_status", "operator": "IN", "value": ["ACTIVE"]},
        ]),
        "limit": 1000,
    })

def fetch_paused_campaigns(account_id):
    rows = get(account_id + "/insights", {
        "level": "campaign",
        "fields": "campaign_id,campaign_name,objective,spend,website_purchase_roas,actions",
        "date_preset": "last_90d",
        "limit": 500,
    })
    sales = {"OUTCOME_SALES", "CONVERSIONS"}
    return [r for r in rows if r.get("objective") in sales]

def main():
    out = {"accounts": [], "ads": [], "ad_metadata": {}, "ad_daily": [], "campaigns_history": []}
    for acc in ACCOUNT_IDS:
        acc = acc.strip()
        out["accounts"].append({"id": acc, "name": ACCOUNT_NAMES.get(acc, acc)})
        print("[Meta] Fetching " + acc, file=sys.stderr)
        ads = fetch_ads_with_insights(acc)
        for r in ads:
            r["_account_id"] = acc
            r["_account_name"] = ACCOUNT_NAMES.get(acc, acc)
        out["ads"].extend(ads)
        print("  -> " + str(len(ads)) + " active sales ads", file=sys.stderr)
        daily = fetch_ad_per_day_spend(acc)
        for r in daily:
            r["_account_id"] = acc
        out["ad_daily"].extend(daily)
        print("  -> " + str(len(daily)) + " daily ad-rows", file=sys.stderr)
        hist = fetch_paused_campaigns(acc)
        for c in hist:
            c["_account_id"] = acc
            c["_account_name"] = ACCOUNT_NAMES.get(acc, acc)
        out["campaigns_history"].extend(hist)
        print("  -> " + str(len(hist)) + " historical campaigns", file=sys.stderr)
    ad_ids = sorted({a["ad_id"] for a in out["ads"] if a.get("ad_id")})
    print("[Meta] Fetching creative metadata for " + str(len(ad_ids)) + " ads", file=sys.stderr)
    out["ad_metadata"] = fetch_ad_metadata(ad_ids)
    output_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/meta_data.json"
    with open(output_path, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=True)
    print("[Meta] Wrote " + output_path, file=sys.stderr)

if __name__ == "__main__":
    main()
