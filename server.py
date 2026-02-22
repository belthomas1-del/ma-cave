from flask import Flask, request, jsonify
from flask_cors import CORS
import requests, time, hashlib

app = Flask(__name__)
CORS(app)

CACHE = {}
CACHE_TTL = 3600

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.vivino.com/search/wines?q=",
    "Origin": "https://www.vivino.com",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
})

def init_session():
    try:
        session.get("https://www.vivino.com", timeout=10)
    except:
        pass

init_session()

def cache_key(q):
    return hashlib.md5(q.lower().strip().encode()).hexdigest()

def parse_matches(matches):
    results = []
    for m in matches[:12]:
        v = m.get("vintage") or {}
        w = v.get("wine") or {}
        s = v.get("statistics") or {}
        img = (v.get("image") or {}).get("location", "")
        if img and not img.startswith("http"):
            img = "https:" + img
        rg = w.get("region") or {}
        pd = m.get("price") or {}
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
            "price": f"{pd['amount']:.0f}\u20ac" if pd.get("amount") else None,
            "image": img or None,
            "grape": ", ".join(g.get("name","") for g in w.get("grapes",[]) if g.get("name")) or None,
            "description": w.get("description"),
            "seo_name": w.get("seo_name", ""),
        })
    return results

@app.route("/health")
def health():
    return jsonify({"status": "ok", "cache_size": len(CACHE), "version": "2.0"})

@app.route("/debug")
def debug():
    q = request.args.get("q", "margaux")
    url = "https://www.vivino.com/api/explore/explore"
    params = {"q": q, "country_code": "FR", "currency_code": "EUR", "language": "fr", "page": 1}
    info = {"steps": []}
    try:
        info["steps"].append("Session request...")
        r = session.get(url, params=params, timeout=12)
        info["status"] = r.status_code
        info["body_preview"] = r.text[:500]
        if r.status_code == 200:
            data = r.json()
            matches = (data.get("explore_vintage") or {}).get("matches", [])
            info["match_count"] = len(matches)
            if matches:
                info["first_wine"] = matches[0].get("vintage",{}).get("wine",{}).get("name","")
        elif r.status_code in (403, 429, 503):
            info["steps"].append("Blocked, refreshing session...")
            session.cookies.clear()
            session.get("https://www.vivino.com", timeout=10)
            r2 = session.get(url, params=params, timeout=12)
            info["retry_status"] = r2.status_code
            info["retry_preview"] = r2.text[:300]
    except Exception as e:
        info["error"] = str(e)
    return jsonify(info)

@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "Missing q"}), 400
    ck = cache_key(q)
    if ck in CACHE and time.time() - CACHE[ck]["ts"] < CACHE_TTL:
        return jsonify(CACHE[ck]["data"])

    url = "https://www.vivino.com/api/explore/explore"
    params = {"q": q, "country_code": request.args.get("country", "FR"),
              "currency_code": "EUR", "language": "fr", "page": 1,
              "price_range_min": 0, "price_range_max": 500}

    # Strategy 1: session
    try:
        r = session.get(url, params=params, timeout=12)
        if r.status_code == 200:
            matches = (r.json().get("explore_vintage") or {}).get("matches", [])
            if matches:
                res = parse_matches(matches)
                resp = {"results": res, "count": len(res), "source": "session"}
                CACHE[ck] = {"data": resp, "ts": time.time()}
                return jsonify(resp)
        if r.status_code in (403, 429, 503):
            session.cookies.clear()
            session.get("https://www.vivino.com", timeout=10)
            r2 = session.get(url, params=params, timeout=12)
            if r2.status_code == 200:
                matches = (r2.json().get("explore_vintage") or {}).get("matches", [])
                if matches:
                    res = parse_matches(matches)
                    resp = {"results": res, "count": len(res), "source": "refresh"}
                    CACHE[ck] = {"data": resp, "ts": time.time()}
                    return jsonify(resp)
    except requests.Timeout:
        return jsonify({"error": "Vivino timeout"}), 504
    except:
        pass

    # Strategy 2: fresh request
    try:
        h = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)", "Accept": "application/json"}
        r = requests.get(url, params=params, headers=h, timeout=12)
        if r.status_code == 200:
            matches = (r.json().get("explore_vintage") or {}).get("matches", [])
            if matches:
                res = parse_matches(matches)
                resp = {"results": res, "count": len(res), "source": "fresh"}
                CACHE[ck] = {"data": resp, "ts": time.time()}
                return jsonify(resp)
    except:
        pass

    return jsonify({"error": "Vivino inaccessible", "hint": "Reessayez dans quelques minutes"}), 502

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=True)
