"""Apply the remaining social-preview integrations, then remove this helper."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"Expected patch anchor missing from {path}: {old!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    ROOT / "main.py",
    "import settlement_updater\nimport trans_updater\nimport wallet\n",
    "import settlement_updater\nimport social_preview\nimport trans_updater\nimport wallet\n",
)
replace_once(
    ROOT / "main.py",
    "app = FastAPI(title=const.APP_NAME, lifespan=lifespan)\n"
    "app.mount(\"/static\", StaticFiles(directory=str(const.STATIC_DIR)), name=\"static\")\n"
    "app.include_router(public_router)\n",
    "app = FastAPI(title=const.APP_NAME, lifespan=lifespan)\n"
    "app.add_middleware(social_preview.SocialPreviewMiddleware)\n"
    "app.mount(\"/static\", StaticFiles(directory=str(const.STATIC_DIR)), name=\"static\")\n"
    "app.include_router(public_router)\n"
    "app.include_router(social_preview.router)\n",
)

requirements_path = ROOT / "requirements.txt"
requirements = requirements_path.read_text(encoding="utf-8")
if "Pillow==12.3.0" not in requirements:
    anchor = "MarkupSafe==3.0.3\n"
    if anchor not in requirements:
        raise RuntimeError("requirements.txt Pillow insertion anchor is missing")
    requirements_path.write_text(
        requirements.replace(anchor, anchor + "Pillow==12.3.0\n", 1),
        encoding="utf-8",
    )

roadmap_path = ROOT / "static" / "roadmap.json"
roadmap = json.loads(roadmap_path.read_text(encoding="utf-8"))
for section in roadmap.get("sections", []):
    if section.get("heading") == "August":
        if "Admin Panel" not in section.setdefault("items", []):
            section["items"].append("Admin Panel")
        break
else:
    raise RuntimeError("Roadmap August section is missing")
roadmap_path.write_text(json.dumps(roadmap, indent=2) + "\n", encoding="utf-8")

static_tests = ROOT / "tests" / "test_static_pages.py"
static_text = static_tests.read_text(encoding="utf-8")
if '                    "Admin Panel",' not in static_text:
    anchor = '                    "Better Marketing",\n'
    if anchor not in static_text:
        raise RuntimeError("Roadmap regression-test anchor is missing")
    static_tests.write_text(
        static_text.replace(anchor, anchor + '                    "Admin Panel",\n', 1),
        encoding="utf-8",
    )

for relative_path in (
    ".github/workflows/apply-social-preview.yml",
    "social-preview-patch-error.txt",
):
    path = ROOT / relative_path
    if path.exists():
        path.unlink()
Path(__file__).unlink()
