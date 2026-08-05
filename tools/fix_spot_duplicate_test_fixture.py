from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / "tests/test_spot_duplication.py"
text = path.read_text(encoding="utf-8")
old = """                auto_reverse_geocode=False,
            )
            async with db_access.transaction(db, immediate=True):
                copy_id = await duplicate_owned_spot_as_draft(
"""
new = """                auto_reverse_geocode=False,
            )
            await db.commit()
            async with db_access.transaction(db, immediate=True):
                copy_id = await duplicate_owned_spot_as_draft(
"""
if text.count(old) != 1:
    raise RuntimeError(f"Expected one Prizedraw fixture match, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
(root / ".github/workflows/fix-spot-duplicate-test-fixture.yml").unlink()
Path(__file__).unlink()
