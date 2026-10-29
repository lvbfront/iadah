"""Escalation rules: decide small vs big tier. No LLM here."""
import config

CONFIDENCE_THRESHOLD = 0.7


def pick_tier(*, confidence: float = 1.0, root_causes: int = 1, seen_before: bool = True,
              failed_fix: bool = False, invalid_output: bool = False, unknown: bool = False) -> tuple[str, str]:
    """Return (tier, reason). root_causes = independent faults after topology removes symptoms."""
    if config.MODE == "big-only":
        return "big", "mode=big-only"
    if config.MODE == "small-only":
        return "small", "mode=small-only"
    if invalid_output:
        return "big", "small model returned invalid JSON twice"
    if failed_fix:
        return "big", "previous fix failed"
    if unknown:
        return "big", "incident type unknown"
    if root_causes > 1:
        return "big", f"{root_causes} independent root causes"
    if not seen_before:
        return "big", "unseen incident type (no runbook)"
    if confidence < CONFIDENCE_THRESHOLD:
        return "big", f"low confidence {confidence:.2f} < {CONFIDENCE_THRESHOLD}"
    return "small", f"routine: known type, confidence {confidence:.2f}"


if __name__ == "__main__":
    assert pick_tier()[0] == "small"
    assert pick_tier(failed_fix=True)[0] == "big"
    assert pick_tier(root_causes=2)[0] == "big"
    assert pick_tier(seen_before=False)[0] == "big"
    assert pick_tier(confidence=0.5)[0] == "big"
    assert pick_tier(invalid_output=True)[0] == "big"
    assert pick_tier(unknown=True)[0] == "big"
    config.MODE = "small-only"
    assert pick_tier(failed_fix=True)[0] == "small"
    config.MODE = "big-only"
    assert pick_tier()[0] == "big"
    print("router ok")
