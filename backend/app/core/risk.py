"""Risk categories from predicted RUL.

Thresholds are configurable DEMONSTRATION values (defaults 60 / 30 cycles), not validated
maintenance rules. A category is decision support, not a guarantee about failure.

    RUL > warning              -> normal
    critical < RUL <= warning  -> warning
    RUL <= critical            -> critical
"""
from __future__ import annotations

RISK_ORDER = {"normal": 0, "warning": 1, "critical": 2}


def risk_category(rul: float, warning: float, critical: float) -> str:
    if rul <= critical:
        return "critical"
    if rul <= warning:
        return "warning"
    return "normal"
