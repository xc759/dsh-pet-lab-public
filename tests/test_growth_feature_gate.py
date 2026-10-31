# -*- coding: utf-8 -*-
"""成长档案的模块闸：功能页能不能关掉它、关掉之后后台是不是真的闭嘴。

两层开关要分开测：``config`` 里的 5 个键管"提醒细节"（B3 面板里的勾选框），
``feature_activation`` 管"这个模块在不在"（功能页卡片）。只做前者的话，用户
把模块整个关掉，档案还会继续长、日报还会继续冒——那就是"关不掉"。
"""
from __future__ import annotations

import pytest

from pet import feature_activation as fa
from pet.features import FEATURES, MIGRATION_BASELINE

_GROWTH_CONFIG_KEYS = {
    "growth_nudge_enabled",
    "growth_proactive_chore_enabled",
    "growth_proactive_review_enabled",
    "growth_proactive_care_enabled",
    "growth_handoff_enabled",
}


class _Cfg:
    instance_id = ""

    def __init__(self, root):
        self.dir = root

    def get(self, key, default=None):
        return default


@pytest.fixture(autouse=True)
def _clean_activation():
    fa.reset_snapshot_for_tests()
    yield
    fa.reset_snapshot_for_tests()


def _shell(tmp_path):
    """裸 AppShell：只放门控用得到的那几个属性，方法解析走类。"""
    import pet.app as app_mod

    shell = object.__new__(app_mod.AppShell)
    shell.config = _Cfg(tmp_path)
    shell.growth_service = None
    shell.growth_panel = None
    shell._growth_started = False
    shell.unregistered = 0

    def _unregister():
        shell.unregistered += 1

    shell._unregister_growth_prompt = _unregister
    return shell


def test_growth_feature_is_registered():
    spec = FEATURES["growth"]
    assert spec.menu_action_ids == ("growth_panel",)
    assert set(spec.config_keys) == _GROWTH_CONFIG_KEYS
    # 面板与档案全在本机；提炼要用 chat，但那是可选依赖，不配模型也能记能看
    assert spec.needs_network is False
    assert spec.needs_paid is False
    assert spec.requires == ()
    assert spec.optional_deps == ("chat",)


def test_growth_not_in_migration_baseline():
    """新模块默认关：老用户升级不该凭空多出一个会写档案的模块。"""
    assert "growth" not in MIGRATION_BASELINE


def test_wanted_follows_module_activation(tmp_path):
    shell = _shell(tmp_path)
    fa.freeze_snapshot_for_tests(MIGRATION_BASELINE)
    assert shell._growth_wanted() is False

    fa.freeze_snapshot_for_tests([*MIGRATION_BASELINE, "growth"])
    assert shell._growth_wanted() is True


def test_start_does_not_arm_when_module_inactive(tmp_path):
    fa.freeze_snapshot_for_tests(MIGRATION_BASELINE)
    shell = _shell(tmp_path)
    shell._start_growth_service()
    assert shell._growth_started is False
    assert shell.unregistered == 1  # 对话注入不留残影


def test_sync_stops_recording_when_module_inactive(tmp_path):
    fa.freeze_snapshot_for_tests(MIGRATION_BASELINE)
    shell = _shell(tmp_path)
    shell._growth_started = True
    shell.growth_service = object()

    class _Panel:
        closed = 0

        def close(self):
            self.closed += 1

    panel = _Panel()
    shell.growth_panel = panel
    shell._sync_growth_service()
    assert shell._growth_started is False
    assert shell.growth_service is None       # 触点放掉：日切/提醒链自然断
    assert shell.growth_panel is None
    assert panel.closed == 1                  # 开着的面板一并收起
    assert shell.unregistered == 1


def test_sync_reinstalls_when_module_reenabled(tmp_path):
    fa.freeze_snapshot_for_tests([*MIGRATION_BASELINE, "growth"])
    shell = _shell(tmp_path)
    calls = []
    shell._start_growth_service = lambda: calls.append(1)
    shell._sync_growth_service()
    assert calls == [1]


def test_sync_is_idempotent_when_already_started(tmp_path):
    fa.freeze_snapshot_for_tests([*MIGRATION_BASELINE, "growth"])
    shell = _shell(tmp_path)
    shell._growth_started = True
    calls = []
    shell._start_growth_service = lambda: calls.append(1)
    shell._sync_growth_service()
    assert calls == []
