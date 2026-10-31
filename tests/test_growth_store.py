# -*- coding: utf-8 -*-
"""成长档案存储（``pet.growth.store``）：创建 / 自愈 / 坏文件备份 / 并发合并。"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pet.growth.schema import SCHEMA_VERSION, archive_path, normalize_archive
from pet.growth.store import GrowthStore


@pytest.fixture()
def config_dir(tmp_path: Path) -> Path:
    target = tmp_path / "cfg"
    target.mkdir()
    return target


def test_load_creates_default_archive(config_dir):
    store = GrowthStore(config_dir)
    archive = store.load()
    assert store.path == archive_path(config_dir)
    assert store.path.exists()
    assert archive["schema_version"] == SCHEMA_VERSION
    assert archive["emotion"]["state"] == "calm"
    assert archive["privacy"]["sync_summary_only"] is True
    # 再读一次不应重建（内容稳定）
    assert store.load() == archive


def test_instance_id_gets_own_file(config_dir):
    a = GrowthStore(config_dir)
    b = GrowthStore(config_dir, "slot2")
    assert a.path.name == "archive.json"
    assert b.path.name == "archive-slot2.json"
    b.load()
    assert not a.path.exists()
    assert b.path.exists()


def test_corrupt_file_is_backed_up_then_rebuilt(config_dir):
    store = GrowthStore(config_dir)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{ not json", encoding="utf-8")

    archive = store.load()

    assert archive["schema_version"] == SCHEMA_VERSION
    backups = list(store.path.parent.glob("archive.corrupt-*.json"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{ not json"
    # 主文件已重建为合法 JSON
    assert json.loads(store.path.read_text(encoding="utf-8"))["schema_version"] == SCHEMA_VERSION


def test_json_array_is_treated_as_broken(config_dir):
    store = GrowthStore(config_dir)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("[]", encoding="utf-8")

    archive = store.load()

    assert archive["schema_version"] == SCHEMA_VERSION
    assert list(store.path.parent.glob("archive.not-object-*.json"))


def test_unknown_keys_are_preserved(config_dir):
    store = GrowthStore(config_dir)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps({
        "schema_version": SCHEMA_VERSION,
        "future_field": {"keep": True},
        "profile": {"learning": {"goal": "上岸"}, "future_profile": 1},
    }), encoding="utf-8")

    archive = store.load()

    assert archive["future_field"] == {"keep": True}
    assert archive["profile"]["future_profile"] == 1
    assert archive["profile"]["learning"]["goal"] == "上岸"


def test_broken_field_types_are_repaired(config_dir):
    store = GrowthStore(config_dir)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps({
        "schema_version": "不是数字",
        "emotion": {"state": "暴走", "energy": 999, "daily": "not-a-list"},
        "profile": {"learning": {"exam_date": "2028年12月", "subjects": "408"}},
    }), encoding="utf-8")

    archive = store.load()

    assert archive["schema_version"] == SCHEMA_VERSION
    assert archive["emotion"]["state"] == "calm"
    assert archive["emotion"]["energy"] == 100
    assert archive["emotion"]["daily"] == []
    assert archive["profile"]["learning"]["exam_date"] == ""
    assert archive["profile"]["learning"]["subjects"] == []


def test_update_merges_and_bumps_updated_at(config_dir):
    store = GrowthStore(config_dir)

    def bump(archive):
        archive["sessions"]["total"] += 1

    before = store.load()["updated_at"]
    result = store.update(bump)
    assert result["sessions"]["total"] == 1
    assert result["updated_at"] >= before
    assert store.load()["sessions"]["total"] == 1


def test_concurrent_updates_do_not_lose_writes(config_dir):
    store = GrowthStore(config_dir)
    store.load()

    def worker():
        for _ in range(5):
            store.update(lambda a: a["sessions"].__setitem__(
                "total", int(a["sessions"].get("total") or 0) + 1))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert store.load()["sessions"]["total"] == 20


def test_update_on_corrupt_backs_up_before_replace(config_dir):
    store = GrowthStore(config_dir)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("坏掉的内容", encoding="utf-8")

    result = store.update(lambda a: a["emotion"].__setitem__("energy", 50))

    assert result["emotion"]["energy"] == 50
    assert list(store.path.parent.glob("archive.corrupt-*.json"))


def test_normalize_archive_handles_non_dict():
    archive = normalize_archive(["nope"])
    assert archive["schema_version"] == SCHEMA_VERSION


def test_health_reports_status(config_dir):
    store = GrowthStore(config_dir)
    assert store.health()["status"] == "missing"
    store.load()
    health = store.health()
    assert health["status"] == "ok"
    assert health["schema_version"] == SCHEMA_VERSION
    assert health["bytes"] > 0
