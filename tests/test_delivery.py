from epsilon_flow.delivery import clean_transcript


def test_clean_transcript_preserves_legacy_product_name_cleanup():
    transcript = "  open claw asked epsilon to check codex for floramis with raghav.  "

    assert clean_transcript(transcript) == "OpenClaw asked Epsilon to check Codex for Floramis with Raghav."


def test_clean_transcript_preserves_legacy_spaced_openclaw_cleanup():
    assert clean_transcript("Open Claw") == "OpenClaw"
