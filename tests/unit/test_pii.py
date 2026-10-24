from app.safety.pii import mask_pii, redact_for_audit


def test_policy_number_masked():
    assert mask_pii("Policy GLD-4471-2290 is active") == "Policy GLD-****-2290 is active"


def test_phone_masked():
    assert "41177" not in mask_pii("Call +91-98200-41177")
    assert mask_pii("Call +91-98200-41177").endswith("1177")


def test_email_masked():
    assert mask_pii("mail arjun.mehta@example.com now") == "mail ***@example.com now"


def test_labelled_dob_masked_but_other_dates_kept():
    text = "DOB: 1968-04-12, appointment on 2026-10-27"
    assert mask_pii(text) == "DOB: ****-**-**, appointment on 2026-10-27"


def test_aadhaar_masked():
    assert mask_pii("id 1234 5678 9012") == "id XXXX XXXX 9012"


def test_clinical_numbers_untouched():
    text = "HbA1c 7.8 %, fasting glucose 132 mg/dL, cost INR 350000"
    assert mask_pii(text) == text


def test_audit_redaction_truncates():
    assert len(redact_for_audit("x" * 2000)) == 501
