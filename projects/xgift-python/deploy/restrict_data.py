"""Run only during administrator-requested installation, never on module import."""
import argparse
import os
from pathlib import Path
import subprocess
from deploy.environment import load_environment

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--account',required=True);args=parser.parse_args()
    load_environment(os.environ['XGIFT_ENV_FILE'])
    import django
    os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings');django.setup()
    from django.conf import settings
    folders={Path(settings.XGIFT_KEY_FILE).resolve().parent,Path(settings.DATABASES['default']['NAME']).resolve().parent}
    root=Path(settings.BASE_DIR).resolve()
    for folder in folders:
        if folder==root or folder.parent==folder or folder in root.parents:
            raise ValueError('Use dedicated data/key directories, not project/root directories')
        folder.mkdir(parents=True,exist_ok=True)
        subprocess.run(['icacls',str(folder),'/T','/inheritance:r','/grant:r',args.account+':(OI)(CI)M','SYSTEM:(OI)(CI)F','BUILTIN\\Administrators:(OI)(CI)F'],check=True,capture_output=True)
