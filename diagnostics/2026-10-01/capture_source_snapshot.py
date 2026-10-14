"""Fingerprint investigated code without reading credentials or runtime history."""

import hashlib
import json
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).with_name("source_snapshot.json")
EXTENSIONS = {".py", ".md", ".js", ".yaml", ".toml"}
FILES = {}
for root_name in ("runtime", "packages", "apps"):
    for path in sorted((REPOSITORY / root_name).rglob("*")):
        if (
            path.is_file()
            and path.suffix in EXTENSIONS
            and not {"node_modules", "__pycache__", ".venv"}.intersection(path.parts)
        ):
            FILES[path.relative_to(REPOSITORY).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
digest = hashlib.sha256(
    json.dumps(FILES, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()
record = {
    "purpose": "On-disk source investigated, not a historical backend build identity",
    "repository_head": "537943a",
    "source_set_sha256": digest,
    "file_count": len(FILES),
    "files": FILES,
}
OUTPUT.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
print(  # noqa: T201  # diagnostic CLI summary
    json.dumps({key: value for key, value in record.items() if key != "files"})
)
