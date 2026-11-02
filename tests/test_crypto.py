import pytest

from egorovsystem import crypto


def test_hkdf_matches_rfc5869_case_1():
    ikm = bytes([0x0B] * 22)
    salt = bytes(range(0x00, 0x0D))
    info = bytes(range(0xF0, 0xFA))
    okm = crypto.hkdf_sha256(ikm, info, 42, salt=salt)
    assert okm.hex() == (
        "3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf34007208d5b887185865"
    )


def test_seal_roundtrip_and_tamper_detection():
    key = crypto.new_vault_key()
    blob = crypto.seal(key, b"hello", b"aad")
    assert crypto.unseal(key, blob, b"aad") == b"hello"
    assert crypto.is_blob(blob)
    with pytest.raises(crypto.CryptoError):
        crypto.unseal(key, blob, b"other aad")
    with pytest.raises(crypto.CryptoError):
        crypto.unseal(crypto.new_vault_key(), blob, b"aad")
    version, nonce, body = blob.split(".")
    flipped = body[:-2] + ("A" if body[-2] != "A" else "B") + body[-1]
    with pytest.raises(crypto.CryptoError):
        crypto.unseal(key, f"{version}.{nonce}.{flipped}", b"aad")


def test_items_are_bound_to_kind_and_name():
    key = crypto.new_vault_key()
    blob = crypto.encrypt_item(key, "variable", "openai", {"value": "sk-123", "note": ""})
    assert crypto.decrypt_item(key, "variable", "openai", blob)["value"] == "sk-123"
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt_item(key, "variable", "aws", blob)
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt_item(key, "function", "openai", blob)


def test_admin_keys_are_independent_and_deterministic():
    kdf = crypto.new_kdf_params(1000)
    a = crypto.derive_admin_keys("pässword", kdf)
    b = crypto.derive_admin_keys("pässword", kdf)  # NFD form of the same text
    assert a == b
    assert a.auth_key != a.wrap_key


def test_device_token_roundtrip_and_repr_hides_secret():
    vault_key = crypto.new_vault_key()
    token, auth_hash, wrapped = crypto.provision_device(vault_key)
    parsed = crypto.parse_device_token(str(token))
    assert parsed == token
    assert crypto.auth_hash(parsed.auth_key) == auth_hash
    assert crypto.unwrap_vault_key(parsed.wrap_key, wrapped) == vault_key
    assert crypto.b64e(token.secret) not in repr(token)
    assert token.authorization().startswith(f"Bearer {token.device_id}.")


@pytest.mark.parametrize("bad", ["", "abc", "ego1.xyz.abc", "ego2.0123456789ab.AAAA", "ego1.0123456789ab.AAAA"])
def test_bad_device_tokens_are_rejected(bad):
    with pytest.raises(crypto.CryptoError):
        crypto.parse_device_token(bad)


def test_recovery_key_format_is_forgiving():
    raw = crypto.new_recovery_key()
    text = crypto.format_recovery_key(raw)
    assert crypto.parse_recovery_key(text) == raw
    assert crypto.parse_recovery_key(text.lower().replace("-", " ")) == raw
    with pytest.raises(crypto.CryptoError):
        crypto.parse_recovery_key("not a key")


def test_kdf_validation():
    good = crypto.new_kdf_params()
    assert crypto.validate_kdf_params(good) == good
    for bad in (None, {}, {**good, "alg": "md5"}, {**good, "iterations": 10}, {**good, "iterations": True},
                {**good, "salt": "AAAA"}):
        with pytest.raises(crypto.CryptoError):
            crypto.validate_kdf_params(bad)


@pytest.mark.parametrize("bad", ["a+b", "a/b", "abcde", "a=", 123])
def test_b64d_is_strict(bad):
    with pytest.raises(crypto.CryptoError):
        crypto.b64d(bad)
