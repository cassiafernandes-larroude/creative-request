"""
rebuild_v5.py — analysis pipeline reading directly from fetch_meta.py + fetch_shopify.py output.
Generates analysis.json in OUTPUT_DIR.

Logic mirrors rebuild_v4 (Sales+Active filter, max-6-ads, Pareto 50%, fatigue rules, etc).
Input: META_DATA_JSON, SHOPIFY_DATA_JSON paths in env. Output: $OUTPUT_DIR/analysis.json
"""
import json, os, sys, re, math, unicodedata, random
from pathlib import Path
from collections import defaultdict, Counter
from urllib.parse import urlparse

random.seed(7)

META_PATH = Path(os.environ.get("META_DATA_JSON", "/tmp/meta_data.json"))
SHP_PATH  = Path(os.environ.get("SHOPIFY_DATA_JSON", "/tmp/shopify_data.json"))
OUT_DIR   = Path(os.environ.get("OUTPUT_DIR", "."))

MAX_ADS = 5
PAUSE_MIN_SPEND = 100
PAUSE_MAX_ROAS = 2.0
MIN_CLUSTER = 3

def f(v):
    if v in (None,""): return 0.0
    try: return float(str(v).replace(",",""))
    except: return 0.0
def i_(v):
    if v in (None,""): return 0
    try: return int(float(str(v).replace(",","")))
    except: return 0
def stem(w):
    if len(w) > 4 and w.endswith("s") and not w.endswith("ss"): return w[:-1]
    return w
def slug(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii","ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+","-",s).strip("-")

STOP = {"the","and","for","with","from","beige","black","ivory","white","brown","tan","light","dark","leather","crochet","linen","velvet","sale","ads","ad","cmp","cv","conversion","sales","mainaccount","preorder","pre","adv","catalog","shoe","style","copy","test","mix","scale","spring","summer","holiday","promo","ready","ship","best","2026","30d","newdrop","drop"}

def tokens(text):
    raw = re.split(r"[^a-zA-Z0-9]+", (text or "").lower())
    return {stem(w) for w in raw if len(w)>=4 and w not in STOP}

def is_catalog(name):
    return any(k in (name or "").lower() for k in ["catalog","catálogo","dpa","dynamic"])

def extract_purchases(insight):
    """Pull purchases count + revenue from Meta insights.
    Use ONE action_type to avoid double-counting (purchase vs omni_purchase vs offsite_conversion.fb_pixel_purchase
    are reported as separate rows for the SAME conversion event).
    Priority: purchase > omni_purchase > offsite_conversion.fb_pixel_purchase."""
    PRIORITY = ["purchase", "omni_purchase", "offsite_conversion.fb_pixel_purchase"]
    actions = {a.get("action_type",""): a for a in (insight.get("actions") or [])}
    values  = {a.get("action_type",""): a for a in (insight.get("action_values") or [])}
    pur = 0; rev = 0.0
    for key in PRIORITY:
        if key in actions:
            pur = i_(actions[key].get("value", 0))
            break
    for key in PRIORITY:
        if key in values:
            rev = f(values[key].get("value", 0))
            break
    return pur, rev

def extract_roas(insight):
    arr = insight.get("website_purchase_roas") or []
    if arr and isinstance(arr, list):
        try: return f(arr[0].get("value", 0))
        except: return 0.0
    return 0.0

COPY_LEVELS = ["Problem Aware", "Solution Aware", "Product Aware", "Most Aware"]

def detect_copy_level(ad_name):
    """Identify copy level from ad name."""
    n = (ad_name or "").lower()
    if "most aware" in n: return "Most Aware"
    if "product aware" in n: return "Product Aware"
    if "solution aware" in n: return "Solution Aware"
    if "problem aware" in n: return "Problem Aware"
    return "No Copy"

def extract_creative_copy(creative):
    """Robust title/body extraction across all ad types: link, video, carousel,
    dynamic creative (asset_feed_spec), template ads."""
    if not isinstance(creative, dict):
        return ("", "")
    title, body = "", ""

    # --- Tier 1: top-level fields ---
    title = creative.get("title", "") or ""
    body = creative.get("body", "") or ""

    spec = creative.get("object_story_spec") or {}
    link = spec.get("link_data") or {}
    video = spec.get("video_data") or {}
    template = spec.get("template_data") or {}

    # --- Tier 2: object_story_spec.link_data ---
    if not title:
        title = link.get("name", "") or link.get("caption", "") or ""
    if not body:
        body = link.get("message", "") or link.get("description", "") or ""

    # --- Tier 3: object_story_spec.video_data ---
    if not title:
        title = video.get("title", "") or video.get("name", "") or ""
    if not body:
        body = video.get("message", "") or video.get("description", "") or ""

    # --- Tier 4: object_story_spec.template_data (DPA / catalog ads) ---
    if not title:
        title = template.get("name", "") or ""
    if not body:
        body = template.get("message", "") or template.get("description", "") or ""

    # --- Tier 5: link_data.child_attachments (Carousel) - first slide ---
    if (not title or not body) and link:
        kids = link.get("child_attachments") or []
        if kids and isinstance(kids, list):
            k0 = kids[0] if isinstance(kids[0], dict) else {}
            if not title:
                title = k0.get("name", "") or ""
            if not body:
                body = k0.get("description", "") or ""

    # --- Tier 6: asset_feed_spec (Dynamic Creative / Advantage+ creative) ---
    afs = creative.get("asset_feed_spec") or {}
    if not title:
        titles = afs.get("titles") or []
        if titles and isinstance(titles, list):
            t0 = titles[0]
            if isinstance(t0, dict):
                title = t0.get("text", "") or ""
            elif isinstance(t0, str):
                title = t0
    if not body:
        bodies = afs.get("bodies") or []
        if bodies and isinstance(bodies, list):
            b0 = bodies[0]
            if isinstance(b0, dict):
                body = b0.get("text", "") or ""
            elif isinstance(b0, str):
                body = b0
    if not body:
        descs = afs.get("descriptions") or []
        if descs and isinstance(descs, list):
            d0 = descs[0]
            if isinstance(d0, dict):
                body = d0.get("text", "") or ""

    return (title, body)


def detect_destination(ad_name):
    """Identify destination/landing-page type from ad name."""
    n = ad_name or ""
    # Order matters: more specific suffixes first
    if "ProductPage_Catalog" in n: return "ProductPage_Catalog"
    if "Collection_Catalog" in n: return "Collection_Catalog"
    if "Home_Catalog" in n:        return "Home_Catalog"
    if "ProductPage" in n:         return "ProductPage"
    if "Collection" in n:          return "Collection"
    if "HomePage" in n:            return "HomePage"
    if "Catalog" in n:             return "Catalog"
    return "Other"

DESTINATIONS = ["ProductPage","ProductPage_Catalog","Collection","Collection_Catalog","HomePage","Home_Catalog","Catalog","Other"]


import re as _re_sku
_SIZE_TOKEN = _re_sku.compile(r"^\d{1,2}(?:\.\d)?$")

def _normalize_sku(sku):
    """Remove size-like tokens (5.0, 5.5, 10) from a SKU. Shopify variants include size in the SKU
    (e.g. L532-GEOR-5.0-INDI-2563), but ad names use the size-less SKU (L532-GEOR-INDI-2563)."""
    if not sku:
        return ""
    parts = [p for p in str(sku).split("-") if p and not _SIZE_TOKEN.match(p)]
    return "-".join(parts).upper()

def _stock_with_has_ad(stock_list, ads_list):
    """Mark each product with has_ad=True if any normalized SKU matches the first
    underscore-separated token of any active ad name. Pattern (per parametrization sheet):
    Ad name = <SKU>_<format>_<destination>_<copy>_<angle>_<variation>
    Shopify variant SKU may include size: L532-GEOR-5.0-INDI-2563 → normalized to L532-GEOR-INDI-2563."""
    # First-token of each ad name (this is the SKU per parametrization)
    ad_first_tokens = set()
    ad_names_upper = []
    for a in ads_list:
        nm = (a.get("ad_name") or "").strip()
        if not nm:
            continue
        ad_names_upper.append(nm.upper())
        first = nm.split("_", 1)[0].strip().upper()
        if first:
            ad_first_tokens.add(first)
    # Also collect Catalog ad product_ids (numeric first-tokens, ~10-15 digits)
    ad_product_ids = {t for t in ad_first_tokens if t.isdigit() and len(t) >= 8}
    out = []
    for p in stock_list:
        raw_skus = [(s or "").strip() for s in (p.get("skus") or []) if s]
        # Generate normalized SKU variants
        normalized = set()
        for s in raw_skus:
            normalized.add(s.upper())
            n = _normalize_sku(s)
            if n:
                normalized.add(n)
        # Also try matching by product_id (Catalog ads use numeric ids)
        pid = str(p.get("product_id") or "")
        matched_sku = None
        # 1) Exact first-token match
        for s in normalized:
            if s in ad_first_tokens:
                matched_sku = s
                break
        # 2) Product ID match (Catalog ads)
        if not matched_sku and pid in ad_product_ids:
            matched_sku = pid
        # 3) Substring match as last fallback (SKU appears anywhere in ad name)
        if not matched_sku:
            for s in normalized:
                if len(s) < 6:
                    continue
                for nm in ad_names_upper:
                    if s in nm:
                        matched_sku = s
                        break
                if matched_sku:
                    break
        out.append({**p, "has_ad": bool(matched_sku), "matched_sku": matched_sku or ""})
    return out


def extract_destination_url(creative):
    """Extract Meta ad's destination URL from creative metadata.
    Tries link_data.link, video_data CTA link, and asset_feed_spec.link_urls."""
    if not isinstance(creative, dict):
        return ""
    spec = creative.get("object_story_spec") or {}
    # link_data.link (Single Image / Carousel)
    link = (spec.get("link_data") or {}).get("link", "")
    if link: return link
    # video_data CTA
    vd = spec.get("video_data") or {}
    cta = vd.get("call_to_action") or {}
    cta_value = cta.get("value") or {}
    if cta_value.get("link"): return cta_value["link"]
    # asset_feed_spec link_urls
    afs = creative.get("asset_feed_spec") or {}
    link_urls = afs.get("link_urls") or []
    if isinstance(link_urls, list) and link_urls:
        first = link_urls[0] if isinstance(link_urls[0], dict) else {}
        if first.get("website_url"): return first["website_url"]
    # link_data.child_attachments (Carousel each slide may have own link)
    kids = (spec.get("link_data") or {}).get("child_attachments") or []
    if isinstance(kids, list) and kids and isinstance(kids[0], dict):
        if kids[0].get("link"): return kids[0]["link"]
    return ""

def normalize_url(url):
    """Strip query params/UTMs, normalize for grouping."""
    if not url: return ""
    try:
        from urllib.parse import urlparse, urlunparse
        p = urlparse(url)
        return urlunparse((p.scheme, p.netloc, p.path.rstrip("/"), "", "", ""))
    except: return url


def extract_link_clicks(insight):
    """Extract link clicks from Meta insight via multiple fallback paths."""
    if not isinstance(insight, dict):
        return 0
    # Tier 1: direct fields
    v = i_(insight.get("inline_link_clicks", 0))
    if v > 0: return v
    v = i_(insight.get("link_clicks", 0))
    if v > 0: return v
    # Tier 2: actions array - prefer link_click then post_engagement.link_click
    for a in (insight.get("actions") or []):
        atype = (a.get("action_type") or "").lower()
        if atype == "link_click":
            return i_(a.get("value", 0))
    # Tier 3: any clicks
    v = i_(insight.get("clicks", 0))
    return v


def grade_for_roas(roas, baseline):
    """A+ A B C D F based on ROAS vs account baseline."""
    if baseline <= 0: return "N/A"
    ratio = roas / baseline
    if ratio >= 1.5: return "A+"
    if ratio >= 1.2: return "A"
    if ratio >= 1.0: return "B"
    if ratio >= 0.8: return "C"
    if ratio >= 0.5: return "D"
    return "F"


# ─── Load fetched data ──
meta = json.loads(META_PATH.read_text())
shp  = json.loads(SHP_PATH.read_text())

# Map ad metadata
ad_meta = meta.get("ad_metadata", {})

# Aggregate daily counts per ad
days_by_ad = defaultdict(set)
for d in meta.get("ad_daily", []):
    aid = str(d.get("ad_id",""))
    date = d.get("date_start","")
    if aid and date and i_(d.get("impressions",0)) > 0:
        days_by_ad[aid].add(date)
days_count = {ad: len(d) for ad, d in days_by_ad.items()}

# Normalize ads
ads = []
for r in meta.get("ads", []):
    aid = str(r.get("ad_id",""))
    pur, rev = extract_purchases(r)
    cost = f(r.get("spend", 0))
    impressions = i_(r.get("impressions", 0))
    md = ad_meta.get(aid, {})
    creative = (md.get("creative") or {}) if isinstance(md, dict) else {}
    ads.append({
        "campaign_id":     str(r.get("campaign_id","")),
        "campaign_name":   r.get("campaign_name",""),
        "campaign_objective": r.get("objective",""),
        "campaign_status": "ACTIVE",  # filtered upstream
        "adset_id":        str(r.get("adset_id","")),
        "adset_name":      r.get("adset_name",""),
        "adset_status":    "ACTIVE",
        "ad_id":           aid,
        "ad_name":         r.get("ad_name",""),
        "ad_status":       "ACTIVE",
        "destination_url": extract_destination_url(creative),
        "thumbnail_url":   creative.get("thumbnail_url",""),
        "creative_image_url": creative.get("image_url",""),
        "creative_object_type": creative.get("object_type",""),
        "creative_title":   extract_creative_copy(creative)[0],
        "creative_body":    extract_creative_copy(creative)[1],
        "copy_level":       detect_copy_level(r.get("ad_name","")),
        "destination":      detect_destination(r.get("ad_name","")),
        "clicks":           extract_link_clicks(r),
        "ctr":              round(f(r.get("ctr",0)) or f(r.get("inline_link_click_ctr",0)),3),
        "spend":         round(cost, 2),
        "impressions":   impressions,
        "clicks":        0,
        "ctr":           0.0,
        "cpc":           0.0,
        "cpm":           round(cost/impressions*1000, 2) if impressions else 0,
        "purchases":     pur,
        "revenue":       round(rev, 2),
        "roas":          round(rev/cost, 2) if cost > 0 else 0,
        "account_id":    r.get("_account_id",""),
        "account_name":  r.get("_account_name",""),
        "days_active":   days_count.get(aid, 0),
    })

products = shp.get("products", [])
products.sort(key=lambda p: p.get("total_sales",0), reverse=True)

# ─── ad name parsing + product matching ──
def parse_name(name):
    parts = (name or "").split("_")
    return {"product_seg": parts[1] if len(parts)>1 else None,
            "asset_seg":   parts[2].upper() if len(parts)>2 else None,
            "page_type":   parts[3] if len(parts)>3 else None,
            "copy_level":  parts[4] if len(parts)>4 else None,
            "angle":       parts[5] if len(parts)>5 else None}

products_by_handle = {slug(p.get("title","")): p for p in products}
def match_ad(ad):
    seg = (ad["ad_name"].split("_")[1] if "_" in ad["ad_name"] else "").lower()
    if seg and len(seg) >= 4:
        for h, p in products_by_handle.items():
            ts = h.split("-")
            if seg in ts: return p
    return None

for a in ads:
    a["parsed_name"] = parse_name(a["ad_name"])
    matched = match_ad(a)
    a["matched_product_id"]    = matched["product_id"] if matched else None
    a["matched_product_title"] = matched["title"] if matched else None
    a["matched_product_image"] = matched["image_url"] if matched else ""
    a["score"] = round(a["roas"] * math.log(a["purchases"] + 1), 3)
    asset_seg = (a["parsed_name"].get("asset_seg") or "").upper()
    if "VIDEO" in asset_seg: a["asset_format"] = "VIDEO"
    elif "STATIC" in asset_seg or "IMAGE" in asset_seg: a["asset_format"] = "STATIC"
    elif "GIF" in asset_seg or "CAROUSEL" in asset_seg: a["asset_format"] = "GIF/CAROUSEL"
    else:
        cot = (a.get("creative_object_type") or "").upper()
        if "VIDEO" in cot: a["asset_format"] = "VIDEO"
        elif "PHOTO" in cot or "IMAGE" in cot: a["asset_format"] = "STATIC"
        else: a["asset_format"] = "OTHER"

# Aggregations
total_spend = sum(a["spend"] for a in ads)
total_rev_meta = sum(a["revenue"] for a in ads)
total_pur = sum(a["purchases"] for a in ads)
total_imp = sum(a["impressions"] for a in ads)

asset_perf = defaultdict(lambda: {"spend":0,"revenue":0,"purchases":0,"n_ads":0})
for a in ads:
    fmt = a["asset_format"]
    asset_perf[fmt]["spend"] += a["spend"]; asset_perf[fmt]["revenue"] += a["revenue"]
    asset_perf[fmt]["purchases"] += a["purchases"]; asset_perf[fmt]["n_ads"] += 1
roas_by_asset = sorted([{"asset":k,**v,"roas":round(v["revenue"]/v["spend"],2) if v["spend"]>0 else 0} for k,v in asset_perf.items()], key=lambda x:x["roas"], reverse=True)
for r in roas_by_asset:
    r["spend"] = round(r["spend"],2); r["revenue"] = round(r["revenue"],2)

angle_perf = defaultdict(lambda: {"spend":0,"revenue":0,"n":0})
for a in ads:
    if a["spend"] < 50: continue
    ang = (a["parsed_name"].get("angle") or "Unknown").strip()
    angle_perf[ang]["spend"] += a["spend"]; angle_perf[ang]["revenue"] += a["revenue"]; angle_perf[ang]["n"] += 1
top_angles = sorted([{"angle":k,"spend":round(v["spend"],2),"revenue":round(v["revenue"],2),"n":v["n"],"roas":round(v["revenue"]/v["spend"],2) if v["spend"]>0 else 0} for k,v in angle_perf.items() if k!="Unknown"], key=lambda x:x["roas"], reverse=True)

# campaigns aggregation (active only — already filtered)
cmap = defaultdict(lambda: {"spend":0,"revenue":0,"purchases":0,"impressions":0,"ads":[]})
for a in ads:
    c = cmap[a["campaign_id"]]
    c["campaign_id"] = a["campaign_id"]; c["campaign_name"] = a["campaign_name"]
    c["objective"] = a["campaign_objective"]; c["status"] = a["campaign_status"]
    c["account_id"] = a["account_id"]; c["account_name"] = a["account_name"]
    c["spend"] += a["spend"]; c["revenue"] += a["revenue"]
    c["purchases"] += a["purchases"]; c["impressions"] += a["impressions"]
    c["ads"].append(a)
for c in cmap.values():
    c["n_ads"] = len(c["ads"])
    c["spend"] = round(c["spend"],2); c["revenue"] = round(c["revenue"],2)
    c["roas"] = round(c["revenue"]/c["spend"],2) if c["spend"]>0 else 0
    c["score"] = round(c["roas"]*math.log(c["purchases"]+1),3)
campaigns = sorted(cmap.values(), key=lambda c:c["spend"], reverse=True)

# Paused good campaigns (last 90d, ROAS>=3, spend>=10K, non-catalog)
paused = []
for h in meta.get("campaigns_history", []):
    pur, rev = extract_purchases(h)
    spend = f(h.get("spend",0))
    if h.get("effective_status") == "PAUSED" and not is_catalog(h.get("campaign_name","")) and spend >= 10000:
        roas = round(rev/spend,2) if spend>0 else 0
        if roas >= 3:
            paused.append({"name":h.get("campaign_name",""),"id":h.get("campaign_id",""),"status":"PAUSED",
                          "spend":round(spend,2),"roas":roas,"purchases":pur})

# Pause / Refresh logic with 3-day rule
ads_to_refresh = []; ads_to_pause = []
for a in ads:
    if a["spend"] >= PAUSE_MIN_SPEND and a["roas"] < PAUSE_MAX_ROAS and a["days_active"] >= 3:
        if a["matched_product_id"]:
            ads_to_refresh.append(a)
        else:
            ads_to_pause.append(a)

# Per-campaign optimization
slots_avail = {}
campaign_opt = []
for c in campaigns:
    ads_in_c = sorted(c["ads"], key=lambda a: (a["score"], a["roas"]), reverse=True)
    refresh_in_c = [a for a in ads_in_c if a in ads_to_refresh]
    pause_in_c   = [a for a in ads_in_c if a in ads_to_pause]
    keep = [a for a in ads_in_c if a not in refresh_in_c and a not in pause_in_c]
    # Total slots shown = keep + refresh + pause + open = MAX_ADS
    after = len(keep) + len(refresh_in_c) + len(pause_in_c)
    slots = max(0, MAX_ADS - after)
    slots_avail[c["campaign_id"]] = slots
    campaign_opt.append({
        "campaign_id": c["campaign_id"], "campaign_name": c["campaign_name"],
        "account_id": c.get("account_id",""), "account_name": c.get("account_name",""),
        "spend": c["spend"], "roas": c["roas"], "purchases": c["purchases"], "n_ads": c["n_ads"], "score": c["score"],
        "ads_keep_count": len(keep),
        "ads_pause": [{k:a[k] for k in ("ad_id","ad_name","spend","roas","purchases","thumbnail_url","creative_image_url","matched_product_image","matched_product_title","days_active")} for a in pause_in_c],
        "ads_refresh": [{k:a[k] for k in ("ad_id","ad_name","spend","roas","purchases","thumbnail_url","creative_image_url","matched_product_image","matched_product_title","days_active")} for a in refresh_in_c],
        "slots_available": slots,
    })

# Pareto 50%
total_rev_shop = sum(p.get("total_sales",0) for p in products)
target = total_rev_shop * 0.50
cumul = 0; pareto = []
for p in products:
    if cumul >= target: break
    pareto.append(p); cumul += p.get("total_sales",0)
pareto_pids = {p["product_id"] for p in pareto}
advertised = {a["matched_product_id"] for a in ads if a["matched_product_id"]}
def is_non_product(p):
    n = (p.get("title","") + " " + p.get("type","")).lower()
    if p.get("price",0) < 50: return True
    return any(k in n for k in ["free return","gift card","shipping","coverage","warranty"])
pareto_no_ad = [p for p in pareto if p["product_id"] not in advertised and not is_non_product(p)]

# Allocate gaps to existing/paused/new campaigns
camp_tokens = {c["campaign_id"]: tokens(c["campaign_name"]) for c in campaigns}
paused_tokens = {p["id"]: tokens(p["name"]) for p in paused}

def primary_token(p):
    p_tok = sorted(tokens(p.get("title","")))
    used = set().union(*camp_tokens.values()) if camp_tokens else set()
    used |= set().union(*paused_tokens.values()) if paused_tokens else set()
    novel = [t for t in p_tok if t not in used]
    return (novel or p_tok or [(p.get("type") or "Product").lower()])[0]

cluster = defaultdict(list)
for p in pareto_no_ad[:25]:
    cluster[primary_token(p)].append(p)

allocations = []
for p in pareto_no_ad[:20]:
    p_tok = tokens(p.get("title","") + " " + p.get("type",""))
    # active match w/ slot
    best = None; bw = 0; ovl = []
    for c in campaigns:
        if is_catalog(c["campaign_name"]) or slots_avail.get(c["campaign_id"],0) <= 0: continue
        o = p_tok & camp_tokens.get(c["campaign_id"], set())
        if o:
            w = len(o)*100 + c["score"]
            if w > bw: bw = w; best = c; ovl = sorted(o)
    if best:
        slots_avail[best["campaign_id"]] -= 1
        allocations.append({"product":p,"campaign_target":best["campaign_name"],"kind":"existing_active",
                          "overlap":ovl,"match_roas":best["roas"],
                          "suggested_account_id":best.get("account_id",""),
                          "suggested_account_name":best.get("account_name","Larroude US")})
        continue
    # paused match
    best = None; bw = 0; ovl = []
    for pp in paused:
        o = p_tok & paused_tokens.get(pp["id"], set())
        if o:
            w = len(o)*100 + pp["roas"]*5
            if w > bw: bw = w; best = pp; ovl = sorted(o)
    if best:
        allocations.append({"product":p,"campaign_target":best["name"],"kind":"reactivate","overlap":ovl,
                          "match_roas":best["roas"],
                          "suggested_account_id":"act_929449929417505",
                          "suggested_account_name":"PRE-ORDER US"})
        continue
    # cluster check for new campaign
    pt = primary_token(p)
    if len(cluster.get(pt,[])) >= MIN_CLUSTER:
        allocations.append({"product":p,"campaign_target":f"NEW: A-Sale_{pt.capitalize()}","kind":"new_campaign",
                          "overlap":[pt],"cluster_size":len(cluster[pt]),
                          "suggested_account_id":"act_929449929417505",
                          "suggested_account_name":"PRE-ORDER US"})
        continue
    # fallback
    fb = next((c for c in sorted(campaigns,key=lambda c:c["score"],reverse=True) if slots_avail.get(c["campaign_id"],0)>0 and not is_catalog(c["campaign_name"])), None)
    if fb:
        slots_avail[fb["campaign_id"]] -= 1
        allocations.append({"product":p,"campaign_target":fb["campaign_name"],"kind":"fallback_active","overlap":[],
                          "match_roas":fb["roas"],"suggested_account_id":fb.get("account_id",""),
                          "suggested_account_name":fb.get("account_name","Larroude US")})

# Diversification
ASSET_DIST = ["STATIC","VIDEO","GIF","STATIC","VIDEO","STATIC","GIF","VIDEO","STATIC","VIDEO","GIF","STATIC","VIDEO","STATIC","VIDEO","GIF","STATIC","VIDEO","STATIC","GIF"]
ASSET_PROFILES = {
    "VIDEO":  {"asset_type":"MP4","aspect_ratio":"1:1\n9:16\n1.91:1","dimensions":"1200x1200\n1080x1920\n1200 x 628 px","file_size":"up to 30 MB"},
    "STATIC": {"asset_type":"JPEG","aspect_ratio":"1:1\n9:16\n1.91:1","dimensions":"1200x1200\n1080x1920\n1200 x 628 px","file_size":"up to 5 MB"},
    "GIF":    {"asset_type":"MP4","aspect_ratio":"1:1\n9:16\n1.91:1","dimensions":"1200x1200\n1080x1920\n1200 x 628 px","file_size":"up to 15 MB"},
}
COPY_TEMPLATES = {
    "Product Aware": ["{p} — your next favorite.","Meet the {p}.","{p}: a forever-piece for your closet.","Hand-crafted. Effortless. {p}.","{p} — built for everyday luxe."],
    "Solution Aware": ["All-day comfort, runway-ready style. Meet {p}.","From day to dinner — {p} keeps up.","The {ps} you didn't know you needed.","Effortless looks start with the {ps}.","Tired of choosing between style and comfort? {p}."],
    "Most Aware": ["Selling fast: {p}.","Restocked. Won't last long. {p}.","Your closet called — it wants the {ps}.","Top reviews can't be wrong. Try {p}.","30% off this week only — {ps}."],
}
LEVELS = ["Product Aware","Solution Aware","Most Aware","Product Aware","Solution Aware","Solution Aware","Product Aware","Most Aware","Product Aware","Solution Aware","Most Aware","Product Aware","Solution Aware","Product Aware","Solution Aware","Most Aware","Product Aware","Solution Aware","Product Aware","Most Aware"]

def short_n(t):
    s = re.sub(r"\s+in\s+.*$","",t,flags=re.IGNORECASE).strip()
    parts = s.split(); return " ".join(parts[:3]) if len(parts)>3 else s

ANGLE_AFF = {"Unboxing":["crochet","velvet","linen","gioiello"],"UGC":["sandal","mule","flat"],"On-foot":["sandal","mule","slipper","heel","wedge","raffia","leather"],"Product-focused":["boot","boat","loafer","gioiello","texture"],"Product Mix":["set","bundle"],"Partnership":["limited","collab"],"Lifestyle":[]}
used_a = Counter(); used_c = defaultdict(set)
def pick_a(p):
    pt = tokens(p.get("title","") + " " + p.get("type",""))
    best = None; bw = -9999
    for a in top_angles:
        ang = a["angle"]; aff = sum(1 for t in ANGLE_AFF.get(ang,[]) if t in pt)
        rn = a["roas"]/max(t["roas"] for t in top_angles); w = aff*2 + rn - used_a[ang]*1.5
        if w > bw: bw = w; best = ang
    used_a[best] += 1
    return best
def pick_c(p, lvl):
    pool = COPY_TEMPLATES[lvl]; used = used_c[lvl]
    avail = [t for t in pool if t not in used]
    if not avail: used.clear(); avail = pool
    t = random.choice(avail); used.add(t)
    return t.format(p=p["title"], ps=short_n(p["title"]))

recommendations = []
for idx, alloc in enumerate(allocations):
    p = alloc["product"]; ang = pick_a(p); lvl = LEVELS[idx % len(LEVELS)]; ass = ASSET_DIST[idx % len(ASSET_DIST)]
    profile = ASSET_PROFILES[ass]
    diag = {
        "existing_active": f"Pareto 50% product (${p['total_sales']:,.0f}/{p['units_sold']} un) without active ad. Slot in '{alloc['campaign_target']}' (ROAS {alloc.get('match_roas','?')}x).",
        "reactivate": f"Pareto 50% product (${p['total_sales']:,.0f}/{p['units_sold']} un) without active ad. REACTIVATE paused campaign '{alloc['campaign_target']}' (historical ROAS {alloc.get('match_roas','?')}x).",
        "new_campaign": f"Pareto 50% product (${p['total_sales']:,.0f}/{p['units_sold']} un). Cluster of {alloc.get('cluster_size','?')} similar products → create new Sales campaign.",
        "fallback_active": f"Pareto 50% product (${p['total_sales']:,.0f}/{p['units_sold']} un). No specific match — placed in best active campaign with slot.",
    }.get(alloc["kind"], "")
    recommendations.append({
        "rec_id": f"add_{p['product_id']}_{idx}", "kind": "add_new_creative",
        "diagnostic": diag, "product": p,
        "asset": ass, **profile,
        "objective":"Conversion","funnel":"MOF","priority":"High","key_highlight":"",
        "copy_level": lvl, "copy": pick_c(p, lvl), "creative_angle": ang,
        "link_to_site": f"https://larroude.com/products/{p.get('handle') or slug(p['title'])}",
        "campaign_target": alloc["campaign_target"], "campaign_target_kind": alloc["kind"],
        "campaign_match_overlap": alloc.get("overlap",[]), "campaign_match_roas": alloc.get("match_roas"),
        "suggested_account_id": alloc.get("suggested_account_id",""),
        "suggested_account_name": alloc.get("suggested_account_name",""),
    })

# Refresh recs
RL = ["Solution Aware","Most Aware","Solution Aware","Product Aware","Solution Aware","Most Aware"]
RA = ["STATIC","VIDEO","STATIC","VIDEO","GIF","VIDEO"]
for idx, ad in enumerate(ads_to_refresh[:12]):
    p = next((pp for pp in products if pp["product_id"] == ad["matched_product_id"]), None) or {"title":ad["ad_name"],"image_url":"","type":"","total_sales":0,"units_sold":0,"price":0,"handle":""}
    ang = pick_a(p); lvl = RL[idx % len(RL)]; ass = RA[idx % len(RA)]; profile = ASSET_PROFILES[ass]
    recommendations.append({
        "rec_id": f"refresh_{ad['ad_id']}", "kind":"refresh_existing",
        "diagnostic": f"Fatigued ad in '{ad['campaign_name']}' (spend ${ad['spend']:,.0f}, ROAS {ad['roas']}). Replace creative.",
        "product": p,
        "underperforming_ad": {k: ad.get(k) for k in ("ad_id","ad_name","spend","roas","purchases","thumbnail_url","creative_image_url","matched_product_image","days_active","account_id","account_name")},
        "asset": ass, **profile,
        "objective":"Conversion","funnel":"MOF","priority":"High","key_highlight":"",
        "copy_level": lvl, "copy": pick_c(p, lvl) if p.get("title") else f"Refreshed {ad['ad_name']}",
        "creative_angle": ang,
        "link_to_site": f"https://larroude.com/products/{p.get('handle') or slug(p['title'])}" if p.get("title") else "",
        "campaign_target": ad["campaign_name"], "campaign_target_kind": "existing_active",
    })

pause_recs = []
# Filter: only pause recs for ads with spend > $1,000
ads_to_pause = [a for a in ads_to_pause if a.get("spend", 0) > 1000]
for ad in ads_to_pause[:12]:
    pause_recs.append({
        "rec_id": f"pause_{ad['ad_id']}", "kind": "pause_creative",
        "diagnostic": f"Fatigued (spend ${ad['spend']:,.0f}, ROAS {ad['roas']}, {ad['days_active']} days). Recommend pause.",
        "underperforming_ad": {k: ad.get(k) for k in ("ad_id","ad_name","spend","roas","purchases","thumbnail_url","creative_image_url","matched_product_image","matched_product_title","days_active","account_id","account_name")},
        "campaign_target": ad["campaign_name"],
    })

significant = [a for a in ads if a["spend"] >= 50]
top_creatives = sorted(significant, key=lambda a: a["score"], reverse=True)[:15]
underperformers = sorted([a for a in ads if a["spend"]>=PAUSE_MIN_SPEND and a["roas"]<PAUSE_MAX_ROAS and a["days_active"]>=3], key=lambda a: a["spend"], reverse=True)
best_conv = max([c for c in campaigns if c["spend"]>=500 and c["purchases"]>=5], key=lambda c: c["score"], default=None)

from datetime import datetime, timezone

# === Account-level CTR ===
total_clicks = sum(a.get("clicks",0) for a in ads)
total_impressions_for_ctr = sum(a.get("impressions",0) for a in ads)
account_ctr = round(100.0 * total_clicks / total_impressions_for_ctr, 2) if total_impressions_for_ctr > 0 else 0

# === Destination Performance Aggregation ===
dest_perf = {}
for d in DESTINATIONS:
    rel = [a for a in ads if a.get("destination") == d]
    if not rel: continue
    spend_d = sum(a.get("spend",0) for a in rel)
    rev_d = sum(a.get("revenue",0) for a in rel)
    pur_d = sum(a.get("purchases",0) for a in rel)
    clk_d = sum(a.get("clicks",0) for a in rel)
    imp_d = sum(a.get("impressions",0) for a in rel)
    dest_perf[d] = {
        "n_ads": len(rel),
        "spend": round(spend_d,2),
        "revenue": round(rev_d,2),
        "purchases": pur_d,
        "clicks": clk_d,
        "impressions": imp_d,
        "ctr": round(100.0*clk_d/imp_d,2) if imp_d>0 else 0,
        "roas": round(rev_d/spend_d,2) if spend_d>0 else 0,
        "cpa": round(spend_d/pur_d,2) if pur_d>0 else 0,
    }
    dest_perf[d]["grade"] = grade_for_roas(dest_perf[d]["roas"], (total_rev_meta/total_spend) if total_spend>0 else 0)

# === Top destination URLs per account (last 7d, Top 5 by ROAS+CVR score) ===
WEIGHT_ROAS = 0.6
WEIGHT_CVR = 0.4
TOP_N_URLS = 10
combined_url_stats = {}  # {url_norm: aggregated record across all accounts}
for a in ads:
    url_raw = a.get("destination_url") or ""
    if not url_raw: continue
    url_norm = normalize_url(url_raw)
    if not url_norm: continue
    rec = combined_url_stats.setdefault(url_norm, {
        "url": url_norm, "sample_full_url": url_raw,
        "spend": 0.0, "link_clicks": 0,
        "purchases": 0, "purchase_value": 0.0,
        "impressions": 0,
    })
    rec["spend"] += float(a.get("spend",0) or 0)
    rec["link_clicks"] += int(a.get("clicks",0) or 0)
    rec["purchases"] += int(a.get("purchases",0) or 0)
    rec["purchase_value"] += float(a.get("revenue",0) or 0)
    rec["impressions"] += int(a.get("impressions",0) or 0)

# Filter URLs with spend > $1,000
MIN_SPEND_USD = 1000
combined_urls = [r for r in combined_url_stats.values() if r["spend"] > MIN_SPEND_USD]
for r in combined_urls:
    r["roas"] = round(r["purchase_value"] / r["spend"], 2) if r["spend"] > 0 else 0
    r["cvr"] = round(r["purchases"] / r["link_clicks"], 4) if r["link_clicks"] > 0 else 0
# Normalize ROAS and CVR globally for scoring
max_roas = max([r["roas"] for r in combined_urls], default=0)
max_cvr = max([r["cvr"] for r in combined_urls], default=0)
for r in combined_urls:
    norm_roas = (r["roas"] / max_roas) if max_roas > 0 else 0
    norm_cvr = (r["cvr"] / max_cvr) if max_cvr > 0 else 0
    r["score"] = round(WEIGHT_ROAS * norm_roas + WEIGHT_CVR * norm_cvr, 4)
combined_urls.sort(key=lambda x: x["score"], reverse=True)

# Single combined block (no per-account separation)
top_destination_urls = [{
    "id": "combined",
    "label": "All accounts",
    "date_start": "last_7d_start",
    "date_stop": "today",
    "top": combined_urls[:TOP_N_URLS],
    "n_total_urls": len(combined_urls),
}]

# === Copy Level Performance Aggregation ===
copy_perf = {}
for lvl in COPY_LEVELS + ["No Copy"]:
    rel = [a for a in ads if a.get("copy_level") == lvl]
    spend_sum = sum(a.get("spend",0) for a in rel)
    rev_sum = sum(a.get("revenue",0) for a in rel)
    pur_sum = sum(a.get("purchases",0) for a in rel)
    roas = round(rev_sum/spend_sum, 2) if spend_sum > 0 else 0.0
    copy_perf[lvl] = {
        "n_ads": len(rel),
        "spend": round(spend_sum, 2),
        "revenue": round(rev_sum, 2),
        "purchases": pur_sum,
        "roas": roas,
        "cpa": round(spend_sum/pur_sum, 2) if pur_sum > 0 else 0.0,
    }
account_roas_baseline = round((total_rev_meta/total_spend), 2) if total_spend > 0 else 0
for lvl, perf in copy_perf.items():
    perf["grade"] = grade_for_roas(perf["roas"], account_roas_baseline) if perf["n_ads"] > 0 else "N/A"

# Find best copy level (most spend with positive ROAS) for recommendation suggestions
best_copy_level = "Product Aware"
best_score = 0
for lvl, perf in copy_perf.items():
    if perf["n_ads"] >= 2 and perf["roas"] >= account_roas_baseline:
        score = perf["roas"] * perf["spend"]
        if score > best_score:
            best_score = score
            best_copy_level = lvl

# Update each recommendation with the suggested copy_level (if currently default)
for rec in recommendations:
    rec["suggested_copy_level"] = best_copy_level

# Build copy_ads list for the new Copy tab (sorted by spend desc, max 50)
copy_ads_list = []
for a in sorted(ads, key=lambda x: (x.get("roas",0) * x.get("purchases",0), x.get("spend",0)), reverse=True)[:50]:
    copy_ads_list.append({
        "ad_id": a.get("ad_id",""),
        "ad_name": a.get("ad_name",""),
        "copy_level": a.get("copy_level","No Copy"),
        "creative_title": a.get("creative_title",""),
        "creative_body": a.get("creative_body",""),
        "thumbnail_url": a.get("thumbnail_url","") or a.get("creative_image_url",""),
        "spend": a.get("spend",0),
        "purchases": a.get("purchases",0),
        "revenue": a.get("revenue",0),
        "roas": a.get("roas",0),
        "campaign_name": a.get("campaign_name",""),
    })

analysis = {
    "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    "period": "last_7_days",
    "filter": "OUTCOME_SALES + ACTIVE campaigns + ACTIVE adsets + ACTIVE ads only",
    "max_ads_per_campaign": MAX_ADS,
    "pause_criteria": f"spend≥${PAUSE_MIN_SPEND} AND ROAS<{PAUSE_MAX_ROAS} AND days_active>=3",
    "min_cluster_for_new_campaign": MIN_CLUSTER,
    "kpis": {
        "spend": round(total_spend,2), "revenue_meta": round(total_rev_meta,2),
        "purchases": total_pur, "impressions": total_imp,
        "account_roas": round(total_rev_meta/total_spend,2) if total_spend>0 else 0,
        "cpa": round(total_spend/total_pur,2) if total_pur>0 else 0,
        "ctr": account_ctr,
        "clicks": total_clicks,
        "shop_revenue": round(total_rev_shop,2),
        "shop_units": sum(p.get("units_sold",0) for p in products),
        "n_products_sold": len(products), "n_ads_active": len(ads),
        "n_campaigns_active": len(campaigns),
    },
    "top_creatives": top_creatives, "underperformers": underperformers[:10],
    "campaigns": [{**{k:v for k,v in c.items() if k!="ads"}} for c in campaigns],
    "campaign_optimization": campaign_opt, "paused_good_campaigns": paused,
    "top_angles": top_angles, "roas_by_asset": roas_by_asset,
    "gap_products": pareto_no_ad[:20],
    "advertised_product_ids": list(advertised),
    "best_conversion_campaign": best_conv and {k:v for k,v in best_conv.items() if k!="ads"},
    "top_sales_campaigns": [{k:v for k,v in c.items() if k!="ads"} for c in sorted(campaigns,key=lambda c:c["score"],reverse=True)[:5] if c["spend"]>=500 and c["purchases"]>=5],
    "products_top20": _stock_with_has_ad(products[:20], ads),
    "products_by_stock": _stock_with_has_ad(shp.get("products_by_stock", []), ads),
    "recommendations": recommendations, "pause_recommendations": pause_recs,
    "n_total_products": len(products), "n_total_ads": len(ads),
    "n_advertised_products": len(advertised),
    "pareto_50pct": {
        "total_revenue": round(total_rev_shop,2), "target_50pct": round(target,2),
        "n_pareto_products": len(pareto), "n_pareto_with_ads": sum(1 for p in pareto if p["product_id"] in advertised),
        "n_pareto_without_ads": len(pareto_no_ad),
        "pareto_products": [{**p,"advertised":p["product_id"] in advertised} for p in pareto],
        "pareto_without_ads": pareto_no_ad,
    },
    "copy_performance": copy_perf,
    "destination_performance": dest_perf,
    "top_destination_urls": top_destination_urls,
    "best_copy_level": best_copy_level,
    "account_roas_baseline": account_roas_baseline,
    "copy_ads": copy_ads_list,
}

OUT_DIR.mkdir(parents=True, exist_ok=True)
out_path = OUT_DIR / "analysis.json"
out_path.write_text(json.dumps(analysis, indent=2, ensure_ascii=False))
print(f"[Pipeline] Wrote analysis: {out_path}  ({out_path.stat().st_size:,} bytes)", file=sys.stderr)
print(f"  Active sales ads:   {len(ads)}", file=sys.stderr)
print(f"  Active campaigns:   {len(campaigns)}", file=sys.stderr)
print(f"  Recommendations:    {len(recommendations)} ADD + {len(pause_recs)} PAUSE", file=sys.stderr)
