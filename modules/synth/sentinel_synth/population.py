"""Branches, customers, accounts, devices and loans."""

from datetime import date, datetime, time, timedelta

from sentinel_synth import names
from sentinel_synth.world import IST, Account, Branch, Device, Loan, Party, World, nearest

# segment -> (share of individuals, monthly income range in rupees)
SEGMENTS: dict[str, tuple[float, tuple[int, int]]] = {
    "salaried": (0.30, (25_000, 90_000)),
    "trader": (0.15, (40_000, 250_000)),
    "self_employed": (0.10, (20_000, 120_000)),
    "farmer": (0.15, (8_000, 30_000)),
    "pensioner": (0.12, (9_000, 35_000)),
    "homemaker": (0.10, (5_000, 20_000)),
    "student": (0.08, (3_000, 12_000)),
}
ORGANISATION_SHARE = 0.05
LOAN_PRODUCTS = {
    # product -> (principal range, annual rate, tenure months range)
    "gold": ((20_000, 300_000), 0.11, (6, 24)),
    "personal": ((30_000, 500_000), 0.15, (12, 48)),
    "two_wheeler": ((40_000, 120_000), 0.13, (12, 36)),
    "agri_kcc": ((25_000, 300_000), 0.07, (12, 12)),
    "msme": ((100_000, 1_500_000), 0.14, (12, 60)),
    "housing": ((500_000, 3_000_000), 0.09, (60, 240)),
}
_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"


def emi(principal: int, annual_rate: float, months: int) -> int:
    monthly = annual_rate / 12
    return round(principal * monthly * (1 + monthly) ** months / ((1 + monthly) ** months - 1))


def make_branches(world: World) -> None:
    for n in range(world.config.branches):
        locality = names.LOCALITY[n % len(names.LOCALITY)]
        suffix = "" if n < len(names.LOCALITY) else f" {n // len(names.LOCALITY) + 1}"
        world.branches.append(Branch(f"BR{n + 1:03d}", f"{locality}{suffix} Branch"))


def new_pan(world: World, kind: str, name: str) -> str:
    """A value with the shape of a PAN. The fourth letter follows the holder type and the fifth
    the name, as in the real format; the rest is random, so these are not real PANs."""
    holder = "P" if kind == "individual" else world.rng.choice("FCA")
    initial = next((c for c in name.split()[-1].upper() if c in _LETTERS), "X")
    head = "".join(world.rng.choice(_LETTERS) for _ in range(3))
    return f"{head}{holder}{initial}{world.rng.randrange(10_000):04d}{world.rng.choice(_LETTERS)}"


def new_phone(world: World) -> str:
    # A fixed synthetic pattern (61 followed by a counter). Not checked against real numbering
    # plans, so treat these as placeholders, never as contactable numbers.
    return f"6{world.next_id('phone') + 10**8:09d}"


def new_address(world: World) -> str:
    rng = world.rng
    return (
        f"{rng.randrange(1, 400)}, Lane {rng.randrange(1, 30)}, {rng.choice(names.LOCALITY)}, "
        f"{rng.choice(names.TOWN)}"
    )


def new_party(
    world: World,
    *,
    kind: str = "individual",
    segment: str | None = None,
    onboarded: date | None = None,
    name: str | None = None,
    branch: int | None = None,
) -> int:
    """Create a party and return its index."""
    rng = world.rng
    number = world.next_id("party")
    if kind == "organisation":
        segment = segment or "organisation"
        name = name or (
            f"{rng.choice(names.FAMILY)} {rng.choice(names.BUSINESS_KIND)}"
            if rng.random() < 0.7
            else f"{rng.choice(names.LOCALITY)} {rng.choice(names.ORG_KIND)}"
        )
        income = rng.randrange(150_000, 1_500_000)
        dob = date(1985, 1, 1) + timedelta(days=rng.randrange(0, 14_000))
    else:
        if segment is None:
            segment = rng.choices(list(SEGMENTS), [s[0] for s in SEGMENTS.values()])[0]
        low, high = SEGMENTS[segment][1]
        income = rng.randrange(low, high)
        given = rng.choice(names.GIVEN_F if rng.random() < 0.48 else names.GIVEN_M)
        name = name or f"{given} {rng.choice(names.FAMILY)}"
        if segment == "pensioner":
            age = rng.randrange(60, 85)
        elif segment == "student":
            age = rng.randrange(18, 25)
        else:
            age = rng.randrange(23, 62)
        dob = world.config.end_date - timedelta(days=age * 365 + rng.randrange(365))
    if onboarded is None:
        onboarded = world.start.date() - timedelta(days=int(rng.triangular(30, 4000, 600)))
    party = Party(
        customer_id=f"C{number:07d}",
        kind=kind,
        segment=segment,
        name=name,
        pan=new_pan(world, kind, name),
        phone=new_phone(world),
        email=f"c{number}@example.invalid" if rng.random() < 0.7 else "",
        address=new_address(world),
        dob=dob,
        risk=rng.choices(["low", "medium", "high"], [82, 15, 3])[0],
        onboarded=onboarded,
        branch=branch if branch is not None else rng.randrange(len(world.branches)),
        monthly_income=income,
        net_prefix=f"2001:db8:{rng.randrange(0x1000, 0xEFFF):x}:{rng.randrange(0xFFFF):x}:",
    )
    world.parties.append(party)
    return len(world.parties) - 1


def new_account(
    world: World,
    party: int,
    product: str,
    *,
    opened: date | None = None,
    status: str = "active",
    base_balance: int | None = None,
) -> str:
    owner = world.parties[party]
    number = f"{owner.branch + 1:03d}{world.next_id('account'):011d}"
    rng = world.rng
    if base_balance is None:
        base_balance = (
            0
            if product in {"loan", "overdraft"}
            else round(owner.monthly_income * rng.uniform(0.2, 3.0)) * 100
        )
    world.accounts[number] = Account(
        number, party, owner.branch, product, status, opened or owner.onboarded, base_balance
    )
    return number


def new_device(
    world: World, party: int, first_seen: datetime | None = None, *, rooted: bool = False
) -> str:
    rng = world.rng
    owner = world.parties[party]
    device_id = f"dev-{world.next_id('device'):08x}-{rng.randrange(16**8):08x}"
    platform = rng.choices(["android", "ios", "web"], [78, 17, 5])[0]
    version = {
        "android": f"{rng.randrange(10, 16)}",
        "ios": f"{rng.randrange(15, 19)}.{rng.randrange(6)}",
        "web": "browser",
    }[platform]
    seen = first_seen or max(
        datetime.combine(owner.onboarded, time(10), IST),
        world.start - timedelta(days=rng.randrange(1, 700)),
    )
    world.devices.append(
        Device(device_id, owner.customer_id, platform, version, rooted, seen, seen)
    )
    owner.devices.append(device_id)
    return device_id


def link_device(world: World, device_id: str, party: int, first_seen: datetime) -> None:
    """Record that an existing device was also used by another customer."""
    source = next(d for d in world.devices if d.device_id == device_id)
    owner = world.parties[party]
    world.devices.append(
        Device(
            device_id,
            owner.customer_id,
            source.platform,
            source.os_version,
            source.rooted,
            first_seen,
            first_seen,
        )
    )
    owner.devices.append(device_id)


def new_loan(
    world: World,
    party: int,
    product: str,
    disbursed_on: date,
    *,
    principal: int | None = None,
    disbursement_account: str = "",
) -> Loan:
    rng = world.rng
    (low, high), rate, (t_low, t_high) = LOAN_PRODUCTS[product]
    principal = principal or nearest(rng.randrange(low, high), 1000)
    tenure = rng.randrange(t_low, t_high + 1)
    account = new_account(world, party, "loan", opened=disbursed_on)
    loan = Loan(
        loan_id=f"L{world.next_id('loan'):07d}",
        customer_id=world.parties[party].customer_id,
        account_number=account,
        product=product,
        principal=principal,
        annual_rate=rate,
        tenure_months=tenure,
        disbursed_on=disbursed_on,
        emi=emi(principal, rate, tenure),
        disbursement_account=disbursement_account,
    )
    world.loans.append(loan)
    return loan


def populate_bank(world: World) -> None:
    """Customers of a co-operative bank: deposit accounts, devices, contacts and loans."""
    rng = world.rng
    make_branches(world)
    for _ in range(world.config.customers):
        kind = "organisation" if rng.random() < ORGANISATION_SHARE else "individual"
        index = new_party(world, kind=kind)
        party = world.parties[index]
        product = "current" if kind == "organisation" or party.segment == "trader" else "savings"
        party.account = new_account(world, index, product)
        if rng.random() < 0.12:
            new_account(
                world, index, "term_deposit", base_balance=rng.randrange(25_000, 500_000) * 100
            )
        if kind == "individual" and rng.random() < 0.05:
            # An old, unused second account.
            new_account(
                world,
                index,
                "savings",
                status="dormant",
                base_balance=rng.randrange(500, 5_000) * 100,
            )
        digital = {
            "salaried": 0.95,
            "trader": 0.9,
            "self_employed": 0.9,
            "student": 0.98,
            "homemaker": 0.75,
            "farmer": 0.45,
            "pensioner": 0.4,
        }.get(party.segment, 0.8)
        if rng.random() < digital:
            new_device(world, index)
            if rng.random() < 0.08:
                new_device(world, index)
    individuals = [i for i, p in enumerate(world.parties) if p.kind == "individual"]
    for index in individuals:
        size = rng.randrange(2, 8)
        world.parties[index].contacts = [c for c in rng.sample(individuals, size) if c != index]
    # Loans: some taken before the window, some disbursed inside it.
    for index in individuals:
        party = world.parties[index]
        if rng.random() >= 0.12:
            continue
        product = (
            "agri_kcc"
            if party.segment == "farmer"
            else rng.choice(
                [
                    "gold",
                    "personal",
                    "two_wheeler",
                    "msme" if party.segment in {"trader", "self_employed"} else "personal",
                ]
            )
        )
        inside = rng.random() < 0.25
        day = (
            world.when(world.start + timedelta(days=2), world.end - timedelta(days=5)).date()
            if inside
            else world.start.date() - timedelta(days=rng.randrange(20, 700))
        )
        new_loan(world, index, product, day, disbursement_account=party.account)


def populate_nbfc(world: World) -> None:
    """Borrowers of an NBFC: one loan account each, repaid from accounts held elsewhere."""
    rng = world.rng
    make_branches(world)
    for _ in range(world.config.customers):
        segment = rng.choices(["salaried", "trader", "self_employed", "farmer"], [40, 25, 25, 10])[
            0
        ]
        index = new_party(world, segment=segment)
        party = world.parties[index]
        if rng.random() < 0.85:
            new_device(world, index)
        product = rng.choice(["gold", "personal", "two_wheeler", "msme"])
        inside = rng.random() < 0.3
        day = (
            world.when(world.start + timedelta(days=2), world.end - timedelta(days=5)).date()
            if inside
            else world.start.date() - timedelta(days=rng.randrange(20, 500))
        )
        external, _bank = world.external_account()
        loan = new_loan(world, index, product, day, disbursement_account=external)
        party.account = loan.account_number
