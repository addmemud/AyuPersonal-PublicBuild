"""Validate same-run and downloaded-artifact provenance without macOS signing."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path("tools").resolve()))
from prepare_altstore import source_artifact_id

assert source_artifact_id("123", "same-run") is None
assert source_artifact_id("123", "456") == 456
for run, artifact in [("0", "same-run"), ("123", "0"), ("123", "../bad")]:
    try:
        source_artifact_id(run, artifact)
    except ValueError:
        pass
    else:
        raise AssertionError("Invalid provenance accepted")
print("AltStore provenance distinguishes same-run and downloaded artifacts")
