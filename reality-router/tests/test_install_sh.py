"""Tests for install.sh idempotency and optional isolated override variables."""
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "install.sh"


def test_install_sh_syntax_and_defaults():
    """install.sh passes bash -n and preserves standard production defaults."""
    res = subprocess.run(["bash", "-n", str(INSTALL_SH)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr

    content = INSTALL_SH.read_text(encoding="utf-8")
    assert 'REPO_URL="${REALITY_ROUTER_REPO_URL:-https://github.com/Lars-confi/RealityRouter}"' in content
    assert 'BRANCH="${REALITY_ROUTER_BRANCH:-main}"' in content
    assert 'TARGET_DIR="${REALITY_ROUTER_HOME:-$HOME/.reality_router}"' in content


def test_install_sh_isolated_branch_and_idempotent_rerun(tmp_path):
    """install.sh installs a custom repo/branch into an isolated home without touching shell profiles."""
    origin_repo = tmp_path / "origin_repo"
    origin_repo.mkdir()
    git_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(["git", "init", "-b", "rr/test-branch"], cwd=origin_repo, check=True, env=git_env, capture_output=True)
    (origin_repo / "reality-router").mkdir()
    (origin_repo / "reality-router" / "requirements.txt").write_text("", encoding="utf-8")
    (origin_repo / "start_router.py").write_text("import os\nprint(os.environ.get('REALITY_ROUTER_HOME', ''))\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=origin_repo, check=True, env=git_env, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=origin_repo, check=True, env=git_env, capture_output=True)

    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    bashrc = fake_home / ".bashrc"
    bashrc.write_text("# original bashrc\n", encoding="utf-8")

    target_dir = tmp_path / "custom_rr_home"
    bin_dir = tmp_path / "custom_bin"

    run_env = {
        **os.environ,
        "HOME": str(fake_home),
        "SHELL": "/bin/bash",
        "REALITY_ROUTER_REPO_URL": str(origin_repo),
        "REALITY_ROUTER_BRANCH": "rr/test-branch",
        "REALITY_ROUTER_HOME": str(target_dir),
        "REALITY_ROUTER_BIN_DIR": str(bin_dir),
        "REALITY_ROUTER_BIN_NAME": "reality-router-dev",
        "REALITY_ROUTER_SKIP_SHELL_PROFILE": "1",
        "REALITY_ROUTER_SKIP_PIP": "1",
    }

    r1 = subprocess.run(["bash", str(INSTALL_SH)], env=run_env, capture_output=True, text=True)
    assert r1.returncode == 0, f"stdout:\n{r1.stdout}\nstderr:\n{r1.stderr}"
    assert (target_dir / ".git").is_dir()
    assert (target_dir / "venv").is_dir()
    wrapper = bin_dir / "reality-router-dev"
    assert wrapper.is_file() and os.access(wrapper, os.X_OK)
    assert bashrc.read_text(encoding="utf-8") == "# original bashrc\n"
    assert not (fake_home / ".reality_router").exists()

    w_res = subprocess.run([str(wrapper)], capture_output=True, text=True, env={"PATH": os.environ.get("PATH", ""), "HOME": str(fake_home)})
    assert w_res.returncode == 0, w_res.stderr
    assert w_res.stdout.strip() == str(target_dir)

    (target_dir / ".env").write_text("DEFAULT_STRATEGY=expected_utility\n", encoding="utf-8")
    r2 = subprocess.run(["bash", str(INSTALL_SH)], env=run_env, capture_output=True, text=True)
    assert r2.returncode == 0, f"stdout:\n{r2.stdout}\nstderr:\n{r2.stderr}"
    assert (target_dir / ".env").read_text(encoding="utf-8") == "DEFAULT_STRATEGY=expected_utility\n"
