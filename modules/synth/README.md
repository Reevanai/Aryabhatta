# synth

Generates a synthetic co-operative bank or NBFC: customers, accounts, loans, devices and
transactions, with injected fraud that is **labelled**. Every later phase is tested and
demonstrated on this data only.

Nothing here is, or is derived from, real data. Family names and places are invented; emails use
`example.invalid`; IP addresses are in the IPv6 documentation range `2001:db8::/32`; bank codes
start with `XSYN`; PAN-shaped values have the right format and random content.

The generator is standalone. It imports nothing from the platform, so its output cannot be shaped
by the code it is used to test. A boundary rule in CI enforces this.

## Run

```bash
uv run sentinel-synth generate --profile demo --out var/synth/demo
```

```bash
uv run sentinel-synth generate --profile tiny --institution nbfc --out var/synth/nbfc
```

```bash
uv run sentinel-synth describe var/synth/demo
```

`make seed` generates the `demo` profile and loads it. `PROFILE=small make seed` loads a larger one.

| Profile | Customers | Days | Transactions | Fraud scenarios | Generation time |
|---------|-----------|------|--------------|-----------------|-----------------|
| `tiny` | 300 | 30 | about 17,000 | 15 | 0.3 s |
| `demo` | 1,500 | 45 | about 122,000 | 39 | 2.9 s |
| `small` | 5,000 | 90 | about 808,000 | 117 | 20 s |
| `medium` | 30,000 | 180 | not measured | 585 | not measured |

The same configuration and seed always produce identical files (tested).

## Output

| File | Contents |
|------|----------|
| `branches.csv`, `parties.csv`, `accounts.csv`, `loans.csv`, `devices.csv`, `transactions.csv` | Data, in the ingestion CSV formats. Load in the order given in the manifest. |
| `labels/transactions.csv`, `labels/parties.csv`, `labels/accounts.csv` | Ground truth: scenario, role, `is_fraud`. Victims are marked as not perpetrators. |
| `labels/scenarios.json` | One record per scenario: type, description, time range, parties, accounts, details. |
| `manifest.json` | Configuration, statistics and a SHA-256 for each file. |

Labels are never mixed into the data files. A test checks that no label word appears in them.

## What is generated

**Ordinary activity.** Seven customer segments (salaried, trader, self-employed, farmer,
pensioner, homemaker, student) and organisations, each with its own income pattern and rates of
UPI, card, ATM, AePS, cash, bill and transfer activity. Transfers between customers of the same
institution appear as a debit leg and a credit leg sharing a reference. About 3% of payments
fail. Balances never go negative. Activity follows a daytime curve.

**Fraud scenarios**

| Scenario | Shape |
|----------|-------|
| `mule_chain` | Several victim payments into a mule, passed along 2–4 mule accounts within minutes each, then cashed out. |
| `app_scam` | A real customer, on their own device, sends 1–4 escalating payments to a new payee; half go through an internal mule that forwards them out. |
| `account_takeover` | An unfamiliar device and network, a one-rupee test payment, then 3–8 transfers draining the account within about an hour. Mostly at night. Attacker devices recur across victims. |
| `layering` | An inflow of ₹8–40 lakh into a new shell account, split across 4–7 accounts, moved through a second layer, and sent out. |
| `circular_flow` | 3–6 accounts pass the same funds round a loop 2–4 times. |
| `structuring` | 6–20 cash deposits, each at 70–98% of a configured threshold, across several days and branches, then moved out. |
| `fraud_ring` | 5–10 newly opened, linked customers sharing a device, phone or address, transacting among themselves and cashing out inflows. |
| `synthetic_identity` | 3–6 fabricated identities built from shared attributes, each taking a loan that is never repaid. |
| `loan_fraud` | A loan drawn down and moved out within a day, with every instalment failing. |

**Labelled look-alikes** (`is_fraud = false`): a trader's same-day pass-through, a phone change,
a one-off large payment, a cash-heavy trader, and money lent and returned.

**Unlabelled look-alike background**: quick receive-and-forward payments and phone upgrades among
ordinary customers.

For an NBFC only `fraud_ring`, `synthetic_identity` and `loan_fraud` apply: there are no deposit
accounts or outgoing payment rails.

## Is the fraud findable but not trivial?

`make baseline` runs three deliberately naive rules against the labels. Measured on this code:

| Rule | Target | `demo`: recall / precision | `small`: recall / precision |
|------|--------|---------------------------|-----------------------------|
| Cash deposit at 70–100% of the threshold | structuring | 0.93 / 0.11 | 0.93 / 0.05 |
| Debit of 80–100% within 30 minutes of a credit | mule chain | 0.60 / 0.27 | 0.53 / 0.13 |
| Payment at night from a device new to the account | account takeover | 0.83 / 0.93 | 0.69 / 0.92 |

Precision counts a hit on any labelled fraud as correct. Two of the three rules are noisy, as
intended. The night-time new-device rule stays precise: that pattern is still too clean in this
data, and real data would have more night-time device changes.

## Limits

- **It is a simulation written by the people who will write the detectors.** Good results on it
  show the system works as designed, not that it would catch real fraud. Only real, labelled bank
  data can show that.
- **Amounts, rates and segment mixes are plausible assumptions, not calibrated** to any bank or
  to published statistics.
- **The structuring threshold is a generator parameter** (default ₹50,000). It is not a statement
  of any regulatory limit.
- **No reversals, chargebacks or disputes.** No interest, fees or term-deposit activity.
- **UPI is modelled from publicly visible fields only**, not from NPCI message specifications.
- **One currency, one country, one time zone.**
- **Card transactions carry no device or location.**
- **Phone numbers follow a fixed synthetic pattern** that has not been checked against real
  numbering plans. Treat them as placeholders.
- **`medium` has not been run.** Generation is single-threaded pure Python and holds everything in
  memory.
- **Labels are files, not database rows.** Loading them for backtests is Phase 4 work.
