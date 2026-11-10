"""
test_oauth_helper.py — the parts of the OAuth helper that broke in practice.

Two real-world failures are pinned here:

  1. Error 400: redirect_uri_mismatch — caused by using a Playground-style
     Web client where a Desktop client is needed, or by a redirect URI that was
     never registered. `build_auth_url` must therefore use
     http://127.0.0.1:<port>/ (the only manual flow Google still supports) and
     must carry access_type=offline + prompt=consent, otherwise Google returns
     no refresh token at all.

  2. The out-of-band flow (`urn:ietf:wg:oauth:2.0:oob`) was removed in Oct
     2022, so users can no longer copy "a code off the page". They paste the
     whole redirect URL instead; `parse_code_from_pasted` must handle that,
     the bare-code form, and reject junk with a readable message.
"""

from __future__ import annotations

import urllib.parse

import pytest

import oauth_helper as oh


# --------------------------------------------------------------------------- #
# auth URL shape
# --------------------------------------------------------------------------- #
def test_auth_url_uses_loopback_and_offline_access():
    url = oh.build_auth_url(
        "cid.apps.googleusercontent.com", "http://127.0.0.1:8765/", oh.SCOPES["readonly"], "st8"
    )
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)

    assert parsed.netloc == "accounts.google.com"
    assert query["redirect_uri"][0] == "http://127.0.0.1:8765/"
    # without these two Google hands back an access token only -> bot dies in 1h
    assert query["access_type"][0] == "offline"
    assert query["prompt"][0] == "consent"
    assert query["response_type"][0] == "code"
    assert query["scope"][0] == "https://www.googleapis.com/auth/drive.readonly"


def test_auth_url_never_uses_the_removed_oob_redirect():
    url = oh.build_auth_url("cid", "http://127.0.0.1:8765/", oh.SCOPES["readonly"], "s")
    assert "oob" not in url


def test_full_scope_is_offered_for_quotafix():
    assert oh.SCOPES["full"] == "https://www.googleapis.com/auth/drive"


# --------------------------------------------------------------------------- #
# pasted input
# --------------------------------------------------------------------------- #
def test_parse_full_redirect_url():
    pasted = (
        "http://127.0.0.1:8765/?state=abc123&code=4/0AX4XfWh-abc_DEF&scope=https://www.googleapis.com/auth/drive.readonly"
    )
    code, state = oh.parse_code_from_pasted(pasted)
    assert code == "4/0AX4XfWh-abc_DEF"
    assert state == "abc123"


def test_parse_bare_code():
    code, state = oh.parse_code_from_pasted("  4/0AX4XfWh-abc_DEF  ")
    assert code == "4/0AX4XfWh-abc_DEF"
    assert state is None


def test_parse_query_string_only():
    code, state = oh.parse_code_from_pasted("?code=4/0AX4XfWh-xyz&state=zz")
    assert code == "4/0AX4XfWh-xyz" and state == "zz"


def test_parse_error_in_redirect_is_surfaced():
    with pytest.raises(ValueError) as err:
        oh.parse_code_from_pasted("http://127.0.0.1:8765/?error=access_denied")
    assert "access_denied" in str(err.value)


def test_parse_junk_is_rejected_clearly():
    with pytest.raises(ValueError) as err:
        oh.parse_code_from_pasted("hmm not sure")
    assert "address-bar URL" in str(err.value) or "code" in str(err.value)


# --------------------------------------------------------------------------- #
# client json handling
# --------------------------------------------------------------------------- #
def test_read_client_accepts_desktop_and_web(tmp_path):
    desktop = tmp_path / "desktop.json"
    desktop.write_text('{"installed": {"client_id": "a.apps.googleusercontent.com", "client_secret": "s"}}')
    assert oh.read_client(desktop) == ("a.apps.googleusercontent.com", "s")

    web = tmp_path / "web.json"
    web.write_text('{"web": {"client_id": "b.apps.googleusercontent.com", "client_secret": "t"}}')
    cid, secret = oh.read_client(web)
    assert (cid, secret) == ("b.apps.googleusercontent.com", "t")


def test_read_client_explains_missing_secret(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"installed": {"client_id": "only-id"}}')
    with pytest.raises(SystemExit):
        oh.read_client(bad)
