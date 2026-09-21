#!/usr/bin/env python3
"""Check eligible files or the exact staged blobs without printing secret values."""

import argparse
import ipaddress
import re
import subprocess
from pathlib import Path

PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "machine home path": re.compile(
        r"/(?:root|home|Users)(?:/|\b(?![.\w-]))|[A-Za-z]:\\Users\\"
    ),
}
IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
EXAMPLE_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
)
ARTIFACT_DIRS = {
    ".local",
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "dist",
    "playwright-report",
    "test-results",
}
PRIVATE_FILES = {
    ".env",
    ".local/secrets/credential_key",
    "deploy/build-ca.pem",
    "ops/build-ca.pem",
    ".README.md.swp",
}


def inspect(name, data):
    problems = []
    path = Path(name)
    if (
        ARTIFACT_DIRS.intersection(path.parts)
        or (path.name.startswith(".env") and path.name != ".env.example")
        or name in PRIVATE_FILES
        or path.suffix in {".log", ".dump", ".pem", ".key", ".swp", ".swo"}
    ):
        problems.append(f"{name}: private or runtime file eligible for Git")
    content = data.decode(errors="replace")
    for kind, pattern in PATTERNS.items():
        if pattern.search(content):
            problems.append(f"{name}: {kind}")
    for match in IPV4.finditer(content):
        # A browser User-Agent version is not a network address.
        if re.search(r"\b(?:Chrome|Chromium|Version)/$", content[: match.start()]):
            continue
        try:
            address = ipaddress.ip_address(match.group())
        except ValueError:
            continue
        if not (
            address.is_loopback
            or address.is_unspecified
            or any(address in network for network in EXAMPLE_NETWORKS)
        ):
            problems.append(f"{name}: non-example IPv4 address")
            break
    return problems


def audit(root, staged=False):
    args = ["git", "ls-files", "--cached"]
    if not staged:
        args += ["--others", "--exclude-standard"]
    names = subprocess.check_output(args + ["-z"], cwd=root).decode().split("\0")
    problems = []
    count = 0
    for name in filter(None, names):
        if staged:
            data = subprocess.check_output(["git", "show", f":{name}"], cwd=root)
        else:
            path = root / name
            if not path.is_file():
                continue
            data = path.read_bytes()
        problems.extend(inspect(name, data))
        count += 1
    for name in sorted(PRIVATE_FILES):
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "-q", name], cwd=root
        )
        if result.returncode:
            problems.append(f"{name}: missing ignore rule")
    return count, problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="Read Git index blobs")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    count, problems = audit(root, staged=args.staged)
    if problems:
        raise SystemExit("\n".join(problems))
    source = "staged" if args.staged else "eligible"
    print(
        f"Hygiene checks passed for {count} {source} files; private files are ignored."
    )


if __name__ == "__main__":
    main()
