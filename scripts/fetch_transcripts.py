import sys, os, json
from youtube_transcript_api import YouTubeTranscriptApi

video_ids = sys.argv[1].split(",")
outdir = "/home/$USER/jarvis/docs/video_notes/raw"
os.makedirs(outdir, exist_ok=True)
api = YouTubeTranscriptApi()
results = {}
for vid in video_ids:
    try:
        fetched = api.fetch(vid, languages=["en", "en-US", "en-GB", "a.en"])
        text = " ".join(s.text for s in fetched.snippets)
        path = os.path.join(outdir, f"{vid}.txt")
        with open(path, "w") as f:
            f.write(text)
        results[vid] = {"ok": True, "chars": len(text), "path": path}
    except Exception as e:
        results[vid] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
print(json.dumps(results, indent=2))
