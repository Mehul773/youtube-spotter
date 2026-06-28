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

# flash = good accuracy. For lowest cost (less accurate) use "gemini-2.5-flash-lite".
MODEL = "gemini-2.5-flash"

# "Any" first (default), then the specific filters the UI offers.
CATEGORIES = ["Any", "Movie", "TV series", "Anime", "Video game",
              "Software / app / tool", "Car / vehicle", "Product", "Song", "Place / location"]

PROMPT = """You are watching a YouTube video. Creators often SHOW or TALK ABOUT things
without naming them, and the viewer wants the name.

Identify every notable thing a viewer might want to name: a movie, TV series, anime,
documentary, video game, software tool / app / website / AI product, a gadget or
product, or a song that is playing.

CRITICAL accuracy rules — read carefully:
- Do NOT identify from an actor's face alone. The same actor appears in many films, and
  many movies share similar scenes (car chases, stunts, fights, running scenes).
- Anchor your answer on DISTINCTIVE, verifiable evidence: a visible title card or logo,
  on-screen text or captions, a unique location, a specific plot beat, or the video's own
  title / caption.
- If no definitive title or credit is visible and you are inferring, set "confidence" to
  at most 0.6 and put up to 2 other plausible titles in "alternatives".
- Prefer admitting uncertainty over a confident wrong guess.

For EACH thing return:
- "name": your best identification (the real title / product name)
- "category": one of movie | series | anime | game | tool | app | website | product | song | other | unknown
- "what_it_is": one short line on what it is or does
- "confidence": a number 0.0 to 1.0
- "timestamp": rough time it appears like "1:23", or "" if unclear
- "evidence": the exact on-screen text, logo, caption, or spoken words you used
- "alternatives": array of other possible names, or []

Rules:
- Only include things actually worth identifying. Skip irrelevant background.
- If you are NOT reasonably sure, use "category":"unknown" and a low confidence. Never invent.
- Keep every field concise; keep "evidence" under 25 words.
- If EXTRA CONTEXT (video description / top comments) is given below, treat it as a strong
  hint — viewers often name the thing in comments — but verify it against what you see and
  hear. If a comment named it, say so in "evidence".
- Return STRICT JSON only: {"items": [ ... ]}. No text outside the JSON.
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


def youtube_context(url: str, max_comments: int = 15) -> str:
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
            parts.append("DESCRIPTION:\n" + snip["description"][:1500])
    except Exception:
        pass
    try:
        data = _get("commentThreads", {"part": "snippet", "videoId": vid, "order": "relevance",
                                       "maxResults": max_comments, "textFormat": "plainText"})
        comments = [t["snippet"]["topLevelComment"]["snippet"]["textDisplay"][:300]
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
            video_metadata=types.VideoMetadata(fps=2),
        ),
        types.Part(text=prompt),
    ])
    resp = get_client().models.generate_content(
        model=MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            thinking_config=types.ThinkingConfig(thinking_budget=0),
            max_output_tokens=4096,
        ),
    )
    items = parse_json(resp.text).get("items", [])

    for it in items:
        name = str(it.get("name") or "").strip()
        cat = str(it.get("category") or "").lower()
        it["links"] = search_urls(name) if (name and name.lower() != "unknown" and cat != "unknown") else None
    items.sort(key=lambda x: x.get("confidence", 0) or 0, reverse=True)
    return items
