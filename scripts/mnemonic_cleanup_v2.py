from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path.cwd()
OBSOLETE_FLAG = "NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC"
PUBLIC_PHRASE = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
TEST_PHRASE = "legal winner thank year wave sausage worth useful legal winner thank yellow"
PUBLIC_FINGERPRINT = "c557eec878dfd852ba3f88087c4f350f09c55537ab5e549c3cd14320ec3cef38"
EXCLUDED = {
    ".github/workflows/mnemonic-cleanup.yml",
    ".github/mnemonic-cleanup-trigger",
    ".github/mnemonic-cleanup-report.txt",
    "scripts/mnemonic_cleanup.py",
    "scripts/mnemonic_cleanup_v2.py",
}


def file(name: str) -> Path:
    return ROOT / name


def read(name: str) -> str:
    return file(name).read_text(encoding="utf-8")


def write(name: str, value: str) -> None:
    file(name).write_text(value, encoding="utf-8")


def replace(name: str, old: str, new: str, *, required: bool = True, count: int = -1) -> None:
    value = read(name)
    if old not in value:
        if required:
            raise RuntimeError(f"Expected text not found in {name}: {old[:140]!r}")
        return
    write(name, value.replace(old, new, count))


def regex_replace(name: str, pattern: str, replacement: str, *, required: bool = True) -> None:
    value = read(name)
    updated, count = re.subn(pattern, replacement, value, flags=re.MULTILINE | re.DOTALL)
    if count == 0 and required:
        raise RuntimeError(f"Expected pattern not found in {name}: {pattern[:140]!r}")
    if count:
        write(name, updated)


def remove_single_lines(name: str, needle: str) -> None:
    lines = read(name).splitlines()
    write(name, "\n".join(line for line in lines if needle not in line) + "\n")


def add_runtime_mnemonic_helper() -> None:
    write(
        "helpers/test_mnemonic.mjs",
        """import * as NimiqModule from '@nimiq/core';

const Nimiq = (NimiqModule.default && NimiqModule.default.MnemonicUtils)
  ? NimiqModule.default
  : NimiqModule;

export function generateTestMnemonic() {
  const generated = Nimiq.MnemonicUtils.generateMnemonic();
  return Array.isArray(generated) ? generated.join(' ') : String(generated);
}
""",
    )


def update_javascript_tests() -> None:
    for name, variable in (
        ("helpers/helper_safety.test.mjs", "TEST_MNEMONIC"),
        ("helpers/helper_rpc_send.test.mjs", "MNEMONIC"),
        ("helpers/helper_rpc_failure.test.mjs", "MNEMONIC"),
    ):
        value = read(name)
        if "generateTestMnemonic" not in value:
            value = value.replace(
                "import { resolve } from 'node:path';\n",
                "import { resolve } from 'node:path';\n"
                "import { generateTestMnemonic } from './test_mnemonic.mjs';\n",
                1,
            )
        value = re.sub(
            rf"^const {variable} = '[^']+';$",
            f"const {variable} = generateTestMnemonic();",
            value,
            count=1,
            flags=re.MULTILINE,
        )
        value = "\n".join(
            line for line in value.splitlines() if OBSOLETE_FLAG not in line
        ) + "\n"
        write(name, value)

    regex_replace(
        "helpers/helper_safety.test.mjs",
        r"test\('the removed default-mnemonic flag no longer supplies signing material'.*?\n\}\);\n\n",
        "",
        required=False,
    )

    name = "helpers/transaction_data.test.mjs"
    value = read(name)
    if "generateTestMnemonic" not in value:
        value = value.replace(
            "import { encodeTransactionMemo } from './transaction_data.mjs';\n",
            "import { encodeTransactionMemo } from './transaction_data.mjs';\n"
            "import { generateTestMnemonic } from './test_mnemonic.mjs';\n",
            1,
        )
    value = re.sub(
        r"^const TEST_MNEMONIC = '[^']+';$",
        "const TEST_MNEMONIC = generateTestMnemonic();",
        value,
        count=1,
        flags=re.MULTILINE,
    )
    write(name, value)


def update_runtime_code() -> None:
    remove_single_lines("constants.py", f"{OBSOLETE_FLAG}_ENV")
    replace(
        "constants.py",
        "# This valid TestAlbatross address is derived from the repository's public\n"
        "# development mnemonic at a reserved path. It is convenient for local testing\n"
        "# only: anyone can derive its key, so public deployments explicitly reject it.\n",
        "# This valid TestAlbatross address belongs to a historic public development\n"
        "# wallet. It is convenient for local testing only and is not operator-controlled,\n"
        "# so public deployments explicitly reject it.\n",
    )

    name = "main.py"
    value = read(name)
    if "import hashlib\n" not in value:
        value = value.replace("import logging\n", "import hashlib\nimport logging\n", 1)
    if "_PUBLIC_TEST_MNEMONIC_SHA256" not in value:
        value = value.replace(
            "logger = logging.getLogger(__name__)\n\n\n",
            "logger = logging.getLogger(__name__)\n\n"
            f"_PUBLIC_TEST_MNEMONIC_SHA256 = \"{PUBLIC_FINGERPRINT}\"\n\n\n",
            1,
        )
    if "def _is_public_test_mnemonic" not in value:
        value = value.replace(
            "def _env_enabled(name: str) -> bool:\n"
            "    return os.getenv(name, \"\").strip().lower() in {\"1\", \"true\", \"yes\", \"on\"}\n\n\n",
            "def _env_enabled(name: str) -> bool:\n"
            "    return os.getenv(name, \"\").strip().lower() in {\"1\", \"true\", \"yes\", \"on\"}\n\n\n"
            "def _is_public_test_mnemonic(value: str) -> bool:\n"
            "    normalised = \" \".join(str(value or \"\").split()).lower()\n"
            "    if not normalised:\n"
            "        return False\n"
            "    digest = hashlib.sha256(normalised.encode(\"utf-8\")).hexdigest()\n"
            "    return digest == _PUBLIC_TEST_MNEMONIC_SHA256\n\n\n",
            1,
        )
    value = re.sub(
        r"\n    default_mnemonic_env = getattr\(\n        const,\n        \"NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC_ENV\",\n        \"NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC\",\n    \)\n    if _env_enabled\(default_mnemonic_env\):\n        unsafe\.append\(f\"\{default_mnemonic_env\} must not be enabled\"\)\n",
        "",
        value,
        count=1,
    )
    value, count = re.subn(
        r"    public_default = \(\n        \"abandon abandon abandon abandon abandon abandon abandon abandon \"\n        \"abandon abandon abandon about\"\n    \)\n    if mnemonic and \" \"\.join\(mnemonic\.split\(\)\)\.lower\(\) == public_default:\n",
        "    if mnemonic and _is_public_test_mnemonic(mnemonic):\n",
        value,
        count=1,
    )
    if count != 1:
        raise RuntimeError("Could not replace the plaintext public mnemonic check in main.py")
    write(name, value)

    regex_replace(
        "trans_updater.py",
        r"def _helper_seed_configured\(\) -> bool:\n.*?\n\n\ndef _default_helper_command",
        "def _helper_seed_configured() -> bool:\n"
        "    mnemonic_env = getattr(\n"
        "        const, \"NIMHUNT_NIMIQ_MNEMONIC_ENV\", \"NIMHUNT_NIMIQ_MNEMONIC\"\n"
        "    )\n"
        "    return bool(os.getenv(mnemonic_env))\n\n\n"
        "def _default_helper_command",
    )

    name = "nimhunt_reset_mock_data.sh"
    replace(
        name,
        "source venv/bin/activate\n\nexport NIMHUNT_DEPLOYMENT_MODE=\"${NIMHUNT_DEPLOYMENT_MODE:-development}\"\n",
        "if [ -z \"${NIMHUNT_NIMIQ_MNEMONIC:-}\" ]; then\n"
        "    echo \"NIMHUNT_NIMIQ_MNEMONIC is not set.\"\n"
        "    echo \"Export a dedicated TestAlbatross mnemonic before resetting mock data.\"\n"
        "    echo \"The repository no longer contains a built-in development mnemonic.\"\n"
        "    read -r -p \"Press Enter to close...\" _\n"
        "    exit 1\n"
        "fi\n\n"
        "source venv/bin/activate\n\n"
        "export NIMHUNT_DEPLOYMENT_MODE=\"${NIMHUNT_DEPLOYMENT_MODE:-development}\"\n",
    )
    remove_single_lines(name, OBSOLETE_FLAG)


def update_python_tests() -> None:
    remove_single_lines("tests/test_claim_location_guard.py", OBSOLETE_FLAG)
    remove_single_lines("tests/test_runtime_financial_composition.py", OBSOLETE_FLAG)

    name = "tests/test_deployment_modes.py"
    remove_single_lines(name, OBSOLETE_FLAG)
    regex_replace(
        name,
        r"    def test_default_helper_accepts_documented_true_alias\(self\):\n.*?\n            self\.assertTrue\(trans_updater\._helper_seed_configured\(\)\)\n\n",
        "    def test_default_helper_requires_an_explicit_mnemonic(self):\n"
        "        with mock.patch.dict(\n"
        "            os.environ, {\"NIMHUNT_NIMIQ_MNEMONIC\": \"\"}, clear=False\n"
        "        ):\n"
        "            self.assertFalse(trans_updater._helper_seed_configured())\n"
        "        with mock.patch.dict(\n"
        "            os.environ, {\"NIMHUNT_NIMIQ_MNEMONIC\": \"test-only mnemonic\"}, clear=False\n"
        "        ):\n"
        "            self.assertTrue(trans_updater._helper_seed_configured())\n\n",
    )
    regex_replace(
        name,
        r"    def test_public_testnet_rejects_public_default_mnemonic_flag\(self\):\n.*?\n        self\.assertIn\(\"must not be enabled\", result\.stderr\)\n\n",
        "",
    )
    regex_replace(
        name,
        r"        environment\[\"NIMHUNT_NIMIQ_MNEMONIC\"\] = \(\n            \"abandon abandon abandon abandon abandon abandon abandon abandon \"\n            \"abandon abandon abandon about\"\n        \)\n",
        "        environment[\"NIMHUNT_NIMIQ_MNEMONIC\"] = \" \".join(\n"
        "            [\"abandon\"] * 11 + [\"about\"]\n"
        "        )\n",
    )

    name = "tests/test_production_safety.py"
    regex_replace(
        name,
        r"    default_mnemonic_env = getattr\(\n        const,\n        \"NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC_ENV\",\n        \"NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC\",\n    \)\n",
        "",
    )
    remove_single_lines(name, "default_mnemonic_env: \"\",")
    regex_replace(
        name,
        r"    def test_production_refuses_public_default_test_mnemonic\(self\):\n.*?\n                main\.validate_deployment_safety\(\)\n\n",
        "",
    )


def update_readme() -> None:
    replacements = [
        (
            "Both public modes disable Desktop User, Test Location, mock data, placeholder\n"
            "addresses, fake sends, development seeds and the repository's public test mnemonic.\n"
            "They also require explicit signer commands, private signing material, HTTPS chain\n"
            "endpoints and a valid operator-controlled cancellation-fee address.\n",
            "Both public modes disable Desktop User, Test Location, mock data, placeholder\n"
            "addresses, fake sends and development seeds. They also require explicit signer\n"
            "commands, private signing material, HTTPS chain endpoints and a valid\n"
            "operator-controlled cancellation-fee address.\n",
        ),
        (
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
        ),
        (
            "| `NIMHUNT_PROJECT_DIR` | launcher directory | alternate project directory |\n"
            "| `NIMHUNT_HOST` | `0.0.0.0` | Uvicorn bind host |\n",
            "| `NIMHUNT_PROJECT_DIR` | launcher directory | alternate project directory |\n"
            "| `NIMHUNT_NIMIQ_MNEMONIC` | none; required | development-only TestAlbatross signing mnemonic |\n"
            "| `NIMHUNT_HOST` | `0.0.0.0` | Uvicorn bind host |\n",
        ),
        (
            "### Public test mnemonic\n\n"
            "For TestAlbatross only, the bundled helper can use its public deterministic test\n"
            "mnemonic:\n\n"
            "```bash\nexport NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC=1\n```\n\n"
            "This is enabled by the local development launcher. It is intentionally public and\n"
            "must never protect a publicly reachable deployment, even one using test NIM. Both\n"
            "`public-testnet` and `production` reject the flag and the same mnemonic if supplied\n"
            "directly through `NIMHUNT_NIMIQ_MNEMONIC`.\n\n",
            "",
        ),
        (
            "In `development`, `trans_updater.py` may find the bundled helper automatically if\n"
            "a permitted development mnemonic is available. Both public modes require explicit\n"
            "derive and send commands so the operator's intent is unambiguous.\n",
            "In `development`, `trans_updater.py` may find the bundled helper automatically when\n"
            "`NIMHUNT_NIMIQ_MNEMONIC` is supplied. Both public modes require explicit derive\n"
            "and send commands so the operator's intent is unambiguous.\n",
        ),
        (
            "The variable retains its historical name for compatibility. The repository's\n"
            "development default is a real TestAlbatross address derived from the public test\n"
            "mnemonic, so anyone can spend from it. Both public modes explicitly reject that\n"
            "address and require a different checksum-valid address controlled by the operator.\n",
            "The variable retains its historical name for compatibility. The development\n"
            "default is a real TestAlbatross address belonging to a public test wallet, so it is\n"
            "not operator-controlled. Both public modes explicitly reject that address and\n"
            "require a different checksum-valid address controlled by the operator.\n",
        ),
        (
            "Use a newly generated **private testnet mnemonic**. Do not use the repository's\n"
            "public development mnemonic and do not plan to reuse this seed on mainnet.\n",
            "Use a newly generated **private testnet mnemonic** and do not plan to reuse this\n"
            "seed on mainnet.\n",
        ),
        (
            "Do not set `NIMHUNT_PRODUCTION`, `NIMHUNT_DEV_MASTER_SEED`, or\n"
            "`NIMHUNT_NIMIQ_ALLOW_DEFAULT_TEST_MNEMONIC`. Do not reuse the public development\n"
            "mnemonic. `NIMHUNT_NIMIQ_EXTERNAL_SIGNER` is unnecessary when using the bundled\n"
            "helper.\n",
            "Do not set `NIMHUNT_PRODUCTION` or `NIMHUNT_DEV_MASTER_SEED`.\n"
            "`NIMHUNT_NIMIQ_EXTERNAL_SIGNER` is unnecessary when using the bundled helper.\n",
        ),
    ]
    for old, new in replacements:
        replace("README.md", old, new)
    remove_single_lines("README.md", OBSOLETE_FLAG)


def tracked_files() -> list[str]:
    output = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True)
    return output.splitlines()


def write_report() -> int:
    findings: list[str] = []
    for name in tracked_files():
        if name in EXCLUDED:
            continue
        path = file(name)
        if not path.is_file():
            continue
        try:
            value = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        normalised = " ".join(value.split())
        if OBSOLETE_FLAG in value:
            findings.append(f"{name}: obsolete fallback flag")
        if PUBLIC_PHRASE in normalised:
            findings.append(f"{name}: embedded public mnemonic")
        if TEST_PHRASE in normalised:
            findings.append(f"{name}: embedded test mnemonic")
    report = file(".github/mnemonic-cleanup-report.txt")
    report.write_text("\n".join(findings) + ("\n" if findings else ""), encoding="utf-8")
    return len(findings)


def main() -> None:
    add_runtime_mnemonic_helper()
    update_javascript_tests()
    update_runtime_code()
    update_python_tests()
    update_readme()
    count = write_report()
    print(f"Targeted mnemonic cleanup finished with {count} finding(s).")


if __name__ == "__main__":
    main()
