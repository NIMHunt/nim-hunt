from pathlib import Path

path = Path(__file__).resolve().parents[1] / "templates" / "find_spots.html"
text = path.read_text(encoding="utf-8")
old = "-mobile-location-v1-20260728\">"
new = "-mobile-location-v2-20260728\">"
if old in text:
    text = text.replace(old, new, 1)
elif new not in text:
    raise RuntimeError("Could not find the Find Spots stylesheet cache key")
path.write_text(text, encoding="utf-8")
