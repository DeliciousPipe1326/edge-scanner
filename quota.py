"""Per-request quota accounting; no shared state or persisted API credentials."""

from dataclasses import dataclass


def header_integer(headers, name):
    value = headers.get(name)
    if not isinstance(value, (str, int)):
        return None
    try:
        number = int(value)
    except (ValueError, TypeError):
        return None
    return number if number >= 0 else None


@dataclass
class Quota:
    credits: int = 0
    remaining: int | None = None
    account_used: int | None = None
    complete: bool = True
    responses: int = 0

    def record(self, headers, paid):
        self.responses += 1
        remaining = header_integer(headers, "x-requests-remaining")
        used = header_integer(headers, "x-requests-used")
        self.remaining = remaining
        self.account_used = used
        if paid:
            cost = header_integer(headers, "x-requests-last")
            if cost is None:
                self.complete = False
            else:
                self.credits += cost

    def as_dict(self):
        return {
            "credits_used": self.credits if self.complete and self.responses else None,
            "known_credits_used": self.credits,
            "remaining": self.remaining,
            "account_used": self.account_used,
            "complete": self.complete and self.responses > 0,
        }
