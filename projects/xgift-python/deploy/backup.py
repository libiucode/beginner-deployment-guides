"""Offline backup: stop both processes first; never replace an existing backup."""
from pathlib import Path
import argparse
from contextlib import closing
import shutil
import sqlite3
import os
import getpass
import subprocess

def backup(database, key, destination):
    database=Path(database).resolve(); key=Path(key).resolve(); destination=Path(destination).resolve()
    if not database.is_file() or len(key.read_bytes())!=32: raise ValueError('Database/key missing or invalid')
    destination.mkdir(parents=True,exist_ok=False)
    try:
        if os.name=='nt':
            subprocess.run(['icacls',str(destination),'/inheritance:r','/grant:r',getpass.getuser()+':(OI)(CI)F','SYSTEM:(OI)(CI)F','BUILTIN\\Administrators:(OI)(CI)F'],check=True,capture_output=True)
        else: destination.chmod(0o700)
        with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as source:
            with closing(sqlite3.connect(destination/'db.sqlite3')) as target:
                source.backup(target)
                if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('Backup integrity failed')
        shutil.copyfile(key,destination/'master.key')
    except BaseException:
        shutil.rmtree(destination)
        raise

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--destination',required=True)
    args=parser.parse_args()
    import os
    import django
    os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); django.setup()
    from django.conf import settings
    backup(settings.DATABASES['default']['NAME'],settings.XGIFT_KEY_FILE,args.destination)
    print('Backup complete; protect directory permissions.')
