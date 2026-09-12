"""The hard rules a candidate has to clear before it becomes a trade.

These are not tuned parameters. Each one is the scar of a specific loss taken
while this was running with real money, and they live in one place, in the
open, so that a backtest cannot quietly relax one of them:

1. Open only before noon local. Afternoon entries measured -61% to -100%: by
   then the answer is largely locked and the remaining move is already priced.
2. Limit price <= 0.25. Above that you are buying the favourite, which means
   paying for the market's own opinion.
3. Safety multiple by price tier. A 4x model-to-price ratio on a 3-cent ticket
   is not the same claim as 4x on a 20-cent ticket -- cheap tickets need a
   wider margin, because the cheap end is exactly where a model's tail is least
   trustworthy.
4. One ticket per bucket per day, a cap on tickets per day, a fixed stake. No
   averaging down and no stop loss: on a book this thin a stop loss fills at
   zero, which was measured, not assumed.

Rule 4 interacts with the venue in a way worth writing down: with a fixed $1
ticket and a 5-share minimum order, the highest price actually reachable is
$1/5 = 0.20, so the 0.20-0.25 slice of rule 2 is unreachable in live trading
even though the backtest can express it.
"""

from __future__ import annotations

from dataclasses import dataclass

# (price ceiling, required model-probability / price ratio)
SAFETY_TIERS = ((0.02, 10.0), (0.10, 4.0), (0.25, 2.0))

MAX_ENTRY_HOUR = 12  # local hour, inclusive
MAX_LIMIT_PRICE = 0.25
MAX_TICKETS_PER_DAY = 2
MIN_ORDER_SHARES = 5


def required_multiple(price: float):
    """Ratio the model must clear at this price, or None if out of range."""
    for ceiling, multiple in SAFETY_TIERS:
        if price <= ceiling:
            return multiple
    return None


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reason: str
    multiple: float = 0.0


def check(*, hour: int, model_p: float, price: float, tickets_used: int) -> GateResult:
    if hour > MAX_ENTRY_HOUR:
        return GateResult(False, "after noon cutoff")
    if tickets_used >= MAX_TICKETS_PER_DAY:
        return GateResult(False, "daily ticket cap")
    if not 0 < price <= MAX_LIMIT_PRICE:
        return GateResult(False, "price above limit cap")
    need = required_multiple(price)
    if need is None:
        return GateResult(False, "price out of tiered range")
    multiple = model_p / price
    if multiple < need:
        return GateResult(False, f"safety multiple {multiple:.2f}x below {need:.0f}x", multiple)
    return GateResult(True, "ok", multiple)
