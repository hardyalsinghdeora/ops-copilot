from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from ops_copilot.config import Settings, project_root


def test_project_root_prefers_env_then_source_checkout_then_cwd(tmp_path, monkeypatch):
    checkout = tmp_path / "repo"
    (checkout / "src" / "ops_copilot").mkdir(parents=True)
    in_checkout = checkout / "src" / "ops_copilot" / "config.py"
    in_site_packages = tmp_path / "venv" / "lib" / "site-packages" / "ops_copilot" / "config.py"
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.delenv("OPS_COPILOT_ROOT", raising=False)

    assert project_root(in_checkout) == checkout.resolve()
    assert project_root(in_site_packages) == workdir.resolve()  # regular pip install: run next to data/

    monkeypatch.setenv("OPS_COPILOT_ROOT", str(tmp_path / "elsewhere"))
    assert project_root(in_checkout) == (tmp_path / "elsewhere").resolve()


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_api_token_and_key_count_as_unset(blank, monkeypatch):
    monkeypatch.setenv("OPS_COPILOT_API_TOKEN", blank)
    monkeypatch.setenv("ANTHROPIC_API_KEY", blank)
    s = Settings(_env_file=None)
    assert s.api_token is None and s.anthropic_api_key is None


def test_short_api_token_is_rejected_at_startup(monkeypatch):
    monkeypatch.setenv("OPS_COPILOT_API_TOKEN", "too-short")
    with pytest.raises(ValidationError, match="OPS_COPILOT_API_TOKEN is too short"):
        Settings(_env_file=None)


def test_long_api_token_is_kept_and_keys_are_secret(monkeypatch):
    monkeypatch.setenv("OPS_COPILOT_API_TOKEN", "x" * 16)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    s = Settings(_env_file=None)
    assert isinstance(s.api_token, SecretStr) and s.api_token.get_secret_value() == "x" * 16
    assert isinstance(s.anthropic_api_key, SecretStr)
    assert "sk-ant-test" not in repr(s)
