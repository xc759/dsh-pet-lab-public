# -*- coding: utf-8 -*-
"""成长档案服务（``petwisp.growth.service``）与一次性迁移。

刻意不依赖 Qt：用例直接构造 ``GrowthService``，不需要 QApplication。
"""
from __future__ import annotations

import json
import os
from datetime import datetime

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from petwisp.growth import migrate as migrate_mod
from petwisp.growth.service import GrowthService
from petwisp.growth.store import GrowthStore


class _FakeConfig:
    """只实现 GrowthService 会用到的两个属性。"""

    def __init__(self, base, *, instance_id="", character="dafeiyu"):
        self.dir = base
        self.instance_id = instance_id
        self._values = {"character": character}

    def get(self, key, default=None):
        return self._values.get(key, default)


@pytest.fixture()
def config(tmp_path):
    base = tmp_path / "cfg"
    base.mkdir()
    return _FakeConfig(base)


def test_start_creates_archive(config):
    service = GrowthService(config)
    archive = service.start()
    assert (config.dir / "growth" / "archive.json").exists()
    assert archive["identity"]["character_id"] == "dafeiyu"


def test_record_session_counts_by_character(config):
    service = GrowthService(config)
    service.start()
    service.record_session("dafeiyu")
    service.record_session("dafeiyu")
    service.record_session("xiaoqiu")

    sessions = service.load()["sessions"]
    assert sessions["total"] == 3
    assert sessions["by_character"]["dafeiyu"] == 2
    assert sessions["by_character"]["xiaoqiu"] == 1
    assert sessions["last_at"]


def test_set_emotion_rejects_unknown_state(config):
    service = GrowthService(config)
    service.start()
    service.set_emotion("暴走", energy=120)
    emotion = service.load()["emotion"]
    assert emotion["state"] == "calm"
    assert emotion["energy"] == 100


def test_record_day_accumulates_same_day(config):
    service = GrowthService(config)
    service.start()
    day = datetime(2026, 10, 30, 9, 0, 0).astimezone()
    service.record_day(focus_minutes=25, distractions=1, mood=1, moment=day)
    service.record_day(focus_minutes=15, distractions=2, mood=-1, moment=day)

    daily = service.load()["emotion"]["daily"]
    assert len(daily) == 1
    assert daily[0]["date"] == "2026-10-30"
    assert daily[0]["focus_minutes"] == 40
    assert daily[0]["distractions"] == 3
    assert daily[0]["mood"] == 0


def test_record_day_splits_across_days(config):
    service = GrowthService(config)
    service.start()
    service.record_day(focus_minutes=25, moment=datetime(2026, 10, 29, 9).astimezone())
    service.record_day(focus_minutes=25, moment=datetime(2026, 10, 30, 9).astimezone())
    assert len(service.load()["emotion"]["daily"]) == 2


def test_injection_text_is_empty_without_profile(config):
    service = GrowthService(config)
    service.start()
    assert service.injection_text() == ""


def test_injection_text_reports_only_known_facts(config):
    service = GrowthService(config)
    service.start()

    def _fill(archive):
        learning = archive["profile"]["learning"]
        learning["goal"] = "26 考研 408"
        learning["exam_date"] = "2027-12-18"
        learning["subjects"] = [{"name": "数学一"}, {"name": "英语一"}, {"name": "408"}]
        archive["emotion"]["state"] = "focused"
        archive["emotion"]["streak_days"] = 3
        archive["emotion"]["daily"] = [
            {"date": "2026-10-30", "focus_minutes": 90, "distractions": 0, "mood": 1}]

    service.store.update(_fill)
    text = service.injection_text(datetime(2026, 10, 30, 12).astimezone())

    assert "26 考研 408" in text
    assert "距考试还有 414 天" in text
    assert "数学一、英语一、408" in text
    assert "本周已专注 90 分钟" in text
    assert "已连续使用 3 天" in text
    assert "当前情绪状态：focused" in text


def test_exam_countdown_none_when_absent_or_past(config):
    service = GrowthService(config)
    service.start()
    assert service.exam_countdown() is None

    service.store.update(lambda a: a["profile"]["learning"].__setitem__("exam_date", "2020-01-01"))
    assert service.exam_countdown() is None


def test_listener_fires_on_update(config):
    service = GrowthService(config)
    service.start()
    seen = []
    service.add_listener(seen.append)
    service.record_session("dafeiyu")
    service.remove_listener(seen.append)
    service.record_session("dafeiyu")
    assert len(seen) == 1


def test_poke_emits_weekly_snapshot_once(config):
    service = GrowthService(config)
    service.start()
    # start 已出过本周快照；强制清空记账模拟"新的一周"
    service.store.update(lambda a: a["snapshot"].__setitem__("last_period", ""))
    service._last_poke_date = ""
    service.poke()

    snapshots = list((config.dir / "growth" / "snapshots").glob("*.json"))
    assert snapshots
    assert service.load()["snapshot"]["last_period"] != ""


def test_migration_counts_legacy_sessions(tmp_path):
    base = tmp_path / "cfg"
    sessions = base / "sessions" / "dafeiyu"
    sessions.mkdir(parents=True)
    for name in ("a.json", "b.json", "c.json"):
        (sessions / name).write_text("{}", encoding="utf-8")

    config = _FakeConfig(base)
    service = GrowthService(config)
    archive = service.start()

    assert archive["sessions"]["total"] == 3
    assert archive["sessions"]["by_character"]["dafeiyu"] == 3


def test_migration_is_idempotent(config):
    service = GrowthService(config)
    service.start()
    service.record_session("dafeiyu")
    before = service.load()["sessions"]["total"]

    migrate_mod.migrate(service.store, config=config)

    assert service.load()["sessions"]["total"] == before


def test_migration_records_schema_upgrade(config):
    store = GrowthStore(config.dir)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps({"schema_version": 0, "sessions": {"total": 2}}),
                          encoding="utf-8")

    archive = migrate_mod.migrate(store, config=config)

    assert archive["schema_version"] == 1
    assert any(row["from"] == 0 and row["to"] == 1 for row in archive["migrations"])
    assert archive["sessions"]["total"] == 2


def test_needs_migration_false_for_fresh_start(config):
    service = GrowthService(config)
    service.start()
    assert migrate_mod.needs_migration(service.store) is False


def test_legacy_session_root_matches_session_store():
    """旧会话目录口径必须与 SessionStore 逐字一致（slot-0 不加后缀）。"""
    from pathlib import Path

    from petwisp.chat.session_store import SessionStore

    base = Path("C:/cfg")
    for instance_id in ("", "slot-0", "slot-1", "abc"):
        assert migrate_mod._session_root(base, instance_id) == SessionStore(base, instance_id).root
