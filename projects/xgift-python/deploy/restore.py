"""Offline restore: stop both services first; never overwrite without explicit --replace.

The backup directory must contain a consistent pair: db.sqlite3 and master.key.
Existing files are renamed aside (never deleted) so the previous deployment stays
rollback-able, and stale SQLite WAL/SHM side files are moved with them.
"""
import argparse
import os
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path


def restore(source, database, key, *, replace=False):
    source = Path(source).resolve()
    database = Path(database).resolve()
    key = Path(key).resolve()
    source_db = source / 'db.sqlite3'
    source_key = source / 'master.key'

    if not source_db.is_file() or not source_key.is_file():
        raise ValueError('备份目录缺少 db.sqlite3 或 master.key')
    if len(source_key.read_bytes()) != 32:
        raise ValueError('备份主密钥无效（必须为 32 字节）')
    with closing(sqlite3.connect(source_db.as_uri() + '?mode=ro', uri=True)) as check:
        if check.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('备份数据库完整性检查失败')

    targets = [database, key]
    targets += [database.with_name(database.name + suffix) for suffix in ('-wal', '-shm')]
    existing = [path for path in targets if path.exists()]
    if existing and not replace:
        raise ValueError('目标已存在数据库/密钥/残留文件；确认服务已停止后使用 --replace 保留旧数据并替换')

    if existing:
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        for path in existing:
            path.rename(path.with_name(path.name + '.replaced-' + stamp))

    database.parent.mkdir(parents=True, exist_ok=True)
    key.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_db, database)
    shutil.copyfile(source_key, key)
    if os.name == 'nt':
        try:
            os.chmod(key, 0o600)
        except OSError:
            pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True, help='备份目录（含 db.sqlite3 与 master.key）')
    parser.add_argument('--replace', action='store_true', help='将现有数据库/密钥改名保留后替换')
    args = parser.parse_args()
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    import django
    django.setup()
    from django.conf import settings
    restore(args.source, settings.DATABASES['default']['NAME'], settings.XGIFT_KEY_FILE, replace=args.replace)
    print('恢复完成；请先运行 manage.py check，再启动网站与 worker。')
