from pathlib import Path
import os
import re

def boolean(value):
    if value.lower() in ('1','true'): return True
    if value.lower() in ('0','false'): return False
    raise ValueError('Boolean must be true/false or 1/0')

def load_environment(path, target=None):
    target = os.environ if target is None else target
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        line=line.strip()
        if not line or line.startswith('#'): continue
        name, separator, value=line.partition('=')
        name=name.strip(); value=value.strip()
        if not separator or not re.fullmatch(r'[A-Z][A-Z0-9_]*',name):
            raise ValueError('Invalid environment file entry')
        if value.startswith(('"',"'")):
            if len(value)<2 or value[-1]!=value[0]: raise ValueError('Unclosed environment value')
            value=value[1:-1]
        target[name]=value
