"""初始化；重复执行不覆盖主密钥和密码。"""
import getpass
import os
from pathlib import Path
import subprocess
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.core.exceptions import ValidationError
from gift.models import Secret, Code


class Command(BaseCommand):
    help = "初始化独立主密钥和管理员，不覆盖已有配置"

    def add_arguments(self, parser):
        parser.add_argument("--username", default="admin", help="初始管理员用户名")
        parser.add_argument("--password", help="仅供自动测试使用；日常运行请使用隐藏输入")

    def handle(self, *args, **options):
        User = get_user_model()
        username = options["username"]
        existing = User.objects.filter(username=username).first()
        if existing and not (existing.is_active and existing.is_staff and existing.is_superuser):
            raise CommandError("现有账户不是可用管理员，请选择其他用户名；原账户不会修改")
        password = None
        if not existing:
            password = options["password"] or getpass.getpass("初始管理员密码：")
            try:
                validate_password(password, User(username=username))
            except ValidationError as error:
                raise CommandError("；".join(error.messages)) from None
        key_file = Path(settings.XGIFT_KEY_FILE)
        if not key_file.exists():
            if Secret.objects.exists() or Code.objects.exists():
                raise CommandError("已有加密配置但主密钥缺失，请恢复原密钥")
            key_file.parent.mkdir(parents=True, exist_ok=True)
            try:
                with key_file.open("xb") as stream:
                    stream.write(os.urandom(32))
                if os.name == "nt":
                    result = subprocess.run(
                        ["icacls", str(key_file), "/inheritance:r", "/grant:r", f"{getpass.getuser()}:(F)"],
                        capture_output=True, check=False,
                    )
                    if result.returncode:
                        key_file.unlink()
                        raise CommandError("主密钥文件访问权限设置失败")
                else:
                    key_file.chmod(0o600)
            except FileExistsError:
                pass
        if len(key_file.read_bytes()) != 32:
            raise CommandError("主密钥文件格式无效，不会覆盖")
        if not existing:
            User.objects.create_superuser(username=username, password=password)
        self.stdout.write("初始化完成；已有密钥和管理员密码均未覆盖。")
