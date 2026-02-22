from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import time
import hashlib
import json
import re
import urllib.parse
from bs4 import BeautifulSoup

app = Flask(__name__)
CORS(app)

CACHE = {}
CACHE_TTL = 3600
MAX_CACHE = 300

def ck(q): return hashlib.md5(q.lower().strip().encode()).hexdigest()
def clean():
    if len(CACHE) > MAX_CACHE:
        for k in sorted(CACHE, key=lambda k: CACHE[k]["t"])[:len(CACHE)//2]:
            del CACHE[k]

def vurl(q, country="FR"):
    return "https://www.vivino.com/api/explore/explore?" + urllib.parse.urlencode({
        "q": q, "country_code": country, "currency_code": "EUR",
        "language": "fr", "page": 1, "price_range_min": 0, "price_range_max": 500
    })

def parse_api(data):
    """Parse Vivino API JSON response"""
    matches = (data.get("explore_vintage") or {}).get("matches", [])
    out = []
    for m in matches[:12]:
        v = m.get("vintage") or {}
        w = v.get("wine") or {}
        s = v.get("statistics") or {}
        img = (v.get("image") or {}).get("location", "")
        if img and not img.startswith("http"): img = "https:" + img
        rg = w.get("region") or {}
        pd = m.get("price") or {}
        name = w.get("name", "")
        if not name: continue
        out.append({
            "name": name,
            "winery": (w.get("winery") or {}).get("name", ""),
            "vintage": v.get("year"),
            "type_id": w.get("type_id", 1),
            "region": rg.get("name", "") or (rg.get("country") or {}).get("name", ""),
            "rating": round(s["ratings_average"], 1) if s.get("ratings_average") else None,
            "price": f"{pd['amount']:.0f}€" if pd.get("amount") else None,
            "image": img or None,
            "grape": ", ".join(g.get("name","") for g in w.get("grapes",[]) if g.get("name")) or None,
            "description": w.get("description"),
            "seo_name": w.get("seo_name", ""),
        })
    return out

def parse_html(html, query):
    """Parse Vivino search HTML page to extract wine data"""
    results = []
    try:
        # Strategy A: Look for embedded JSON (React hydration data)
        for pattern in [
            r'window\.__PRELOADED_STATE__\s*=\s*({.+?});?\s*</script>',
            r'window\.__NEXT_DATA__\s*=\s*({.+?});?\s*</script>',
            r'"explore_vintage"\s*:\s*(\{.+?\})\s*[,}]',
            r'data-json=["\']({.*?explore.*?})["\']',
        ]:
            m = re.search(pattern, html, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(1))
                    # Navigate to matches
                    if "explore_vintage" in str(data)[:1000]:
                        api_results = parse_api(data)
                        if api_results: return api_results
                except: pass

        # Strategy B: Parse wine cards from HTML
        soup = BeautifulSoup(html, 'lxml')
        
        # Try various CSS selectors that Vivino has used
        cards = (soup.select('.wine-card') or 
                 soup.select('.search-results-list .card') or
                 soup.select('[data-testid="wine-card"]') or
                 soup.select('.explorerCard') or
                 soup.select('.vintageCard'))
        
        for card in cards[:12]:
            name_el = (card.select_one('.wine-card__name') or 
                      card.select_one('.winery-name') or
                      card.select_one('[data-testid="wine-name"]') or
                      card.select_one('a[href*="/w/"]'))
            if not name_el: continue
            name = name_el.get_text(strip=True)
            if not name: continue
            
            # Try to find other data
            winery = ""
            w_el = card.select_one('.wine-card__winery') or card.select_one('.winery')
            if w_el: winery = w_el.get_text(strip=True)
            
            rating = None
            r_el = card.select_one('.average__number') or card.select_one('[data-testid="average-rating"]')
            if r_el:
                try: rating = round(float(r_el.get_text(strip=True).replace(',','.')), 1)
                except: pass
            
            price = None
            p_el = card.select_one('.wine-price-value') or card.select_one('.price')
            if p_el: price = p_el.get_text(strip=True)
            
            img = None
            img_el = card.select_one('img[src*="vivino"]') or card.select_one('img[src*="images"]')
            if img_el:
                img = img_el.get('src') or img_el.get('data-src')
                if img and not img.startswith("http"): img = "https:" + img
            
            link = ""
            a_el = card.select_one('a[href*="/w/"]') or card.select_one('a[href*="/wines/"]')
            if a_el: link = a_el.get('href', '')
            
            results.append({
                "name": name, "winery": winery, "vintage": None,
                "type_id": 1, "region": "", "rating": rating,
                "price": price, "image": img, "grape": None,
                "description": None, "seo_name": link,
            })
        
        # Strategy C: Find any wine names in the page via regex
        if not results:
            # Look for wine data in script tags
            for script in soup.select('script'):
                txt = script.string or ""
                if "wine" in txt.lower() and "name" in txt.lower():
                    # Try to find JSON objects with wine data
                    json_blocks = re.findall(r'\{[^{}]*"name"\s*:\s*"[^"]+[^{}]*"wine"[^{}]*\}', txt)
                    for block in json_blocks[:12]:
                        try:
                            obj = json.loads(block)
                            if obj.get("name"):
                                results.append({
                                    "name": obj["name"], "winery": obj.get("winery", {}).get("name", ""),
                                    "vintage": obj.get("year"), "type_id": obj.get("type_id", 1),
                                    "region": "", "rating": None, "price": None,
                                    "image": None, "grape": None, "description": None, "seo_name": "",
                                })
                        except: pass
    except Exception as e:
        pass
    return results

# ══════════════════════════════
# STRATEGIES
# ══════════════════════════════

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
})

# Warm up
try: SESSION.get("https://www.vivino.com", timeout=10)
except: pass

def strat_proxy_api(url, timeout=18):
    """Try proxied API call"""
    proxies = [
        ("allorigins", lambda u: f"https://api.allorigins.win/get?url={urllib.parse.quote(u, safe='')}&charset=UTF-8"),
        ("allorigins-raw", lambda u: f"https://api.allorigins.win/raw?url={urllib.parse.quote(u, safe='')}"),
        ("corsproxy", lambda u: f"https://corsproxy.io/?{urllib.parse.quote(u, safe='')}"),
        ("codetabs", lambda u: f"https://api.codetabs.com/v1/proxy?quest={urllib.parse.quote(u, safe='')}"),
        ("thingproxy", lambda u: f"https://thingproxy.freeboard.io/fetch/{u}"),
    ]
    for name, fn in proxies:
        try:
            purl = fn(url)
            r = requests.get(purl, timeout=timeout, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
            if r.status_code not in (200, 201): continue
            txt = r.text.strip()
            if not txt or len(txt) < 20: continue
            try:
                j = json.loads(txt)
                # allorigins wrapper
                if "contents" in j and isinstance(j["contents"], str):
                    j = json.loads(j["contents"])
                if "explore_vintage" in j:
                    results = parse_api(j)
                    if results: return results, name
            except: pass
        except: pass
    return None, "all proxies failed"

def strat_html_scrape(query, timeout=18):
    """Fetch Vivino search HTML page through proxy and parse it"""
    search_url = f"https://www.vivino.com/search/wines?q={urllib.parse.quote(query)}"
    proxies = [
        ("ao-html", lambda u: f"https://api.allorigins.win/raw?url={urllib.parse.quote(u, safe='')}"),
        ("ct-html", lambda u: f"https://api.codetabs.com/v1/proxy?quest={urllib.parse.quote(u, safe='')}"),
        ("tp-html", lambda u: f"https://thingproxy.freeboard.io/fetch/{u}"),
    ]
    for name, fn in proxies:
        try:
            purl = fn(search_url)
            r = requests.get(purl, timeout=timeout, headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            })
            if r.status_code != 200: continue
            html = r.text
            if len(html) < 500: continue
            if "vivino" not in html.lower(): continue
            results = parse_html(html, query)
            if results: return results, name
        except: pass
    return None, "html scrape failed"

def strat_direct_html(query, timeout=15):
    """Direct request to Vivino search page with session cookies"""
    try:
        search_url = f"https://www.vivino.com/search/wines?q={urllib.parse.quote(query)}"
        r = SESSION.get(search_url, timeout=timeout)
        if r.status_code == 200 and len(r.text) > 500:
            results = parse_html(r.text, query)
            if results: return results, "direct-html"
    except: pass
    return None, "direct html failed"

def strat_direct_api(query, timeout=15):
    """Direct API call with proper session"""
    try:
        url = vurl(query)
        SESSION.headers["Accept"] = "application/json"
        SESSION.headers["Referer"] = f"https://www.vivino.com/search/wines?q={urllib.parse.quote(query)}"
        SESSION.headers["X-Requested-With"] = "XMLHttpRequest"
        r = SESSION.get(url, timeout=timeout)
        SESSION.headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        if r.status_code == 200:
            data = r.json()
            results = parse_api(data)
            if results: return results, "direct-api"
    except: pass
    return None, "direct api failed"

ALL_STRATS = [
    ("proxy-api", lambda q: strat_proxy_api(vurl(q))),
    ("html-proxy", lambda q: strat_html_scrape(q)),
    ("direct-html", lambda q: strat_direct_html(q)),
    ("direct-api", lambda q: strat_direct_api(q)),
]

# ══════════════════════════════

@app.route("/health")
def health():
    return jsonify({"status": "ok", "v": "4.0", "cache": len(CACHE)})

@app.route("/debug")
def debug():
    q = request.args.get("q", "margaux")
    info = {"query": q, "strategies": []}
    for name, fn in ALL_STRATS:
        step = {"name": name}
        try:
            results, source = fn(q)
            if results:
                step["ok"] = True
                step["count"] = len(results)
                step["source"] = source
                step["first"] = results[0]["name"] if results else None
            else:
                step["ok"] = False
                step["reason"] = source
        except Exception as e:
            step["ok"] = False
            step["error"] = str(e)[:200]
        info["strategies"].append(step)
    return jsonify(info)

@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    if not q: return jsonify({"error": "Missing q"}), 400

    k = ck(q)
    if k in CACHE and time.time() - CACHE[k]["t"] < CACHE_TTL:
        d = CACHE[k]["d"]; d["cached"] = True; return jsonify(d)

    errors = []
    for name, fn in ALL_STRATS:
        try:
            results, source = fn(q)
            if results:
                resp = {"results": results, "count": len(results), "source": source, "cached": False}
                clean()
                CACHE[k] = {"d": resp, "t": time.time()}
                return jsonify(resp)
            errors.append(f"{name}: {source}")
        except Exception as e:
            errors.append(f"{name}: {str(e)[:100]}")

    return jsonify({"error": "Recherche impossible", "details": errors,
                     "hint": "Vivino est temporairement inaccessible. Utilisez la saisie manuelle."}), 502

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=True)
