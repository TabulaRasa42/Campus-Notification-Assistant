"""Isolate the external credential store; no test reads real user secrets."""

import httpx
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

from campus_notification_assistant import credentials


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        self.values = {}

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, password):
        self.values[service, username] = password

    def delete_password(self, service, username):
        if self.values.pop((service, username), None) is None:
            raise PasswordDeleteError("No test credential")


@pytest.fixture(autouse=True)
def isolated_credentials(monkeypatch):
    backend = MemoryKeyring()
    monkeypatch.setattr(credentials, "WinVaultKeyring", lambda: backend)


@pytest.fixture(autouse=True)
def forbid_real_http(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError(
            "Tests must replace the external HTTP boundary; real requests forbidden"
        )

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
