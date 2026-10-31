from __future__ import annotations

from ops_copilot.config import project_root


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
