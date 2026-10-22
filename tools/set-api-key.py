#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Save the GLM API key in a local file outside the repository.

The key is read with a hidden prompt and written with mode 600. The report
tool resolves it automatically; the key is never printed, committed, or
included in a report.
"""
from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path

DEFAULT_PATH = Path(os.getenv("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "paxel-test" / "glm_api_key"


def main() -> int:
    parser = argparse.ArgumentParser(description="Store the GLM API key locally (mode 600, outside the repository)")
    parser.add_argument("--path", type=Path, default=None,
                        help=f"key file path (default: {DEFAULT_PATH})")
    args = parser.parse_args()

    target = (args.path or DEFAULT_PATH).expanduser()
    key = getpass.getpass("GLM API key (input hidden): ").strip()
    if not key:
        print("No key entered; nothing written.")
        return 2

    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.write_text(key + "\n", encoding="utf-8")
    try:
        os.chmod(target, 0o600)
        os.chmod(target.parent, 0o700)
    except OSError:
        pass

    print(f"Saved to {target}")
    print("Mode 600, outside the repository. The report tool reads it automatically.")
    print("Do not commit this file or paste the key into a public issue.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
