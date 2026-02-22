from flask import Flask, request, jsonify
from flask_cors import CORS
import requests, time, hashlib

app = Flask(__name__)
CORS(app)

CACHE = {}
CACHE_TTL = 3600
MAX_CACHE = 300
VIVINO_URL = "https://www.vivino.com/api/explore/explore"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

def cache_key(q):
    return hashlib.md5(q.lower().strip().encode()).hexdigest()

def clean_cache():
    if len(CACHE) > MAX_CACHE:
        for k, _ in sorted(CACHE.items(), key=lambda x: x[1]["ts"])[:len(CACHE)//2]:
            del CACHE[k]

@app.route("/health")
def health():
    return jsonify({"status": "ok", "cache_size": len(CACHE)})

@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "Missing q"}), 400
    ck = cache_key(q)
    if ck in CACHE and time.time() - CACHE[ck]["ts"] < CACHE_TTL:
        return jsonify(CACHE[ck]["data"])
    try:
        r = requests.get(VIVINO_URL, params={"q": q, "country_code": request.args.get("country", "FR"),
            "currency_code": "EUR", "language": "fr", "page": 1, "price_range_min": 0, "price_range_max": 500},
            headers=HEADERS, timeout=12)
        r.raise_for_status()
        data = r.json()
        results = []
        for m in data.get("explore_vintage", {}).get("matches", [])[:10]:
            v, w, s = m.get("vintage", {}), m.get("vintage", {}).get("wine", {}), m.get("vintage", {}).get("statistics", {})
            img = v.get("image", {}).get("location", "")
            if img and not img.startswith("http"): img = "https:" + img
            rg = w.get("region", {})
            price_data = m.get("price", {})
            name = w.get("name", "")
            if not name: continue
            results.append({"name": name, "winery": w.get("winery", {}).get("name", ""),
                "vintage": v.get("year"), "type_id": w.get("type_id", 1),
                "region": rg.get("name", "") or rg.get("country", {}).get("name", ""),
                "rating": round(s["ratings_average"], 1) if s.get("ratings_average") else None,
                "price": f"{price_data['amount']:.0f}€" if price_data.get("amount") else None,
                "image": img or None,
                "grape": ", ".join(g.get("name", "") for g in w.get("grapes", []) if g.get("name")) or None,
                "description": w.get("description"),
                "seo_name": w.get("seo_name", "")})
        response = {"results": results, "count": len(results)}
        clean_cache()
        CACHE[ck] = {"data": response, "ts": time.time()}
        return jsonify(response)
    except requests.Timeout:
        return jsonify({"error": "Vivino timeout"}), 504
    except requests.RequestException as e:
        return jsonify({"error": str(e)}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=True)
