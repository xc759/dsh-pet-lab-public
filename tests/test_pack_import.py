# -*- coding: utf-8 -*-
"""形象包导入层测试（Petdex / 通用精灵表 / 视频素材树）。

测试自己**合成**输入，不联网、不依赖外部素材：Petdex 的几何是硬规格
（1536×2288、8×11、单格 192×208），照规格画一张就能覆盖全部解析分支；
真实样本只用来肉眼确认观感，不进自动化。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from petwisp.pack_import import build as build_mod          # noqa: E402
from petwisp.pack_import import cli as cli_mod              # noqa: E402
from petwisp.pack_import import grid as grid_mod            # noqa: E402
from petwisp.pack_import import petdex as petdex_mod        # noqa: E402
from petwisp.pack_import import spec as spec_mod            # noqa: E402
from petwisp.pack_import import video as video_mod          # noqa: E402

PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw                            # noqa: E402

CELL = (192, 208)
ROW_FRAMES = (6, 8, 8, 4, 5, 8, 6, 6, 6)


def _synth_atlas(directory: Path, *, version: int = 2, residue: bool = False,
                 hole: tuple[int, int] | None = None) -> Path:
    """按 Petdex 规格合成一张图集 + pet.json。"""
    width, height, columns, rows = petdex_mod.ATLAS_LAYOUTS[version][:4]
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    ring = ROW_FRAMES + (8, 8)
    for row in range(rows):
        frames = ring[row] if row < len(ring) else columns
        for col in range(frames):
            if hole == (row, col):
                continue
            x0, y0 = col * CELL[0], row * CELL[1]
            pad = 40 - col * 2
            draw.ellipse([x0 + pad, y0 + pad, x0 + CELL[0] - pad, y0 + CELL[1] - pad],
                         fill=(40 + row * 16, 120, 200, 255))
            if residue:
                draw.rectangle([x0 + 2, y0 + 2, x0 + 10, y0 + 10], fill=(255, 0, 0, 0))
    directory.mkdir(parents=True, exist_ok=True)
    image.save(directory / "spritesheet.webp", "WEBP", lossless=True, exact=True)
    (directory / "pet.json").write_text(json.dumps({
        "id": "testpet", "displayName": "测试宠物", "description": "合成样本",
        "spritesheetPath": "spritesheet.webp", "spriteVersionNumber": version,
        "license": "CC0-1.0",
    }, ensure_ascii=False), encoding="utf-8")
    return directory


@pytest.fixture(scope="module")
def petdex_dir(tmp_path_factory) -> Path:
    return _synth_atlas(tmp_path_factory.mktemp("petdex"))


# --------------------------------------------------------------------------
# 纯逻辑
# --------------------------------------------------------------------------

def test_timeline_keeps_total_duration():
    durations = (280, 110, 110, 140, 140, 320)
    order = build_mod.timeline(durations, 24.0)
    assert len(order) == round(sum(durations) / (1000 / 24.0))
    assert order[0] == 0 and order[-1] == len(durations) - 1
    assert all(0 <= index < len(durations) for index in order)
    # 320ms 的末帧必须被采样到多次，否则"停顿感"丢失
    assert order.count(len(durations) - 1) >= 6


def test_timeline_short_frame_survives():
    """110ms 的眨眼帧在 24fps（41.67ms/帧）下仍要被采到。"""
    order = build_mod.timeline((280, 110, 110, 140, 140, 320), 24.0)
    assert order.count(1) >= 1 and order.count(2) >= 1


def test_placement_feet_on_ground_and_uniform_scale():
    """两件事：每帧都贴住基线；整段共用一个缩放因子（否则呼吸帧越缩越大）。"""
    cells = []
    for pad in (10, 20, 30):
        cell = Image.new("RGBA", CELL, (0, 0, 0, 0))
        ImageDraw.Draw(cell).ellipse([pad, pad, CELL[0] - pad, CELL[1] - pad], fill=(1, 2, 3, 255))
        cells.append(cell)
    placement = build_mod.plan_placement(cells, canvas=(640, 360), feet_y=330)
    frames = [build_mod.render_frame(cell, placement, canvas=(640, 360), feet_y=330)
              for cell in cells]
    boxes = [frame.getchannel("A").getbbox() for frame in frames]
    assert {box[3] for box in boxes} == {330}, f"脚底没贴基线：{boxes}"
    # 每帧的尺寸都应是"自身内容尺寸 × 同一个 scale"（误差 1 像素内）
    for cell, box in zip(cells, boxes):
        src = cell.getchannel("A").getbbox()
        expect_w = (src[2] - src[0]) * placement.scale
        expect_h = (src[3] - src[1]) * placement.scale
        assert abs((box[2] - box[0]) - expect_w) <= 1.5, (box, expect_w)
        assert abs((box[3] - box[1]) - expect_h) <= 1.5, (box, expect_h)


def test_spec_rejects_bad_input():
    base = {"schema": 1, "id": "x", "grid": {"cell_w": 8, "cell_h": 8, "columns": 2, "rows": 2}}
    with pytest.raises(spec_mod.ImportError_):
        spec_mod.spec_from_dict({**base, "actions": [
            {"name": "a", "category": "fly", "row": 0, "frames": 2}]})
    with pytest.raises(spec_mod.ImportError_):
        spec_mod.spec_from_dict({**base, "actions": [
            {"name": "a", "category": "idle", "row": 0, "frames": 2, "durations": [100]}]})
    with pytest.raises(spec_mod.ImportError_):
        spec_mod.spec_from_dict({**base, "actions": [
            {"name": "a", "category": "idle", "row": 9, "frames": 1}]})
    with pytest.raises(spec_mod.ImportError_):
        spec_mod.spec_from_dict({**base, "actions": [
            {"name": "a", "category": "idle", "row": 0, "frames": 5}]})  # 超出列数


# --------------------------------------------------------------------------
# Petdex
# --------------------------------------------------------------------------

def test_petdex_spec_maps_actions(petdex_dir):
    parsed = petdex_mod.spec_from_petdex(petdex_dir)
    categories = {action.category for action in parsed.actions}
    assert {"idle", "move", "turn", "click", "drag"} <= categories
    assert parsed.core_gaps() == []
    assert parsed.id == "testpet" and parsed.fps == 24.0
    assert len(parsed.actions) == 10


def test_petdex_rejects_wrong_size(tmp_path):
    directory = _synth_atlas(tmp_path / "p")
    image = Image.open(directory / "spritesheet.webp")
    image.crop((0, 0, 1024, 1024)).save(directory / "spritesheet.webp", "WEBP", lossless=True)
    with pytest.raises(petdex_mod.ImportError_, match="画布尺寸"):
        petdex_mod.spec_from_petdex(directory)


def test_petdex_rejects_residue(tmp_path):
    directory = _synth_atlas(tmp_path / "p", residue=True)
    with pytest.raises(petdex_mod.ImportError_, match="残留"):
        petdex_mod.spec_from_petdex(directory)
    parsed = petdex_mod.spec_from_petdex(directory, allow_residue=True)
    assert parsed.core_gaps() == []


def test_petdex_rejects_hole(tmp_path):
    directory = _synth_atlas(tmp_path / "p", hole=(1, 3))
    with pytest.raises(petdex_mod.ImportError_, match="为空"):
        petdex_mod.spec_from_petdex(directory)


def test_petdex_v1_needs_turn_decision(tmp_path):
    directory = _synth_atlas(tmp_path / "p", version=1)
    parsed = petdex_mod.spec_from_petdex(directory)
    assert parsed.core_gaps() == ["turn"]
    fixed = petdex_mod.spec_from_petdex(directory, allow_missing_turn=True)
    assert fixed.core_gaps() == []


def test_pet_json_version_string_rejected(tmp_path):
    directory = _synth_atlas(tmp_path / "p")
    data = json.loads((directory / "pet.json").read_text(encoding="utf-8"))
    data["spriteVersionNumber"] = "2"
    (directory / "pet.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(petdex_mod.ImportError_, match="spriteVersionNumber"):
        petdex_mod.read_pet_json(directory)


# --------------------------------------------------------------------------
# 落盘
# --------------------------------------------------------------------------

def test_build_writes_adoptable_pack(petdex_dir, tmp_path):
    parsed = petdex_mod.spec_from_petdex(petdex_dir)
    target = build_mod.build_pack(parsed, tmp_path / "out", version="1.0.0")
    assert target.name == "1.0.0-base-640x360"
    index = json.loads((target / "character.json").read_text(encoding="utf-8"))
    assert index["id"] == "testpet" and index["canvas"] == [640, 360]
    assert {clip["part"] for clip in index["clips"]} == {"core", "extra"}

    from petwisp import character_pack as cp

    loaded = cp.load_index(target)
    for clip in loaded.clips:
        frames = sorted((target / clip.dir).glob("f_*.webp"))
        assert len(frames) == clip.frames
        assert build_mod.clip_digest(frames) == clip.content_sha
        meta = json.loads((target / clip.dir / "meta.json").read_text(encoding="utf-8"))
        assert meta["content_sha"] == clip.content_sha
        assert meta["encoder"] == build_mod._encoder_constants()[0]
        assert meta["frames"] == clip.frames
    # 运行时的采纳闸门（需要 Qt；无 Qt 环境跳过但上面的等价断言已覆盖摘要/帧数）
    pytest.importorskip("PySide6")
    assert cp.core_gaps(target, loaded) == []


def test_build_refuses_same_version(petdex_dir, tmp_path):
    parsed = petdex_mod.spec_from_petdex(petdex_dir)
    build_mod.build_pack(parsed, tmp_path / "out", version="1.0.0")
    with pytest.raises(spec_mod.ImportError_, match="目标已存在"):
        build_mod.build_pack(parsed, tmp_path / "out", version="1.0.0")
    again = build_mod.build_pack(parsed, tmp_path / "out", version="1.0.0", overwrite=True)
    assert again.is_dir()


def test_build_strips_residue(tmp_path):
    directory = _synth_atlas(tmp_path / "p", residue=True)
    parsed = petdex_mod.spec_from_petdex(directory, allow_residue=True)
    target = build_mod.build_pack(parsed, tmp_path / "out", version="1.0.0")
    frame = Image.open(sorted((target / "anim" / "idle" / "idle_loop").glob("f_*.webp"))[0])
    frame = frame.convert("RGBA")
    alpha = frame.getchannel("A").tobytes()
    rgb = frame.convert("RGB").tobytes()
    ghost = [1 for i, value in enumerate(alpha)
             if value == 0 and (rgb[i * 3] or rgb[i * 3 + 1] or rgb[i * 3 + 2])]
    assert not ghost, f"落盘帧仍有 {len(ghost)} 个残影像素"


# --------------------------------------------------------------------------
# 通用精灵表
# --------------------------------------------------------------------------

def _write_grid_pack(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (CELL[0] * 4, CELL[1] * 5), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for row in range(5):
        for col in range(4):
            x0, y0 = col * CELL[0], row * CELL[1]
            draw.rectangle([x0 + 30, y0 + 30, x0 + CELL[0] - 30, y0 + CELL[1] - 30],
                           fill=(200, 80 + row * 20, 60, 255))
    image.save(directory / "sheet.webp", "WEBP", lossless=True)
    data = grid_mod.template(image="sheet.webp", columns=4, rows=5)
    data["id"] = "gridpet"
    data["name"] = "网格宠物"
    for action in data["actions"]:
        action["frames"] = min(action["frames"], 4)
    (directory / grid_mod.SPEC_NAME).write_text(json.dumps(data, ensure_ascii=False),
                                              encoding="utf-8")
    return directory


def test_grid_pack_roundtrip(tmp_path):
    source = _write_grid_pack(tmp_path / "src")
    parsed, problems = grid_mod.check_pack(source, strict=False)
    assert problems == []
    assert parsed.core_gaps() == []
    target = build_mod.build_pack(parsed, tmp_path / "out", version="0.1.0")
    assert (target / "character.json").is_file()
    # 落盘帧数是**重采样后**的：源的 120ms/帧 在 24fps 包内必须摊成 3 帧，
    # 否则播放速度会变成 1.9 倍（运行时契约只有 fps + frames，没有变帧率）。
    expected = sum(len(build_mod.timeline(a.durations_ms, parsed.fps))
                   for a in parsed.actions)
    assert len(list((target / "anim").rglob("f_*.webp"))) == expected
    assert expected > parsed.total_frames()      # 源帧率低于 24fps 时必须更多


def test_grid_pack_detects_empty_cell(tmp_path):
    source = _write_grid_pack(tmp_path / "src")
    sheet = Image.open(source / "sheet.webp")
    sheet.paste(Image.new("RGBA", CELL, (0, 0, 0, 0)), (0, 0))
    sheet.save(source / "sheet.webp", "WEBP", lossless=True)
    _parsed, problems = grid_mod.check_pack(source, strict=False)
    assert any("为空" in item for item in problems)


# --------------------------------------------------------------------------
# 视频素材树
# --------------------------------------------------------------------------

def _fake_webm(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x1a\x45\xdf\xa3" + b"\0" * 64)


def test_video_import_layouts_by_category(tmp_path):
    source = tmp_path / "src"
    for category in ("idle", "move", "turn", "click", "drag"):
        _fake_webm(source / category / f"{category}_a.webm")
    target = video_mod.import_videos(source, tmp_path / "out", pack_id="vidpet")
    assert (target / "videos" / "idle" / "idle_a.webm").is_file()
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert {clip["category"] for clip in manifest["clips"]} == {
        "idle", "move", "turn", "click", "drag"}
    assert (target / "character.json").is_file()      # core 齐全时顺手写索引


def test_video_import_requires_category(tmp_path):
    source = tmp_path / "src"
    _fake_webm(source / "mystery.webm")
    with pytest.raises(spec_mod.ImportError_, match="推不出动作类别"):
        video_mod.import_videos(source, tmp_path / "out", pack_id="vidpet")


def test_video_import_rejects_fake_container(tmp_path):
    source = tmp_path / "src"
    bad = source / "idle" / "idle.webm"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"NOTWEBM" + b"\0" * 32)
    with pytest.raises(spec_mod.ImportError_, match="EBML"):
        video_mod.import_videos(source, tmp_path / "out", pack_id="vidpet")


# --------------------------------------------------------------------------
# 命令行
# --------------------------------------------------------------------------

def test_cli_check_and_build(petdex_dir, tmp_path, capsys):
    assert cli_mod.main(["check", str(petdex_dir)]) == 0
    out = tmp_path / "out"
    assert cli_mod.main(["build", str(petdex_dir), "--out", str(out),
                         "--version", "2.0.0"]) == 0
    assert (out / "testpet" / "2.0.0-base-640x360" / "character.json").is_file()


def test_cli_check_fails_on_broken_source(tmp_path, capsys):
    directory = _synth_atlas(tmp_path / "p", hole=(0, 2))
    assert cli_mod.main(["check", str(directory)]) == cli_mod.EXIT_BAD
    out = capsys.readouterr().out
    assert "不合格" in out and "为空" in out


def test_cli_build_refuses_broken_source(tmp_path, capsys):
    directory = _synth_atlas(tmp_path / "p", hole=(0, 2))
    code = cli_mod.main(["build", str(directory), "--out", str(tmp_path / "out")])
    assert code == cli_mod.EXIT_USAGE
    assert "源不合格" in capsys.readouterr().err


def test_cli_json_output(petdex_dir, capsys):
    assert cli_mod.main(["check", str(petdex_dir), "--json"]) == 0
    text = capsys.readouterr().out
    payload = json.loads(text[text.index("{"):])   # 人类可读行在前，JSON 在后
    assert payload["ok"] is True


def test_cli_new(tmp_path):
    target = tmp_path / "pack"
    assert cli_mod.main(["new", str(target), "--id", "abc"]) == 0
    data = json.loads((target / grid_mod.SPEC_NAME).read_text(encoding="utf-8"))
    assert data["id"] == "abc" and data["actions"]


# --------------------------------------------------------------------------
# 安装进用户角色目录
# --------------------------------------------------------------------------

def _install_env(monkeypatch, tmp_path):
    """把用户数据目录指到 tmp，避免测试污染真实的 %APPDATA%\\petwisp。"""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "appdata"))
    from petwisp import catalog

    return catalog


def test_install_writes_active_pointer(petdex_dir, tmp_path, monkeypatch):
    catalog = _install_env(monkeypatch, tmp_path)
    parsed = petdex_mod.spec_from_petdex(petdex_dir)
    target = build_mod.build_pack(parsed, tmp_path / "out", version="1.0.0")

    dest = cli_mod.install_pack(target)

    assert dest == catalog.user_characters_dir() / "testpet" / "1.0.0-base-640x360"
    assert (dest / "character.json").is_file()
    pointer = json.loads((dest.parent / "active.json").read_text(encoding="utf-8"))
    assert pointer["use"] == "1.0.0-base-640x360"
    # 运行时视角必须真的"看得见、用得上"：只复制不写指针会静默回退默认角色
    assert catalog.resolve_character_video_dir("testpet") == dest
    assert "testpet" in catalog.list_available_characters()


def test_install_refuses_existing_target(petdex_dir, tmp_path, monkeypatch):
    _install_env(monkeypatch, tmp_path)
    parsed = petdex_mod.spec_from_petdex(petdex_dir)
    target = build_mod.build_pack(parsed, tmp_path / "out", version="1.0.0")
    cli_mod.install_pack(target)
    with pytest.raises(spec_mod.ImportError_, match="目标已存在"):
        cli_mod.install_pack(target)


def test_cli_build_install_flag(petdex_dir, tmp_path, monkeypatch, capsys):
    catalog = _install_env(monkeypatch, tmp_path)
    out = tmp_path / "out"
    code = cli_mod.main(["build", str(petdex_dir), "--out", str(out), "--install"])
    assert code == 0
    assert "已安装" in capsys.readouterr().out
    assert (catalog.user_characters_dir() / "testpet" / "active.json").is_file()
