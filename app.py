"""
What's in this video? — paste a YouTube link, get the movies / TV series / anime /
games AND the software tools, apps, and products the creator showed but never named.

No more "comment and I'll DM you the link." Runs locally. YouTube only.

The clever bit: we don't build any intelligence. Gemini already "knows" these things.
We just hand it the link, ask, and show the answer.
"""
import os
import re
import json
from urllib.parse import quote_plus

# Optional: load GEMINI_API_KEY from a local .env file so you don't have to set it
# in your shell every time. Safe to skip if python-dotenv isn't installed.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from google import genai
from google.genai import types
import gradio as gr

# Cheapest setup by default. flash-lite = cheapest video-capable model.
# If it starts missing things, switch to "gemini-2.5-flash".
MODEL = "gemini-2.5-flash-lite"

PROMPT = """You are watching a YouTube video. Creators often SHOW or TALK ABOUT things
without clearly naming them, and the viewer wants the name.

Find every notable thing in the video a viewer might want to identify, such as:
- a movie, TV series, anime, or documentary
- a video game
- a software tool, app, website, browser extension, or AI product
- a gadget / physical product, or a song that is playing

For EACH thing, return:
- "name": your best identification (the real title / product name)
- "category": one of movie | series | anime | game | tool | app | website | product | song | other | unknown
- "what_it_is": one short line on what it is or what it does
- "confidence": a number 0.0 to 1.0 for how sure you are
- "timestamp": rough time it appears like "1:23", or "" if unclear
- "evidence": the on-screen text, logo, UI, or spoken words you used to decide

Rules:
- Only include things actually worth identifying. Skip irrelevant background.
- If you are NOT reasonably sure, set "category":"unknown" and a low confidence.
  Never invent a name to look confident.
- If EXTRA CONTEXT (video description / top comments) is given below, treat it as a strong
  hint — viewers often name the thing in comments — but verify it against what you see and
  hear. If a comment named it, say so in "evidence".
- Return STRICT JSON only: {"items": [ ... ]}. No text outside the JSON.
"""

# Built lazily so importing this file (e.g. for tests) never needs a key.
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


CATEGORIES = ["Any", "Movie", "TV series", "Anime", "Video game",
              "Software / app / tool", "Car / vehicle", "Product", "Song", "Place / location"]


def video_id(url: str):
    """Pull the 11-char YouTube video id out of any common YouTube URL shape."""
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None


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
    try:  # title + description
        items = _get("videos", {"part": "snippet", "id": vid}).get("items") or [{}]
        snip = items[0].get("snippet", {})
        if snip.get("title"):
            parts.append("TITLE: " + snip["title"])
        if snip.get("description"):
            parts.append("DESCRIPTION:\n" + snip["description"][:1500])
    except Exception:
        pass
    try:  # top comments, ranked by relevance
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
    """Hand the YouTube link to Gemini and get back a list of identified items."""
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
            file_data=types.FileData(file_uri=url),          # Gemini watches the URL
            video_metadata=types.VideoMetadata(fps=1),       # 1 frame/sec = fewer tokens
        ),
        types.Part(text=prompt),
    ])
    resp = get_client().models.generate_content(
        model=MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW,  # ~4x fewer video tokens
            thinking_config=types.ThinkingConfig(thinking_budget=0),      # skip thinking = cheaper
            max_output_tokens=1024,
        ),
    )
    return parse_json(resp.text).get("items", [])


def search_links(name: str) -> str:
    """We build search links ourselves (never trust an AI-invented URL)."""
    q = quote_plus(name)
    google = f"https://www.google.com/search?q={q}"
    youtube = f"https://www.youtube.com/results?search_query={q}"
    return f"[Google search]({google}) · [YouTube search]({youtube})"


def to_markdown(items: list) -> str:
    if not items:
        return ("**Nothing identified confidently.** Try a clearer or longer video, "
                "or pick a category / add a focus hint to narrow it down.")

    items = sorted(items, key=lambda x: x.get("confidence", 0) or 0, reverse=True)
    out = [f"### Found {len(items)} thing(s) in this video\n"]
    for it in items:
        name = it.get("name") or "Unknown"
        cat = it.get("category", "?")
        conf = it.get("confidence", 0) or 0
        dot = "🟢" if conf >= 0.75 else ("🟡" if conf >= 0.45 else "🔴")
        head = f"**{name}** · _{cat}_ · {dot} {conf:.0%}"
        if it.get("timestamp"):
            head += f" · ⏱ {it['timestamp']}"
        out.append(head)
        if it.get("what_it_is"):
            out.append(f"> {it['what_it_is']}")
        if name.lower() != "unknown" and cat != "unknown":
            out.append(f"🔎 {search_links(name)}")
        if it.get("evidence"):
            out.append(f"_why: {it['evidence']}_")
        out.append("")  # spacer line
    out.append("_🟢 sure · 🟡 maybe · 🔴 guess. Low confidence can be wrong — treat it as a hint, not truth._")
    return "\n".join(out)


def run(url: str, category: str = "Any", focus: str = "") -> str:
    url = (url or "").strip()
    if not url:
        return "Paste a YouTube link first."
    if "youtu" not in url:
        return "This build is **YouTube-only** for now. Paste a youtube.com or youtu.be link."
    try:
        return to_markdown(identify(url, category, focus))
    except Exception as e:
        return f"⚠️ Failed: {e}"


def embed_html(url: str) -> str:
    """Build the responsive YouTube embed for the preview panel (or a placeholder)."""
    vid = video_id(url or "")
    if not vid:
        return ("<div class='video-wrap'><div class='placeholder'>"
                "▶ Paste a YouTube link to preview it here</div></div>")
    src = f"https://www.youtube.com/embed/{vid}"
    return ("<div class='video-wrap'><iframe src='" + src + "' "
            "allow='accelerometer; clipboard-write; encrypted-media; picture-in-picture' "
            "allowfullscreen></iframe></div>")


HERO = """
<div id="hero">
  <h1>🎬 What's in this video?</h1>
  <p>Paste a YouTube link and find the movie, series, anime, game, or tool the creator never named.</p>
</div>
"""

FOOT = ("<div id='foot'>Runs locally · powered by Gemini · "
        "low-confidence guesses can be wrong, so verify.</div>")

CSS = """
.gradio-container {max-width: 1080px !important; margin: 0 auto !important;}
#hero {text-align:center; padding: 14px 0 4px;}
#hero h1 {font-size: 2rem; font-weight: 800; margin:0; letter-spacing:-0.02em;}
#hero p {color: var(--body-text-color-subdued); margin:6px 0 0;}
#go {font-weight:600; border-radius:12px;}
.video-wrap {position:relative; width:100%; aspect-ratio:16/9; border-radius:14px;
  overflow:hidden; background:#000; border:1px solid var(--border-color-primary);}
.video-wrap iframe {position:absolute; inset:0; width:100%; height:100%; border:0;}
.placeholder {display:flex; align-items:center; justify-content:center; height:100%;
  color:#9ca3af; font-size:.95rem; text-align:center; padding:0 16px;}
.card {border:1px solid var(--border-color-primary); border-radius:14px;
  padding:4px 18px; background:var(--background-fill-secondary); min-height:200px;}
#foot {text-align:center; color:var(--body-text-color-subdued); font-size:.85rem; margin-top:14px;}
footer {display:none !important;}
"""


THEME = gr.themes.Soft(
    primary_hue="red", neutral_hue="slate",
    font=[gr.themes.GoogleFont("Inter"), "system-ui", "sans-serif"],
)


def build():
    """Construct the Gradio UI (kept separate from launch so it can be import-tested)."""
    with gr.Blocks(title="What's in this video?") as demo:
        gr.HTML(HERO)
        with gr.Group():
            url = gr.Textbox(label="YouTube link", lines=1,
                             placeholder="https://www.youtube.com/watch?v=…")
            with gr.Row():
                category = gr.Dropdown(choices=CATEGORIES, value="Any",
                                       label="What are you looking for?", scale=2)
                focus = gr.Textbox(label="Anything specific? (optional)", scale=3,
                                   placeholder="e.g. the editing tool · the game")
            go = gr.Button("🔍 Identify", variant="primary", elem_id="go")
        with gr.Row(equal_height=True):
            preview = gr.HTML(embed_html(""))
            out = gr.Markdown("*Results will appear here after you hit Identify.*",
                              elem_classes=["card"])
        gr.Examples(examples=[["https://www.youtube.com/watch?v=dQw4w9WgXcQ", "Song", ""]],
                    inputs=[url, category, focus], label="Try an example")
        gr.HTML(FOOT)

        url.change(embed_html, url, preview)          # live preview as you paste
        go.click(embed_html, url, preview)            # ensure preview on click too
        go.click(run, [url, category, focus], out)    # identify
        url.submit(run, [url, category, focus], out)  # Enter = identify
    return demo


def main():
    # Add share=True for a temporary public link you can send to a friend.
    build().launch(theme=THEME, css=CSS)


if __name__ == "__main__":
    main()
