# -*- coding: utf-8 -*-
"""角色包管理（v5.1 新增，M3）。

职责边界：
- **不改动**上游的角色发现/加载链路（catalog.py），只在其之上做「写」操作；
- 角色目录 = ``resolve_character_video_dir(id).parent``，即
  ``characters/<id>/``（外部/用户根）或 ``assets/characters/<id>/``（内置）；
- 人设落盘位置与上游 ``catalog.load_character_manifest`` 的查找顺序一致：
  先 ``<角色目录>/videos/manifest.json``，再 ``<角色目录>/manifest.json``，
  字段为 ``chat.system_prompt``（见 chat/prompt.py 的 load_character_prompt）；
- 聊天历史天然按角色隔离（chat/session_store.py 的 root = sessions/<character_id>），
  本模块不参与，只做说明性注释，避免重复实现。

设计约束：
- 停用角色不物理删除：整目录移动到 ``<用户角色根>/.parked/<id>-<时间戳>``，可手工恢复；
- 导入角色包只接受含 ``videos/``（或本身就是 videos 目录）且至少有一个
  webm/gif/mp4/apng 的目录，复制而非移动，源目录不动。
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from . import catalog

MANIFEST = "manifest.json"
VIDEO_SUFFIXES = {".webm", ".gif", ".mp4", ".apng"}


# ---------------------------------------------------------------- 基础查询
def list_characters() -> list[str]:
    """当前所有可用角色 id（内置 + 外部/用户目录，上游负责去重与排序）。"""
    try:
        return list(catalog.list_available_characters())
    except Exception:
        return []


def pack_dir(character_id: str) -> Path | None:
    """角色素材目录 ``characters/<id>``；解析不到返回 None。"""
    try:
        video_dir = Path(catalog.resolve_character_video_dir(character_id))
    except Exception:
        return None
    return video_dir.parent


def _manifest_candidates(character_id: str) -> list[Path]:
    root = pack_dir(character_id)
    if root is None:
        return []
    return [root / "videos" / MANIFEST, root / MANIFEST]


def _existing_manifest(character_id: str) -> Path | None:
    for path in _manifest_candidates(character_id):
        if path.is_file():
            return path
    return None


def read_manifest(character_id: str) -> dict:
    path = _existing_manifest(character_id)
    if path is None:
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_manifest(character_id: str, data: dict) -> bool:
    path = _existing_manifest(character_id)
    if path is None:
        root = pack_dir(character_id)
        if root is None:
            return False
        path = root / MANIFEST  # 老布局：新建在角色根，与 catalog 的第二候选位一致
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False


def is_builtin(character_id: str) -> bool:
    """目录是否位于内置 assets 下（内置包不建议停用）。"""
    root = pack_dir(character_id)
    if root is None:
        return False
    try:
        root.resolve().relative_to(Path(catalog.characters_dir()).resolve())
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 人设
def get_persona(character_id: str) -> str:
    chat = read_manifest(character_id).get("chat")
    if isinstance(chat, dict):
        return str(chat.get("system_prompt", "") or "")
    return ""


def set_persona(character_id: str, text: str) -> bool:
    """写入角色专属人设（manifest.chat.system_prompt）。空串=清除，回落全局人设。"""
    if pack_dir(character_id) is None:
        return False
    data = read_manifest(character_id)
    chat = data.get("chat")
    chat = dict(chat) if isinstance(chat, dict) else {}
    text = (text or "").strip()
    if text:
        chat["system_prompt"] = text
    else:
        chat.pop("system_prompt", None)
    if chat:
        data["chat"] = chat
    else:
        data.pop("chat", None)
    return _write_manifest(character_id, data)


# ---------------------------------------------------------------- 导入 / 停用
def _video_files(videos: Path) -> list[Path]:
    try:
        return [p for p in videos.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES]
    except OSError:
        return []


def validate_pack(src: Path) -> tuple[bool, str]:
    """src 可以是角色目录（含 videos/）或 videos 目录本身。"""
    src = Path(src)
    if not src.is_dir():
        return False, "不是有效目录"
    videos = src / "videos"
    if not _video_files(videos) and _video_files(src):
        videos = src  # 用户直接选了 videos 目录
    hits = _video_files(videos)
    if not hits:
        return False, "目录里没有 webm/gif/mp4/apng 素材（需要 videos/ 或直接选中 videos 目录）"
    return True, f"ok（{len(hits)} 段素材）"


def import_pack(src: Path, character_id: str | None = None) -> tuple[bool, str]:
    """把外部角色包复制进用户角色根目录。返回 (成功, 新 id / 失败原因)。"""
    src = Path(src)
    ok, msg = validate_pack(src)
    if not ok:
        return False, msg
    is_videos_dir = not _video_files(src / "videos") and bool(_video_files(src))
    cid = (character_id or ("pack" if is_videos_dir else src.name)).strip()
    if not cid:
        return False, "角色 id 为空"
    dst = Path(catalog.user_characters_dir()) / cid
    if dst.exists():
        return False, f"用户目录已存在同名角色：{cid}（可先停用旧角色）"
    try:
        dst.mkdir(parents=True, exist_ok=True)
        if is_videos_dir:
            shutil.copytree(src, dst / "videos")
        elif (src / "videos").is_dir():
            shutil.copytree(src / "videos", dst / "videos")
            for extra in ("frameseq", "manifest.json", "character.json"):
                if (src / extra).exists():
                    target = dst / extra
                    if target.is_dir():
                        shutil.copytree(src / extra, target, dirs_exist_ok=True)
                    elif not target.exists():
                        shutil.copy2(src / extra, target)
        else:  # pragma: no cover - validate 已保证不会走到
            shutil.copytree(src, dst / "videos")
    except OSError as exc:
        return False, f"复制失败：{exc}"
    return True, cid


def park_character(character_id: str) -> tuple[bool, str]:
    """停用角色：把**用户目录下**的角色目录整体移到 .parked/<id>-<ts>，内置包不动。"""
    root = pack_dir(character_id)
    if root is None:
        return False, "找不到该角色的素材目录"
    try:
        root.resolve().relative_to(Path(catalog.characters_dir()).resolve())
        return False, "该角色是内置形象，不能停用（可改用「统一人格」开关关闭其人设）"
    except Exception:
        pass
    park_root = Path(catalog.user_characters_dir()) / ".parked"
    target = park_root / f"{root.name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    try:
        park_root.mkdir(parents=True, exist_ok=True)
        shutil.move(str(root), str(target))
    except OSError as exc:
        return False, f"移动失败（目录可能被占用）：{exc}"
    return True, f"已停用并移到 {park_root}"
