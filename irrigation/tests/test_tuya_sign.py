import hashlib, hmac

def test_string_to_sign(garden):
    sts = garden.tuya_string_to_sign("GET", "", "/v1.0/token?grant_type=1")
    body_hash = hashlib.sha256(b"").hexdigest()
    assert sts == f"GET\n{body_hash}\n\n/v1.0/token?grant_type=1"

def test_signature_matches_manual_hmac(garden):
    # Tuya concatenation order: client_id + access_token + t + nonce + stringToSign
    payload = "cid" + "" + "1700000000000" + "n" + "GET\nx\n\n/p"
    expected = hmac.new(b"sec", payload.encode(), hashlib.sha256).hexdigest().upper()
    sig = garden.tuya_sign(client_id="cid", secret="sec", access_token="",
                           t="1700000000000", nonce="n", string_to_sign="GET\nx\n\n/p")
    assert sig == expected
