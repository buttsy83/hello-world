#!/usr/bin/env python3
"""Build an offline LEGO-set catalog from the Marstoy "brick-kits" collection.

Marstoy item codes are LEGO set numbers with an "M" prefix and the digits
reversed (M06457 -> 75460, M14301 -> 10341). This script:

  1. crawls every collection page and collects code, title, price and URL;
  2. reverses each code into a candidate LEGO set number;
  3. verifies the candidate on Rebrickable (official name, year, image);
  4. downloads the images and writes ONE self-contained HTML file with the
     images embedded as data URIs (search box, sorted by set number);
  5. optionally exports a PDF copy (needs Playwright + Chromium).

Codes that cannot map to a real set (e.g. a reversal that starts with 0)
or that Rebrickable doesn't know are flagged, never guessed.

Usage:
    pip install requests beautifulsoup4          # required
    pip install playwright && playwright install chromium   # only for --pdf
    export REBRICKABLE_KEY=...   # free key: rebrickable.com/users/<you>/settings/#api
    python build_catalog.py --pdf
"""

import argparse
import base64
import html
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://marstoy.com"
COLLECTION = f"{BASE}/collections/brick-kits"
REBRICKABLE_API = "https://rebrickable.com/api/v3/lego/sets/{}/"
CODE_RE = re.compile(r"\bM\d{4,6}\b")
HEADERS = {"User-Agent": "Mozilla/5.0 (personal catalog builder)"}

session = requests.Session()
session.headers.update(HEADERS)


def get(url, **kw):
    for attempt in range(4):
        try:
            r = session.get(url, timeout=30, **kw)
            if r.status_code == 429:
                time.sleep(2 ** attempt * 5)
                continue
            return r
        except requests.RequestException:
            time.sleep(2 ** attempt)
    return None


# ---------------------------------------------------------------- crawling

def crawl_shopify_json(handle_url):
    """Shopify stores expose /products.json; far more reliable than HTML."""
    items, page = [], 1
    while True:
        r = get(f"{handle_url}/products.json", params={"limit": 250, "page": page})
        if r is None or r.status_code != 200:
            return None if page == 1 else items
        try:
            products = r.json().get("products", [])
        except ValueError:
            return None if page == 1 else items
        if not products:
            return items
        for p in products:
            text = " ".join([p.get("title", ""), p.get("handle", "")] +
                            [v.get("sku") or "" for v in p.get("variants", [])])
            m = CODE_RE.search(text.upper())
            price = p["variants"][0].get("price") if p.get("variants") else ""
            items.append({
                "code": m.group(0) if m else "",
                "title": p.get("title", ""),
                "price": price,
                "url": f"{BASE}/products/{p.get('handle', '')}",
            })
        page += 1


def crawl_html(pages):
    items, seen = [], set()
    for n in range(1, pages + 1):
        r = get(COLLECTION, params={"page_num": n})
        if r is None or r.status_code != 200:
            print(f"  page {n}: failed ({getattr(r, 'status_code', 'no response')})")
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        found = 0
        for a in soup.select('a[href*="/products/"]'):
            href = a["href"].split("?")[0]
            url = href if href.startswith("http") else BASE + href
            if url in seen:
                continue
            card = a.find_parent(["li", "div", "article"]) or a
            text = card.get_text(" ", strip=True)
            m = CODE_RE.search(text.upper()) or CODE_RE.search(url.upper())
            if not m:
                continue
            price = re.search(r"[$€£]\s?\d[\d,]*(?:\.\d{2})?", text)
            title = a.get_text(" ", strip=True) or text[:120]
            seen.add(url)
            found += 1
            items.append({"code": m.group(0), "title": title,
                          "price": price.group(0) if price else "", "url": url})
        print(f"  page {n}: {found} items")
        time.sleep(1)
    return items


# ------------------------------------------------------------ verification

def reverse_code(code):
    """M06457 -> ('75460', None); M12340 -> (None, reason)."""
    digits = code.upper().lstrip("M")
    if not digits.isdigit():
        return None, "code has no digits"
    rev = digits[::-1]
    if rev.startswith("0"):
        return None, f"reverses to {rev} (leading zero, not a set number)"
    return rev, None


def verify(set_num, key, cache):
    if set_num in cache:
        return cache[set_num]
    result = None
    if key:
        r = get(REBRICKABLE_API.format(f"{set_num}-1"),
                headers={"Authorization": f"key {key}"})
        if r is not None and r.status_code == 200:
            d = r.json()
            result = {"name": d.get("name"), "year": d.get("year"),
                      "pieces": d.get("num_parts"), "img": d.get("set_img_url"),
                      "link": d.get("set_url")}
        time.sleep(1.1)  # Rebrickable allows ~1 request/second
    else:
        # No key: probe the public CDN image only; no official name available.
        img = f"https://cdn.rebrickable.com/media/sets/{set_num}-1.jpg"
        r = get(img)
        if r is not None and r.status_code == 200:
            result = {"name": None, "year": None, "pieces": None, "img": img,
                      "link": f"https://rebrickable.com/sets/{set_num}-1/"}
    cache[set_num] = result
    return result


def embed_image(url, img_dir):
    if not url:
        return ""
    fn = img_dir / re.sub(r"[^\w.-]", "_", url.rsplit("/", 1)[-1])
    if not fn.exists():
        r = get(url)
        if r is None or r.status_code != 200:
            return ""
        fn.write_bytes(r.content)
    mime = "image/png" if fn.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(fn.read_bytes()).decode()


# ------------------------------------------------------------------ output

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Marstoy LEGO Lookup</title>
<style>
:root{--bg:#f6f6f4;--card:#fff;--fg:#1d1d1f;--muted:#6b6b70;--line:#e2e2e0;--warn:#b54708;--warnbg:#fff4e5;--accent:#0b63ce}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--card:#1f1f22;--fg:#f2f2f2;--muted:#a0a0a8;--line:#333338;--warn:#ffb46b;--warnbg:#3a2a17;--accent:#6aa8ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.4 -apple-system,system-ui,sans-serif}
header{position:sticky;top:0;background:var(--bg);padding:12px 16px;border-bottom:1px solid var(--line);z-index:1}
h1{font-size:18px;margin:0 0 8px}input{width:100%;padding:10px 12px;font-size:16px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--fg)}
.meta{color:var(--muted);font-size:13px;margin-top:6px}
main{padding:12px 16px;display:grid;gap:10px;grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}
.c{display:flex;gap:12px;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px;break-inside:avoid}
.c img{width:96px;height:96px;object-fit:contain;flex:none;background:#fff;border-radius:8px}
.ph{width:96px;height:96px;flex:none;border-radius:8px;background:var(--line)}
.n{font-weight:700;font-size:17px}.t{margin:2px 0}.s{color:var(--muted);font-size:13px}
.flag{background:var(--warnbg);color:var(--warn);border-radius:6px;padding:2px 6px;font-size:12px;display:inline-block;margin-top:4px}
a{color:var(--accent);text-decoration:none}
@media print{header{position:static}input{display:none}main{display:block}.c{margin-bottom:8px}}
</style></head><body>
<header><h1>Marstoy &rarr; LEGO set lookup</h1>
<input id="q" type="search" placeholder="Search set number, Marstoy code or name" autocomplete="off">
<div class="meta" id="meta">__META__</div></header>
<main id="list">__CARDS__</main>
<script>
const q=document.getElementById('q'),cards=[...document.querySelectorAll('.c')],meta=document.getElementById('meta'),base=meta.textContent;
q.addEventListener('input',()=>{const v=q.value.trim().toLowerCase();let n=0;
cards.forEach(c=>{const hit=!v||c.dataset.k.includes(v);c.style.display=hit?'':'none';n+=hit});
meta.textContent=v?n+' of '+cards.length+' shown':base;});
</script></body></html>"""


def card(it):
    e = html.escape
    img = f'<img src="{it["img_data"]}" alt="">' if it.get("img_data") else '<div class="ph"></div>'
    name = it.get("lego_name") or it["title"]
    bits = [b for b in (it.get("year"), f'{it["pieces"]} pcs' if it.get("pieces") else None) if b]
    num = it.get("set_num") or "—"
    key = " ".join(str(x) for x in (num, it["code"], name, it["title"])).lower()
    flag = f'<div class="flag">⚠ {e(it["flag"])}</div>' if it.get("flag") else ""
    lego = f' · <a href="{e(it["link"])}">Rebrickable</a>' if it.get("link") else ""
    return (f'<div class="c" data-k="{e(key)}">{img}<div>'
            f'<div class="n">{e(num)}</div><div class="t">{e(name)}</div>'
            f'<div class="s">{e(it["code"])} · {e(str(it["price"]))}'
            f'{" · " + e(" · ".join(map(str, bits))) if bits else ""}</div>'
            f'<div class="s"><a href="{e(it["url"])}">Marstoy</a>{lego}</div>{flag}</div></div>')


def sort_key(it):
    n = it.get("set_num")
    return (0, int(n)) if n else (1, it["code"])


def write_html(items, path):
    ok = sum(1 for i in items if not i.get("flag"))
    meta = f"{len(items)} items · {ok} verified · {len(items) - ok} flagged"
    out = PAGE.replace("__META__", meta).replace(
        "__CARDS__", "\n".join(card(i) for i in sorted(items, key=sort_key)))
    path.write_text(out, encoding="utf-8")


def write_pdf(html_path, pdf_path):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto(html_path.resolve().as_uri())
        pg.pdf(path=str(pdf_path), format="A4", print_background=True,
               margin={"top": "10mm", "bottom": "10mm", "left": "8mm", "right": "8mm"})
        b.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=19)
    ap.add_argument("--out", default="output")
    ap.add_argument("--pdf", action="store_true")
    ap.add_argument("--html-only", action="store_true",
                    help="skip the Shopify JSON endpoint and scrape HTML pages")
    a = ap.parse_args()

    out = Path(a.out)
    img_dir = out / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    key = os.environ.get("REBRICKABLE_KEY")
    if not key:
        print("No REBRICKABLE_KEY set: images only, no official titles.")

    print("Crawling Marstoy...")
    items = None if a.html_only else crawl_shopify_json(COLLECTION)
    if not items:
        items = crawl_html(a.pages)
    # Deduplicate by code.
    items = list({(i["code"] or i["url"]): i for i in items}.values())
    print(f"Collected {len(items)} items")

    cache_file = out / "rebrickable_cache.json"
    cache = json.loads(cache_file.read_text()) if cache_file.exists() else {}
    for n, it in enumerate(items, 1):
        if not it["code"]:
            it["flag"] = "no M-code found on listing"
            continue
        set_num, reason = reverse_code(it["code"])
        if not set_num:
            it["flag"] = reason
            continue
        it["set_num"] = set_num
        info = verify(set_num, key, cache)
        if not info:
            it["flag"] = f"{set_num} not found on Rebrickable"
            continue
        it.update(lego_name=info["name"], year=info["year"], pieces=info["pieces"],
                  link=info["link"], img_data=embed_image(info["img"], img_dir))
        print(f"  [{n}/{len(items)}] {it['code']} -> {set_num} {info['name'] or ''}")
    cache_file.write_text(json.dumps(cache, indent=1))

    (out / "items.json").write_text(json.dumps(
        [{k: v for k, v in i.items() if k != "img_data"} for i in items], indent=1))
    html_path = out / "marstoy_lego_catalog.html"
    write_html(items, html_path)
    print(f"Wrote {html_path}")
    if a.pdf:
        pdf_path = out / "marstoy_lego_catalog.pdf"
        write_pdf(html_path, pdf_path)
        print(f"Wrote {pdf_path}")
    flagged = [i for i in items if i.get("flag")]
    if flagged:
        print(f"\n{len(flagged)} flagged:")
        for i in flagged:
            print(f"  {i['code'] or '?'}  {i['flag']}  {i['url']}")


if __name__ == "__main__":
    sys.exit(main())
