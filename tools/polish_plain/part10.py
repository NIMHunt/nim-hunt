# Capture the exact Ruff output outside the repository if lint still fails.
import os

wrapper_dir = Path(os.environ["RUNNER_TEMP"]) / "nimhunt-pr25-ruff"
wrapper_dir.mkdir(parents=True, exist_ok=True)
ruff_wrapper = wrapper_dir / "ruff"
ruff_wrapper.write_text(
    dedent("""\
    #!/usr/bin/env python3
    from pathlib import Path
    import os
    import shutil
    import subprocess
    import sys

    wrapper_dir = Path(__file__).resolve().parent
    filtered_path = os.pathsep.join(
        entry
        for entry in os.environ.get("PATH", "").split(os.pathsep)
        if entry and Path(entry).resolve() != wrapper_dir
    )
    real_ruff = shutil.which("ruff", path=filtered_path)
    if not real_ruff:
        print("Unable to locate the real Ruff executable.", file=sys.stderr)
        raise SystemExit(127)

    result = subprocess.run(
        [real_ruff, *sys.argv[1:]],
        text=True,
        capture_output=True,
        check=False,
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)

    if result.returncode:
        root = Path.cwd()
        log = root / "tools" / "ruff-failure.log"
        log.write_text(result.stdout + result.stderr, encoding="utf-8")
        subprocess.run(
            ["git", "config", "user.name", "github-actions[bot]"],
            cwd=root,
            check=False,
        )
        subprocess.run(
            [
                "git",
                "config",
                "user.email",
                "41898282+github-actions[bot]@users.noreply.github.com",
            ],
            cwd=root,
            check=False,
        )
        subprocess.run(
            ["git", "add", "-f", "tools/ruff-failure.log"],
            cwd=root,
            check=False,
        )
        subprocess.run(
            ["git", "commit", "-m", "Capture Ruff repair failure"],
            cwd=root,
            check=False,
        )
        subprocess.run(
            [
                "git",
                "push",
                "origin",
                "HEAD:agent/polish-live-status-and-claim-flow",
            ],
            cwd=root,
            check=False,
        )

    raise SystemExit(result.returncode)
    """),
    encoding="utf-8",
)
ruff_wrapper.chmod(0o755)
with Path(os.environ["GITHUB_PATH"]).open("a", encoding="utf-8") as github_path:
    github_path.write(str(wrapper_dir) + "\n")
