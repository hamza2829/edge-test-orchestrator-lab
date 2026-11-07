"""Check tracked files for the requested punctuation rule."""

import subprocess
from pathlib import Path

paths = subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\0")
bad = [name for name in paths if name and chr(0x2014) in Path(name).read_text(encoding="utf-8")]
assert not bad, f"Em dash characters found in {bad}"
print("Tracked text passed the punctuation check")
