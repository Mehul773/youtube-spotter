"""
Core logic for "Spotter" — identify the movie / series / anime / game / tool shown in a
YouTube video or Short. No UI here; app.py (Flask) imports this.

We don't build any intelligence — Gemini already knows these things. We hand it the link,
ask with a careful prompt, and return structured results.
"""
import os
import re
import json
from urllib.parse import quote_plus

# Load GEMINI_API_KEY from the .env next to this file (works from any cwd).
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

from google import genai
from google.genai import types

# flash-lite = cheapest. Accuracy is held up by the description + top-comments context
# (which usually names the thing). Bump to "gemini-2.5-flash" only if you need more visual power.
MODEL = "gemini-2.5-flash-lite"

# "Any" first (default), then the specific filters the UI offers.
CATEGORIES = ["Any", "Movie", "TV series", "Anime", "Video game",
              "Software / app / tool", "Car / vehicle", "Product", "Song", "Place / location"]

PROMPT = """You are watching a YouTube video. Creators often show or talk about things
without naming them; the viewer wants the name.

Identify the notable things a viewer would want named — a movie, TV series, anime, video
game, software tool / app / website, a product, or a song playing. Return AT MOST 5, most
notable first.

Accuracy rules:
- Don't identify from an actor's face alone — actors appear in many films, and many films
  share similar scenes (chases, stunts, fights).
- Anchor on DISTINCTIVE evidence: a visible title / logo, on-screen text or caption, a
  unique location, a specific plot beat, or the video's own title.
- If EXTRA CONTEXT (description / top comments) is given below, use it as a strong hint —
  viewers often name the thing there — but verify against what you see and hear; if a comment
  named it, say so in "evidence".
- If you can't confirm a definitive title, set confidence <= 0.6 and add up to 2
  "alternatives". Never invent a name.

For each item, JSON keys: "name", "category"
(movie|series|anime|game|tool|app|website|product|song|other|unknown), "what_it_is"
(<=12 words), "confidence" (0-1), "timestamp" ("m:ss" or ""), "evidence" (<=20 words),
"alternatives" (<=2 names, or []).

Return STRICT JSON only: {"items": [ ... ]}. No text outside the JSON.
"""

_client = None


def get_client():
    global _client
    if _client is None:
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Get a free key at "
                "https://aistudio.google.com/apikey and put it in a .env file."
            )
        _client = genai.Client(api_key=key)
    return _client


def parse_json(text: str) -> dict:
    """Tolerant JSON parse — handles the rare case the model wraps it in ``` fences."""
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            return json.loads(match.group(0))
        raise


def video_id(url: str):
    """Pull the 11-char YouTube video id out of any common YouTube URL shape."""
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})", url or "")
    return m.group(1) if m else None


def is_short(url: str) -> bool:
    return "/shorts/" in (url or "")


def search_urls(name: str):
    q = quote_plus(name)
    return {"google": f"https://www.google.com/search?q={q}",
            "youtube": f"https://www.youtube.com/results?search_query={q}"}


def youtube_context(url: str, max_comments: int = 10) -> str:
    """Optional booster: if YOUTUBE_API_KEY is set, fetch the description + top comments.
    Viewers usually name the movie/show/game/tool in the comments. Returns "" if there is
    no key, no video id, or comments are disabled — the app still works on video alone."""
    key = os.environ.get("YOUTUBE_API_KEY")
    vid = video_id(url)
    if not key or not vid:
        return ""

    import urllib.request
    import urllib.parse

    def _get(endpoint, params):
        params["key"] = key
        u = f"https://www.googleapis.com/youtube/v3/{endpoint}?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(u, timeout=15) as r:
            return json.load(r)

    parts = []
    try:
        items = _get("videos", {"part": "snippet", "id": vid}).get("items") or [{}]
        snip = items[0].get("snippet", {})
        if snip.get("title"):
            parts.append("TITLE: " + snip["title"])
        if snip.get("description"):
            parts.append("DESCRIPTION:\n" + snip["description"][:800])
    except Exception:
        pass
    try:
        data = _get("commentThreads", {"part": "snippet", "videoId": vid, "order": "relevance",
                                       "maxResults": max_comments, "textFormat": "plainText"})
        comments = [t["snippet"]["topLevelComment"]["snippet"]["textDisplay"][:200]
                    for t in data.get("items", [])]
        if comments:
            parts.append("TOP COMMENTS:\n- " + "\n- ".join(comments))
    except Exception:
        pass
    return "\n\n".join(parts)


def identify(url: str, category: str = "Any", focus: str = "") -> list:
    """Hand the YouTube link to Gemini and return a list of identified items (enriched
    with ready-made search links, built locally — never trust an AI-invented URL)."""
    prompt = PROMPT
    if category and category != "Any":
        prompt += (f'\n\nThe viewer is looking ONLY for a {category}. Prioritize the '
                   f'{category}; you may ignore unrelated categories.')
    if focus.strip():
        prompt += f'\nThe viewer specifically wants: "{focus.strip()}". Prioritize that.'

    context = youtube_context(url)
    if context:
        prompt += ("\n\nEXTRA CONTEXT (video description and top comments — viewers often "
                   "name the thing here):\n" + context)

    contents = types.Content(parts=[
        types.Part(
            file_data=types.FileData(file_uri=url),
            video_metadata=types.VideoMetadata(fps=1),          # 1 fps = half the video tokens
        ),
        types.Part(text=prompt),
    ])
    resp = get_client().models.generate_content(
        model=MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW,  # ~4x fewer video tokens
            thinking_config=types.ThinkingConfig(thinking_budget=0),      # no thinking = cheaper
            max_output_tokens=2048,
        ),
    )
    items = parse_json(resp.text).get("items", [])

    for it in items:
        name = str(it.get("name") or "").strip()
        cat = str(it.get("category") or "").lower()
        it["links"] = search_urls(name) if (name and name.lower() != "unknown" and cat != "unknown") else None
    items.sort(key=lambda x: x.get("confidence", 0) or 0, reverse=True)
    return items
