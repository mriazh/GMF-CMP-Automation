"""Tests for OTP token extraction from email bodies."""


from cmp_automation.utils import extract_token_from_email_body


class TestTokenExtraction:
    """Tests for extracting OTP tokens from email bodies."""

    def test_extract_6_digit_numeric(self):
        """Test extraction of 6-digit numeric token."""
        body = "Your OTP code is 123456. Do not share it."
        token = extract_token_from_email_body(body)
        assert token == "123456"

    def test_extract_8_digit_numeric(self):
        """Test extraction of 8-digit numeric token."""
        body = "Token: 12345678"
        token = extract_token_from_email_body(body)
        assert token == "12345678"

    def test_extract_alphanumeric_6_chars(self):
        """Test extraction of 6-char alphanumeric token."""
        body = "Your verification code: ABC123"
        token = extract_token_from_email_body(body)
        assert token == "ABC123"

    def test_extract_alphanumeric_8_chars(self):
        """Test extraction of 8-char alphanumeric token."""
        body = "OTP: XYZ789AB"
        token = extract_token_from_email_body(body)
        assert token == "XYZ789AB"

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

    def test_prefers_labeled_token(self):
        """Test that labeled tokens are preferred over bare numbers."""
        body = "Your code: 123456 and reference is 789012"
        token = extract_token_from_email_body(body)
        # Labeled token should be preferred
        assert token == "123456"

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
