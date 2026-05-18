"""
Direct Shopify Admin API client for the Larroudé creative dashboard.
Pulls products + orders (last 7 days) and aggregates to product-level sales.
Outputs JSON consumed by rebuild_v4.py.
"""
import os, sys, json, time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

DOMAIN = os.environ.get("SHOPIFY_US_STORE_DOMAIN", "larroude-com.myshopify.com")
TOKEN  = os.environ["SHOPIFY_US_ADMIN_API_TOKEN"]
VERSION = os.environ.get("SHOPIFY_API_VERSION", "2025-01")
BASE = f"https://{DOMAIN}/admin/api/{VERSION}"

def get(path, params=None):
    """GET with Shopify cursor pagination via Link header."""
    rows = []
    url = f"{BASE}/{path}"
    if params:
        url += "?" + urlencode(params)
    while url:
        try:
            req = Request(url, headers={"X-Shopify-Access-Token": TOKEN, "Accept": "application/json"})
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
                link = resp.headers.get("Link", "")
        except HTTPError as e:
            err = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Shopify {e.code} on {url[:120]}...: {err[:300]}")
        # Append top-level array (e.g., 'products' or 'orders')
        for k, v in data.items():
            if isinstance(v, list):
                rows.extend(v)
        # Next page cursor from Link header
        url = None
        for part in link.split(","):
            part = part.strip()
            if 'rel="next"' in part:
                start = part.find("<") + 1
                end = part.find(">")
                if start > 0 and end > start:
                    url = part[start:end]
        time.sleep(0.5)  # respect rate limit
    return rows

def fetch_products():
    return get("products.json", {"limit": 250, "fields": "id,title,product_type,vendor,status,handle,images,variants,created_at"})

def fetch_orders_last_7d():
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return get("orders.json", {
        "limit": 250,
        "status": "any",
        "created_at_min": since,
        "fields": "id,total_price,subtotal_price,total_discounts,line_items,created_at,financial_status",
    })

def aggregate_sales(products, orders):
    """Build per-product sales for last 7 days."""
    products_by_id = {str(p["id"]): p for p in products}
    agg = {}  # product_id -> {gross_sales, units, orders}

    for o in orders:
        if o.get("financial_status") in (None, "voided", "refunded"):
            continue
        for li in o.get("line_items", []):
            pid = str(li.get("product_id") or "")
            if not pid: continue
            qty = int(li.get("quantity") or 0)
            price = float(li.get("price") or 0)
            line_total = qty * price
            agg.setdefault(pid, {"gross_sales": 0.0, "units_sold": 0, "n_orders": set()})
            agg[pid]["gross_sales"] += line_total
            agg[pid]["units_sold"]  += qty
            agg[pid]["n_orders"].add(o["id"])

    output = []
    for pid, p in products_by_id.items():
        a = agg.get(pid, {"gross_sales": 0.0, "units_sold": 0, "n_orders": set()})
        if a["units_sold"] == 0:  # only include products with sales in last 7d
            continue
        first_variant = (p.get("variants") or [{}])[0]
        first_image = (p.get("images") or [{}])[0]
        output.append({
            "product_id": pid,
            "title": p.get("title", ""),
            "type":  p.get("product_type", ""),
            "vendor": p.get("vendor", ""),
            "image_url": first_image.get("src", ""),
            "price": float(first_variant.get("price") or 0),
            "handle": p.get("handle", ""),
            "created_at": p.get("created_at", ""),
            "gross_sales": round(a["gross_sales"], 2),
            "total_sales": round(a["gross_sales"], 2),  # before discounts/returns; matches Supermetrics naming
            "net_sales":   round(a["gross_sales"], 2),
            "units_sold":  a["units_sold"],
            "n_orders":    len(a["n_orders"]),
            "gross_profit": 0.0,  # not avail without cost data
        })
    output.sort(key=lambda p: p["total_sales"], reverse=True)
    return output

def main():
    print(f"[Shopify] Fetching products from {DOMAIN}", file=sys.stderr)
    products = fetch_products()
    print(f"  → {len(products)} products", file=sys.stderr)

    print(f"[Shopify] Fetching last-7d orders", file=sys.stderr)
    orders = fetch_orders_last_7d()
    print(f"  → {len(orders)} orders", file=sys.stderr)

    print(f"[Shopify] Aggregating product sales...", file=sys.stderr)
    products_with_sales = aggregate_sales(products, orders)
    print(f"  → {len(products_with_sales)} products with sales in 7d", file=sys.stderr)

    out = {
        "store_domain": DOMAIN,
        "store_name": "Larroude US",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "products": products_with_sales,
        "raw_product_count": len(products),
        "raw_order_count": len(orders),
    }
    output_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/shopify_data.json"
    with open(output_path, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"[Shopify] Wrote {output_path} ({os.path.getsize(output_path):,} bytes)", file=sys.stderr)

if __name__ == "__main__":
    main()
