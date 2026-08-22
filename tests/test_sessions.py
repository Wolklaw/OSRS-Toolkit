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
    signer = SessionSigner("secret")
    cookie = signer.issue("111")

    assert signer.read(cookie[:-1] + ("x" if cookie[-1] != "x" else "y")) is None


def test_a_cookie_signed_under_a_different_secret_is_rejected():
    cookie = SessionSigner("secret-a").issue("111")

    assert SessionSigner("secret-b").read(cookie) is None


def test_garbage_input_does_not_raise():
    assert SessionSigner("secret").read("not-a-real-cookie-at-all") is None
