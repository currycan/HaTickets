"""start_ticket_grabbing.sh 参数解析层的 fail-fast 测试（U-02）。

用 subprocess 直接驱动 bash 脚本，无需真机 / poetry / adb：
- typo 用例在参数解析层（一切副作用之前）即退出
- 合法参数用例通过裁剪 PATH（无 poetry）证明「解析层放行、版本诊断后止步于环境预检」
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "mobile" / "scripts" / "start_ticket_grabbing.sh"
# 无 poetry 的最小 PATH，用于验证合法参数停在运行环境预检层（exit 2）
SAFE_PATH = "/usr/bin:/bin"


def run_script(args, env=None, stdin=subprocess.DEVNULL, script=SCRIPT):
    return subprocess.run(
        ["bash", str(script), *args],
        capture_output=True,
        text=True,
        env=env,
        stdin=stdin,
        timeout=30,
    )


def copy_isolated_start_script(tmp_path):
    """Copy the startup layer so the real repository's .venv cannot affect tests."""
    root = tmp_path / "repo"
    script = root / "mobile" / "scripts" / "start_ticket_grabbing.sh"
    preflight = root / "mobile" / "scripts" / "lib" / "preflight.sh"
    preflight.parent.mkdir(parents=True)
    shutil.copy2(SCRIPT, script)
    shutil.copy2(REPO / "mobile" / "scripts" / "lib" / "preflight.sh", preflight)
    return script


@pytest.fixture
def tmp_config(tmp_path):
    config = tmp_path / "config.jsonc"
    config.write_text(
        '{"probe_only": true, "if_commit_order": false}', encoding="utf-8"
    )
    return config


class TestUnknownArgsFailFast:
    @pytest.mark.parametrize(
        "argv",
        [
            ["--porbe", "--yes"],
            ["--prob"],
            ["--probe=true", "--yes"],
            ["--yes", "--porbe"],
            ["junk-positional"],
            ["-probe"],
        ],
        ids=lambda a: " ".join(a) if isinstance(a, list) else a,
    )
    def test_unknown_arg_fails_fast_and_keeps_config(self, argv, tmp_config):
        before = tmp_config.read_bytes()
        r = run_script(argv)
        assert r.returncode == 1
        combined = r.stdout + r.stderr
        assert "未知参数" in combined
        # 被拒 token 原样出现在报错里
        bad_token = next(a for a in argv if a not in ("--yes",))
        assert bad_token in combined
        # 用法帮助打到 stderr
        assert "用法" in r.stderr
        assert "--probe" in r.stderr
        # 配置逐字节不变
        assert tmp_config.read_bytes() == before
        # 证明在 poetry 预检与任何配置改写之前就退出（解析已上移到脚本顶部）
        assert "已写回配置文件" not in combined
        assert "启动大麦" not in combined
        assert "Poetry 未安装" not in combined

    def test_yes_porbe_order_insensitive(self, tmp_config):
        """合法参数在前不会让循环提前放行。"""
        r = run_script(["--yes", "--porbe"])
        assert r.returncode == 1
        assert "未知参数" in (r.stdout + r.stderr)


class TestHelpAndConfigEdge:
    @pytest.mark.parametrize("flag", ["--help", "-h"])
    def test_help_exits_zero(self, flag, tmp_config):
        before = tmp_config.read_bytes()
        r = run_script([flag])
        assert r.returncode == 0
        assert "用法" in r.stdout
        assert "--probe" in r.stdout
        assert "未知参数一律报错" in r.stdout
        assert "未知参数:" not in r.stdout + r.stderr
        assert tmp_config.read_bytes() == before

    def test_config_flag_missing_value(self):
        r = run_script(["--probe", "--yes", "--config"])
        assert r.returncode == 1
        assert "--config 需要一个文件路径" in (r.stdout + r.stderr)

    def test_config_eq_empty_value_rejected(self, tmp_config):
        before = tmp_config.read_bytes()
        r = run_script(["--config=", "--probe"])
        assert r.returncode == 1
        assert "--config= 需要一个文件路径" in (r.stdout + r.stderr)
        assert tmp_config.read_bytes() == before


class TestLegalArgsPassParser:
    @pytest.mark.parametrize(
        "argv",
        [
            ["--probe", "--yes"],
            ["--probe"],
            ["--probe", "--yes", "--config", "/tmp/x.jsonc"],
        ],
        ids=lambda a: " ".join(a) if isinstance(a, list) else a,
    )
    def test_legal_args_stop_at_environment_precheck(self, argv, tmp_path):
        # 防呆：若某环境把 poetry 装进 /usr/bin，本测试假设不成立
        assert shutil.which("poetry", path=SAFE_PATH) is None, (
            "SAFE_PATH 下不应有 poetry，可调整 SAFE_PATH"
        )
        script = copy_isolated_start_script(tmp_path)
        r = run_script(
            argv,
            env={"PATH": SAFE_PATH, "HOME": str(tmp_path)},
            script=script,
        )
        assert r.returncode == 2
        combined = r.stdout + r.stderr
        assert "Poetry 未安装" in combined
        assert "未知参数" not in combined
