"""Injected fraud scenarios and legitimate look-alikes, all labelled.

Each scenario records the parties and accounts involved and labels every transaction it
creates with a role. Look-alikes are ordinary activity that shares a surface feature with a
fraud pattern; they are labelled `is_fraud = false` so detection precision can be measured.
"""

from datetime import date, datetime, time, timedelta

from sentinel_synth import names
from sentinel_synth.behaviour import disburse, instalment
from sentinel_synth.population import link_device, new_account, new_device, new_loan, new_party
from sentinel_synth.world import IST, Loan, Scenario, Txn, World, nearest

LAKH = 100_000 * 100  # paise
SCAM_TYPES = (
    "investment",
    "impersonation_of_authority",
    "job_task",
    "parcel_customs",
    "kyc_update",
)


class Injector:
    def __init__(self, world: World) -> None:
        self.w = world
        self.rng = world.rng
        self.used: set[int] = set()
        self.scam_mules: list[int] = []
        self.attacker_devices: list[str] = []

    # Building blocks ---------------------------------------------------------------

    def anchor(self, lead_days: int = 7, tail_days: int = 6, night: bool = False) -> datetime:
        w = self.w
        tail = min(tail_days, max(1, w.config.days // 5))
        lead = min(lead_days, max(1, w.config.days // 4))
        ts = w.when(w.start + timedelta(days=lead), w.end - timedelta(days=tail))
        if night:
            ts = ts.replace(hour=self.rng.randrange(0, 5))
        return ts

    def after(self, ts: datetime, low_minutes: float, high_minutes: float) -> datetime:
        return ts + timedelta(seconds=self.rng.uniform(low_minutes, high_minutes) * 60)

    def existing(self, segments: tuple[str, ...], *, min_income: int = 0) -> int:
        candidates = [
            i
            for i, p in enumerate(self.w.parties)
            if i not in self.used
            and p.segment in segments
            and p.devices
            and p.account
            and p.monthly_income >= min_income
            and self.w.accounts[p.account].product != "loan"
        ]
        if not candidates:
            raise RuntimeError(f"no unused customer in segments {segments}; increase `customers`")
        chosen = self.rng.choice(candidates)
        self.used.add(chosen)
        return chosen

    def fresh(
        self,
        before: datetime,
        *,
        kind: str = "individual",
        segment: str | None = None,
        days_before: tuple[int, int] = (5, 40),
        product: str | None = None,
        season: bool = True,
    ) -> int:
        """A newly onboarded customer with an account, a device and a little activity."""
        w, rng = self.w, self.rng
        onboarded = (before - timedelta(days=rng.randrange(*days_before))).date()
        if segment is None and kind == "individual":
            segment = rng.choice(["student", "homemaker", "self_employed", "salaried"])
        index = new_party(w, kind=kind, segment=segment, onboarded=onboarded)
        party = w.parties[index]
        product = product or ("current" if kind == "organisation" else "savings")
        party.account = new_account(w, index, product, base_balance=rng.randrange(500, 6_000) * 100)
        opened = datetime.combine(onboarded, time(11), IST)
        new_device(w, index, first_seen=opened + timedelta(hours=rng.randrange(1, 48)))
        self.used.add(index)
        if season:
            self.season(index, max(opened, w.start), before)
        return index

    def season(
        self, index: int, start: datetime, end: datetime, count: int | None = None
    ) -> list[Txn]:
        """A few small ordinary payments, so the account is not completely empty."""
        w, rng = self.w, self.rng
        party = w.parties[index]
        made: list[Txn] = []
        if end <= start:
            return made
        for _ in range(count if count is not None else rng.randrange(2, 9)):
            ts = start + (end - start) * rng.random()
            vpa, mcc = w.merchant()
            made.append(
                w.pay_out(
                    ts,
                    party.account,
                    w.amount(180, 0.6, 10, 900),
                    "upi",
                    vpa=vpa,
                    device=party.devices[0],
                    ip=w.ip_for(party),
                    mcc=mcc,
                )
            )
        return made

    def mule(self, before: datetime) -> int:
        if self.rng.random() < 0.6:
            return self.fresh(before)
        return self.existing(("student", "homemaker", "farmer", "self_employed"))

    def enrol(self, scenario: Scenario, index: int, role: str) -> None:
        party = self.w.parties[index]
        scenario.parties[party.customer_id] = role
        if party.account:
            scenario.accounts[party.account] = role

    def send(
        self, ts: datetime, src: int, dst: int, amount: int, channel: str | None = None
    ) -> tuple[Txn, Txn]:
        w = self.w
        sender = w.parties[src]
        channel = channel or self.rng.choice(["imps", "upi"])
        if amount >= 2 * LAKH and channel == "neft" and self.rng.random() < 0.5:
            channel = "rtgs"
        return w.transfer(
            ts,
            sender.account,
            w.parties[dst].account,
            amount,
            channel,
            device=sender.devices[0] if sender.devices else None,
            ip=w.ip_for(sender) if sender.devices else None,
        )

    def cash_out(self, ts: datetime, index: int, amount: int) -> list[Txn]:
        """Take the money out: ATM withdrawals in several pulls, with whatever the pulls cannot
        cover sent to an account elsewhere; or the whole amount sent elsewhere."""
        w, rng = self.w, self.rng
        party = w.parties[index]
        device = party.devices[0] if party.devices else None
        ip = w.ip_for(party) if party.devices else None
        made: list[Txn] = []
        left = amount
        if rng.random() < 0.55:
            while left >= 500_00 and len(made) < 8:
                take = min(
                    left // 100_00 * 100_00, rng.choice([5_000, 10_000, 10_000, 20_000]) * 100
                )
                ts = self.after(ts, 1, 12)
                made.append(w.cash(ts, party.account, "debit", take, attrs={"instrument": "atm"}))
                left -= take
        if left >= 100_00:
            made.append(
                w.pay_out(
                    self.after(ts, 2, 30),
                    party.account,
                    left,
                    "imps",
                    to_account=w.external_account(),
                    device=device,
                    ip=ip,
                )
            )
        return made

    # Fraud scenarios ---------------------------------------------------------------

    def mule_chain(self) -> None:
        w, rng = self.w, self.rng
        t0 = self.anchor()
        chain = [self.mule(t0) for _ in range(rng.randrange(2, 5))]
        sc = w.scenario(
            "mule_chain", f"Victim funds passed through {len(chain)} mule accounts and cashed out"
        )
        for index in chain:
            self.enrol(sc, index, "mule")
        victims = rng.randrange(3, 9)
        for _ in range(victims):
            ts = self.after(t0, 0, 36 * 60)
            amount = rng.randrange(5_000, 95_000) * 100
            first = w.parties[chain[0]]
            if rng.random() < 0.6:
                inbound = w.pay_in(ts, first.account, amount, "upi", vpa=w.external_vpa("payer"))
            else:
                inbound = w.pay_in(
                    ts, first.account, amount, "imps", from_account=w.external_account()
                )
            w.label(sc, "mule_in", inbound)
            for hop in range(len(chain) - 1):
                ts = self.after(ts, 2, 30)
                amount = nearest(amount * rng.uniform(0.93, 0.99), 100)
                w.label(sc, "mule_hop", *self.send(ts, chain[hop], chain[hop + 1], amount))
            w.label(
                sc,
                "cash_out",
                *self.cash_out(self.after(ts, 5, 60), chain[-1], nearest(amount * 0.97, 100)),
            )
        sc.details = {
            "chain": [w.parties[i].customer_id for i in chain],
            "victim_payments": victims,
        }

    def app_scam(self) -> None:
        w, rng = self.w, self.rng
        t0 = self.anchor().replace(hour=rng.randrange(10, 20))
        victim = self.existing(
            ("salaried", "pensioner", "self_employed", "homemaker"), min_income=10_000
        )
        party = w.parties[victim]
        kind = rng.choice(SCAM_TYPES)
        sc = w.scenario("app_scam", f"Customer deceived into sending payments ({kind})")
        self.enrol(sc, victim, "victim")
        mule: int | None = None
        external = w.external_account()
        if rng.random() < 0.5:
            if self.scam_mules and rng.random() < 0.5:
                mule = rng.choice(self.scam_mules)
            else:
                mule = self.mule(t0)
                self.scam_mules.append(mule)
            self.enrol(sc, mule, "mule")
        amount = nearest(party.monthly_income * rng.uniform(0.3, 0.8), 100) * 100
        ts = t0
        payments = rng.randrange(1, 5)
        for _ in range(payments):
            if mule is not None:
                legs = self.send(ts, victim, mule, amount, rng.choice(["upi", "imps"]))
                w.label(sc, "victim_payment", *legs)
                out = w.pay_out(
                    self.after(ts, 10, 40),
                    w.parties[mule].account,
                    nearest(amount * 0.95, 100),
                    "imps",
                    to_account=w.external_account(),
                    device=w.parties[mule].devices[0],
                    ip=w.ip_for(w.parties[mule]),
                )
                w.label(sc, "mule_out", out)
            else:
                w.label(
                    sc,
                    "victim_payment",
                    w.pay_out(
                        ts,
                        party.account,
                        amount,
                        rng.choice(["upi", "imps"]),
                        to_account=external,
                        device=party.devices[0],
                        ip=w.ip_for(party),
                    ),
                )
            amount = nearest(amount * rng.uniform(1.3, 2.0), 10000)
            ts = self.after(ts, 30, 30 * 60)
        sc.details = {
            "scam_type": kind,
            "payments": payments,
            "via_internal_mule": mule is not None,
        }

    def account_takeover(self) -> None:
        w, rng = self.w, self.rng
        # Most takeovers happen while the customer is asleep, but not all.
        t0 = self.anchor(night=rng.random() < 0.65)
        victim = self.existing(
            ("salaried", "self_employed", "trader", "pensioner"), min_income=20_000
        )
        party = w.parties[victim]
        sc = w.scenario(
            "account_takeover",
            "Account operated from an unfamiliar device and network, then drained",
        )
        self.enrol(sc, victim, "victim")
        if self.attacker_devices and rng.random() < 0.5:
            device = rng.choice(self.attacker_devices)
            link_device(w, device, victim, t0)
        else:
            device = new_device(w, victim, first_seen=t0, rooted=rng.random() < 0.5)
            self.attacker_devices.append(device)
        # The attacker's device is appended last; the victim's own stays first.
        ip = w.foreign_ip()
        made = [
            w.pay_out(t0, party.account, 100, "upi", vpa=w.external_vpa(), device=device, ip=ip)
        ]
        beneficiaries = [w.external_account() for _ in range(rng.randrange(2, 5))]
        budget = round(party.monthly_income * rng.uniform(1.0, 3.0)) * 100
        ts = t0
        count = rng.randrange(3, 9)
        for _ in range(count):
            ts = self.after(ts, 1, 9)
            made.append(
                w.pay_out(
                    ts,
                    party.account,
                    nearest(budget / count * rng.uniform(0.6, 1.4), 100),
                    rng.choice(["imps", "upi"]),
                    to_account=rng.choice(beneficiaries),
                    device=device,
                    ip=ip,
                )
            )
        w.label(sc, "ato_transfer", *made)
        sc.details = {"attacker_device": device, "transfers": count}

    def layering(self) -> None:
        w, rng = self.w, self.rng
        t0 = self.anchor(tail_days=8)
        shell = self.fresh(t0, kind="organisation", days_before=(10, 60))
        sc = w.scenario(
            "layering", "Large inflow split across accounts, moved through layers, and sent out"
        )
        self.enrol(sc, shell, "shell")
        total = rng.randrange(8, 40) * LAKH
        w.label(
            sc,
            "layer_in",
            w.pay_in(
                t0, w.parties[shell].account, total, "rtgs", from_account=w.external_account()
            ),
        )
        layer1 = [
            self.fresh(t0, kind=rng.choice(["individual", "organisation"]))
            for _ in range(rng.randrange(4, 8))
        ]
        layer2 = [
            self.fresh(t0, kind=rng.choice(["individual", "organisation"]))
            for _ in range(rng.randrange(2, 5))
        ]
        for index in layer1 + layer2:
            self.enrol(sc, index, "layer")
        exits = [w.external_account() for _ in range(rng.randrange(1, 3))]
        weights = [rng.uniform(0.5, 1.5) for _ in layer1]
        held: dict[int, int] = {}
        arrived: dict[int, datetime] = {}
        for index, weight in zip(layer1, weights, strict=True):
            amount = nearest(total * 0.99 * weight / sum(weights), 100)
            ts = self.after(t0, 60, 30 * 60)
            w.label(sc, "layer_hop", *self.send(ts, shell, index, amount, "neft"))
            parts = rng.randrange(1, 3)
            for target in rng.sample(layer2, min(parts, len(layer2))):
                share = nearest(amount * rng.uniform(0.96, 0.99) / parts, 100)
                hop_ts = self.after(ts, 3 * 60, 48 * 60)
                w.label(sc, "layer_hop", *self.send(hop_ts, index, target, share, "neft"))
                held[target] = held.get(target, 0) + share
                arrived[target] = max(arrived.get(target, hop_ts), hop_ts)
        for target in layer2:
            if not held.get(target):
                continue
            ts = arrived[target]
            party = w.parties[target]
            w.label(
                sc,
                "layer_out",
                w.pay_out(
                    self.after(ts, 2 * 60, 36 * 60),
                    party.account,
                    nearest(held[target] * 0.99, 100),
                    "neft",
                    to_account=rng.choice(exits),
                    device=party.devices[0],
                    ip=w.ip_for(party),
                ),
            )
        sc.details = {"amount_rupees": total // 100, "layer1": len(layer1), "layer2": len(layer2)}

    def circular_flow(self) -> None:
        w, rng = self.w, self.rng
        cycles = rng.randrange(2, 5)
        t0 = self.anchor(tail_days=min(cycles * 6, w.config.days // 2))
        ring = [
            self.fresh(
                t0,
                kind=rng.choice(["organisation", "individual"]),
                segment=None,
                days_before=(20, 120),
            )
            for _ in range(rng.randrange(3, 7))
        ]
        sc = w.scenario(
            "circular_flow", f"Funds moved in a loop through {len(ring)} accounts, {cycles} times"
        )
        for index in ring:
            self.enrol(sc, index, "cycle_member")
        amount = rng.randrange(2, 15) * LAKH
        w.label(
            sc,
            "cycle_seed",
            w.pay_in(
                self.after(t0, -600, -60),
                w.parties[ring[0]].account,
                amount,
                "neft",
                from_account=w.external_account(),
            ),
        )
        ts = t0
        for _ in range(cycles):
            for position, index in enumerate(ring):
                ts = self.after(ts, 60, 20 * 60)
                amount = nearest(amount * rng.uniform(0.985, 1.0), 100)
                w.label(
                    sc,
                    "cycle_hop",
                    *self.send(ts, index, ring[(position + 1) % len(ring)], amount, "neft"),
                )
            ts = self.after(ts, 2 * 24 * 60, 5 * 24 * 60)
        sc.details = {"members": [w.parties[i].customer_id for i in ring], "cycles": cycles}

    def structuring(self) -> None:
        w, rng = self.w, self.rng
        t0 = self.anchor(tail_days=14)
        owner = self.existing(("trader", "self_employed", "salaried"))
        party = w.parties[owner]
        sc = w.scenario("structuring", "Cash deposited in many amounts kept just under a threshold")
        self.enrol(sc, owner, "structurer")
        targets = [party.account]
        relative = next(
            (c for c in party.contacts if c not in self.used and w.parties[c].account), None
        )
        if relative is not None and rng.random() < 0.5:
            self.used.add(relative)
            self.enrol(sc, relative, "associate")
            targets.append(w.parties[relative].account)
        branches = rng.sample(range(len(w.branches)), min(len(w.branches), rng.randrange(1, 4)))
        threshold = w.config.structuring_threshold
        count = rng.randrange(6, 21)
        span = rng.randrange(5, 16)
        total = 0
        last = t0
        for _ in range(count):
            day = t0 + timedelta(days=rng.randrange(span))
            ts = day.replace(hour=rng.randrange(10, 16), minute=rng.randrange(60))
            amount = nearest(threshold * rng.uniform(0.70, 0.98), 100) * 100
            total += amount
            last = max(last, ts)
            w.label(
                sc,
                "structured_deposit",
                w.cash(ts, rng.choice(targets), "credit", amount, branch=rng.choice(branches)),
            )
        w.label(
            sc,
            "structuring_exit",
            w.pay_out(
                self.after(last, 24 * 60, 72 * 60),
                party.account,
                nearest(total * 0.6, 100),
                "neft",
                to_account=w.external_account(),
                device=party.devices[0],
                ip=w.ip_for(party),
            ),
        )
        sc.details = {
            "deposits": count,
            "threshold_rupees": threshold,
            "total_rupees": total // 100,
        }

    def _share_identity(self, members: list[int]) -> dict[str, list[str]]:
        """Give some members the same phone, address or device. Returns what was shared."""
        w, rng = self.w, self.rng
        shared: dict[str, list[str]] = {"phone": [], "address": [], "device": []}
        lead = w.parties[members[0]]
        for index in members[1:]:
            party = w.parties[index]
            if rng.random() < 0.4:
                party.phone = lead.phone
                shared["phone"].append(party.customer_id)
            if rng.random() < 0.6:
                party.address = lead.address
                shared["address"].append(party.customer_id)
            if rng.random() < 0.7 and lead.devices:
                first_seen = datetime.combine(party.onboarded, time(12), IST)
                link_device(w, lead.devices[0], index, max(first_seen, w.start))
                # Use the shared device first.
                party.devices.insert(0, party.devices.pop())
                shared["device"].append(party.customer_id)
        return shared

    def fraud_ring(self) -> None:
        w, rng = self.w, self.rng
        t0 = self.anchor(lead_days=10, tail_days=10)
        nbfc = w.config.institution == "nbfc"
        size = rng.randrange(5, 11)
        members = [self._ring_member(t0) for _ in range(size)]
        sc = w.scenario("fraud_ring", f"{size} recently opened, linked customers acting together")
        for index in members:
            self.enrol(sc, index, "ring_member")
        shared = self._share_identity(members)
        if nbfc:
            loans = [
                self._bust_out_loan(sc, index, self.after(t0, 0, 5 * 24 * 60)) for index in members
            ]
            sc.details = {"shared": shared, "loans": [loan.loan_id for loan in loans]}
            return
        for _ in range(size * 3):
            a, b = rng.sample(members, 2)
            ts = self.after(t0, -10 * 24 * 60, 6 * 24 * 60)
            w.label(
                sc, "ring_internal", *self.send(ts, a, b, rng.randrange(500, 5_000) * 100, "upi")
            )
        for _ in range(rng.randrange(5, 16)):
            index = rng.choice(members)
            ts = self.after(t0, 0, 6 * 24 * 60)
            amount = rng.randrange(10_000, 80_000) * 100
            w.label(
                sc,
                "ring_in",
                w.pay_in(
                    ts, w.parties[index].account, amount, "imps", from_account=w.external_account()
                ),
            )
            w.label(
                sc,
                "ring_cash_out",
                *self.cash_out(self.after(ts, 5, 90), index, nearest(amount * 0.96, 100)),
            )
        sc.details = {"shared": shared}

    def _ring_member(self, t0: datetime) -> int:
        if self.w.config.institution == "nbfc":
            index = new_party(
                self.w,
                segment=self.rng.choice(["salaried", "self_employed"]),
                onboarded=(t0 - timedelta(days=self.rng.randrange(1, 15))).date(),
            )
            new_device(self.w, index, first_seen=t0 - timedelta(days=1))
            self.used.add(index)
            return index
        return self.fresh(t0, days_before=(16, 30))

    def synthetic_identity(self) -> None:
        w, rng = self.w, self.rng
        t0 = self.anchor(lead_days=10, tail_days=8)
        size = rng.randrange(3, 7)
        dob = date(1988, 1, 1) + timedelta(days=rng.randrange(4_000))
        family = rng.sample(names.FAMILY, 2)
        members = []
        for n in range(size):
            index = self._ring_member(t0)
            party = w.parties[index]
            given = rng.choice(names.GIVEN_M + names.GIVEN_F)
            party.name = f"{given} {family[n % 2]}"
            party.dob = dob if n % 2 == 0 else dob + timedelta(days=rng.randrange(1, 4))
            party.email = f"{given.lower()}.{family[0].lower()}{n + 1}@example.invalid"
            party.risk = "low"
            members.append(index)
        sc = w.scenario(
            "synthetic_identity", f"{size} fabricated identities built from shared attributes"
        )
        for index in members:
            self.enrol(sc, index, "synthetic_identity")
        shared = self._share_identity(members)
        loans = [
            self._bust_out_loan(sc, index, self.after(t0, 0, 4 * 24 * 60)) for index in members
        ]
        sc.details = {
            "shared": shared,
            "loans": [loan.loan_id for loan in loans],
            "date_of_birth_cluster": True,
        }

    def loan_fraud(self) -> None:
        w, rng = self.w, self.rng
        t0 = self.anchor(lead_days=8, tail_days=8)
        borrower = (
            self._ring_member(t0)
            if rng.random() < 0.6 or w.config.institution == "nbfc"
            else self.existing(("student", "homemaker", "self_employed"))
        )
        sc = w.scenario("loan_fraud", "Loan drawn down and moved out at once, with no repayment")
        self.enrol(sc, borrower, "borrower")
        loans = [self._bust_out_loan(sc, borrower, t0)]
        if rng.random() < 0.3:
            loans.append(
                self._bust_out_loan(
                    sc, borrower, self.after(t0, 24 * 60, 6 * 24 * 60), product="gold"
                )
            )
        sc.details = {"loans": [loan.loan_id for loan in loans]}

    def _bust_out_loan(
        self, sc: Scenario, index: int, ts: datetime, product: str = "personal"
    ) -> Loan:
        """Disburse a loan that is emptied straight away and never repaid."""
        w, rng = self.w, self.rng
        party = w.parties[index]
        ts = w.clamp(ts).replace(hour=rng.randrange(10, 17))
        nbfc = w.config.institution == "nbfc"
        external, _bank = w.external_account()
        loan = new_loan(
            w,
            index,
            product,
            ts.date(),
            principal=nearest(rng.randrange(100_000, 400_000), 1000),
            disbursement_account=external if nbfc else party.account,
        )
        if nbfc and not party.account:
            party.account = loan.account_number
        sc.accounts[loan.account_number] = "fraudulent_loan"
        w.label(sc, "loan_disbursement", *disburse(w, loan, party, ts))
        if not nbfc:
            w.label(
                sc,
                "bust_out",
                *self.cash_out(
                    self.after(ts, 30, 24 * 60),
                    index,
                    nearest(loan.principal * 100 * rng.uniform(0.85, 0.98), 100),
                ),
            )
        due = ts + timedelta(days=30)
        missed = 0
        while due <= w.end:
            w.label(sc, "missed_emi", *instalment(w, loan, party, due.replace(hour=8), failed=True))
            missed += 1
            due += timedelta(days=30)
        loan.status = "overdue" if missed else "active"
        loan.days_past_due = (
            max(0, (w.end.date() - (ts.date() + timedelta(days=30))).days) if missed else 0
        )
        return loan

    # Legitimate look-alikes ----------------------------------------------------------

    def hard_negative(self, kind: str) -> None:
        w, rng = self.w, self.rng
        t0 = self.anchor().replace(hour=rng.randrange(10, 18))
        sc = w.scenario(f"lookalike:{kind}", _LOOKALIKES[kind], is_fraud=False)
        if kind == "pass_through":
            index = self.existing(("trader", "self_employed"))
            party = w.parties[index]
            amount = rng.randrange(1, 5) * LAKH
            made = [
                w.pay_in(
                    t0,
                    party.account,
                    amount,
                    "neft",
                    from_account=w.external_account(),
                    attrs={"purpose": "customer_payment"},
                ),
                w.pay_out(
                    self.after(t0, 120, 360),
                    party.account,
                    nearest(amount * rng.uniform(0.85, 0.95), 100),
                    "neft",
                    to_account=w.external_account(),
                    device=party.devices[0],
                    ip=w.ip_for(party),
                    attrs={"purpose": "supplier"},
                ),
            ]
        elif kind == "new_device":
            index = self.existing(("salaried", "student", "self_employed"))
            party = w.parties[index]
            if rng.random() < 0.3:  # set up late at night
                t0 = t0.replace(hour=rng.randrange(0, 5))
            device = new_device(w, index, first_seen=t0)
            made = []
            for n in range(rng.randrange(5, 11)):
                vpa, mcc = w.merchant()
                made.append(
                    w.pay_out(
                        # The first few payments follow soon after the phone is set up.
                        self.after(t0, 2, 90 if n < 3 else 5 * 24 * 60),
                        party.account,
                        w.amount(300, 0.8, 20, 4_000),
                        "upi",
                        vpa=vpa,
                        device=device,
                        ip=w.ip_for(party),
                        mcc=mcc,
                    )
                )
        elif kind == "large_payment":
            index = self.existing(("salaried", "self_employed"), min_income=30_000)
            party = w.parties[index]
            amount = rng.randrange(1, 6) * LAKH
            made = [
                w.pay_in(
                    self.after(t0, -6 * 24 * 60, -24 * 60),
                    party.account,
                    amount,
                    "neft",
                    from_account=w.external_account(),
                    attrs={"purpose": "deposit_maturity"},
                ),
                w.pay_out(
                    t0,
                    party.account,
                    nearest(amount * 0.97, 100),
                    "neft",
                    to_account=w.external_account(),
                    device=party.devices[0],
                    ip=w.ip_for(party),
                    attrs={"purpose": "fees_or_purchase"},
                ),
            ]
        elif kind == "cash_heavy":
            index = self.existing(("trader",))
            party = w.parties[index]
            made = [
                w.cash(
                    self.after(t0, -6 * 24 * 60, 5 * 24 * 60).replace(hour=rng.randrange(10, 16)),
                    party.account,
                    "credit",
                    rng.randrange(100, 450) * 10_000,
                    branch=party.branch,
                )
                for _ in range(rng.randrange(8, 16))
            ]
        else:  # round_trip
            index = self.existing(("salaried", "self_employed", "homemaker"))
            party = w.parties[index]
            friend = next(
                (
                    c
                    for c in party.contacts
                    if c not in self.used and w.parties[c].devices and w.parties[c].account
                ),
                None,
            )
            if friend is None:
                friend = self.existing(("salaried", "self_employed", "homemaker"))
            self.used.add(friend)
            self.enrol(sc, friend, "legitimate")
            amount = rng.randrange(20, 80) * 100_000
            made = [
                *self.send(t0, index, friend, amount, "upi"),
                *self.send(self.after(t0, 3 * 24 * 60, 5 * 24 * 60), friend, index, amount, "upi"),
            ]
        self.enrol(sc, index, "legitimate")
        w.label(sc, "legitimate", *made)


_LOOKALIKES = {
    "pass_through": "Trader receives a large payment and pays a supplier the same day",
    "new_device": "Customer changes phone and carries on as usual",
    "large_payment": "One-off large payment to a new payee, funded days earlier",
    "cash_heavy": "Trader with frequent cash deposits of varied size",
    "round_trip": "Money lent to a friend and returned",
}
LOOKALIKE_KINDS = tuple(_LOOKALIKES)
# Look-alikes that need deposit accounts.
NBFC_LOOKALIKES: tuple[str, ...] = ()


def inject(world: World) -> None:
    injector = Injector(world)
    nbfc = world.config.institution == "nbfc"
    from sentinel_synth.config import BANK_ONLY, SCENARIO_TYPES

    for kind in SCENARIO_TYPES:
        if nbfc and kind in BANK_ONLY:
            continue
        for _ in range(getattr(world.config.scenarios, kind)):
            getattr(injector, kind)()
    kinds = NBFC_LOOKALIKES if nbfc else LOOKALIKE_KINDS
    for n in range(world.config.hard_negatives if kinds else 0):
        injector.hard_negative(kinds[n % len(kinds)])
