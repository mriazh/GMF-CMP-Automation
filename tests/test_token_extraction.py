"""Tests for OTP token extraction from email bodies."""

import logging

from cmp_automation.utils import extract_token_from_email_body


class TestTokenExtraction:
    """Tests for extracting OTP tokens from email bodies."""

    def test_extract_6_digit_numeric(self):
        """Test extraction of 6-digit numeric token."""
        body = "Your OTP code is 123456. Do not share it."
        token = extract_token_from_email_body(body)
        assert token == "123456"

    def test_8_digit_number_not_accepted(self):
        """Test that an 8-digit number is not accepted as a CMP OTP."""
        body = "Your OTP code is 12345678"
        token = extract_token_from_email_body(body)
        assert token is None

    def test_labeled_token_does_not_match_prefix_of_longer_number(self):
        """Regression: a labeled 6-digit prefix of a longer number is rejected.

        ``token: 12345678`` must not yield ``123456`` - the labeled regex must
        not extract the first six digits of a longer number.
        """
        token = extract_token_from_email_body("token: 12345678")
        assert token is None

    def test_alphanumeric_not_accepted(self):
        """Test that alphanumeric strings are not accepted as CMP OTPs."""
        assert extract_token_from_email_body("Your verification code: ABC123") is None
        assert extract_token_from_email_body("OTP: XYZ789AB") is None

    def test_extract_token_with_label(self):
        """Test extraction when token has explicit label."""
        test_cases = [
            ("token: 123456", "123456"),
            ("token 123456", "123456"),
            ("OTP: 123456", "123456"),
            ("OTP 123456", "123456"),
            ("code: 123456", "123456"),
            ("code 123456", "123456"),
            ("verification: 123456", "123456"),
            ("verification 123456", "123456"),
        ]

        for body, expected in test_cases:
            token = extract_token_from_email_body(body)
            assert token == expected, f"Failed for body: {body}"

    def test_extract_token_case_insensitive(self):
        """Test that extraction is case insensitive."""
        test_cases = [
            "TOKEN: 123456",
            "Token: 123456",
            "token: 123456",
            "OTP: 123456",
            "otp: 123456",
            "CODE: 123456",
            "Code: 123456",
        ]

        for body in test_cases:
            token = extract_token_from_email_body(body)
            assert token == "123456"

    def test_extract_token_from_multiline_body(self):
        """Test extraction from multi-line email body."""
        body = """
        Dear User,

        Your CMP Portal OTP is:

        789012

        This code expires in 10 minutes.

        Regards,
        CMP Team
        """
        token = extract_token_from_email_body(body)
        assert token == "789012"

    def test_extract_token_with_special_chars(self):
        """Test extraction with special characters around token."""
        body = "Your code is [123456] - please use it."
        token = extract_token_from_email_body(body)
        assert token == "123456"

    def test_extract_token_with_html_entities(self):
        """Test extraction from HTML-like content."""
        body = "<p>Your OTP: <strong>123456</strong></p>"
        token = extract_token_from_email_body(body)
        assert token == "123456"

    def test_labeled_token_tolerates_html_markup(self):
        """Test that HTML markup between the label and the digits is tolerated."""
        body = "<p>Your <b>token</b>: <!-- spacer --> <strong>654321</strong></p>"
        token = extract_token_from_email_body(body)
        assert token == "654321"

    def test_html_css_noise_does_not_block_token(self):
        """Regression: CSS font declarations and hex colors must not create ambiguity.

        Reproduces the live failure where a real CMP email contained CSS values
        like ``300italic``/``400italic`` and colors like ``0563C1``/``954F72``
        alongside the labeled six-digit OTP.
        """
        body = (
            "<html><head><style>"
            "@font-face { font-family: 'Calibri'; font-weight: 300italic; }"
            "@font-face { font-weight: 400italic; }"
            "@font-face { font-weight: 600italic; }"
            "</style></head><body>"
            "<p>Your CMP token: <strong>654321</strong></p>"
            '<p style="color: #0563C1;">Some text</p>'
            '<p style="color: #954F72;">More text</p>'
            '<p style="background: #FFEB9C;">Note</p>'
            "</body></html>"
        )
        token = extract_token_from_email_body(body)
        assert token == "654321"

    def test_sender_parameter_is_accepted(self):
        """Test that the sender argument is accepted (API compatibility guard)."""
        token = extract_token_from_email_body(
            "token: 654321", sender="noreply@gmf-aeroasia.co.id"
        )
        assert token == "654321"

    def test_no_token_returns_none(self):
        """Test that None is returned when no token found."""
        body = "This email has no token."
        token = extract_token_from_email_body(body)
        assert token is None

    def test_short_numbers_not_matched(self):
        """Test that numbers shorter than 6 digits are not matched."""
        body = "Code: 12345"  # Only 5 digits
        token = extract_token_from_email_body(body)
        assert token is None

    def test_ambiguous_bare_numbers_return_none(self):
        """Test that multiple bare 6-digit numbers without a label fail closed."""
        body = "Your code: 123456 and reference is 789012"
        token = extract_token_from_email_body(body)
        assert token is None

    def test_ambiguous_numbers_log_safe_count_only(self, caplog):
        """Test that ambiguity is logged as a count, never the candidate values."""
        body = "Codes: 123456 and 654321"
        with caplog.at_level(logging.DEBUG, logger="cmp_automation.utils"):
            token = extract_token_from_email_body(body)

        assert token is None
        assert "candidate count" in caplog.text
        assert "123456" not in caplog.text
        assert "654321" not in caplog.text

    def test_token_at_start_of_body(self):
        """Test extraction when token is at start of body."""
        body = "123456 is your OTP"
        token = extract_token_from_email_body(body)
        assert token == "123456"

    def test_token_at_end_of_body(self):
        """Test extraction when token is at end of body."""
        body = "Your OTP is 123456"
        token = extract_token_from_email_body(body)
        assert token == "123456"
