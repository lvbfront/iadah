"""Escalation rules: decide small vs big tier. No LLM here."""

CONFIDENCE_THRESHOLD = 0.7


def pick_tier(*, confidence: float = 1.0, affected_services: int = 1,
              seen_before: bool = True, failed_fix: bool = False) -> tuple[str, str]:
    """Return (tier, reason)."""
    if failed_fix:
        return "big", "previous fix failed"
    if affected_services > 1:
        return "big", f"{affected_services} services affected"
    if not seen_before:
        return "big", "unseen incident type"
    if confidence < CONFIDENCE_THRESHOLD:
        return "big", f"low confidence {confidence:.2f}"
    return "small", "routine"


if __name__ == "__main__":
    assert pick_tier()[0] == "small"
    assert pick_tier(failed_fix=True)[0] == "big"
    assert pick_tier(affected_services=2)[0] == "big"
    assert pick_tier(seen_before=False)[0] == "big"
    assert pick_tier(confidence=0.5)[0] == "big"
    print("router ok")
