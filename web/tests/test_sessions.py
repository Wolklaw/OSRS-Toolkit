from __future__ import annotations

from toolkit_web.sessions import SessionSigner


def test_a_session_round_trips():
    signer = SessionSigner("secret")
    cookie = signer.issue("111")

    assert signer.read(cookie) == "111"


def test_no_cookie_is_no_session():
    assert SessionSigner("secret").read(None) is None
    assert SessionSigner("secret").read("") is None


def test_a_tampered_cookie_is_rejected():
    """Mutated in the middle of the string, not at the very end.

    The last character of a base64url-encoded HMAC can sit on unused padding bits — flipping
    it can occasionally decode to the identical byte sequence by coincidence, which made this
    test flaky rather than wrong. A character mid-signature has no such ambiguity.
    """
    signer = SessionSigner("secret")
    cookie = signer.issue("111")
    middle = len(cookie) // 2

    tampered = cookie[:middle] + ("x" if cookie[middle] != "x" else "y") + cookie[middle + 1 :]

    assert signer.read(tampered) is None


def test_a_cookie_signed_under_a_different_secret_is_rejected():
    cookie = SessionSigner("secret-a").issue("111")

    assert SessionSigner("secret-b").read(cookie) is None


def test_garbage_input_does_not_raise():
    assert SessionSigner("secret").read("not-a-real-cookie-at-all") is None
