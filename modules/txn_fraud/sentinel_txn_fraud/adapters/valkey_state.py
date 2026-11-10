"""Online feature state on Valkey. One round trip to read, one to write.

Keys are prefixed per tenant. The store holds tokens and numbers only, and can be rebuilt by
replaying the transaction topic.
"""

import json
from typing import Any

from sentinel_txn_fraud.domain.features import HISTORY_DAYS, HISTORY_LIMIT, Event, Snapshot, TxnView

HISTORY_TTL = (HISTORY_DAYS + 5) * 86_400
SET_TTL = 180 * 86_400
SEEN_TTL = 7 * 86_400


def _keys(tenant: str, txn: TxnView) -> dict[str, str]:
    base = f"t:{tenant}:"
    return {
        "history": f"{base}ev:{txn.account}",
        "counterparties": f"{base}cp:{txn.account}",
        "devices": f"{base}dv:{txn.account}",
        "ips": f"{base}ip:{txn.account}",
        "device_accounts": f"{base}da:{txn.device}",
    }


def build_state(settings: Any) -> Any:
    """Valkey when configured; otherwise in-memory state, which is per process and lost on
    restart, so only suitable for tests and single-process development."""
    from sentinel_txn_fraud.domain.state import MemoryState

    if not settings.valkey_url:
        return MemoryState()
    import valkey.asyncio as valkey

    client = valkey.from_url(settings.valkey_url, decode_responses=True)  # type: ignore[no-untyped-call]
    return ValkeyState(client)


class ValkeyState:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def snapshot(self, tenant: str, txn: TxnView) -> Snapshot:
        k = _keys(tenant, txn)
        pipe = self._client.pipeline(transaction=False)
        pipe.lrange(k["history"], 0, HISTORY_LIMIT - 1)
        pipe.sismember(k["counterparties"], txn.counterparty or "")
        pipe.hget(k["devices"], txn.device or "")
        pipe.hlen(k["devices"])
        pipe.scard(k["device_accounts"])
        pipe.sismember(k["device_accounts"], txn.account)
        pipe.sismember(k["ips"], txn.ip or "")
        pipe.scard(k["ips"])
        (
            history,
            cp_known,
            first_seen,
            devices,
            on_device,
            member,
            ip_known,
            ips,
        ) = await pipe.execute()
        return Snapshot(
            history=[Event.unpack(json.loads(raw)) for raw in history],
            counterparty_known=bool(cp_known) and txn.counterparty is not None,
            device_first_seen=float(first_seen) if first_seen is not None and txn.device else None,
            known_devices=int(devices),
            device_accounts=(int(on_device) + (0 if member else 1)) if txn.device else 0,
            ip_known=bool(ip_known) and txn.ip is not None,
            known_ips=int(ips),
        )

    async def apply(self, tenant: str, txn: TxnView) -> None:
        k = _keys(tenant, txn)
        pipe = self._client.pipeline(transaction=False)
        pipe.lpush(k["history"], json.dumps(Event.of(txn).pack(), separators=(",", ":")))
        pipe.ltrim(k["history"], 0, HISTORY_LIMIT - 1)
        pipe.expire(k["history"], HISTORY_TTL)
        if txn.counterparty:
            pipe.sadd(k["counterparties"], txn.counterparty)
            pipe.expire(k["counterparties"], SET_TTL)
        if txn.device:
            pipe.hsetnx(k["devices"], txn.device, txn.ts.timestamp())
            pipe.expire(k["devices"], SET_TTL)
            pipe.sadd(k["device_accounts"], txn.account)
            pipe.expire(k["device_accounts"], SET_TTL)
        if txn.ip:
            pipe.sadd(k["ips"], txn.ip)
            pipe.expire(k["ips"], SET_TTL)
        await pipe.execute()

    async def first_time(self, tenant: str, transaction_id: str) -> bool:
        """True the first time a transaction id is seen. Makes stream scoring safe to replay."""
        return bool(
            await self._client.set(f"t:{tenant}:seen:{transaction_id}", 1, nx=True, ex=SEEN_TTL)
        )

    async def forget(self, tenant: str, transaction_id: str) -> None:
        await self._client.delete(f"t:{tenant}:seen:{transaction_id}")
