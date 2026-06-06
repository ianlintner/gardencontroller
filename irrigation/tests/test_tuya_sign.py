import hashlib, hmac
import pytest

def test_string_to_sign(core):
    sts = core.tuya_string_to_sign("GET", "", "/v1.0/token?grant_type=1")
    body_hash = hashlib.sha256(b"").hexdigest()
    assert sts == f"GET\n{body_hash}\n\n/v1.0/token?grant_type=1"

def test_signature_matches_manual_hmac(core):
    # Tuya concatenation order: client_id + access_token + t + nonce + stringToSign
    payload = "cid" + "" + "1700000000000" + "n" + "GET\nx\n\n/p"
    expected = hmac.new(b"sec", payload.encode(), hashlib.sha256).hexdigest().upper()
    sig = core.tuya_sign(client_id="cid", secret="sec", access_token="",
                           t="1700000000000", nonce="n", string_to_sign="GET\nx\n\n/p")
    assert sig == expected


def test_tuya_call_raises_on_unsuccessful(core):
    """Tuya._call must raise TuyaError when success=False."""
    def failing_http(method, url, headers, body):
        return {"success": False, "msg": "boom"}

    t = core.Tuya("cid", "sec", http=failing_http)
    with pytest.raises(core.TuyaError, match="boom"):
        t.token()
