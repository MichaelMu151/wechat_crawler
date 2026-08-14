from click.testing import CliRunner

from wechat_archive.cli import cli


def test_history_blocked_without_force_legacy() -> None:
    result = CliRunner().invoke(cli, ["history"])
    assert result.exit_code != 0
    assert "在线抓取已停用" in (result.output or "") + (result.exception and "" or "")
    combined = f"{result.output}{result.exception or ''}"
    assert "import-schinza-export" in combined


def test_content_blocked_without_force_legacy() -> None:
    result = CliRunner().invoke(cli, ["content"])
    assert result.exit_code != 0
    combined = f"{result.output}{result.exception or ''}"
    assert "在线抓取已停用" in combined
