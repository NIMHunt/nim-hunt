"""Apply the small localisation/version integration and remove this helper."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"Expected patch anchor missing from {path}: {old!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    ROOT / "static" / "interface_text.js",
    """const COMMON_TEXT_EN = {
    notice: {
        readMore: 'Read more',
        ok: 'OK',
    },
};
""",
    """const COMMON_TEXT_EN = {
    notice: {
        readMore: 'Read more',
        ok: 'OK',
    },
    actions: {
        copy: 'Copy',
        copied: 'Copied',
        shareOnX: 'Share on X',
    },
};
""",
)

for template_name in (
    "find_spots.html",
    "my_spots.html",
    "my_claims.html",
    "spot.html",
    "claim.html",
):
    template_path = ROOT / "templates" / template_name
    text = template_path.read_text(encoding="utf-8")
    old_version = "x-share-v2-20260725"
    if old_version not in text:
        raise RuntimeError(f"Share asset version anchor missing from {template_path}")
    template_path.write_text(
        text.replace(old_version, "x-share-v3-20260725"),
        encoding="utf-8",
    )

workflow = ROOT / ".github" / "workflows" / "apply-action-tooltip-catalogue.yml"
if workflow.exists():
    workflow.unlink()
Path(__file__).unlink()
