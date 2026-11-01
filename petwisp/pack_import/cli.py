# -*- coding: utf-8 -*-
"""``python -m petwisp.pack_import`` —— 导入形象包的命令行入口。

四件事：
    info <源>      识别来源类型，打印帧表与 core 闭包结论（只读）
    check <源>     只做校验，非零退出码表示不合格（CI 可用）
    build <源>     落成形象包（帧序列 + character.json）
    video <目录>   把 webm/gif 素材树规整成原生素材树
    new <目录>     生成一份通用精灵表的描述模板

``<源>`` 自动识别：含 ``pet.json`` → Petdex；含 ``petwisp-pack.json`` → 通用精灵表；
含 ``.webm`` / ``.gif`` → 视频素材树。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from .grid import SPEC_NAME, check_pack, find_spec_file, template
from .petdex import PET_JSON, spec_from_petdex
from .spec import ImportError_, PackSpec

EXIT_OK = 0
EXIT_BAD = 1
EXIT_USAGE = 2


def install_pack(pack_dir: Path | str) -> Path:
    """把产物装进用户角色目录，必要时写活动包指针。

    两种产物布局：

    - ``build`` 产物 ``<out>/<id>/<version>-<variant>-<WxH>/``（含 ``anim/``）
      → 装到 ``characters/<id>/<同名版本目录>/``，并写 ``characters/<id>/active.json``；
    - ``video`` 产物 ``<out>/<id>/``（含 ``videos/``）
      → 整目录装到 ``characters/<id>/``，运行时直接按 ``videos/`` 布局识别。

    **为什么要写指针**：运行时不再"按版本号自动选包"（装了什么 != 用哪个），
    只复制过去却不写指针，用户会看到"装好了但形象没变"。
    """
    from .. import catalog
    from .. import character_pack as _cp

    pack_dir = Path(pack_dir)
    is_versioned = (pack_dir / "anim").is_dir() and (pack_dir / "character.json").is_file()
    if is_versioned:
        dest = catalog.user_characters_dir() / pack_dir.parent.name / pack_dir.name
    else:
        dest = catalog.user_characters_dir() / pack_dir.name
    if dest.exists():
        raise ImportError_(f"目标已存在：{dest}（换个版本号，或手动删掉旧目录）")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(pack_dir, dest)
    if is_versioned and not _cp.set_active(dest):
        raise ImportError_(f"已复制，但激活失败（core 不闭环）：{dest}")
    return dest


def detect_kind(source: Path) -> str:
    """返回 ``petdex`` / ``grid`` / ``video``。"""
    if not source.exists():
        raise ImportError_(f"路径不存在: {source}")
    if source.is_file():
        if source.name == PET_JSON:
            return "petdex"
        if source.suffix.lower() in (".webm", ".gif"):
            return "video"
        if source.name == SPEC_NAME:
            return "grid"
        raise ImportError_(f"认不出来源类型: {source.name}（给目录更稳）")
    if (source / PET_JSON).is_file():
        return "petdex"
    if find_spec_file(source) is not None:
        return "grid"
    from .video import probe_videos

    if probe_videos(source):
        return "video"
    raise ImportError_(
        f"目录里既没有 {PET_JSON}、{SPEC_NAME}，也没有 .webm/.gif：{source}")


def load_source(source: Path | str, *, allow_missing_turn: bool = False,
                allow_residue: bool = False) -> tuple[str, PackSpec | None, list[str]]:
    """读源，返回 ``(类型, spec 或 None, 问题清单)``。视频来源没有 spec。"""
    source = Path(source)
    kind = detect_kind(source)
    if kind == "petdex":
        root = source.parent if source.name == PET_JSON else source
        spec = spec_from_petdex(root, allow_missing_turn=allow_missing_turn,
                                allow_residue=allow_residue, check=False)
        from .petdex import ATLAS_LAYOUTS, check_atlas, _open_atlas

        layout = ATLAS_LAYOUTS[int(spec.extras.get("sprite_version", 2))]
        problems = check_atlas(_open_atlas(spec.image), layout,
                               allow_residue=allow_residue)
        return kind, spec, problems
    if kind == "grid":
        root = source.parent if source.is_file() else source
        spec, problems = check_pack(root, strict=False)
        return kind, spec, problems
    return kind, None, []


def _emit(args, payload: dict) -> None:
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=1))


def cmd_info(args) -> int:
    kind, spec, problems = load_source(args.source,
                                       allow_missing_turn=args.allow_missing_turn,
                                       allow_residue=args.allow_residue)
    print(f"来源类型: {kind}")
    if spec is None:
        from .video import probe_videos

        entries = probe_videos(args.source)
        print(f"视频文件: {len(entries)} 个")
        for path, category, stem in entries:
            print(f"  {path.name:<28} 类别: {category or '(需 --as 指定)'}  名字: {stem}")
        _emit(args, {"kind": kind, "problems": [], "videos": len(entries)})
        return EXIT_OK if all(c for _, c, _ in entries) else EXIT_BAD
    from .build import describe

    for line in describe(spec):
        print(line)
    if problems:
        print(f"\n问题 {len(problems)} 条：")
        for item in problems[:20]:
            print(f"  - {item}")
    elif kind == "petdex":
        print("\n图集校验: 通过")
    _emit(args, {"kind": kind, "id": spec.id, "name": spec.name,
                 "fps": spec.fps, "actions": [
                     {"name": a.name, "category": a.category, "frames": len(a.cells),
                      "part": a.part} for a in spec.actions],
                 "core_gaps": spec.core_gaps(), "problems": problems})
    return EXIT_BAD if problems else EXIT_OK


def cmd_check(args) -> int:
    kind, spec, problems = load_source(args.source,
                                       allow_missing_turn=args.allow_missing_turn,
                                       allow_residue=args.allow_residue)
    if spec is not None:
        problems = list(problems)
        gaps = spec.core_gaps()
        if gaps:
            problems.append("core 闭包缺动作: " + "、".join(gaps))
    if problems:
        print(f"不合格（{len(problems)} 条）:")
        for item in problems:
            print(f"  - {item}")
        _emit(args, {"ok": False, "problems": problems})
        return EXIT_BAD
    print("通过")
    _emit(args, {"ok": True, "kind": kind})
    return EXIT_OK


def cmd_build(args) -> int:
    kind, spec, problems = load_source(args.source,
                                       allow_missing_turn=args.allow_missing_turn,
                                       allow_residue=args.allow_residue)
    if kind == "video":
        raise ImportError_("视频来源请用 video 子命令（不转帧序列）")
    if spec is None:
        raise ImportError_("读不到形象描述")
    if problems and not args.force:
        head = "；".join(problems[:6])
        raise ImportError_(f"源不合格，先修或加 --force：{head}")
    if args.id:
        import dataclasses

        spec = dataclasses.replace(spec, id=args.id)
    if args.name:
        import dataclasses

        spec = dataclasses.replace(spec, name=args.name)
    if args.fps:
        import dataclasses

        spec = dataclasses.replace(spec, fps=float(args.fps))

    from .build import build_pack

    target = build_pack(spec, args.out, version=args.version, variant=args.variant,
                        overwrite=args.overwrite)
    frames = sum(1 for _ in target.rglob("f_*.webp"))
    bytes_total = sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
    print(f"已生成: {target}")
    print(f"帧数: {frames}   体积: {bytes_total / 1024 / 1024:.2f} MB")
    gaps = spec.core_gaps()
    if gaps:
        print("注意：core 闭包缺 " + "、".join(gaps) + "，装进运行时不会被激活")
    if args.install:
        dest = install_pack(target)
        print(f"已安装: {dest}")
    else:
        dest = None
    _emit(args, {"ok": True, "path": str(target), "frames": frames,
                 "bytes": bytes_total, "core_gaps": gaps,
                 "installed": str(dest) if dest else ""})
    return EXIT_OK


def cmd_video(args) -> int:
    from .video import import_videos

    target = import_videos(args.source, args.out, pack_id=args.id, name=args.name,
                           as_category=args.as_category, fps=float(args.fps or 24.0),
                           overwrite=args.overwrite, copy=not args.move)
    count = len(list(target.rglob("*")))
    print(f"已生成: {target}")
    print("下一步：运行时首次加载会把这些视频转成帧序列（需要 ffmpeg）。")
    if args.install:
        dest = install_pack(target)
        print(f"已安装: {dest}")
    else:
        dest = None
    _emit(args, {"ok": True, "path": str(target), "entries": count,
                 "installed": str(dest) if dest else ""})
    return EXIT_OK


def cmd_new(args) -> int:
    target = Path(args.dir)
    target.mkdir(parents=True, exist_ok=True)
    spec_path = target / SPEC_NAME
    if spec_path.exists() and not args.overwrite:
        raise ImportError_(f"描述文件已存在: {spec_path}")
    data = template()
    if args.id:
        data["id"] = args.id
    if args.name:
        data["name"] = args.name
    spec_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已写入模板: {spec_path}")
    print("把精灵表放到同目录，按实际行列改 grid 与 actions，然后跑 build。")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m petwisp.pack_import",
        description="把 Petdex / 通用精灵表 / 视频素材导入成 PetWisp 形象包")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p, *, need_out: bool) -> None:
        # --json 挂在**每个子命令**上而不是顶层：顶层 flag 只能写在子命令之前，
        # ``... check x --json`` 这种自然写法会直接报错。
        p.add_argument("--json", action="store_true", help="额外输出机器可读 JSON")
        p.add_argument("--allow-missing-turn", action="store_true",
                       help="缺 turn 时用左右移动帧近似（v1 图集）")
        p.add_argument("--allow-residue", action="store_true",
                       help="容忍透明区残留 RGB（落盘时清零）")
        if need_out:
            p.add_argument("--out", required=True, help="输出根目录")

    p_info = sub.add_parser("info", help="识别来源并打印帧表")
    p_info.add_argument("source")
    common(p_info, need_out=False)
    p_info.set_defaults(func=cmd_info)

    p_check = sub.add_parser("check", help="只校验（不合格非零退出）")
    p_check.add_argument("source")
    common(p_check, need_out=False)
    p_check.set_defaults(func=cmd_check)

    p_build = sub.add_parser("build", help="落成形象包")
    p_build.add_argument("source")
    common(p_build, need_out=True)
    p_build.add_argument("--version", default="1.0.0", help="包版本号（目录名首段）")
    p_build.add_argument("--variant", default="base", help="包变体（目录名次段）")
    p_build.add_argument("--id", default="", help="覆盖包 id")
    p_build.add_argument("--name", default="", help="覆盖包显示名")
    p_build.add_argument("--fps", type=float, default=0.0, help="覆盖帧率")
    p_build.add_argument("--overwrite", action="store_true", help="允许覆盖同版本目录")
    p_build.add_argument("--force", action="store_true", help="源有问题也继续")
    p_build.add_argument("--install", action="store_true",
                         help="生成后直接装进用户角色目录并设为当前角色")
    p_build.set_defaults(func=cmd_build)

    p_video = sub.add_parser("video", help="webm/gif 素材树规整")
    p_video.add_argument("source")
    p_video.add_argument("--out", required=True)
    p_video.add_argument("--id", required=True)
    p_video.add_argument("--name", default="")
    p_video.add_argument("--as", dest="as_category", default="",
                         help="为推不出类别的文件强制指定类别")
    p_video.add_argument("--fps", type=float, default=24.0)
    p_video.add_argument("--overwrite", action="store_true")
    p_video.add_argument("--move", action="store_true", help="移动而不是复制源文件")
    p_video.add_argument("--install", action="store_true",
                         help="规整后直接装进用户角色目录")
    p_video.set_defaults(func=cmd_video)

    p_new = sub.add_parser("new", help="生成描述模板")
    p_new.add_argument("dir")
    p_new.add_argument("--id", default="")
    p_new.add_argument("--name", default="")
    p_new.add_argument("--overwrite", action="store_true")
    p_new.set_defaults(func=cmd_new)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or EXIT_OK)
    except ImportError_ as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except OSError as exc:
        print(f"IO 错误: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":          # pragma: no cover - 入口
    raise SystemExit(main())
