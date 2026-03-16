#!/usr/bin/env python3
from __future__ import annotations

import os
import sys


def main() -> int:
    try:
        from py_clob_client.client import ClobClient
    except Exception as exc:  # noqa: BLE001
        print(f"[error] py-clob-client is not available: {exc}", file=sys.stderr)
        print("[hint] install with: .venv/bin/pip install py-clob-client", file=sys.stderr)
        return 1

    private_key = (os.getenv("PRIVATE_KEY") or os.getenv("POLYMARKET_PRIVATE_KEY") or "").strip()
    if not private_key:
        print("[error] missing PRIVATE_KEY (or POLYMARKET_PRIVATE_KEY) in environment.", file=sys.stderr)
        return 1

    host = (os.getenv("POLYMARKET_CLOB_API_BASE_URL") or "https://clob.polymarket.com").strip()
    chain_id_raw = (os.getenv("POLYMARKET_CHAIN_ID") or "137").strip()
    try:
        chain_id = int(chain_id_raw)
    except ValueError:
        print(f"[error] invalid POLYMARKET_CHAIN_ID={chain_id_raw!r}", file=sys.stderr)
        return 1

    try:
        client = ClobClient(host=host, chain_id=chain_id, key=private_key)
        creds = client.create_or_derive_api_creds()
    except Exception as exc:  # noqa: BLE001
        print(f"[error] failed to derive CLOB credentials: {exc}", file=sys.stderr)
        return 1

    api_key = str(creds.api_key if hasattr(creds, "api_key") else creds.get("apiKey", "")).strip()
    api_secret = str(creds.api_secret if hasattr(creds, "api_secret") else creds.get("secret", "")).strip()
    passphrase = str(creds.api_passphrase if hasattr(creds, "api_passphrase") else creds.get("passphrase", "")).strip()

    print("# Add these lines to your .env")
    print("POLYMARKET_CLOB_ENABLED=true")
    print(f"POLYMARKET_CLOB_API_BASE_URL={host}")
    print(f"POLYMARKET_CLOB_API_KEY={api_key}")
    print(f"POLYMARKET_CLOB_API_SECRET={api_secret}")
    print(f"POLYMARKET_CLOB_API_PASSPHRASE={passphrase}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
