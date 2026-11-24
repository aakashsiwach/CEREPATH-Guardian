from guardian.redaction import redact


def test_redacts_auth_and_secret_values() -> None:
    value = "Authorization: Bearer abc123 GEMINI_API_KEY=xyz Cookie: session=foo"
    cleaned = redact(value)
    assert "abc123" not in cleaned
    assert "xyz" not in cleaned
    assert "session=foo" not in cleaned
    assert "[REDACTED]" in cleaned


def test_redacts_aws_credentials_and_oauth_query_tokens() -> None:
    from guardian.redaction import redact

    result = redact(
        "AWS_SECRET_ACCESS_KEY=aws-secret "
        "https://example.test/callback?access_token=oauth-secret"
    )
    assert "aws-secret" not in result
    assert "oauth-secret" not in result
