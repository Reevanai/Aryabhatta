"""The generated world: entities, the transaction ledger, and labels."""

import math
import random
from bisect import bisect
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, timezone
from itertools import accumulate
from typing import Any

from sentinel_synth.config import SynthConfig

IST = timezone(timedelta(hours=5, minutes=30))
# Share of activity by hour of day (IST) for ordinary customers.
_HOUR_WEIGHTS = (
    1,
    1,
    1,
    1,
    1,
    2,
    5,
    12,
    22,
    34,
    44,
    50,
    48,
    42,
    40,
    42,
    46,
    52,
    56,
    50,
    38,
    24,
    10,
    3,
)
_HOUR_CUM = list(accumulate(_HOUR_WEIGHTS))
EXTERNAL_BANKS = tuple(f"XSYN{n:07d}" for n in range(1, 41))
VPA_HANDLES = ("synpay", "demoupi", "mockbank", "testpsp")


def nearest(value: float, step: int) -> int:
    """Round to the nearest multiple of `step`, as an integer."""
    return round(value / step) * step


@dataclass
class Branch:
    code: str
    name: str


@dataclass
class Party:
    customer_id: str
    kind: str  # individual | organisation
    segment: str
    name: str
    pan: str
    phone: str
    email: str
    address: str
    dob: date
    risk: str
    onboarded: date
    branch: int
    monthly_income: int = 0  # rupees
    devices: list[str] = field(default_factory=list)
    net_prefix: str = ""
    account: str = ""  # main operating account
    contacts: list[int] = field(default_factory=list)


@dataclass
class Account:
    number: str
    party: int
    branch: int
    product: str
    status: str
    opened: date
    base_balance: int = 0  # paise
    opening_balance: int = 0  # paise, set when the ledger is finalised


@dataclass
class Device:
    device_id: str
    customer_id: str
    platform: str
    os_version: str
    rooted: bool
    first_seen: datetime
    last_seen: datetime


@dataclass
class Loan:
    loan_id: str
    customer_id: str
    account_number: str
    product: str
    principal: int  # rupees
    annual_rate: float
    tenure_months: int
    disbursed_on: date
    emi: int  # rupees
    status: str = "active"
    days_past_due: int = 0
    disbursement_account: str = ""


@dataclass
class Txn:
    ts: datetime
    account: str
    channel: str
    direction: str
    amount: int  # paise
    cp_account: str | None = None
    cp_vpa: str | None = None
    cp_bank: str | None = None
    device: str | None = None
    ip: str | None = None
    mcc: str | None = None
    status: str = "completed"
    branch_code: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)
    labels: list[tuple[str, str]] = field(default_factory=list)
    seq: int = 0
    ref: str = ""


@dataclass
class Scenario:
    scenario_id: str
    type: str
    is_fraud: bool
    description: str
    start: datetime
    end: datetime
    parties: dict[str, str] = field(default_factory=dict)  # customer_id -> role
    accounts: dict[str, str] = field(default_factory=dict)  # account number -> role
    details: dict[str, Any] = field(default_factory=dict)


# Roles that mark a party as a victim rather than a perpetrator.
VICTIM_ROLES = frozenset({"victim"})


class World:
    def __init__(self, config: SynthConfig) -> None:
        self.config = config
        self.rng = random.Random(config.seed)
        self.end = datetime.combine(config.end_date, time(23, 59, 59), IST)
        self.start = datetime.combine(
            config.end_date - timedelta(days=config.days - 1), time(0), IST
        )
        self.branches: list[Branch] = []
        self.parties: list[Party] = []
        self.accounts: dict[str, Account] = {}
        self.devices: list[Device] = []
        self.loans: list[Loan] = []
        self.txns: list[Txn] = []
        self.scenarios: list[Scenario] = []
        self._seq = 0
        self._utr = 0
        self._ids: dict[str, int] = {}
        self._merchants: list[tuple[str, str]] = []

    # Identifiers ---------------------------------------------------------------

    def next_id(self, kind: str) -> int:
        self._ids[kind] = self._ids.get(kind, 0) + 1
        return self._ids[kind]

    def utr(self) -> str:
        self._utr += 1
        return f"U{self._utr:012d}"

    def external_account(self) -> tuple[str, str]:
        return f"9{self.rng.randrange(10**13):013d}", self.rng.choice(EXTERNAL_BANKS)

    def external_vpa(self, stem: str = "user") -> str:
        return f"{stem}{self.rng.randrange(10**6)}@{self.rng.choice(VPA_HANDLES)}"

    def merchant(self) -> tuple[str, str]:
        """A (vpa, merchant category) pair from a fixed pool, so merchants recur."""
        if not self._merchants:
            from sentinel_synth.names import MERCHANT_KIND

            pool = max(200, self.config.customers // 3)
            for n in range(pool):
                kind, mcc = MERCHANT_KIND[n % len(MERCHANT_KIND)]
                self._merchants.append((f"{kind}{n}@{VPA_HANDLES[n % len(VPA_HANDLES)]}", mcc))
        # Popularity is skewed: a few merchants take most payments.
        index = min(int(self.rng.paretovariate(1.2)) - 1, len(self._merchants) - 1)
        return self._merchants[index]

    # Random helpers -------------------------------------------------------------

    def poisson(self, lam: float) -> int:
        if lam <= 0:
            return 0
        if lam > 30:
            return max(0, round(self.rng.gauss(lam, math.sqrt(lam))))
        limit, k, p = math.exp(-lam), 0, 1.0
        while True:
            p *= self.rng.random()
            if p <= limit:
                return k
            k += 1

    def amount(self, median: float, sigma: float, low: float = 1.0, high: float = 1e9) -> int:
        """A log-normal rupee amount, returned in paise."""
        value = min(max(self.rng.lognormvariate(math.log(median), sigma), low), high)
        return round(value * 100)

    def when(self, day_from: datetime | None = None, day_to: datetime | None = None) -> datetime:
        """A timestamp in the window, with a realistic hour-of-day distribution."""
        first = (day_from or self.start).date()
        last = (day_to or self.end).date()
        day = first + timedelta(days=self.rng.randrange((last - first).days + 1))
        hour = bisect(_HOUR_CUM, self.rng.random() * _HOUR_CUM[-1])
        return datetime.combine(
            day, time(hour, self.rng.randrange(60), self.rng.randrange(60)), IST
        )

    def clamp(self, ts: datetime) -> datetime:
        return min(max(ts, self.start), self.end)

    def ip_for(self, party: Party) -> str:
        return f"{party.net_prefix}:{self.rng.randrange(1, 5):x}"

    def foreign_ip(self) -> str:
        return f"2001:db8:{self.rng.randrange(0xF000, 0xFFFF):x}:{self.rng.randrange(0xFFFF):x}::{self.rng.randrange(1, 255):x}"

    # Ledger ----------------------------------------------------------------------

    def add(self, txn: Txn) -> Txn:
        self._seq += 1
        txn.seq = self._seq
        txn.ts = self.clamp(txn.ts)
        self.txns.append(txn)
        return txn

    def transfer(
        self,
        ts: datetime,
        src: str,
        dst: str,
        amount: int,
        channel: str,
        *,
        device: str | None = None,
        ip: str | None = None,
        status: str = "completed",
        attrs: dict[str, Any] | None = None,
    ) -> tuple[Txn, Txn]:
        """A transfer between two accounts of this institution: a debit leg and a credit leg
        that share a reference (`utr`)."""
        shared = {"utr": self.utr(), **(attrs or {})}
        debit = self.add(
            Txn(
                ts,
                src,
                channel,
                "debit",
                amount,
                cp_account=dst,
                device=device,
                ip=ip,
                status=status,
                attrs=dict(shared),
            )
        )
        credit = self.add(
            Txn(
                ts + timedelta(seconds=self.rng.randrange(1, 20)),
                dst,
                channel,
                "credit",
                amount,
                cp_account=src,
                status=status,
                attrs=dict(shared),
            )
        )
        return debit, credit

    def pay_out(
        self,
        ts: datetime,
        account: str,
        amount: int,
        channel: str,
        *,
        vpa: str | None = None,
        to_account: tuple[str, str] | None = None,
        device: str | None = None,
        ip: str | None = None,
        mcc: str | None = None,
        status: str = "completed",
        attrs: dict[str, Any] | None = None,
    ) -> Txn:
        """A payment to someone outside this institution."""
        return self.add(
            Txn(
                ts,
                account,
                channel,
                "debit",
                amount,
                cp_vpa=vpa,
                cp_account=to_account[0] if to_account else None,
                cp_bank=to_account[1]
                if to_account
                else (self.rng.choice(EXTERNAL_BANKS) if vpa else None),
                device=device,
                ip=ip,
                mcc=mcc,
                status=status,
                attrs=attrs or {},
            )
        )

    def pay_in(
        self,
        ts: datetime,
        account: str,
        amount: int,
        channel: str,
        *,
        vpa: str | None = None,
        from_account: tuple[str, str] | None = None,
        status: str = "completed",
        attrs: dict[str, Any] | None = None,
    ) -> Txn:
        """A payment received from outside this institution."""
        return self.add(
            Txn(
                ts,
                account,
                channel,
                "credit",
                amount,
                cp_vpa=vpa,
                cp_account=from_account[0] if from_account else None,
                cp_bank=from_account[1]
                if from_account
                else (self.rng.choice(EXTERNAL_BANKS) if vpa else None),
                status=status,
                attrs=attrs or {},
            )
        )

    def cash(
        self,
        ts: datetime,
        account: str,
        direction: str,
        amount: int,
        *,
        branch: int | None = None,
        channel: str = "cash",
        attrs: dict[str, Any] | None = None,
    ) -> Txn:
        code = self.branches[branch].code if branch is not None else None
        return self.add(
            Txn(ts, account, channel, direction, amount, branch_code=code, attrs=attrs or {})
        )

    # Labels ------------------------------------------------------------------------

    def scenario(self, kind: str, description: str, *, is_fraud: bool = True) -> Scenario:
        number = self.next_id("scenario")
        made = Scenario(f"SCN-{number:04d}", kind, is_fraud, description, self.end, self.start)
        self.scenarios.append(made)
        return made

    def label(self, scenario: Scenario, role: str, *txns: Txn) -> None:
        for txn in txns:
            txn.labels.append((scenario.scenario_id, role))
            scenario.start = min(scenario.start, txn.ts)
            scenario.end = max(scenario.end, txn.ts)

    # Finalisation ---------------------------------------------------------------------

    def finalise(self) -> None:
        """Order the ledger, assign references, and set opening balances so that no deposit
        account goes negative. Records each completed transaction's resulting balance."""
        self.txns.sort(key=lambda t: (t.ts, t.seq))
        low: dict[str, int] = {}
        running: dict[str, int] = {}
        for txn in self.txns:
            if txn.status != "completed":
                continue
            delta = txn.amount if txn.direction == "credit" else -txn.amount
            running[txn.account] = running.get(txn.account, 0) + delta
            low[txn.account] = min(low.get(txn.account, 0), running[txn.account])
        for number, account in self.accounts.items():
            if account.product in {"loan", "overdraft"}:
                account.opening_balance = 0
            else:
                account.opening_balance = max(account.base_balance, -low.get(number, 0) + 50_000)
        balance = {number: account.opening_balance for number, account in self.accounts.items()}
        for index, txn in enumerate(self.txns, start=1):
            txn.ref = f"SYN{index:010d}"
            if txn.status == "completed":
                balance[txn.account] += txn.amount if txn.direction == "credit" else -txn.amount
            if self.accounts[txn.account].product not in {"loan", "overdraft"}:
                txn.attrs["balance_after"] = f"{balance[txn.account] / 100:.2f}"

    def as_utc(self, ts: datetime) -> str:
        return ts.astimezone(UTC).isoformat()
