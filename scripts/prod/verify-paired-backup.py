#!/usr/bin/env python3
"""Validate one committed PKUBA DB/media/archive backup without mutating it."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


PAYLOADS = (
    "database.dump",
    "private-media.tar.gz",
    "archive-staging.tar.gz",
    "private-media.files.sha256",
    "archive-staging.files.sha256",
    "previous-release.env",
    "MANIFEST.env",
    "season-integrity-after-migrate.json",
    "core-migrations.txt",
    "release.json",
)
REQUIRED = frozenset((*PAYLOADS, "SHA256SUMS", "SUCCESS"))
KEY_VALUE = re.compile(r"^([A-Z0-9_]+)=([^\r\n=]*)$")
SHA_LINE = re.compile(r"^([0-9a-f]{64})  ([^\r\n]+)$")
TAG = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
COMPACT_TIME = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")
IMAGE = re.compile(r"^ghcr\.io/jiumao2/pkuba-(api|web)@sha256:[0-9a-f]{64}$")
POSTGRES_SOURCE_DIGEST = (
    "sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73"
)
CADDY_SOURCE_DIGEST = (
    "sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d"
)


def fail(message: str) -> None:
    raise SystemExit(f"paired backup error: {message}")


def parse_fixed(path: Path, expected: set[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = KEY_VALUE.fullmatch(line)
        if not match:
            fail(f"invalid key/value line in {path.name}")
        key, value = match.groups()
        if key not in expected or key in values:
            fail(f"unexpected or duplicate key in {path.name}: {key}")
        values[key] = value
    if values.keys() != expected:
        fail(f"missing fixed keys in {path.name}")
    return values


def regular_file(path: Path) -> None:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        fail(f"required payload is not a regular non-symlink file: {path.name}")
    if metadata.st_nlink != 1:
        fail(f"required payload has multiple hard links: {path.name}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_file_manifest(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = SHA_LINE.fullmatch(line)
        if not match:
            fail(f"invalid file checksum line in {path.name}")
        digest, raw_name = match.groups()
        if not raw_name.startswith("./"):
            fail(f"file checksum path must start with ./: {raw_name}")
        pure = PurePosixPath(raw_name[2:])
        if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
            fail(f"unsafe file checksum path: {raw_name}")
        name = pure.as_posix()
        if name in result:
            fail(f"duplicate file checksum path: {raw_name}")
        result[name] = digest
    return result


def verify_tar(archive: Path, manifest: Path, scratch_root: Path) -> None:
    expected = parse_file_manifest(manifest)
    with tempfile.TemporaryDirectory(prefix="pkuba-restore-preflight-", dir=scratch_root) as temp:
        destination = Path(temp)
        with tarfile.open(archive, "r:gz") as bundle:
            members = bundle.getmembers()
            seen_members: set[str] = set()
            for member in members:
                pure = PurePosixPath(member.name)
                if pure.is_absolute() or any(part == ".." for part in pure.parts):
                    fail(f"unsafe tar path in {archive.name}: {member.name}")
                if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                    fail(f"unsupported tar entry in {archive.name}: {member.name}")
                if not (member.isdir() or member.isfile()):
                    fail(f"unsupported tar entry type in {archive.name}: {member.name}")
                canonical_member = pure.as_posix().removeprefix("./")
                if member.isfile() and canonical_member in seen_members:
                    fail(f"duplicate tar member in {archive.name}: {member.name}")
                if member.isfile():
                    seen_members.add(canonical_member)
            bundle.extractall(destination, members=members, filter="data")
        actual: dict[str, str] = {}
        for item in destination.rglob("*"):
            if item.is_symlink():
                fail(f"scratch extraction created a symlink: {item}")
            if item.is_file():
                actual[item.relative_to(destination).as_posix()] = sha256(item)
        if actual != expected:
            fail(f"{archive.name} does not match {manifest.name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup-dir", required=True, type=Path)
    parser.add_argument("--backup-root", required=True, type=Path)
    parser.add_argument("--release-root", required=True, type=Path)
    parser.add_argument("--repository-dir", required=True, type=Path)
    parser.add_argument("--identity-validator", required=True, type=Path)
    parser.add_argument("--scratch-root", type=Path)
    parser.add_argument("--allow-test-root", action="store_true")
    parser.add_argument("--metadata-only", action="store_true")
    args = parser.parse_args()

    backup_root = args.backup_root.resolve(strict=True)
    backup_dir = args.backup_dir.resolve(strict=True)
    if not args.backup_root.is_absolute() or str(args.backup_root) != str(backup_root):
        fail("backup root must use its canonical absolute path")
    if args.backup_root.is_symlink():
        fail("backup root must be a real directory")
    if not args.backup_dir.is_absolute() or str(args.backup_dir) != str(backup_dir):
        fail("backup directory must use its canonical absolute path")
    if backup_dir.parent != backup_root:
        fail("backup directory must be an immediate child of the backup root")
    if args.backup_dir.is_symlink() or not backup_dir.is_dir():
        fail("backup directory must be a real directory")
    if not args.allow_test_root and backup_root.name != "backups":
        fail("production backup root must use the canonical backups directory")

    present = {item.name for item in backup_dir.iterdir()}
    if present != REQUIRED:
        fail(f"backup directory has missing or extra entries: {sorted(present ^ REQUIRED)}")
    for name in REQUIRED:
        regular_file(backup_dir / name)

    checksum_entries: dict[str, str] = {}
    for line in (backup_dir / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        match = SHA_LINE.fullmatch(line)
        if not match:
            fail("SHA256SUMS contains an invalid, absolute or traversing entry")
        digest, name = match.groups()
        if name not in PAYLOADS or name in checksum_entries or Path(name).name != name:
            fail("SHA256SUMS must contain the fixed payload allowlist exactly once")
        checksum_entries[name] = digest
    if checksum_entries.keys() != set(PAYLOADS):
        fail("SHA256SUMS does not contain the fixed payload allowlist")
    for name, digest in checksum_entries.items():
        if sha256(backup_dir / name) != digest:
            fail(f"checksum mismatch: {name}")

    manifest = parse_fixed(
        backup_dir / "MANIFEST.env",
        {
            "MANIFEST_VERSION",
            "TRANSACTION_ID",
            "CREATED_AT",
            "FROM_TAG",
            "FROM_COMMIT",
            "TO_TAG",
            "TO_COMMIT",
            "DATABASE_BYTES",
            "MEDIA_BYTES",
            "ARCHIVE_BYTES",
        },
    )
    if manifest["MANIFEST_VERSION"] != "2":
        fail("unsupported backup manifest version")
    if not TAG.fullmatch(manifest["FROM_TAG"]) or not TAG.fullmatch(manifest["TO_TAG"]):
        fail("invalid manifest release tag")
    if not COMMIT.fullmatch(manifest["FROM_COMMIT"]) or not COMMIT.fullmatch(manifest["TO_COMMIT"]):
        fail("invalid manifest release commit")
    compact = manifest["CREATED_AT"].replace("-", "").replace(":", "")
    if not COMPACT_TIME.fullmatch(compact):
        fail("invalid manifest creation time")
    transaction_id = f"deploy-{compact}-{manifest['FROM_TAG']}-to-{manifest['TO_TAG']}"
    if manifest["TRANSACTION_ID"] != transaction_id:
        fail("backup transaction identity does not match its manifest")
    if backup_dir.name != f"{compact}-pre-{manifest['TO_TAG']}":
        fail("backup directory name does not match its transaction")
    for key in ("DATABASE_BYTES", "MEDIA_BYTES", "ARCHIVE_BYTES"):
        if not manifest[key].isdigit():
            fail(f"invalid manifest size: {key}")

    success = parse_fixed(
        backup_dir / "SUCCESS",
        {"TRANSACTION_ID", "MANIFEST_SHA256", "COMMITTED_AT"},
    )
    if success["TRANSACTION_ID"] != transaction_id:
        fail("SUCCESS belongs to another transaction")
    if success["MANIFEST_SHA256"] != sha256(backup_dir / "SHA256SUMS"):
        fail("SUCCESS does not commit the verified SHA256SUMS")
    committed_compact = success["COMMITTED_AT"].replace("-", "").replace(":", "")
    if not COMPACT_TIME.fullmatch(committed_compact):
        fail("invalid SUCCESS commit time")

    identity = subprocess.run(
        [
            "bash",
            str(args.identity_validator),
            str(backup_dir / "previous-release.env"),
            str(args.release_root),
            str(args.repository_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.rstrip("\n").split("\t")
    if len(identity) < 3 or identity[1:3] != [manifest["FROM_TAG"], manifest["FROM_COMMIT"]]:
        fail("previous release identity does not match the backup manifest")

    release = json.loads((backup_dir / "release.json").read_text(encoding="utf-8"))
    if not isinstance(release, dict) or set(release) != {
        "tag",
        "commit",
        "slot",
        "previous_slot",
        "api_image",
        "web_image",
        "postgres_source_digest",
        "postgres_mirror_digest",
        "caddy_source_digest",
        "caddy_mirror_digest",
        "switched_at",
    }:
        fail("release.json does not use the fixed deployment schema")
    if release.get("tag") != manifest["TO_TAG"] or release.get("commit") != manifest["TO_COMMIT"]:
        fail("release.json does not match the backup manifest")
    if release.get("slot") not in {"blue", "green"}:
        fail("release.json contains an invalid target slot")
    if release.get("previous_slot") not in {"blue", "green"}:
        fail("release.json contains an invalid previous slot")
    if release["slot"] == release["previous_slot"]:
        fail("release.json target and previous slots must differ")
    if not IMAGE.fullmatch(str(release.get("api_image", ""))):
        fail("release.json contains an invalid API image")
    if not IMAGE.fullmatch(str(release.get("web_image", ""))):
        fail("release.json contains an invalid web image")
    if release["postgres_source_digest"] != POSTGRES_SOURCE_DIGEST:
        fail("release.json contains an unapproved PostgreSQL source digest")
    if release["postgres_mirror_digest"] != POSTGRES_SOURCE_DIGEST:
        fail("release.json PostgreSQL mirror does not match its source digest")
    if release["caddy_source_digest"] != CADDY_SOURCE_DIGEST:
        fail("release.json contains an unapproved Caddy source digest")
    if release["caddy_mirror_digest"] != CADDY_SOURCE_DIGEST:
        fail("release.json Caddy mirror does not match its source digest")
    switched_compact = str(release.get("switched_at", "")).replace("-", "").replace(":", "")
    if not COMPACT_TIME.fullmatch(switched_compact):
        fail("release.json contains an invalid switch time")
    json.loads((backup_dir / "season-integrity-after-migrate.json").read_text(encoding="utf-8"))
    if not (backup_dir / "core-migrations.txt").read_text(encoding="utf-8").strip():
        fail("core migration audit is empty")

    if not args.metadata_only:
        if args.scratch_root is None:
            fail("--scratch-root is required for a full backup verification")
        args.scratch_root.mkdir(parents=True, exist_ok=True)
        verify_tar(
            backup_dir / "private-media.tar.gz",
            backup_dir / "private-media.files.sha256",
            args.scratch_root,
        )
        verify_tar(
            backup_dir / "archive-staging.tar.gz",
            backup_dir / "archive-staging.files.sha256",
            args.scratch_root,
        )
    print(
        "\t".join(
            (
                str(backup_dir),
                manifest["FROM_TAG"],
                manifest["FROM_COMMIT"],
                manifest["TO_TAG"],
                manifest["TO_COMMIT"],
                transaction_id,
            )
        )
    )


if __name__ == "__main__":
    main()
