# YouTube Spotter

You're watching a reel. A movie plays. A tool flashes on screen. Creator never names it.
You comment asking. No reply.

Paste the link. Get the name.

---

Identifies movies, TV series, anime, games, software tools, and songs shown in YouTube
videos or Shorts — things creators show but never name.

## How it works

Link → Gemini AI watches the video directly (no download) → name, confidence, evidence.

**The cost problem — and how it's solved:**
Powerful Gemini model = accurate but ₹0.5–3/video.
Cheap model alone = confidently wrong.
Viewers almost always name the thing in the top comments.
Cheapest model + comments = accurate + cheap.

**₹0.04/reel. 100 videos ≈ ₹4.**

## Setup

Needs Python 3.10+ and a free Gemini key → https://aistudio.google.com/apikey

```bash
pip install -r requirements.txt
```

Copy `.env.example` → `.env`:

```
GEMINI_API_KEY=your_key_here
```

**Optional booster** — adds video description + top comments for better accuracy:

```
YOUTUBE_API_KEY=your_key_here
```

Enable "YouTube Data API v3" in Google Cloud Console.

```bash
python app.py
```

Open → http://127.0.0.1:7860

## What you get

Video player + result cards, side by side. Shorts load vertical (9:16), regular videos
load wide. Filter by category. Light/dark toggle.

Some clips block iframe embedding — identification still works. Gemini reads server-side,
not via the embed.

## Limits

- Popular titles: accurate. Obscure: uncertain.
- Low confidence = treat as hint, not fact.
- Private, age-restricted, members-only videos can't be read.

## Roadmap

- Instagram reels (needs yt-dlp + login)
- Copy-all button + lookup history

## License

MIT
