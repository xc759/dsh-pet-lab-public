# -*- coding: utf-8 -*-
"""首次启动向导（阶段 C）：判定口径 + 三步落盘口径。

这里刻意分两层测：

* ``state`` 的判定是"会不会骚扰用户"的唯一闸门，四条路径必须逐条锁死——
  全新目录要弹、老用户升级不能弹、走完/跳过/关窗之后不再弹；
* ``wizard`` 只测"答案有没有落到该落的地方"（config / 成长档案 / 完成标记），
  不去驱动按钮点击（GUI 交互由人工验收，避免用例依赖像素级布局）。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QDate, QEvent
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

from petwisp.config import APP_DIR_NAME, Config
from petwisp.growth import GrowthService
from petwisp.onboarding import state as onboarding_state

app = QApplication.instance() or QApplication([])


class _Cfg:
    """最小 Config 替身：判定逻辑只读 ``get`` 与 ``dir``。"""

    def __init__(self, root, values=None):
        self.dir = root
        self._values = dict(values or {})

    def get(self, key, default=None):
        return self._values.get(key, default)


# ---------------------------------------------------------------- state
def test_needs_first_run_on_brand_new_directory(tmp_path):
    assert onboarding_state.needs_first_run(_Cfg(tmp_path)) is True


def test_existing_config_means_not_first_run(tmp_path):
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    assert onboarding_state.needs_first_run(_Cfg(tmp_path)) is False


def test_existing_slot_config_also_means_not_first_run(tmp_path):
    """主配置被用户删掉、只剩多开槽位配置时，不能又把向导弹一遍。"""
    (tmp_path / "config-slot-1.json").write_text("{}", encoding="utf-8")
    assert onboarding_state.needs_first_run(_Cfg(tmp_path)) is False


def test_done_flag_wins_over_missing_config(tmp_path):
    """标记为真就不弹——用户走完向导后又手删 config.json 不该重走一遍流程。"""
    cfg = _Cfg(tmp_path, {onboarding_state.FIRST_RUN_KEY: True})
    assert onboarding_state.needs_first_run(cfg) is False


def test_mark_first_run_done_persists_to_disk(tmp_path):
    config = Config(base=tmp_path)
    assert onboarding_state.needs_first_run(config) is True
    assert onboarding_state.mark_first_run_done(config) is True
    assert (config.dir / "config.json").is_file()
    # 重新构造 = 模拟下次启动：标记必须从盘上读回来。
    assert onboarding_state.needs_first_run(Config(base=tmp_path)) is False


def test_broken_config_get_does_not_raise(tmp_path):
    class _Broken(_Cfg):
        def get(self, key, default=None):
            raise RuntimeError("boom")

    assert onboarding_state.needs_first_run(_Broken(tmp_path)) is False


# ---------------------------------------------------------------- wizard
@pytest.fixture()
def config(tmp_path):
    return Config(base=tmp_path)


def _wizard(monkeypatch, config, characters=("demo",), chat=True):
    from petwisp.onboarding import wizard as wizard_mod

    monkeypatch.setattr(wizard_mod, "_character_ids", lambda _cfg: list(characters))
    monkeypatch.setattr(wizard_mod, "chat_available", lambda: chat)
    return wizard_mod.OnboardingWizard(config)


def test_apply_writes_character_exam_date_and_flag(monkeypatch, config):
    wizard = _wizard(monkeypatch, config)
    wizard.character_list.setCurrentRow(0)
    wizard.exam_enabled.setChecked(True)
    wizard.exam_edit.setDate(QDate(2027, 12, 25))

    result = wizard.apply()

    assert result["character"] == "demo"
    assert config.get("character") == "demo"
    assert result["exam_date"] == "2027-12-25"
    assert result["first_run"] is True
    assert config.get("first_run_done") is True
    service = GrowthService(config)
    service.start()
    assert service.store.load()["profile"]["learning"]["exam_date"] == "2027-12-25"
    wizard.deleteLater()


def test_apply_without_exam_checkbox_leaves_archive_empty(monkeypatch, config):
    wizard = _wizard(monkeypatch, config)
    wizard.character_list.setCurrentRow(0)
    assert wizard.exam_enabled.isChecked() is False

    result = wizard.apply()

    assert result["exam_date"] == ""
    service = GrowthService(config)
    service.start()
    assert service.store.load()["profile"]["learning"]["exam_date"] == ""
    wizard.deleteLater()


def test_model_step_hidden_when_chat_not_packaged(monkeypatch, config):
    """no-chat 变体：不能出现"填 Key"这一步（填了也没地方用）。"""
    wizard = _wizard(monkeypatch, config, chat=False)
    assert wizard._show_model_step is False
    assert wizard._step_indexes() == [0, 1, 3]
    wizard.deleteLater()


def test_model_step_shown_when_chat_packaged(monkeypatch, config):
    wizard = _wizard(monkeypatch, config, chat=True)
    assert wizard._step_indexes() == [0, 1, 2, 3]
    wizard.deleteLater()


def test_character_step_skipped_when_no_assets(monkeypatch, config):
    wizard = _wizard(monkeypatch, config, characters=())
    assert wizard._step_indexes() == [1, 2, 3]
    assert wizard.selected_character() == ""
    wizard.deleteLater()


def test_close_event_marks_done_so_it_never_nags_twice(monkeypatch, config):
    """用户直接叉掉窗口 = 跳过：标记必须落盘，否则每次启动都再弹一次。"""
    wizard = _wizard(monkeypatch, config)
    wizard.closeEvent(QCloseEvent())
    assert config.get("first_run_done") is True
    assert onboarding_state.needs_first_run(config) is False


def test_maybe_start_returns_none_when_already_done(monkeypatch, config):
    from petwisp.onboarding import wizard as wizard_mod

    assert wizard_mod.maybe_start_first_run(None, config, needed=False) is None


def test_apply_tolerates_broken_shell(monkeypatch, config):
    """shell 是脏的（切换角色抛异常）时，其余步骤仍要落盘。"""

    class _BadShell:
        growth_service = None

        def switch_character(self, character_id):
            raise RuntimeError("boom")

    wizard = _wizard(monkeypatch, config, characters=("frost",))  # 与默认角色不同 → 走切换
    wizard.shell = _BadShell()
    wizard.character_list.setCurrentRow(0)
    wizard.exam_enabled.setChecked(True)
    wizard.exam_edit.setDate(QDate(2027, 12, 25))

    result = wizard.apply()

    assert result["character"] == ""
    assert result["exam_date"] == "2027-12-25"
    assert config.get("first_run_done") is True
    wizard.deleteLater()


def test_wizard_module_does_not_import_chat_at_top_level():
    """no-chat 打包变体里没有 petwisp.chat：向导模块顶层的 import 列表不能碰它。"""
    import ast
    import inspect

    from petwisp.onboarding import wizard as wizard_mod

    tree = ast.parse(inspect.getsource(wizard_mod))
    for node in tree.body:  # 只看模块级，函数内部（延迟 import）不算
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("chat"), node.module
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("petwisp.chat"), alias.name
