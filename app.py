"""
What's in this video? — paste a YouTube link, get the movies / TV series / anime /
games AND the software tools, apps, and products the creator showed but never named.

No more "comment and I'll DM you the link." Runs locally. YouTube only.

The clever bit: we don't build any intelligence. Gemini already "knows" these things.
We just hand it the link, ask, verify, and show the answer.
"""
import os
import re
import html
import json
from urllib.parse import quote_plus

# Load GEMINI_API_KEY from the .env that sits next to this file (works no matter
# which folder you launch from). Safe to skip if python-dotenv isn't installed.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

from google import genai
from google.genai import types
import gradio as gr

# flash = good accuracy. For lowest cost (less accurate) use "gemini-2.5-flash-lite".
MODEL = "gemini-2.5-flash"

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

CATEGORIES = ["Any", "Movie", "TV series", "Anime", "Video game",
              "Software / app / tool", "Car / vehicle", "Product", "Song", "Place / location"]

CAT_EMOJI = {"movie": "🎬", "series": "📺", "anime": "🌸", "game": "🎮", "tool": "🛠️",
             "app": "📱", "website": "🌐", "product": "📦", "song": "🎵",
             "place": "📍", "other": "✨", "unknown": "❓"}

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
            video_metadata=types.VideoMetadata(fps=2),       # 2 fps = catches more detail
        ),
        types.Part(text=prompt),
    ])
    resp = get_client().models.generate_content(
        model=MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            thinking_config=types.ThinkingConfig(thinking_budget=0),  # off = cheaper + no truncation
            max_output_tokens=4096,
        ),
    )
    return parse_json(resp.text).get("items", [])


def _search_urls(name: str):
    q = quote_plus(name)
    return (f"https://www.google.com/search?q={q}",
            f"https://www.youtube.com/results?search_query={q}")


def conf_class(c: float) -> str:
    return "high" if c >= 0.75 else ("mid" if c >= 0.45 else "low")


def to_html(items: list) -> str:
    """Render the results as styled cards (clean, scannable, professional)."""
    if not items:
        return ("<div class='empty'>Nothing identified confidently. Try a longer or clearer "
                "clip, pick a category, or add a focus hint.</div>")

    items = sorted(items, key=lambda x: x.get("confidence", 0) or 0, reverse=True)
    cards = []
    for it in items:
        raw = str(it.get("name") or "Unknown")
        name = html.escape(raw)
        cat = html.escape(str(it.get("category", "?")))
        conf = it.get("confidence", 0) or 0
        cc = conf_class(conf)
        emoji = CAT_EMOJI.get(cat.lower(), "✨")
        ts = html.escape(str(it.get("timestamp") or ""))
        chip = f"<span class='chip'>⏱ {ts}</span>" if ts else ""
        desc = html.escape(str(it.get("what_it_is") or ""))
        why = html.escape(str(it.get("evidence") or ""))

        links = ""
        if raw and raw.lower() != "unknown" and cat.lower() != "unknown":
            g, y = _search_urls(raw)
            links = ("<div class='links'>"
                     f"<a class='btn' href='{g}' target='_blank' rel='noopener'>Google ↗</a>"
                     f"<a class='btn' href='{y}' target='_blank' rel='noopener'>YouTube ↗</a></div>")

        alts = it.get("alternatives") or []
        alt_html = ""
        if alts:
            chips = " ".join(f"<span class='alt'>{html.escape(str(a))}</span>" for a in alts[:3])
            alt_html = f"<div class='alts'>Could also be: {chips}</div>"

        cards.append(
            "<div class='card-item'>"
            f"<div class='row1'><span class='emoji'>{emoji}</span>"
            f"<span class='iname'>{name}</span>"
            f"<span class='badge'>{cat}</span>"
            f"<span class='conf {cc}'>{conf:.0%}</span>{chip}</div>"
            + (f"<div class='desc'>{desc}</div>" if desc else "")
            + alt_html + links
            + (f"<div class='why'>Why: {why}</div>" if why else "")
            + "</div>"
        )

    note = ("<div class='note'><span class='dot high'></span>sure"
            "<span class='dot mid'></span>maybe"
            "<span class='dot low'></span>guess &mdash; low confidence can be wrong, so verify.</div>")
    plural = "s" if len(items) != 1 else ""
    head = f"<div class='rhead'>Found {len(items)} thing{plural}</div>"
    return f"<div class='results'>{head}{''.join(cards)}{note}</div>"


def run(url: str, category: str = "Any", focus: str = "") -> str:
    url = (url or "").strip()
    if not url:
        return "<div class='empty'>Paste a YouTube link first.</div>"
    if "youtu" not in url:
        return ("<div class='empty'>This build is <b>YouTube-only</b> for now. "
                "Paste a youtube.com or youtu.be link.</div>")
    try:
        return to_html(identify(url, category, focus))
    except Exception as e:
        return f"<div class='empty err'>⚠️ Failed: {html.escape(str(e))}</div>"


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
  <h1><span class="logo">▶</span> What's in this video?</h1>
  <p>Paste a YouTube link and find the movie, series, anime, game, or tool the creator never named.</p>
</div>
"""

FOOT = ("<div id='foot'>Runs locally · powered by Gemini · "
        "low-confidence guesses can be wrong, so verify.</div>")

EMPTY_RESULT = ("<div class='empty'>Your results will appear here.<br>"
                "<span class='empty-sub'>Paste a link, pick what you're after, then hit Identify.</span></div>")

CSS = """
.gradio-container {max-width: 1100px !important; margin: 0 auto !important;}
#hero {text-align:center; padding: 18px 0 2px;}
#hero h1 {font-size: 2.1rem; font-weight: 800; margin:0; letter-spacing:-0.02em;}
#hero h1 .logo {color:#e5322d;}
#hero p {color: var(--body-text-color-subdued); margin:8px 0 0; font-size:1.02rem;}
#go {font-weight:700; border-radius:12px; min-height:46px;}
/* video */
.video-wrap {position:relative; width:100%; aspect-ratio:16/9; border-radius:16px;
  overflow:hidden; background:#000; border:1px solid var(--border-color-primary);}
.video-wrap iframe {position:absolute; inset:0; width:100%; height:100%; border:0;}
.placeholder {display:flex; align-items:center; justify-content:center; height:100%;
  color:#9aa0aa; font-size:.95rem; text-align:center; padding:0 16px;}
/* results */
.results {display:flex; flex-direction:column; gap:12px;}
.rhead {font-weight:700; font-size:1.05rem; color:var(--body-text-color);}
.card-item {border:1px solid var(--border-color-primary); border-radius:14px; padding:14px 16px;
  background:var(--background-fill-primary); box-shadow:0 1px 3px rgba(0,0,0,.05);}
.row1 {display:flex; align-items:center; gap:8px; flex-wrap:wrap;}
.emoji {font-size:1.15rem;}
.iname {font-weight:700; font-size:1.08rem; color:var(--body-text-color);}
.badge {font-size:.7rem; text-transform:uppercase; letter-spacing:.05em; padding:2px 9px;
  border-radius:999px; background:var(--background-fill-secondary);
  border:1px solid var(--border-color-primary); color:var(--body-text-color-subdued);}
.conf {font-weight:700; font-size:.82rem; padding:2px 9px; border-radius:999px;}
.conf.high {color:#0f7b3e; background:#e6f6ec;}
.conf.mid {color:#9a6700; background:#fdf3d6;}
.conf.low {color:#b42318; background:#fde7e4;}
.chip {font-size:.78rem; color:var(--body-text-color-subdued); background:var(--background-fill-secondary);
  padding:2px 9px; border-radius:999px;}
.desc {margin-top:9px; color:var(--body-text-color); line-height:1.5;}
.alts {margin-top:9px; font-size:.85rem; color:var(--body-text-color-subdued);}
.alt {display:inline-block; padding:1px 9px; border:1px dashed var(--border-color-primary);
  border-radius:999px; margin:0 4px 4px 0;}
.links {margin-top:11px; display:flex; gap:8px; flex-wrap:wrap;}
.links .btn {text-decoration:none; font-size:.82rem; font-weight:600; padding:7px 13px;
  border-radius:10px; border:1px solid var(--border-color-primary);
  color:var(--body-text-color); background:var(--background-fill-secondary); transition:.15s;}
.links .btn:hover {border-color:#e5322d; color:#e5322d;}
.why {margin-top:11px; font-size:.82rem; color:var(--body-text-color-subdued);
  border-top:1px dashed var(--border-color-primary); padding-top:9px;}
.note {margin-top:2px; font-size:.8rem; color:var(--body-text-color-subdued);
  display:flex; align-items:center; gap:4px; flex-wrap:wrap;}
.dot {display:inline-block; width:9px; height:9px; border-radius:50%; margin:0 4px 0 8px;}
.dot.high{background:#16a34a;} .dot.mid{background:#d99e00;} .dot.low{background:#e5484d;}
.empty {padding:40px 18px; text-align:center; color:var(--body-text-color-subdued);
  border:1px dashed var(--border-color-primary); border-radius:14px; line-height:1.7;}
.empty.err {border-style:solid; border-color:#f0b4ad; color:#b42318;}
.empty-sub {font-size:.85rem; opacity:.8;}
#foot {text-align:center; color:var(--body-text-color-subdued); font-size:.85rem; margin-top:16px;}
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
            go = gr.Button("🔍  Identify", variant="primary", elem_id="go")
        with gr.Row(equal_height=True):
            preview = gr.HTML(embed_html(""))
            out = gr.HTML(EMPTY_RESULT)
        gr.Examples(examples=[["https://www.youtube.com/watch?v=dQw4w9WgXcQ", "Song", ""]],
                    inputs=[url, category, focus], label="Try an example")
        gr.HTML(FOOT)

        url.change(embed_html, url, preview)            # live preview as you paste
        go.click(embed_html, url, preview)              # ensure preview on click too
        go.click(run, [url, category, focus], out)      # identify
        url.submit(run, [url, category, focus], out)    # Enter = identify
    return demo


def main():
    # Add share=True for a temporary public link you can send to a friend.
    build().launch(theme=THEME, css=CSS)


if __name__ == "__main__":
    main()
