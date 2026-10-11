import importlib.util
from io import StringIO

import pytest


def test_healthz():
    assert importlib.util.find_spec("config"), "站点尚未实现"
    from django.test import Client
    response = Client().get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.django_db
def test_setup_does_not_overwrite(tmp_path, settings, caplog):
    from django.core.management import call_command
    from django.contrib.auth import get_user_model
    settings.XGIFT_KEY_FILE = tmp_path / "master.key"
    call_command("setup_xgift", username="admin", password="Initial-password-123!", stdout=StringIO())
    original = settings.XGIFT_KEY_FILE.read_bytes()
    call_command("setup_xgift", username="admin", password="Changed-password-456!", stdout=StringIO())
    assert settings.XGIFT_KEY_FILE.read_bytes() == original
    assert get_user_model().objects.get(username="admin").check_password("Initial-password-123!")
    assert "Initial-password-123!" not in caplog.text
    assert "Changed-password-456!" not in caplog.text


@pytest.mark.django_db
def test_setup_refuses_lost_key(tmp_path, settings):
    from django.core.management import call_command
    from django.core.management.base import CommandError
    from gift.models import Secret
    settings.XGIFT_KEY_FILE = tmp_path / "missing.key"
    Secret.objects.create(name="session", ciphertext=b"existing-encrypted-value")
    with pytest.raises(CommandError, match="主密钥缺失"):
        call_command("setup_xgift", password="Initial-password-123!", stdout=StringIO())
    assert not settings.XGIFT_KEY_FILE.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("inactive_admin", [False, True])
def test_setup_rejects_existing_non_admin(tmp_path, settings, inactive_admin):
    from django.contrib.auth import get_user_model
    from django.core.management import call_command
    from django.core.management.base import CommandError
    User = get_user_model()
    user = User.objects.create_user(
        username="admin", password="Existing-password-123!",
        is_staff=inactive_admin, is_superuser=inactive_admin,
        is_active=not inactive_admin,
    )
    settings.XGIFT_KEY_FILE = tmp_path / "master.key"
    with pytest.raises(CommandError, match="现有账户不是可用管理员"):
        call_command("setup_xgift", username="admin", stdout=StringIO())
    user.refresh_from_db()
    assert user.check_password("Existing-password-123!")
    assert user.is_active == (not inactive_admin)
    assert user.is_staff == inactive_admin
    assert user.is_superuser == inactive_admin
    assert not settings.XGIFT_KEY_FILE.exists()
