"""
Spotter — local web app. Paste a YouTube video or Shorts link, and it names the
movie / series / anime / game / tool shown but never named.

Custom UI (Flask + templates/index.html). Run: python app.py  ->  http://127.0.0.1:7860
"""
from flask import Flask, request, jsonify, render_template

import core

app = Flask(__name__)


@app.get("/")
def index():
    return render_template("index.html", categories=core.CATEGORIES)


@app.post("/api/identify")
def api_identify():
    data = request.get_json(force=True, silent=True) or {}
    url = (data.get("url") or "").strip()
    category = data.get("category") or "Any"
    focus = data.get("focus") or ""

    if not url:
        return jsonify(error="Paste a YouTube link first.")
    if "youtu" not in url:
        return jsonify(error="This build is YouTube-only for now. Paste a youtube.com or youtu.be link.")
    try:
        items = core.identify(url, category, focus)
        return jsonify(items=items, type=("reel" if core.is_short(url) else "video"))
    except Exception as e:
        return jsonify(error=str(e))


if __name__ == "__main__":
    # Local only. Change host to "0.0.0.0" if you want other devices on your network in.
    app.run(host="127.0.0.1", port=7860, debug=False)
