from pathlib import Path

path = Path(__file__).with_name("apply_transaction_integrity_fixes.py")
text = path.read_text(encoding="utf-8")
old = '''def _execution_result_is_failure(value: Any) -> bool:\n    return isinstance(value, dict) and value.get("executionResult") is False\n'''
new = '''def _execution_result_is_failure(value: Any) -> bool:\n    """Return True when the returned transaction explicitly failed execution."""\n    return isinstance(value, dict) and value.get("executionResult") is False\n'''
if text.count(old) != 1:
    raise RuntimeError(f"Expected one patch-driver match, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
