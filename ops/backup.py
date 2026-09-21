#!/usr/bin/env python3
"""Create a private database + encryption-key recovery bundle."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    os.umask(0o077)
    target = (
        Path(sys.argv[1]).resolve()
        if len(sys.argv) > 1
        else ROOT
        / ".local"
        / "backups"
        / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    target.mkdir(parents=True, exist_ok=False)
    try:
        with (target / "database.dump").open("wb") as output:
            subprocess.run(
                [
                    str(ROOT / "ops/compose.sh"),
                    "exec",
                    "-T",
                    "db",
                    "pg_dump",
                    "-U",
                    "account_manager",
                    "-d",
                    "account_manager",
                    "-Fc",
                ],
                cwd=ROOT,
                stdout=output,
                check=True,
            )
        shutil.copyfile(
            ROOT / ".local/secrets/credential_key", target / "credential_key"
        )
        manifest = {
            "format": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "sha256": {
                name: hashlib.sha256((target / name).read_bytes()).hexdigest()
                for name in ("database.dump", "credential_key")
            },
        }
        (target / "manifest.json").write_text(json.dumps(manifest, indent=2))
    except Exception:
        print("Backup incomplete; do not use this bundle.", file=sys.stderr)
        raise
    print(
        f"Backup complete: {target.name}. Contains the credential decryption key; protect this bundle."
    )


if __name__ == "__main__":
    main()
