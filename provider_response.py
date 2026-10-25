import re


def classify_provider_message(message):
    """Return a non-failure status for a provider's active-order response."""
    normalized = re.sub(r"\s+", " ", (message or "").casefold()).strip()
    arabic_active_order = "طلب نشط" in normalized and "رابط" in normalized
    english_active_order = (
        "active order" in normalized
        and ("link" in normalized or "url" in normalized)
    )
    if arabic_active_order or english_active_order:
        return "active_order"
    return None
