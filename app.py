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


def identify(url: str, focus: str = "") -> list:
    """Hand the YouTube link to Gemini and get back a list of identified items."""
    prompt = PROMPT
    if focus.strip():
        prompt += f'\nThe viewer specifically wants: "{focus.strip()}". Prioritize that.'

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
                "or tell it what you're looking for in the second box.")

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


def run(url: str, focus: str = "") -> str:
    url = (url or "").strip()
    if not url:
        return "Paste a YouTube link first."
    if "youtu" not in url:
        return "This build is **YouTube-only** for now. Paste a youtube.com or youtu.be link."
    try:
        return to_markdown(identify(url, focus))
    except Exception as e:
        return f"⚠️ Failed: {e}"


def main():
    demo = gr.Interface(
        fn=run,
        inputs=[
            gr.Textbox(label="YouTube link",
                       placeholder="https://www.youtube.com/watch?v=..."),
            gr.Textbox(label="Looking for something specific? (optional)",
                       placeholder="e.g. the editing tool · the game · the song"),
        ],
        outputs=gr.Markdown(label="What's in it"),
        title="What's in this video?",
        description=("Paste a YouTube link → get the movies, shows, anime, games, and "
                     "tools the creator showed but didn't name. No 'comment for the link' needed."),
    )
    # Add share=True for a temporary public link you can send to a friend.
    demo.launch()


if __name__ == "__main__":
    main()
