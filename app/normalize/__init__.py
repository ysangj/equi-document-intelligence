"""Deterministic normalization (FR-6, PRD §2 "code for truth").

Pure functions, no DB access:
- `text`: text normalization for snippet verification and alias keys.
- `numbers`: percentages/returns (to fractions), amounts, plain numbers, currencies.
- `dates`: period strings to period start/end dates.
- `names`: fund/account alias keys and the fuzzy matcher (rapidfuzz + guard).
"""
