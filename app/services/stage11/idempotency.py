from __future__ import annotations


def stage11_idempotency_key(
    *,
    client_id: int,
    signal_id: int | None,
    policy_version: str,
    side: str,
    size_bucket: str,
) -> str:
    sid = int(signal_id or 0)
    return f"{int(client_id)}:{sid}:{str(policy_version).strip()}:{str(side).strip().upper()}:{str(size_bucket).strip().lower()}"

