"""The online state behind the features, and an in-memory implementation.

The in-memory store is used by backtests and tests; `adapters/valkey_state.py` is the same
contract on Valkey for live scoring.
"""

from typing import Protocol

from sentinel_txn_fraud.domain.features import HISTORY_LIMIT, Event, Snapshot, TxnView


class StateStore(Protocol):
    async def snapshot(self, tenant: str, txn: TxnView) -> Snapshot: ...

    async def apply(self, tenant: str, txn: TxnView) -> None: ...


class _Account:
    __slots__ = ("counterparties", "devices", "history", "ips")

    def __init__(self) -> None:
        self.history: list[Event] = []
        self.counterparties: set[str] = set()
        self.devices: dict[str, float] = {}
        self.ips: set[str] = set()


class MemoryState:
    def __init__(self) -> None:
        self._accounts: dict[tuple[str, str], _Account] = {}
        self._device_accounts: dict[tuple[str, str], set[str]] = {}

    def snapshot_sync(self, tenant: str, txn: TxnView) -> Snapshot:
        account = self._accounts.get((tenant, txn.account))
        if account is None:
            account = _Account()
        on_device = self._device_accounts.get((tenant, txn.device), set()) if txn.device else set()
        return Snapshot(
            history=account.history,
            counterparty_known=txn.counterparty in account.counterparties,
            device_first_seen=account.devices.get(txn.device) if txn.device else None,
            known_devices=len(account.devices),
            device_accounts=len(on_device | {txn.account}) if txn.device else 0,
            ip_known=txn.ip in account.ips,
            known_ips=len(account.ips),
        )

    def apply_sync(self, tenant: str, txn: TxnView) -> None:
        account = self._accounts.setdefault((tenant, txn.account), _Account())
        account.history.insert(0, Event.of(txn))
        del account.history[HISTORY_LIMIT:]
        if txn.counterparty:
            account.counterparties.add(txn.counterparty)
        if txn.device:
            account.devices.setdefault(txn.device, txn.ts.timestamp())
            self._device_accounts.setdefault((tenant, txn.device), set()).add(txn.account)
        if txn.ip:
            account.ips.add(txn.ip)

    async def snapshot(self, tenant: str, txn: TxnView) -> Snapshot:
        return self.snapshot_sync(tenant, txn)

    async def apply(self, tenant: str, txn: TxnView) -> None:
        self.apply_sync(tenant, txn)
