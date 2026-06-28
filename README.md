# What's in this video?

Paste a YouTube link. Get the movies, TV series, anime, games, and **tools / apps**
the creator showed but never named — so you stop hunting through "comment and I'll DM
you the link."

Runs 100% on your own computer. YouTube only (Instagram comes later). Free to run on
Google's free tier.

## How it works

The app sends the YouTube link to Google's Gemini AI. Gemini watches the video (frames
and audio) and names what it sees, with a confidence score and the evidence it used. The
app shows a clean list with ready-made search links. No video is downloaded — Gemini
reads the link directly.

```
your link → Gemini watches it → list of {name, type, confidence, why} → shown to you
```

## What you need

- Python 3.10 or newer
- A free Gemini API key: https://aistudio.google.com/apikey

## Setup

1. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
2. Add your key. Copy `.env.example` to `.env` and paste your key inside:
   ```
   GEMINI_API_KEY=your_key_here
   ```
3. Run it:
   ```
   python app.py
   ```
4. Open the local link it prints (usually http://127.0.0.1:7860) and paste a YouTube link.

Want a temporary public link to show a friend? Change `demo.launch()` to
`demo.launch(share=True)` in `app.py`.

## Good to know (honest limits)

- Best on popular movies / shows / anime / games and well-known tools. Rare or obscure
  things can be wrong.
- It shows a confidence score. Low confidence can be wrong — treat it as a hint, not truth.
- Some videos (private, age-restricted, members-only) can't be read.
- Each lookup costs a fraction of a rupee; the free tier covers normal hobby use.

## Later ideas

- Instagram reels support (needs a downloader + login).
- Pull the video description and pinned comment as extra hints.
- A "copy all" button and a history of past lookups.

## License

MIT — hobby project, use freely.
