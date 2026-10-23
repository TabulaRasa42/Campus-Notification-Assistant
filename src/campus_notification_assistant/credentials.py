"""只使用 Windows 凭据存储，不允许回退到明文后端。"""

from keyring.backend import KeyringBackend
from keyring.backends.Windows import WinVaultKeyring
from keyring.errors import KeyringError, PasswordDeleteError
from win32ctypes.pywin32 import pywintypes  # type: ignore[import-untyped]

from .errors import ServiceError

DEFAULT_NAMESPACE = "CampusNotificationAssistant.DeepSeek"


class Credentials:
    def __init__(self, namespace: str = DEFAULT_NAMESPACE) -> None:
        self.namespace = namespace
        self.backend: KeyringBackend = WinVaultKeyring()  # type: ignore[no-untyped-call]

    def read(self) -> str | None:
        try:
            return self.backend.get_password(self.namespace, "api-key")
        except (KeyringError, OSError, RuntimeError, pywintypes.error):
            raise ServiceError("无法访问 Windows 凭据存储，请检查当前用户权限。") from None

    def save(self, secret: str) -> None:
        secret = secret.strip()
        if not secret or not secret.isascii() or any(c.isspace() for c in secret):
            raise ServiceError("请输入有效的 API Key（不能包含空白字符）。")
        try:
            self.backend.set_password(self.namespace, "api-key", secret)
        except (KeyringError, OSError, RuntimeError, pywintypes.error):
            raise ServiceError("密钥未保存，无法写入 Windows 凭据存储。") from None

    def clear(self) -> None:
        try:
            self.backend.delete_password(self.namespace, "api-key")
        except PasswordDeleteError:
            pass
        except (KeyringError, OSError, RuntimeError, pywintypes.error):
            raise ServiceError("无法删除 Windows 凭据，请稍后重试。") from None
