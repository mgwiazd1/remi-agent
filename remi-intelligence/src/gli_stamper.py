import urllib.request
import urllib.error
import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

AESTIMA_BASE = os.getenv("AESTIMA_BASE_URL", "http://192.168.1.198:8000")
ACCESS_TOKEN = os.getenv("AESTIMA_ACCESS_TOKEN", "betabog2026")


@dataclass
class GLIStamp:
    gli_phase: Optional[str] = None
    gli_value_bn: Optional[float] = None
    steno_regime: Optional[str] = None
    fiscal_score: Optional[float] = None
    transition_risk: Optional[float] = None
    raw_context: Optional[dict] = None
    stamp_error: Optional[str] = None

    def to_dict(self):
        return {
            "gli_phase": self.gli_phase,
            "gli_value_bn": self.gli_value_bn,
            "steno_regime": self.steno_regime,
            "fiscal_score": self.fiscal_score,
            "transition_risk": self.transition_risk,
        }

    def for_prompt(self):
        if not self.gli_phase or self.gli_value_bn is None:
            return f"GLI context unavailable (error: {self.stamp_error})"
        return (
            f"Phase: {self.gli_phase} | "
            f"GLI: ${self.gli_value_bn:.0f}B | "
            f"Regime: {self.steno_regime} | "
            f"Fiscal: {self.fiscal_score}/10 | "
            f"Transition Risk: {self.transition_risk}/10"
        )


def fetch_gli_stamp() -> GLIStamp:
    try:
        req = urllib.request.Request(
            f"{AESTIMA_BASE}/api/agent/context",
            headers={"X-Access-Token": ACCESS_TOKEN}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        return GLIStamp(
            gli_phase=data.get("gli_phase"),
            gli_value_bn=data.get("gli_value_bn"),
            steno_regime=data.get("steno_regime"),
            fiscal_score=data.get("fiscal_dominance_score"),
            transition_risk=data.get("transition_risk_score"),
            raw_context=data,
        )
    except Exception as e:
        logger.warning(f"GLI stamp fetch failed: {e}")
        return GLIStamp(stamp_error=str(e))


def health_check() -> bool:
    try:
        req = urllib.request.Request(
            f"{AESTIMA_BASE}/api/health",
            headers={"X-Access-Token": ACCESS_TOKEN}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False
