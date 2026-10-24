from __future__ import annotations

import hashlib
import hmac
import sys
from pathlib import Path


def mac_for(key_path: Path, cipher_path: Path) -> str:
    key = key_path.read_bytes().strip()
    mac_key = hmac.new(key, b"JopesVPN backup MAC key v1", hashlib.sha256).digest()
    mac = hmac.new(mac_key, digestmod=hashlib.sha256)
    with cipher_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1_048_576), b""):
            mac.update(chunk)
    return mac.hexdigest()


def main() -> int:
    if len(sys.argv) != 4:
        return 2
    action, key_name, cipher_name = sys.argv[1:4]
    digest = mac_for(Path(key_name), Path(cipher_name))
    if action == "mac":
        print(digest)
        return 0
    if action == "verify":
        expected = Path(sys.argv[3] + ".hmac").read_text(encoding="ascii").strip()
        return 0 if hmac.compare_digest(digest, expected) else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
