"""AI inference and edge expert rule engine."""
import logging
import math
from functools import lru_cache
from pathlib import Path


LOGGER = logging.getLogger(__name__)
MODEL_PATH = Path(__file__).resolve().parents[1] / "cooling_model.joblib"


@lru_cache(maxsize=1)
def _load_cooling_model():
    """Load the optional trained model once per process."""
    if not MODEL_PATH.is_file():
        return None

    try:
        import joblib
    except ImportError:
        LOGGER.exception(
            "A cooling model artifact exists, but joblib is unavailable. "
            "Install the optional dependencies from ml/requirements.txt."
        )
        return None

    try:
        return joblib.load(MODEL_PATH)
    except Exception:
        LOGGER.exception("Could not load cooling model from %s", MODEL_PATH)
        return None


def model_status():
    """Describe which assistant inference path is available."""
    return "trained-cooling-model" if _load_cooling_model() is not None else "rules-fallback"


def _cooling_features(context):
    """Get valid sensor values inside the training data's supported range."""
    if not isinstance(context, dict):
        return None

    metrics = context.get("metrics")
    node = context.get("node")
    metrics = metrics if isinstance(metrics, dict) else {}
    node = node if isinstance(node, dict) else {}

    temperature = metrics.get("airTemp", node.get("roomTemp"))
    humidity = metrics.get("humidity", node.get("humidity"))
    if (isinstance(temperature, bool) or not isinstance(temperature, (int, float))
            or isinstance(humidity, bool) or not isinstance(humidity, (int, float))):
        return None

    temperature = float(temperature)
    humidity = float(humidity)
    if (not math.isfinite(temperature) or not math.isfinite(humidity)
            or not 18 <= temperature <= 40 or not 20 <= humidity <= 90):
        return None
    return temperature, humidity


def run_model(query, context, history):
    """
    Answer cooling-demand estimates with the optional trained model.
    Other requests continue through the safety-focused rule engine.
    """
    del history
    normalized_query = query.lower()
    if not any(term in normalized_query for term in (
        "cooling demand", "cooling load", "how much cooling", "estimate cooling",
    )):
        return None

    features = _cooling_features(context)
    model = _load_cooling_model()
    if features is None or model is None:
        return None

    temperature, humidity = features
    try:
        demand = float(model.predict([[temperature, humidity]])[0])
    except Exception:
        LOGGER.exception("Cooling-demand inference failed")
        return None
    if not math.isfinite(demand):
        LOGGER.error("Cooling-demand model returned a non-finite prediction")
        return None

    demand = max(0, min(100, round(demand)))
    return {
        "reply": (
            f"The trained model estimates cooling demand at {demand}% "
            f"for {temperature:.1f}°C and {humidity:.1f}% relative humidity. "
            "This model was trained on synthetic demonstration data and is not "
            "validated for operational or safety decisions."
        ),
        "lang": "en",
        "meta": {
            "model": "heatguard-cooling-random-forest",
            "cooling_demand_percent": demand,
        },
    }

def rule_reply(query, ctx):
    """Generates context-aware clinical and hardware actuation guidance."""
    q = query.lower().strip()
    m = ctx.get("metrics", {})
    risk = ctx.get("risk", {})
    risk_level = risk.get("level", "Safe")
    air_temp = m.get("airTemp", 32)
    humidity = m.get("humidity", 60)
    wbgt = m.get("wbgt", 28)
    profile = ctx.get("profile") or {}
    conditions = profile.get("conditions", [])

    # Actuation queries
    if any(w in q for w in ("fan", "ac", "air condition", "cooler", "exhaust", "ventilation")):
        if any(w in q for w in ("on", "start", "run", "engage", "activate")):
            rid = "exhaust" if "exhaust" in q else "ac" if "ac" in q or "condition" in q else "evap" if "cooler" in q else "fan"
            return {
                "reply": f"Activating {rid} relay on the edge node now to accelerate cooling.",
                "actions": [{"type": "relay", "id": rid, "on": True}],
                "lang": "en",
            }
        if any(w in q for w in ("off", "stop", "halt", "shutdown", "deactivate")):
            rid = "exhaust" if "exhaust" in q else "ac" if "ac" in q or "condition" in q else "evap" if "cooler" in q else "fan"
            return {
                "reply": f"Deactivating {rid} relay.",
                "actions": [{"type": "relay", "id": rid, "on": False}],
                "lang": "en",
            }

    # Safety status / whether it's safe to work or go out
    if any(w in q for w in ("safe", "go out", "work", "outside", "hot")):
        if risk_level in ("High", "Extreme"):
            hydration = "650 to 850 ml" if risk_level == "Extreme" else "500 to 650 ml"
            health_warning = " Because of your medical history, heat strain escalates rapidly." if conditions else ""
            return {
                "reply": f"It is currently {air_temp}°C with {humidity}% relative humidity (WBGT: {wbgt}°C). Heat risk is {risk_level.upper()}.{health_warning} Avoid strenuous exertion, stay in shaded or air-conditioned spaces, and drink {hydration} of electrolyte water per hour.",
                "lang": "en",
            }
        return {
            "reply": f"Thermal conditions are currently {risk_level} ({air_temp}°C, {humidity}% humidity, WBGT {wbgt}°C). Safe for typical activities with regular hydration.",
            "lang": "en",
        }

    # Why is heat high / root cause
    if "why" in q and ctx.get("causes"):
        return {
            "reply": f"Thermal drivers: {'; '.join(ctx['causes'][:2])}.",
            "lang": "en",
        }

    # Symptoms / first aid
    if any(w in q for w in ("symptom", "stroke", "cramp", "exhaustion", "dizzy", "headache")):
        return {
            "reply": "If experiencing dizziness, confusion, hot dry skin, or severe headache: move to cool shelter, apply damp cool cloths to neck and armpits, sip electrolyte fluids, and dial emergency medical assistance if unresponsiveness or vomiting occurs.",
            "lang": "en",
        }

    # Default overview status
    return {
        "reply": f"Ambient temperature is {air_temp}°C, relative humidity is {humidity}%, and wet-bulb globe temperature is {wbgt}°C. Current personalized risk level is {risk_level}.",
        "lang": "en",
    }
