#!/usr/bin/env python3
"""Acquire the PKUBA production lock without following links, then exec a command."""

from __future__ import annotations

import argparse
import errno
import fcntl
import os
import stat
import sys
import tempfile
import time
from pathlib import Path


def fail(message: str) -> None:
    raise SystemExit(f"deploy lock error: {message}")


def validate_state_directory(path: Path) -> tuple[int, int]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        fail(f"state directory does not exist: {path}")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        fail("state directory must be a real directory")
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        fail("state directory mode must be 0700")
    test_override = os.environ.get("PKUBA_TEST_ALLOW_NON_ROOT_STATE_DIR") == "1"
    if test_override:
        resolved = path.resolve(strict=True)
        test_root = Path(tempfile.gettempdir()).resolve(strict=True)
        if not resolved.is_relative_to(test_root):
            fail("test-only non-root state directory must be below the system temp root")
    elif metadata.st_uid != 0:
        fail("state directory must be owned by root")
    return metadata.st_dev, metadata.st_ino


def open_lock(state_dir: Path) -> int:
    expected_dev, expected_ino = validate_state_directory(state_dir)
    flags = os.O_RDONLY | os.O_DIRECTORY
    flags |= getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(state_dir, flags)
    directory_metadata = os.fstat(directory_fd)
    if (directory_metadata.st_dev, directory_metadata.st_ino) != (
        expected_dev,
        expected_ino,
    ):
        os.close(directory_fd)
        fail("state directory changed while opening the deployment lock")

    lock_flags = os.O_RDWR | os.O_CREAT
    lock_flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        lock_fd = os.open("deploy.lock", lock_flags, 0o600, dir_fd=directory_fd)
    except OSError as error:
        os.close(directory_fd)
        if error.errno == errno.ELOOP:
            fail("deployment lock must not be a symbolic link")
        raise
    finally:
        try:
            os.close(directory_fd)
        except OSError:
            pass

    metadata = os.fstat(lock_fd)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(lock_fd)
        fail("deployment lock must be a regular file")
    if metadata.st_uid != 0:
        os.close(lock_fd)
        fail("deployment lock must be owned by root")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        os.close(lock_fd)
        fail("deployment lock mode must be 0600")
    if metadata.st_nlink != 1:
        os.close(lock_fd)
        fail("deployment lock must have exactly one hard link")
    os.set_inheritable(lock_fd, True)
    return lock_fd


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if os.geteuid() != 0:
        fail("this command must run as root")
    if not command:
        fail("missing command to execute")

    lock_fd = open_lock(args.state_dir)
    deadline = time.monotonic() + args.timeout
    while True:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                fail("another deployment or restore owns the server lock")
            time.sleep(0.2)

    environment = os.environ.copy()
    environment["PKUBA_DEPLOY_LOCK_HELD"] = "1"
    environment["PKUBA_RECOVERY_LOCK_HELD"] = "1"
    environment["PKUBA_DEPLOY_LOCK_FD"] = str(lock_fd)
    os.execvpe(command[0], command, environment)


if __name__ == "__main__":
    main()
