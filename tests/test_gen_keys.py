import importlib.util
import subprocess
import sys

from cryptography.fernet import Fernet

from app.config import PROJECT_ROOT

SCRIPT = PROJECT_ROOT / "scripts" / "gen_keys.py"


def _load():
    spec = importlib.util.spec_from_file_location("gen_keys", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fernet_key_is_valid_and_fresh():
    mod = _load()
    k1, k2 = mod.generate_fernet_key(), mod.generate_fernet_key()
    assert k1 != k2
    f = Fernet(k1.encode())
    assert f.decrypt(f.encrypt(b"x")) == b"x"


def test_session_secret_is_long_and_fresh():
    mod = _load()
    a, b = mod.generate_session_secret(), mod.generate_session_secret()
    assert a != b and len(a) >= 32


def test_script_prints_both_keys_and_does_not_touch_env(tmp_path):
    env_file = PROJECT_ROOT / ".env"
    before = env_file.stat().st_mtime_ns if env_file.exists() else None
    out = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, check=True, cwd=tmp_path)
    lines = out.stdout.strip().splitlines()
    assert [ln.split("=", 1)[0] for ln in lines] == ["FERNET_KEY", "SESSION_SECRET"]
    Fernet(lines[0].split("=", 1)[1].encode())
    after = env_file.stat().st_mtime_ns if env_file.exists() else None
    assert before == after
    assert not (tmp_path / ".env").exists()
