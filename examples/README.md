# examples

Small **synthetic** data set used by `make seed`: 200 parties, 260 accounts, 2,000 transactions.

Everything is invented by `scripts/make_example_data.py` from a fixed seed. Names come from a list
of made-up names; phone numbers, emails (`example.invalid`) and IP addresses (`203.0.113.0/24`,
a documentation range) are fictional. Eight rows are deliberately invalid to demonstrate
quarantine.

Phase 3 replaces this with the full generator and labelled fraud scenarios.
