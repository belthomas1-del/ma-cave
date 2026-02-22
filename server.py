"""
MA CAVE — Backend Vivino (Render-ready)
Déployable en 1 clic sur Render.com (free tier)
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import json
import time
import hashlib
import os

app = Flask(__name__)
CORS(app)

VIVINO_API = "https://www.vivino.com/api/explore/explore"
USER_AGENT = "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36"
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9", "Accept": "application/json"}

# Cache en mémoire (suffisant pour Render free tier)
_cache = {}
CACHE_TTL = 3600 * 24


def cached(key):
    entry = _cache.get(key)
    if entry and time.time() - entry["ts"] < CACHE_TTL:
        return entry["data"]
    return None


def cache_set(key, data):
    _cache[key] = {"ts": time.time(), "data": data}
    # Limite taille cache (évite OOM sur free tier)
    if len(_cache) > 500:
        oldest = sorted(_cache, key=lambda k: _cache[k]["ts"])[:100]
        for k in oldest:
            del _cache[k]


def classify_type(tid):
    return {1: "rouge", 2: "blanc", 3: "petillant", 4: "rose", 7: "rose"}.get(tid, "rouge")


def map_region(name):
    if not name:
        return "Autre"
    r = name.lower()
    mapping = {
        "bordeaux": "Bordeaux", "bourgogne": "Bourgogne", "burgundy": "Bourgogne",
        "rhône": "Rhône", "rhone": "Rhône", "loire": "Loire", "alsace": "Alsace",
        "champagne": "Champagne", "languedoc": "Languedoc", "roussillon": "Languedoc",
        "provence": "Provence", "beaujolais": "Beaujolais", "sud-ouest": "Sud-Ouest",
        "south west": "Sud-Ouest", "jura": "Jura", "savoie": "Savoie",
        "corse": "Corse", "corsica": "Corse",
        "toscana": "Italie", "tuscany": "Italie", "piemonte": "Italie", "piedmont": "Italie",
        "veneto": "Italie", "sicilia": "Italie", "campania": "Italie",
        "rioja": "Espagne", "ribera": "Espagne", "priorat": "Espagne",
        "castilla": "Espagne", "navarra": "Espagne",
    }
    for kw, mapped in mapping.items():
        if kw in r:
            return mapped
    return "Autre"


@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Paramètre 'q' requis", "results": []}), 400

    c = cached(query)
    if c is not None:
        return jsonify({"query": query, "results": c, "count": len(c), "cached": True})

    try:
        url = f"{VIVINO_API}?q={requests.utils.quote(query)}&country_code=FR&currency_code=EUR&language=fr&page=1&price_range_min=0&price_range_max=500"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        results = []
        for m in (data.get("explore_vintage", {}).get("matches", []))[:10]:
            v = m.get("vintage", {})
            w = v.get("wine", {})
            s = v.get("statistics", {})
            img = v.get("image", {}).get("location")
            if img and not img.startswith("http"):
                img = "https:" + img
            region = w.get("region", {}).get("name", "") or w.get("region", {}).get("country", {}).get("name", "")
            grapes = [g["name"] for g in w.get("grapes", []) if g.get("name")]
            price = f"{m['price']['amount']:.0f} €" if m.get("price", {}).get("amount") else None

            results.append({
                "name": w.get("name", "Inconnu"),
                "winery": w.get("winery", {}).get("name", ""),
                "vintage": v.get("year"),
                "type": classify_type(w.get("type_id", 1)),
                "region": map_region(region),
                "region_raw": region,
                "rating": round(s.get("ratings_average", 0), 1) or None,
                "ratings_count": s.get("ratings_count", 0),
                "price": price,
                "image": img,
                "grape": ", ".join(grapes) if grapes else None,
                "description": w.get("description"),
                "vivino_url": f"https://www.vivino.com{w.get('seo_name', '')}" if w.get("seo_name") else None,
                "added": None, "notes": None,
            })

        cache_set(query, results)
        return jsonify({"query": query, "results": results, "count": len(results)})

    except Exception as e:
        return jsonify({"error": str(e), "results": []}), 502


@app.route("/health")
def health():
    return jsonify({"status": "ok", "cache_size": len(_cache)})


@app.route("/")
def index():
    return jsonify({
        "service": "Ma Cave — Vivino Proxy",
        "endpoints": {
            "GET /search?q=...": "Recherche de vins",
            "GET /health": "Status du serveur"
        }
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
