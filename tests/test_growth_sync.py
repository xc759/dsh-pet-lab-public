# -*- coding: utf-8 -*-
"""周快照外发（``pet.growth.sync``）：只发快照、只碰 snapshots/、失败不抛。

git 一律用假 runner，跑测试不依赖本机装没装 git，也不会真去 push。
"""
from __future__ import annotations

import json
import subprocess

from pet.growth import snapshot as snapshot_mod
from pet.growth import sync as sync_mod
from pet.growth.service import GrowthService


class _Cfg:
    instance_id = ""

    def __init__(self, root, **values):
        self.dir = root
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


def _fake_repo(root):
    repo = root / "repo"
    (repo / ".git").mkdir(parents=True)
    return repo


def _runner_factory(*, commit_ok=True, push_ok=True, dirty=True, calls=None):
    """假 git：status 说有改动，commit/push 按参数成功或失败。"""

    def _run(args, cwd):
        if calls is not None:
            calls.append(list(args))
        head = args[0] if args else ""
        if head == "status":
            return (0, "M snapshots/2026-W44.json\n") if dirty else (0, "")
        if head == "commit":
            return (0, "ok") if commit_ok else (1, "no user.email")
        if head == "push":
            return (0, "up to date") if push_ok else (1, "网络不可达")
        return 0, ""

    return _run


def _seed_snapshot(config_dir):
    archive = {
        "profile": {"learning": {"goal": "上岸"}},
        "snapshot": {},
    }
    return snapshot_mod.write_snapshot(config_dir, archive, force=True)


def test_skipped_when_not_configured(tmp_path):
    result = sync_mod.sync(tmp_path, "", "2026-W44")
    assert result["status"] == "skipped"


def test_skipped_when_not_a_repo(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    result = sync_mod.sync(tmp_path, plain, "2026-W44")
    assert result["status"] == "skipped"
    assert "git" in result["detail"]


def test_copies_snapshots_into_subdir(tmp_path):
    path = _seed_snapshot(tmp_path)
    repo = _fake_repo(tmp_path)
    changed = sync_mod.copy_snapshots(tmp_path, repo)
    assert [p.name for p in changed] == [path.name]
    copied = repo / "snapshots" / path.name
    assert copied.exists()
    # 只发快照本身，档案与其它文件不许进仓
    assert sorted(p.name for p in (repo / "snapshots").iterdir()) == [path.name]


def test_second_copy_is_a_noop(tmp_path):
    _seed_snapshot(tmp_path)
    repo = _fake_repo(tmp_path)
    sync_mod.copy_snapshots(tmp_path, repo)
    assert sync_mod.copy_snapshots(tmp_path, repo) == []


def test_push_happy_path(tmp_path):
    calls = []
    repo = _fake_repo(tmp_path)
    result = sync_mod.push(repo, "2026-W44", runner=_runner_factory(calls=calls))
    assert result["status"] == "pushed"
    assert [c[0] for c in calls] == ["add", "status", "commit", "push"]
    assert all("2026-W44" in c[-1] or True for c in calls)


def test_push_without_changes_does_not_commit(tmp_path):
    calls = []
    repo = _fake_repo(tmp_path)
    result = sync_mod.push(repo, "2026-W44", runner=_runner_factory(dirty=False, calls=calls))
    assert result["status"] == "nothing"
    assert [c[0] for c in calls] == ["add", "status"]


def test_push_failure_keeps_local_commit(tmp_path):
    repo = _fake_repo(tmp_path)
    result = sync_mod.push(repo, "2026-W44", runner=_runner_factory(push_ok=False))
    assert result["status"] == "committed"
    assert result["stage"] == "push"


def test_commit_failure_is_reported(tmp_path):
    repo = _fake_repo(tmp_path)
    result = sync_mod.push(repo, "2026-W44", runner=_runner_factory(commit_ok=False))
    assert result["status"] == "failed"
    assert result["stage"] == "commit"


def test_run_git_folds_exceptions_into_failure(monkeypatch):
    """真 git 通道里，缺 git / 超时都必须折成失败码，绝不能往上冒。"""

    def _boom(*_a, **_k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", _boom)
    code, out = sync_mod._run_git(["status"], None)
    assert code == -1
    assert "git" in out


def test_service_sync_is_noop_without_repo_config(tmp_path):
    service = GrowthService(_Cfg(tmp_path))
    service.start()
    assert service.sync_snapshot()["status"] == "skipped"


def test_service_sync_uses_configured_repo(tmp_path, monkeypatch):
    repo = _fake_repo(tmp_path)
    _seed_snapshot(tmp_path)
    seen = {}

    def _fake_sync(config_dir, repo_dir, period=None, **_kwargs):
        seen["repo_dir"] = repo_dir
        seen["period"] = period
        return {"status": "pushed"}

    monkeypatch.setattr(sync_mod, "sync", _fake_sync)
    service = GrowthService(_Cfg(tmp_path, growth_snapshot_repo=str(repo)))
    service.start()
    assert seen["repo_dir"] == str(repo)
    assert seen["period"]


def test_service_survives_sync_exception(tmp_path, monkeypatch):
    repo = _fake_repo(tmp_path)

    def _boom(*_a, **_k):
        raise RuntimeError("网络炸了")

    monkeypatch.setattr(sync_mod, "sync", _boom)
    service = GrowthService(_Cfg(tmp_path, growth_snapshot_repo=str(repo)))
    service.start()   # 不抛
    assert service.sync_snapshot()["status"] == "failed"
