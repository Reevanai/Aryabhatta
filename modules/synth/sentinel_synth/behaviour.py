"""Ordinary, legitimate customer activity."""

from datetime import datetime, time, timedelta

from sentinel_synth.world import IST, Loan, Party, Txn, World, nearest

# segment -> expected events per 30 days
RATES: dict[str, dict[str, float]] = {
    "salaried": {"upi_spend": 35, "card": 5, "atm": 3, "bills": 3, "p2p": 4, "cash_dep": 0.2},
    "trader": {
        "upi_collect": 120,
        "upi_spend": 15,
        "cash_dep": 10,
        "supplier": 12,
        "atm": 2,
        "bills": 3,
        "p2p": 3,
    },
    "self_employed": {
        "upi_collect": 20,
        "upi_spend": 25,
        "card": 4,
        "atm": 2,
        "bills": 3,
        "p2p": 4,
        "cash_dep": 2,
    },
    "farmer": {"upi_spend": 4, "aeps": 3, "cash_dep": 1, "atm": 1, "p2p": 1},
    "pensioner": {"upi_spend": 5, "atm": 3, "bills": 2, "p2p": 1},
    "homemaker": {"upi_spend": 15, "p2p": 3, "atm": 1},
    "student": {"upi_spend": 30, "p2p": 5, "atm": 1},
    "organisation": {"upi_collect": 60, "supplier": 20, "cash_dep": 6, "bills": 4},
}


def _months(world: World) -> list[datetime]:
    """First day of each month that overlaps the window."""
    months = []
    cursor = world.start.replace(day=1)
    while cursor <= world.end:
        months.append(cursor)
        cursor = (cursor + timedelta(days=32)).replace(day=1)
    return months


def _status(world: World) -> str:
    return "failed" if world.rng.random() < world.config.failed_rate else "completed"


def _digital(world: World, party: Party) -> tuple[str | None, str | None]:
    if not party.devices:
        return None, None
    return party.devices[0], world.ip_for(party)


def income(world: World, employers: list[int]) -> None:
    rng = world.rng
    months = _months(world)
    for index, party in enumerate(world.parties):
        if party.kind != "individual" or not party.account:
            continue
        if party.segment == "salaried":
            employer = rng.choice(employers) if employers and rng.random() < 0.35 else None
            source = world.external_account()
            day = rng.randrange(1, 8)
            for month in months:
                ts = month.replace(day=day, hour=rng.randrange(9, 18), minute=rng.randrange(60))
                if not world.start <= ts <= world.end:
                    continue
                amount = party.monthly_income * 100
                if employer is not None:
                    world.transfer(
                        ts,
                        world.parties[employer].account,
                        party.account,
                        amount,
                        "neft",
                        attrs={"purpose": "salary"},
                    )
                else:
                    world.pay_in(
                        ts,
                        party.account,
                        amount,
                        "neft",
                        from_account=source,
                        attrs={"purpose": "salary"},
                    )
        elif party.segment == "pensioner":
            source = world.external_account()
            day = rng.randrange(1, 6)
            for month in months:
                ts = month.replace(day=day, hour=rng.randrange(9, 14))
                if world.start <= ts <= world.end:
                    world.pay_in(
                        ts,
                        party.account,
                        party.monthly_income * 100,
                        "neft",
                        from_account=source,
                        attrs={"purpose": "pension"},
                    )
        elif party.segment == "farmer":
            for _ in range(rng.randrange(1, 4)):
                ts = world.when()
                amount = round(party.monthly_income * rng.uniform(1.5, 5)) * 100
                if rng.random() < 0.5:
                    world.pay_in(
                        ts,
                        party.account,
                        amount,
                        "neft",
                        from_account=world.external_account(),
                        attrs={"purpose": "produce_sale"},
                    )
                else:
                    world.cash(
                        ts, party.account, "credit", nearest(amount, 10000), branch=party.branch
                    )
        elif party.segment in {"student", "homemaker"}:
            giver = rng.choice(party.contacts) if party.contacts and rng.random() < 0.5 else None
            source = world.external_account()
            for month in months:
                ts = month.replace(day=rng.randrange(1, 11), hour=rng.randrange(8, 22))
                if not world.start <= ts <= world.end:
                    continue
                amount = party.monthly_income * 100
                if giver is not None and world.parties[giver].account:
                    device, ip = _digital(world, world.parties[giver])
                    world.transfer(
                        ts,
                        world.parties[giver].account,
                        party.account,
                        amount,
                        "upi",
                        device=device,
                        ip=ip,
                        attrs={"purpose": "family"},
                    )
                else:
                    world.pay_in(
                        ts,
                        party.account,
                        amount,
                        "imps",
                        from_account=source,
                        attrs={"purpose": "family"},
                    )
        del index


def spending(world: World) -> None:
    rng = world.rng
    scale = world.config.days / 30
    for party in world.parties:
        if not party.account or world.accounts[party.account].product == "loan":
            continue
        rates = RATES.get(party.segment, RATES["salaried"])
        device, _ = _digital(world, party)
        cap = max(party.monthly_income * 0.25, 500)
        has_digital = device is not None

        def ip(party: Party = party, has_digital: bool = has_digital) -> str | None:
            return world.ip_for(party) if has_digital else None

        if has_digital:
            for _ in range(world.poisson(rates.get("upi_spend", 0) * scale)):
                vpa, mcc = world.merchant()
                world.pay_out(
                    world.when(),
                    party.account,
                    world.amount(280, 0.9, 10, cap),
                    "upi",
                    vpa=vpa,
                    device=device,
                    ip=ip(),
                    mcc=mcc,
                    status=_status(world),
                )
            for _ in range(world.poisson(rates.get("upi_collect", 0) * scale)):
                world.pay_in(
                    world.when(),
                    party.account,
                    world.amount(220, 0.8, 10, 20_000),
                    "upi",
                    vpa=world.external_vpa("payer"),
                )
            for _ in range(world.poisson(rates.get("bills", 0) * scale)):
                vpa, mcc = world.merchant()
                world.pay_out(
                    world.when(),
                    party.account,
                    world.amount(900, 0.6, 100, 8_000),
                    "upi",
                    vpa=vpa,
                    device=device,
                    ip=ip(),
                    mcc="4900",
                    attrs={"purpose": "bill"},
                )
            for _ in range(world.poisson(rates.get("p2p", 0) * scale)):
                amount = world.amount(1_500, 0.9, 50, cap * 2)
                friend = (
                    rng.choice(party.contacts) if party.contacts and rng.random() < 0.5 else None
                )
                if friend is not None and world.parties[friend].account:
                    world.transfer(
                        world.when(),
                        party.account,
                        world.parties[friend].account,
                        amount,
                        rng.choice(["upi", "upi", "imps"]),
                        device=device,
                        ip=ip(),
                        status=_status(world),
                    )
                else:
                    world.pay_out(
                        world.when(),
                        party.account,
                        amount,
                        "upi",
                        vpa=world.external_vpa(),
                        device=device,
                        ip=ip(),
                        status=_status(world),
                    )
        for _ in range(world.poisson(rates.get("card", 0) * scale)):
            _vpa, mcc = world.merchant()
            world.add(
                Txn(
                    world.when(),
                    party.account,
                    "card",
                    "debit",
                    world.amount(900, 0.8, 50, cap * 2),
                    mcc=mcc,
                    status=_status(world),
                )
            )
        for _ in range(world.poisson(rates.get("atm", 0) * scale)):
            notes = rng.choice([500, 1_000, 2_000, 3_000, 5_000, 10_000])
            world.cash(
                world.when(),
                party.account,
                "debit",
                min(notes, max(500, nearest(cap, 100))) * 100,
                attrs={"instrument": "atm"},
            )
        for _ in range(world.poisson(rates.get("aeps", 0) * scale)):
            world.add(
                Txn(
                    world.when(),
                    party.account,
                    "aeps",
                    "debit",
                    rng.choice([500, 1_000, 2_000, 3_000]) * 100,
                )
            )
        for _ in range(world.poisson(rates.get("cash_dep", 0) * scale)):
            median = 18_000 if party.segment in {"trader", "organisation"} else 6_000
            amount = nearest(world.amount(median, 0.7, 500, 400_000), 10000)
            world.cash(
                world.when(), party.account, "credit", max(amount, 50_000), branch=party.branch
            )
        for _ in range(world.poisson(rates.get("supplier", 0) * scale)):
            world.pay_out(
                world.when(),
                party.account,
                world.amount(15_000, 0.8, 500, 500_000),
                rng.choice(["neft", "imps"]),
                to_account=world.external_account(),
                device=device,
                ip=ip(),
                attrs={"purpose": "supplier"},
            )


def quirks(world: World) -> None:
    """Ordinary behaviour that resembles fraud on the surface. Unlabelled: it is simply part
    of how real customers act, and a detector has to live with it.

    - Receive-and-forward: money arrives and most of it is paid onward within minutes
      (settling a shared bill, paying rent on someone's behalf).
    - Phone upgrades: a customer's device changes part-way through, with no change in habits.
    """
    rng = world.rng
    scale = world.config.days / 30
    digital = [
        p
        for p in world.parties
        if p.kind == "individual"
        and p.devices
        and p.account
        and world.accounts[p.account].product != "loan"
    ]
    for party in digital:
        for _ in range(world.poisson(0.12 * scale)):
            ts = world.when()
            amount = world.amount(9_000, 0.7, 5_000, 60_000)
            if rng.random() < 0.5:
                world.pay_in(ts, party.account, amount, "upi", vpa=world.external_vpa("payer"))
            else:
                world.pay_in(
                    ts, party.account, amount, "imps", from_account=world.external_account()
                )
            onward = nearest(amount * rng.uniform(0.82, 1.0), 100)
            later = ts + timedelta(minutes=rng.uniform(3, 28))
            if rng.random() < 0.5:
                world.pay_out(
                    later,
                    party.account,
                    onward,
                    "upi",
                    vpa=world.external_vpa(),
                    device=party.devices[0],
                    ip=world.ip_for(party),
                )
            else:
                world.pay_out(
                    later,
                    party.account,
                    onward,
                    "imps",
                    to_account=world.external_account(),
                    device=party.devices[0],
                    ip=world.ip_for(party),
                )
    upgraded: dict[str, tuple[str, str, datetime]] = {}
    for party in digital:
        if rng.random() < 0.03:
            switch = world.when(world.start + timedelta(days=3), world.end - timedelta(days=3))
            old = party.devices[0]
            upgraded[party.account] = (old, _upgrade(world, party, switch), switch)
    for txn in world.txns:
        change = upgraded.get(txn.account)
        if change and txn.device == change[0] and txn.ts >= change[2]:
            txn.device = change[1]


def _upgrade(world: World, party: Party, switch: datetime) -> str:
    from sentinel_synth.population import new_device

    index = world.parties.index(party)
    return new_device(world, index, first_seen=switch)


def loan_servicing(world: World, skip: frozenset[str] = frozenset()) -> None:
    """Disbursements inside the window and monthly instalments for ordinary borrowers."""
    rng = world.rng
    by_customer = {p.customer_id: p for p in world.parties}
    months = _months(world)
    for loan in world.loans:
        if loan.loan_id in skip:
            continue
        party = by_customer[loan.customer_id]
        disbursed = datetime.combine(
            loan.disbursed_on, time(rng.randrange(10, 17), rng.randrange(60)), IST
        )
        if world.start <= disbursed <= world.end:
            _disburse(world, loan, party, disbursed)
        late = rng.random() < 0.04
        due_day = min(loan.disbursed_on.day, 28)
        for month in months:
            due = month.replace(day=due_day, hour=rng.randrange(6, 11))
            if due <= disbursed + timedelta(days=20) or not world.start <= due <= world.end:
                continue
            missed = late and rng.random() < 0.6
            _instalment(world, loan, party, due, failed=missed)
            if missed:
                loan.days_past_due += 30
        if loan.days_past_due:
            loan.status = "overdue"


def _disburse(world: World, loan: Loan, party: Party, ts: datetime) -> tuple[Txn, ...]:
    amount = loan.principal * 100
    if world.config.institution == "nbfc":
        return (
            world.pay_out(
                ts,
                loan.account_number,
                amount,
                "neft",
                to_account=(
                    loan.disbursement_account,
                    world.rng.choice(("XSYN0000001", "XSYN0000002")),
                ),
                attrs={"purpose": "loan_disbursement", "loan_id": loan.loan_id},
            ),
        )
    return world.transfer(
        ts,
        loan.account_number,
        party.account,
        amount,
        "internal",
        attrs={"purpose": "loan_disbursement", "loan_id": loan.loan_id},
    )


def _instalment(
    world: World, loan: Loan, party: Party, ts: datetime, *, failed: bool
) -> tuple[Txn, ...]:
    amount = loan.emi * 100
    status = "failed" if failed else "completed"
    attrs = {"purpose": "emi", "loan_id": loan.loan_id}
    if world.config.institution == "nbfc":
        if party.devices and world.rng.random() < 0.3:
            return (
                world.pay_in(
                    ts,
                    loan.account_number,
                    amount,
                    "upi",
                    vpa=f"{party.customer_id.lower()}@synpay",
                    status=status,
                    attrs=attrs,
                ),
            )
        return (
            world.pay_in(
                ts,
                loan.account_number,
                amount,
                "other",
                from_account=(loan.disbursement_account, "XSYN0000001"),
                status=status,
                attrs={**attrs, "instrument": "nach"},
            ),
        )
    return world.transfer(
        ts,
        party.account,
        loan.account_number,
        amount,
        "internal",
        status=status,
        attrs={**attrs, "instrument": "standing_instruction"},
    )


disburse = _disburse
instalment = _instalment
