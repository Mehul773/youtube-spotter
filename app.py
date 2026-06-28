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
        return ("<div class='empty'><span class='eglyph'>🔍</span>"
                "Nothing identified confidently.<br>"
                "<span class='empty-sub'>Try a longer or clearer clip, pick a category, "
                "or add a focus hint.</span></div>")

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
            alt_html = f"<div class='alts'><span class='eyebrow'>Also might be</span>{chips}</div>"

        cards.append(
            "<div class='card-item'>"
            f"<div class='row1'><span class='emoji'>{emoji}</span>"
            f"<span class='iname'>{name}</span></div>"
            f"<div class='meta'><span class='badge'>{cat}</span>"
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
        return "<div class='empty'><span class='eglyph'>🔗</span>Paste a YouTube link first.</div>"
    if "youtu" not in url:
        return ("<div class='empty'><span class='eglyph'>🔗</span>This build is "
                "<b>YouTube-only</b> for now.<br><span class='empty-sub'>"
                "Paste a youtube.com or youtu.be link.</span></div>")
    try:
        return to_html(identify(url, category, focus))
    except Exception as e:
        return (f"<div class='empty err'><span class='eglyph'>⚠️</span>Something went wrong.<br>"
                f"<span class='empty-sub'>{html.escape(str(e))}</span></div>")


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

EMPTY_RESULT = ("<div class='empty'><span class='eglyph'>🔎</span>Your results will appear here.<br>"
                "<span class='empty-sub'>Paste a link, pick what you're after, then hit Identify.</span></div>")

CSS = """
.gradio-container {max-width: 1080px !important; margin: 0 auto !important; overflow-x:hidden;}
body {background: var(--background-fill-primary);}

:root{
  --accent:#e5322d; --txt2:#475569;
  --okfg:#0f7b3e; --okbg:#e6f6ec; --ok:#16a34a;
  --warnfg:#9a6700; --warnbg:#fdf3d6; --warn:#d99e00;
  --badfg:#b42318; --badbg:#fde7e4; --bad:#e5484d;
}
.dark{
  --txt2:#cbd5e1;
  --okfg:#4ade80; --okbg:rgba(34,197,94,.16);
  --warnfg:#fbbf24; --warnbg:rgba(217,158,0,.16);
  --badfg:#f87171; --badbg:rgba(229,72,77,.16);
}

/* hero */
#hero {text-align:center; padding: 22px 0 18px;}
#hero h1 {font-size: clamp(1.6rem, 4.5vw, 2.35rem); font-weight: 800; margin:0; letter-spacing:-0.02em;}
#hero h1 .logo {background:var(--accent); color:#fff; padding:2px 11px; border-radius:11px; font-size:.8em; margin-right:4px;}
#hero p {color: var(--txt2); margin:12px auto 0; font-size:1.02rem; max-width:48ch; line-height:1.5;}

/* input card */
.inputcard {border:1px solid var(--border-color-primary); border-radius:18px; padding:18px;
  background:var(--background-fill-primary); box-shadow:0 4px 20px rgba(2,6,23,.06);}
.dark .inputcard {box-shadow:none;}
#go {width:100%; font-weight:700; font-size:1.02rem; min-height:50px; border-radius:12px;
  box-shadow:0 2px 8px rgba(229,50,45,.25);}
#ex {width:100%; min-height:50px; border-radius:12px; font-weight:600;}

/* main row spacing */
.mainrow {margin-top:18px;}

/* video */
.video-wrap {position:relative; width:100%; aspect-ratio:16/9; border-radius:16px;
  overflow:hidden; background:#000; border:1px solid var(--border-color-primary);}
.video-wrap iframe {position:absolute; inset:0; width:100%; height:100%; border:0;}
.placeholder {display:flex; align-items:center; justify-content:center; height:100%;
  color:#9aa0aa; font-size:.95rem; text-align:center; padding:0 16px;}

/* results */
.results {display:flex; flex-direction:column; gap:14px;}
.rhead {font-weight:700; font-size:1.05rem; color:var(--body-text-color);}
.card-item {border:1px solid var(--border-color-primary); border-radius:16px; padding:18px 20px;
  background:var(--background-fill-primary); box-shadow:0 2px 10px rgba(2,6,23,.06);}
.dark .card-item {box-shadow:inset 0 1px 0 rgba(255,255,255,.05); border-color:#3a4759;}
.row1 {display:flex; align-items:center; gap:8px;}
.emoji {font-size:1.2rem;}
.iname {font-weight:800; font-size:1.22rem; letter-spacing:-0.01em; line-height:1.25; color:var(--body-text-color);}
.meta {display:flex; align-items:center; gap:6px 8px; flex-wrap:wrap; margin-top:7px;}
.badge {font-size:.74rem; text-transform:uppercase; letter-spacing:.05em; padding:2px 9px;
  border-radius:999px; background:var(--background-fill-secondary);
  border:1px solid var(--border-color-primary); color:var(--txt2);}
.conf {font-weight:800; font-size:.85rem; padding:3px 11px; border-radius:999px;}
.conf.high {color:var(--okfg); background:var(--okbg);}
.conf.mid {color:var(--warnfg); background:var(--warnbg);}
.conf.low {color:var(--badfg); background:var(--badbg);}
.chip {font-size:.78rem; color:var(--txt2); background:var(--background-fill-secondary);
  padding:3px 9px; border-radius:999px;}
.desc {margin-top:10px; color:var(--body-text-color); line-height:1.55;}
.alts {margin-top:11px; font-size:.85rem; color:var(--txt2);}
.eyebrow {font-size:.68rem; text-transform:uppercase; letter-spacing:.06em; color:var(--txt2); margin-right:6px;}
.alt {display:inline-block; padding:2px 10px; border-radius:999px; background:var(--background-fill-secondary);
  border:1px solid var(--border-color-primary); margin:0 4px 4px 0; font-size:.8rem; color:var(--body-text-color);}
.links {margin-top:12px; display:flex; gap:8px; flex-wrap:wrap;}
.links .btn {text-decoration:none; font-size:.82rem; font-weight:600; padding:7px 13px;
  border-radius:10px; border:1px solid var(--border-color-primary);
  color:var(--body-text-color); background:var(--background-fill-secondary); transition:.15s;}
.links .btn:hover {border-color:var(--accent); color:var(--accent);
  background:rgba(229,50,45,.08); transform:translateY(-1px);}
.why {margin-top:12px; font-size:.82rem; color:var(--txt2);
  border-top:1px dashed var(--border-color-primary); padding-top:10px; line-height:1.5;}
.note {margin-top:4px; font-size:.8rem; color:var(--txt2); display:flex; align-items:center; flex-wrap:wrap;}
.dot {display:inline-block; width:9px; height:9px; border-radius:50%; margin:0 4px 0 10px;}
.dot.high{background:var(--ok);} .dot.mid{background:var(--warn);} .dot.low{background:var(--bad);}
.empty {padding:44px 22px; text-align:center; color:var(--txt2);
  border:1px solid var(--border-color-primary); border-radius:16px;
  background:var(--background-fill-primary); line-height:1.7;}
.empty .eglyph {display:block; font-size:2.2rem; margin-bottom:10px; opacity:.5;}
.empty.err {border-color:#f0b4ad;}
.empty-sub {font-size:.85rem;}
#foot {text-align:center; color:var(--txt2); font-size:.85rem; margin-top:18px;}
footer {display:none !important;}
"""

THEME = gr.themes.Soft(
    primary_hue="red", neutral_hue="slate",
    font=[gr.themes.GoogleFont("Inter"), "system-ui", "sans-serif"],
).set(
    # Stop the red primary hue from painting field labels like the CTA / an error.
    block_label_background_fill="*background_fill_primary",
    block_label_background_fill_dark="*background_fill_primary",
    block_label_text_color="*neutral_600",
    block_label_text_color_dark="*neutral_300",
    block_title_text_color="*neutral_700",
    block_title_text_color_dark="*neutral_200",
)


def build():
    """Construct the Gradio UI (kept separate from launch so it can be import-tested)."""
    with gr.Blocks(title="What's in this video?") as demo:
        gr.HTML(HERO)
        with gr.Group(elem_classes=["inputcard"]):
            url = gr.Textbox(label="YouTube link", lines=1,
                             placeholder="https://www.youtube.com/watch?v=…")
            with gr.Row():
                category = gr.Dropdown(choices=CATEGORIES, value="Any",
                                       label="What are you looking for?", scale=2)
                focus = gr.Textbox(label="Anything specific? (optional)", scale=3,
                                   placeholder="e.g. the editing tool · the game")
            with gr.Row():
                go = gr.Button("🔍  Identify", variant="primary", elem_id="go", scale=3)
                ex = gr.Button("Try an example", variant="secondary", elem_id="ex", scale=1)
        with gr.Row(elem_classes=["mainrow"]):
            with gr.Column(scale=5, min_width=300):
                preview = gr.HTML(embed_html(""))
            with gr.Column(scale=7, min_width=300):
                out = gr.HTML(EMPTY_RESULT)
        gr.HTML(FOOT)

        example = ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "Song", "")
        url.change(embed_html, url, preview)            # live preview as you paste
        go.click(embed_html, url, preview)              # ensure preview on click too
        go.click(run, [url, category, focus], out)      # identify
        url.submit(run, [url, category, focus], out)    # Enter = identify
        ex.click(lambda: example, outputs=[url, category, focus]).then(embed_html, url, preview)
    return demo


def main():
    # Add share=True for a temporary public link you can send to a friend.
    build().launch(theme=THEME, css=CSS)


if __name__ == "__main__":
    main()
