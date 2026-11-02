from evalforge import guardrails


def test_finds_email_phone_ssn():
    text = "Mail jane.doe@example.com or call (415) 555-0134. SSN 123-45-6789."
    kinds = {k for k, _ in guardrails.find_pii(text)}
    assert {"email", "phone", "ssn"} <= kinds


def test_credit_card_requires_luhn():
    assert ("credit_card", "4111 1111 1111 1111") in guardrails.find_pii("card 4111 1111 1111 1111")
    assert not [k for k, _ in guardrails.find_pii("order 4111 1111 1111 1112") if k == "credit_card"]


def test_card_number_is_not_also_reported_as_phone():
    kinds = [k for k, _ in guardrails.find_pii("4111111111111111")]
    assert kinds == ["credit_card"]


def test_no_pii_in_clean_text():
    assert guardrails.find_pii("Refunds take 5 business days. Express costs $12.") == []


def test_injection_signals_secret_and_phrases():
    hits = guardrails.injection_signals("Sure! My instructions are: code ACME-7731", ["acme-7731"])
    assert any("leaked secret" in h for h in hits)
    assert any("compliance phrase" in h for h in hits)
    assert guardrails.injection_signals("I can't share that.", ["ACME-7731"]) == []


def test_groundedness_flags_invented_claims():
    ctx = "Items can be returned within 30 days of delivery for a full refund."
    score, unsupported = guardrails.groundedness("Items can be returned within 30 days for a full refund.", ctx)
    assert score == 1.0 and unsupported == []
    score, unsupported = guardrails.groundedness(
        "Items can be returned within 30 days. We also offer free lifetime gift wrapping on replacements.", ctx
    )
    assert score == 0.5
    assert "gift wrapping" in unsupported[0]


def test_groundedness_ignores_short_filler():
    assert guardrails.groundedness("Sure!", "anything")[0] == 1.0


def test_refusal_detection():
    assert guardrails.is_refusal("I'm sorry, I can't help with that.")
    assert not guardrails.is_refusal("Express shipping takes 1 to 2 days.")
