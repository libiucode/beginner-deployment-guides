"""新手引导相关：演示数据命令与离线恢复脚本。"""
import os
import sqlite3
from io import StringIO

import pytest


@pytest.mark.django_db
def test_demo_setup_creates_plan_codes_and_is_idempotent(tmp_path, settings):
    from django.core.management import call_command
    from gift.models import Code, Folder, Plan
    settings.XGIFT_KEY_FILE = tmp_path / 'master.key'
    settings.XGIFT_KEY_FILE.write_bytes(b'a' * 32)
    settings.XGIFT_MODE = 'simulated'

    call_command('demo_setup', count=3, stdout=StringIO())
    assert Plan.objects.count() == 1
    assert Code.objects.count() == 3
    assert Folder.objects.count() == 1

    call_command('demo_setup', count=3, only_if_empty=True, stdout=StringIO())
    assert Code.objects.count() == 3

    call_command('demo_setup', count=2, stdout=StringIO())
    assert Code.objects.count() == 5
    assert Plan.objects.count() == 1


@pytest.mark.django_db
def test_demo_setup_refuses_live_mode(tmp_path, settings):
    from django.core.management import call_command
    from django.core.management.base import CommandError
    from gift.models import Code
    settings.XGIFT_KEY_FILE = tmp_path / 'master.key'
    settings.XGIFT_KEY_FILE.write_bytes(b'a' * 32)
    settings.XGIFT_MODE = 'live'
    with pytest.raises(CommandError, match='模拟模式'):
        call_command('demo_setup', stdout=StringIO())
    assert not Code.objects.exists()


def _make_backup(root, *, status='succeeded', key=None):
    root.mkdir(parents=True, exist_ok=True)
    key = key or os.urandom(32)
    (root / 'master.key').write_bytes(key)
    with sqlite3.connect(root / 'db.sqlite3') as connection:
        connection.execute('CREATE TABLE gift_order (status TEXT)')
        connection.execute('INSERT INTO gift_order VALUES (?)', (status,))
    return key


def test_restore_replaces_and_keeps_previous_pair(tmp_path):
    from deploy.restore import restore
    source = tmp_path / 'backup'
    new_key = _make_backup(source)
    target = tmp_path / 'data with spaces'
    target.mkdir()
    (target / 'db.sqlite3').write_bytes(b'old-database')
    (target / 'master.key').write_bytes(b'b' * 32)
    (target / 'db.sqlite3-wal').write_bytes(b'old-wal')

    restore(source, target / 'db.sqlite3', target / 'master.key', replace=True)

    with sqlite3.connect(target / 'db.sqlite3') as restored:
        assert restored.execute('SELECT status FROM gift_order').fetchone()[0] == 'succeeded'
    assert (target / 'master.key').read_bytes() == new_key
    assert not (target / 'db.sqlite3-wal').exists()
    assert list(target.glob('db.sqlite3.replaced-*')) and list(target.glob('master.key.replaced-*'))
    assert list(target.glob('db.sqlite3-wal.replaced-*'))


def test_restore_refuses_untracked_replace_is_required(tmp_path):
    from deploy.restore import restore
    source = tmp_path / 'backup'
    _make_backup(source)
    target = tmp_path / 'data'
    target.mkdir()
    (target / 'db.sqlite3').write_bytes(b'old-database')
    (target / 'master.key').write_bytes(b'b' * 32)
    with pytest.raises(ValueError, match='--replace'):
        restore(source, target / 'db.sqlite3', target / 'master.key')
    assert (target / 'db.sqlite3').read_bytes() == b'old-database'
    assert (target / 'master.key').read_bytes() == b'b' * 32


def test_restore_rejects_invalid_backup_pair(tmp_path):
    from deploy.restore import restore
    source = tmp_path / 'backup'
    source.mkdir()
    (source / 'master.key').write_bytes(b'tooshort')
    with pytest.raises(ValueError, match='db.sqlite3'):
        restore(source, tmp_path / 'data' / 'db.sqlite3', tmp_path / 'data' / 'master.key', replace=True)
