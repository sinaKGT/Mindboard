"""ULIDs: time-sortable, collision-safe, generated without a round-trip."""

import os
import time

_ENC = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32


def ulid() -> str:
    n = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    return "".join(_ENC[(n >> (5 * (25 - i))) & 31] for i in range(26))


def is_ulid(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 26
        and all(c in _ENC for c in value)
    )
