import html
import json
import os
import re
import urllib.request

base = os.environ.get("BASE_URL", "https://nimhunt.app").rstrip("/")
slug = os.environ.get("SPOT_SLUG", "GLqKYswoYdo")
headers = {"User-Agent": "NimHunt-live-deployment-check/1.0"}


def fetch(path: str) -> str:
    request = urllib.request.Request(base + path, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8", errors="replace")
        print(f"FETCH {path}: status={response.status} bytes={len(body)}")
        return body


home = fetch("/")
print("HOME new asset version:", "special-user-badge-v1-20260727" in home)

spots_page = fetch("/spots")
print("SPOTS new asset version:", "special-user-badge-v1-20260727" in spots_page)

spot_ui = fetch("/static/spot_ui.js?v=special-user-badge-v1-20260727")
for marker in ("createUserDisplayName", "nq-hexagon", "This is a special user", "nq-purple"):
    print(f"SPOT_UI contains {marker!r}:", marker in spot_ui)

find_js = fetch("/static/find_spots.js?v=special-user-badge-v1-20260727")
for marker in ("createUserDisplayName", "creator_is_special", ".special-user-badge"):
    print(f"FIND_JS contains {marker!r}:", marker in find_js)

spot_page = fetch(f"/spot/{slug}")
match = re.search(r'<script[^>]+id=["\']spot-data["\'][^>]*>(.*?)</script>', spot_page, re.S | re.I)
if not match:
    print("SPOT_PAGE spot-data: NOT FOUND")
else:
    data = json.loads(html.unescape(match.group(1)).strip())
    selected = {
        key: data.get(key)
        for key in ("id", "link", "title", "created_by", "creator_display_name", "creator_is_special")
    }
    print("SPOT_PAGE selected data:", json.dumps(selected, sort_keys=True))

api = fetch("/api/spots/initial?include_active=true&include_upcoming=true&include_prizedraws=true")
payload = json.loads(api)
spots = payload.get("spots", []) if isinstance(payload, dict) else []
selected_spots = [
    {
        key: spot.get(key)
        for key in ("id", "link", "title", "created_by", "creator_display_name", "creator_is_special")
    }
    for spot in spots
    if spot.get("link") == slug or spot.get("title") == "Kryptostadt Represent!"
]
print("INITIAL_API matching spots:", json.dumps(selected_spots, sort_keys=True))
print("INITIAL_API total spots:", len(spots))
