import importlib.util

import pytest
from cryptography.exceptions import InvalidTag


def vault():
    assert importlib.util.find_spec("gift"), "加密配置尚未实现"
    from gift.services import vault
    return vault


def test_vault_wrong_key(tmp_path, settings):
    service = vault()
    settings.XGIFT_KEY_FILE = tmp_path / "master.key"
    settings.XGIFT_KEY_FILE.write_bytes(bytes(range(32)))
    ciphertext = service.seal(b"test-session-secret", "x-session")
    original = bytes(ciphertext)
    settings.XGIFT_KEY_FILE.write_bytes(b"z" * 32)
    with pytest.raises(InvalidTag):
        service.unseal(ciphertext, "x-session")
    assert ciphertext == original


def test_secret_not_in_logs(tmp_path, settings, caplog):
    service = vault()
    settings.XGIFT_KEY_FILE = tmp_path / "master.key"
    settings.XGIFT_KEY_FILE.write_bytes(b"a" * 32)
    credential = b"test-sensitive-credential"
    payload = service.seal(credential, "session")
    assert service.unseal(payload, "session") == credential
    with pytest.raises(InvalidTag):
        service.unseal(payload, "other-purpose")
    assert credential.decode() not in caplog.text


def test_random_nonce(tmp_path, settings):
    service = vault()
    settings.XGIFT_KEY_FILE = tmp_path / "master.key"
    settings.XGIFT_KEY_FILE.write_bytes(b"a" * 32)
    assert service.seal(b"same", "session") != service.seal(b"same", "session")


def test_tampered_ciphertext(tmp_path, settings):
    service = vault()
    settings.XGIFT_KEY_FILE = tmp_path / "master.key"
    settings.XGIFT_KEY_FILE.write_bytes(b"a" * 32)
    payload = bytearray(service.seal(b"credential", "session"))
    payload[-1] ^= 1
    with pytest.raises(InvalidTag):
        service.unseal(bytes(payload), "session")
    with pytest.raises(ValueError, match="密文"):
        service.unseal(b"short", "session")
