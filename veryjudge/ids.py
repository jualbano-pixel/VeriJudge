"""Prefixed canonical ULIDs (48-bit milliseconds and 80 random bits)."""
import secrets
import time

PREFIXES = frozenset({"sub", "prop", "run", "step", "doc", "ev", "jdg", "rev"})
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_id(prefix: str) -> str:
    if prefix not in PREFIXES:
        raise ValueError("Unknown ID prefix")
    value = (time.time_ns() // 1_000_000 << 80) | secrets.randbits(80)
    chars = []
    for _ in range(26):
        chars.append(ALPHABET[value & 31])
        value >>= 5
    return prefix + "_" + "".join(reversed(chars))

