import re
import sqlite3

import pytest

from egorovsystem import crypto
from egorovsystem.server import cli, vault
from egorovsystem.server.db import Database


@pytest.fixture
def fast_kdf(monkeypatch):
    original = vault.initialize
    monkeypatch.setattr(vault, "initialize", lambda db, u, p: original(db, u, p, iterations=1000))
    original_reset = vault.reset_password
    monkeypatch.setattr(vault, "reset_password", lambda db, r, p: original_reset(db, r, p, iterations=1000))


def feed(monkeypatch, inputs=(), secrets=()):
    inputs, secrets = list(inputs), list(secrets)
    monkeypatch.setattr("builtins.input", lambda prompt="": inputs.pop(0))
    monkeypatch.setattr("getpass.getpass", lambda prompt="": secrets.pop(0))


def test_init_info_backup_reset_and_import(tmp_path, monkeypatch, capsys, fast_kdf):
    data = str(tmp_path / "data")
    feed(monkeypatch, inputs=["root"], secrets=["short", "a strong passphrase", "a strong passphrase"])
    cli.init(["--data-dir", data])
    out = capsys.readouterr().out
    assert "Too weak" in out
    recovery = re.search(r"RECOVERY KEY.*?\n\n\s+(\S+)", out, re.S).group(1)
    assert crypto.parse_recovery_key(recovery)

    with pytest.raises(SystemExit):
        cli.init(["--data-dir", data])

    cli.admin(["--data-dir", data, "info"])
    assert "Admin:      root" in capsys.readouterr().out

    cli.admin(["--data-dir", data, "backup", str(tmp_path / "b.sqlite3")])
    assert (tmp_path / "b.sqlite3").exists()

    feed(monkeypatch, secrets=[recovery, "another strong one", "another strong one"])
    cli.admin(["--data-dir", data, "reset-password"])
    assert "Master password changed" in capsys.readouterr().out

    legacy = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(legacy)
    conn.executescript("CREATE TABLE server_variable (id INTEGER PRIMARY KEY, key TEXT, val TEXT);"
                       "INSERT INTO server_variable (key, val) VALUES ('k', 'v');")
    conn.commit()
    conn.close()
    feed(monkeypatch, secrets=["another strong one"])
    cli.admin(["--data-dir", data, "import-legacy", str(legacy)])
    assert "Imported 1 item(s)" in capsys.readouterr().out

    feed(monkeypatch, secrets=["wrong password here"])
    with pytest.raises(SystemExit) as exc:
        cli.admin(["--data-dir", data, "import-legacy", str(legacy)])
    assert "wrong password" in str(exc.value)

    cli.admin(["--data-dir", data, "disable-totp"])
    cli.admin(["--data-dir", data, "revoke-sessions"])
    with Database(tmp_path / "data" / "egorov.sqlite3").read() as c:
        assert c.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1


def test_run_refuses_without_vault(tmp_path):
    with pytest.raises(SystemExit) as exc:
        cli.run(["--data-dir", str(tmp_path / "empty")])
    assert "egoserver-init" in str(exc.value)
