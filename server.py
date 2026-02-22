from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import time
import hashlib
import json
import urllib.parse

app = Flask(__name__)
CORS(app)

CACHE = {}
CACHE_TTL = 3600
MAX_CACHE = 300

VIVINO_BASE = "https://www.vivino.com/api/explore/explore"

def cache_key(q):
    return hashlib.md5(q.lower().strip().encode()).hexdigest()

def clean_cache():
    if len(CACHE) > MAX_CACHE:
        for k in sorted(CACHE, key=lambda k: CACHE[k]["ts"])[:len(CACHE)//2]:
            del CACHE[k]

def vivino_url(q, country="FR"):
    params = urllib.parse.urlencode({
        "q": q, "country_code": country, "currency_code": "EUR",
        "language": "fr", "page": 1, "price_range_min": 0, "price_range_max": 500
    })
    return f"{VIVINO_BASE}?{params}"

def parse_matches(data):
    matches = (data.get("explore_vintage") or {}).get("matches", [])
    results = []
    for m in matches[:12]:
        v = m.get("vintage") or {}
        w = v.get("wine") or {}
        s = v.get("statistics") or {}
        img = (v.get("image") or {}).get("location", "")
        if img and not img.startswith("http"):
            img = "https:" + img
        rg = w.get("region") or {}
        price_data = m.get("price") or {}
        name = w.get("name", "")
        if not name:
            continue
        results.append({
            "name": name,
            "winery": (w.get("winery") or {}).get("name", ""),
            "vintage": v.get("year"),
            "type_id": w.get("type_id", 1),
            "region": rg.get("name", "") or (rg.get("country") or {}).get("name", ""),
            "rating": round(s["ratings_average"], 1) if s.get("ratings_average") else None,
            "price": f"{price_data['amount']:.0f}€" if price_data.get("amount") else None,
            "image": img or None,
            "grape": ", ".join(g.get("name", "") for g in w.get("grapes", []) if g.get("name")) or None,
            "description": w.get("description"),
            "seo_name": w.get("seo_name", ""),
        })
    return results

# ══════════════════════════════════════════
# PROXY STRATEGIES (server-side, no CORS)
# Vivino blocks cloud IPs, so we route
# through public proxy services
# ══════════════════════════════════════════

def try_allorigins_get(url, timeout=15):
    """allorigins /get returns JSON wrapper: {contents: '...', status: {}}"""
    proxy_url = f"https://api.allorigins.win/get?url={urllib.parse.quote(url, safe='')}"
    r = requests.get(proxy_url, timeout=timeout, headers={"Accept": "application/json"})
    if r.status_code != 200:
        return None, f"allorigins/get HTTP {r.status_code}"
    try:
        wrapper = r.json()
        contents = wrapper.get("contents", "")
        data = json.loads(contents)
        return data, None
    except Exception as e:
        return None, f"allorigins/get parse error: {str(e)[:100]}"

def try_allorigins_raw(url, timeout=15):
    """allorigins /raw returns the raw response"""
    proxy_url = f"https://api.allorigins.win/raw?url={urllib.parse.quote(url, safe='')}"
    r = requests.get(proxy_url, timeout=timeout, headers={"Accept": "application/json"})
    if r.status_code != 200:
        return None, f"allorigins/raw HTTP {r.status_code}"
    try:
        data = r.json()
        return data, None
    except:
        return None, f"allorigins/raw not JSON (got {r.text[:80]})"

def try_corsproxy(url, timeout=15):
    proxy_url = f"https://corsproxy.io/?{urllib.parse.quote(url, safe='')}"
    r = requests.get(proxy_url, timeout=timeout, headers={
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
    })
    if r.status_code != 200:
        return None, f"corsproxy HTTP {r.status_code}"
    try:
        data = r.json()
        return data, None
    except:
        return None, f"corsproxy not JSON"

def try_codetabs(url, timeout=15):
    proxy_url = f"https://api.codetabs.com/v1/proxy?quest={urllib.parse.quote(url, safe='')}"
    r = requests.get(proxy_url, timeout=timeout)
    if r.status_code != 200:
        return None, f"codetabs HTTP {r.status_code}"
    try:
        data = r.json()
        return data, None
    except:
        return None, f"codetabs not JSON"

def try_direct(url, timeout=15):
    """Direct request (unlikely to work from cloud but worth trying)"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "fr-FR,fr;q=0.9",
        "Referer": "https://www.vivino.com/search/wines",
        "Origin": "https://www.vivino.com",
    }
    r = requests.get(url, timeout=timeout, headers=headers)
    if r.status_code != 200:
        return None, f"direct HTTP {r.status_code}"
    try:
        data = r.json()
        return data, None
    except:
        return None, f"direct not JSON"

STRATEGIES = [
    ("allorigins/get", try_allorigins_get),
    ("allorigins/raw", try_allorigins_raw),
    ("corsproxy", try_corsproxy),
    ("codetabs", try_codetabs),
    ("direct", try_direct),
]

# ══════════════════════════════════════════

@app.route("/health")
def health():
    return jsonify({"status": "ok", "version": "3.0", "cache_size": len(CACHE)})

@app.route("/debug")
def debug():
    q = request.args.get("q", "margaux")
    url = vivino_url(q)
    info = {"vivino_url": url, "results": []}

    for name, fn in STRATEGIES:
        step = {"strategy": name}
        try:
            data, err = fn(url)
            if err:
                step["error"] = err
            elif data:
                matches = (data.get("explore_vintage") or {}).get("matches", [])
                step["success"] = True
                step["match_count"] = len(matches)
                if matches:
                    step["first_match"] = (matches[0].get("vintage") or {}).get("wine", {}).get("name", "?")
            else:
                step["error"] = "No data returned"
        except Exception as e:
            step["error"] = f"Exception: {str(e)[:150]}"
        info["results"].append(step)

    return jsonify(info)

@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "Missing q"}), 400

    ck = cache_key(q)
    if ck in CACHE and time.time() - CACHE[ck]["ts"] < CACHE_TTL:
        cached = CACHE[ck]["data"]
        cached["cached"] = True
        return jsonify(cached)

    url = vivino_url(q, request.args.get("country", "FR"))
    errors = []

    for name, fn in STRATEGIES:
        try:
            data, err = fn(url)
            if err:
                errors.append(f"{name}: {err}")
                continue
            if not data:
                errors.append(f"{name}: empty response")
                continue

            matches = (data.get("explore_vintage") or {}).get("matches", [])
            if not matches:
                errors.append(f"{name}: 0 matches")
                continue

            results = parse_matches(data)
            if results:
                response = {
                    "results": results,
                    "count": len(results),
                    "source": name,
                    "cached": False
                }
                clean_cache()
                CACHE[ck] = {"data": response, "ts": time.time()}
                return jsonify(response)
            else:
                errors.append(f"{name}: matches found but parsing failed")
        except Exception as e:
            errors.append(f"{name}: {str(e)[:100]}")

    return jsonify({
        "error": "All strategies failed",
        "details": errors,
        "hint": "Vivino may be temporarily unavailable"
    }), 502

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=True)
