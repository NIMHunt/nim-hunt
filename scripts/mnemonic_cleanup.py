from __future__ import annotations

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OBSOLETE_FLAG = "NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC"
PUBLIC_PHRASE = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
TEST_PHRASE = "legal winner thank year wave sausage worth useful legal winner thank yellow"
EXCLUDED_SCAN_PATHS = {
    ".github/workflows/mnemonic-cleanup.yml",
    "scripts/mnemonic_cleanup.py",
    ".github/mnemonic-cleanup-trigger",
    ".github/mnemonic-cleanup-report.txt",
}


def path(name: str) -> Path:
    return ROOT / name


def replace(name: str, old: str, new: str, *, required: bool = True, count: int = -1) -> None:
    file_path = path(name)
    text = file_path.read_text(encoding="utf-8")
    if old not in text:
        if required:
            raise RuntimeError(f"Expected text not found in {name}: {old[:120]!r}")
        return
    file_path.write_text(text.replace(old, new, count), encoding="utf-8")


def remove_lines_containing(name: str, needle: str) -> None:
    file_path = path(name)
    lines = file_path.read_text(encoding="utf-8").splitlines()
    file_path.write_text(
        "\n".join(line for line in lines if needle not in line) + "\n",
        encoding="utf-8",
    )


def tracked_files() -> list[str]:
    output = subprocess.check_output(
        ["git", "ls-files"], cwd=ROOT, text=True, encoding="utf-8"
    )
    return [line for line in output.splitlines() if line]


def is_text_file(file_path: Path) -> bool:
    try:
        file_path.read_text(encoding="utf-8")
        return True
    except (UnicodeDecodeError, OSError):
        return False


def generate_runtime_test_helper() -> None:
    path("helpers/test_mnemonic.mjs").write_text(
        """import * as NimiqModule from '@nimiq/core';

const Nimiq = (NimiqModule.default && NimiqModule.default.MnemonicUtils)
  ? NimiqModule.default
  : NimiqModule;

export function generateTestMnemonic() {
  const generated = Nimiq.MnemonicUtils.generateMnemonic();
  return Array.isArray(generated) ? generated.join(' ') : String(generated);
}
""",
        encoding="utf-8",
    )


def update_helper_tests() -> None:
    for name, variable in (
        ("helpers/helper_safety.test.mjs", "TEST_MNEMONIC"),
        ("helpers/helper_rpc_send.test.mjs", "MNEMONIC"),
        ("helpers/helper_rpc_failure.test.mjs", "MNEMONIC"),
    ):
        replace(
            name,
            "import { resolve } from 'node:path';\n",
            "import { resolve } from 'node:path';\n"
            "import { generateTestMnemonic } from './test_mnemonic.mjs';\n",
            required="generateTestMnemonic" not in path(name).read_text(encoding="utf-8"),
            count=1,
        )
        file_path = path(name)
        text = file_path.read_text(encoding="utf-8")
        hardcoded = next(
            (
                line
                for line in text.splitlines()
                if line.startswith(f"const {variable} = ") and TEST_PHRASE in line
            ),
            None,
        )
        if hardcoded:
            text = text.replace(
                hardcoded, f"const {variable} = generateTestMnemonic();", 1
            )
            file_path.write_text(text, encoding="utf-8")
        elif f"const {variable} = generateTestMnemonic();" not in text:
            raise RuntimeError(f"Could not update {variable} in {name}")
        remove_lines_containing(name, OBSOLETE_FLAG)

    safety = path("helpers/helper_safety.test.mjs")
    text = safety.read_text(encoding="utf-8")
    start = text.find(
        "test('the removed default-mnemonic flag no longer supplies signing material'"
    )
    if start != -1:
        end = text.find("\n\ntest(", start + 1)
        if end == -1:
            raise RuntimeError("Could not locate the end of the obsolete safety test")
        safety.write_text(text[:start] + text[end + 2 :], encoding="utf-8")


def update_configuration() -> None:
    remove_lines_containing(".github/workflows/ci.yml", OBSOLETE_FLAG)
    remove_lines_containing("constants.py", f"{OBSOLETE_FLAG}_ENV")
    replace(
        "constants.py",
        "# This valid TestAlbatross address is derived from the repository's public\n"
        "# development mnemonic at a reserved path. It is convenient for local testing\n"
        "# only: anyone can derive its key, so public deployments explicitly reject it.\n",
        "# This valid TestAlbatross address belongs to a historic public development\n"
        "# wallet. It is convenient for local testing only and is not operator-controlled,\n"
        "# so public deployments explicitly reject it.\n",
        required=False,
    )


def update_readme() -> None:
    replace(
        "README.md",
        "Both public modes disable Desktop User, Test Location, mock data, placeholder\n"
        "addresses, fake sends, development seeds and the repository's public test mnemonic.\n"
        "They also require explicit signer commands, private signing material, HTTPS chain\n"
        "endpoints and a valid operator-controlled cancellation-fee address.\n",
        "Both public modes disable Desktop User, Test Location, mock data, placeholder\n"
        "addresses, fake sends and development seeds. They also require explicit signer\n"
        "commands, private signing material, HTTPS chain endpoints and a valid\n"
        "operator-controlled cancellation-fee address.\n",
        required=False,
    )
    replace(
        "README.md",
        "The development launcher configures TestAlbatross, the bundled helper and the\n"
        "public test mnemonic unless you override them:\n\n"
        "```bash\n./nimhunt_start_dev.sh\n```\n",
        "The development launcher configures TestAlbatross and the bundled helper.\n"
        "Supply a dedicated TestAlbatross mnemonic explicitly before starting:\n\n"
        "```bash\n"
        "export NIMHUNT_NIMIQ_MNEMONIC='your private TestAlbatross mnemonic'\n"
        "./nimhunt_start_dev.sh\n"
        "```\n\n"
        "Use a development-only mnemonic and never commit it. The launcher stops with a\n"
        "clear error if `NIMHUNT_NIMIQ_MNEMONIC` is missing.\n",
        required=False,
    )
    replace(
        "README.md",
        "| `NIMHUNT_PROJECT_DIR` | launcher directory | alternate project directory |\n"
        "| `NIMHUNT_HOST` | `0.0.0.0` | Uvicorn bind host |\n",
        "| `NIMHUNT_PROJECT_DIR` | launcher directory | alternate project directory |\n"
        "| `NIMHUNT_NIMIQ_MNEMONIC` | none; required | development-only TestAlbatross signing mnemonic |\n"
        "| `NIMHUNT_HOST` | `0.0.0.0` | Uvicorn bind host |\n",
        required=False,
    )
    replace(
        "README.md",
        "### Public test mnemonic\n\n"
        "For TestAlbatross only, the bundled helper can use its public deterministic test\n"
        "mnemonic:\n\n"
        "```bash\nexport NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC=1\n```\n\n"
        "This is enabled by the local development launcher. It is intentionally public and\n"
        "must never protect a publicly reachable deployment, even one using test NIM. Both\n"
        "`public-testnet` and `production` reject the flag and the same mnemonic if supplied\n"
        "directly through `NIMHUNT_NIMIQ_MNEMONIC`.\n\n",
        "",
        required=False,
    )
    replace(
        "README.md",
        "In `development`, `trans_updater.py` may find the bundled helper automatically if\n"
        "a permitted development mnemonic is available. Both public modes require explicit\n"
        "derive and send commands so the operator's intent is unambiguous.\n",
        "In `development`, `trans_updater.py` may find the bundled helper automatically when\n"
        "`NIMHUNT_NIMIQ_MNEMONIC` is supplied. Both public modes require explicit derive\n"
        "and send commands so the operator's intent is unambiguous.\n",
        required=False,
    )
    replace(
        "README.md",
        "The variable retains its historical name for compatibility. The repository's\n"
        "development default is a real TestAlbatross address derived from the public test\n"
        "mnemonic, so anyone can spend from it. Both public modes explicitly reject that\n"
        "address and require a different checksum-valid address controlled by the operator.\n",
        "The variable retains its historical name for compatibility. The development\n"
        "default is a real TestAlbatross address belonging to a public test wallet, so it is\n"
        "not operator-controlled. Both public modes explicitly reject that address and\n"
        "require a different checksum-valid address controlled by the operator.\n",
        required=False,
    )
    replace(
        "README.md",
        "Use a newly generated **private testnet mnemonic**. Do not use the repository's\n"
        "public development mnemonic and do not plan to reuse this seed on mainnet.\n",
        "Use a newly generated **private testnet mnemonic** and do not plan to reuse this\n"
        "seed on mainnet.\n",
        required=False,
    )
    replace(
        "README.md",
        "Do not set `NIMHUNT_PRODUCTION`, `NIMHUNT_DEV_MASTER_SEED`, or\n"
        "`NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC`. Do not reuse the public development\n"
        "mnemonic. `NIMHUNT_NIMIQ_EXTERNAL_SIGNER` is unnecessary when using the bundled\n"
        "helper.\n",
        "Do not set `NIMHUNT_PRODUCTION` or `NIMHUNT_DEV_MASTER_SEED`.\n"
        "`NIMHUNT_NIMIQ_EXTERNAL_SIGNER` is unnecessary when using the bundled helper.\n",
        required=False,
    )
    remove_lines_containing("README.md", OBSOLETE_FLAG)


def remove_obsolete_flag_lines_everywhere() -> None:
    for name in tracked_files():
        if name in EXCLUDED_SCAN_PATHS:
            continue
        file_path = path(name)
        if not file_path.is_file() or not is_text_file(file_path):
            continue
        text = file_path.read_text(encoding="utf-8")
        if OBSOLETE_FLAG not in text:
            continue
        file_path.write_text(
            "\n".join(
                line for line in text.splitlines() if OBSOLETE_FLAG not in line
            )
            + "\n",
            encoding="utf-8",
        )


def write_report() -> int:
    findings: list[str] = []
    for name in tracked_files():
        if name in EXCLUDED_SCAN_PATHS:
            continue
        file_path = path(name)
        if not file_path.is_file() or not is_text_file(file_path):
            continue
        for line_number, line in enumerate(
            file_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for label, needle in (
                ("obsolete fallback flag", OBSOLETE_FLAG),
                ("embedded public mnemonic", PUBLIC_PHRASE),
                ("embedded test mnemonic", TEST_PHRASE),
            ):
                if needle in line:
                    findings.append(f"{name}:{line_number}: {label}: {line.strip()}")
    report = path(".github/mnemonic-cleanup-report.txt")
    report.write_text("\n".join(findings) + ("\n" if findings else ""), encoding="utf-8")
    return len(findings)


def main() -> None:
    generate_runtime_test_helper()
    update_helper_tests()
    update_configuration()
    update_readme()
    remove_obsolete_flag_lines_everywhere()
    count = write_report()
    print(f"Mnemonic cleanup finished with {count} remaining finding(s).")


if __name__ == "__main__":
    main()
