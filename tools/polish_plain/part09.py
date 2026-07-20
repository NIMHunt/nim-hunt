# Remove temporary repair machinery before linting and committing the real changes.
replace_once(
    "tests/test_ui_polish_regressions.py",
    "\nimport pytest\n",
    "\n",
)

import shutil

for relative in (
    "tools/polish_parts",
    "tools/polish_payload",
    "tools/polish_plain",
):
    shutil.rmtree(ROOT / relative, ignore_errors=True)

for relative in (
    ".github/workflows/apply-polish-patch-final.yml",
    "tools/pytest-failure.log",
    "tools/polish-repair.log",
):
    (ROOT / relative).unlink(missing_ok=True)
