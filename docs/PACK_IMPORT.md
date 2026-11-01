# 形象包导入器（把别人的素材变成 PetWisp 能用的包）

桌面宠物生态里有不少现成素材，但格式各不相同。这个导入器把三类常见来源统一转成
PetWisp 的**帧序列包**（`anim/<类别>/<动作>/f_*.webp` + `character.json`），
装进运行时的流程和手写包完全一样。

命令入口：`python -m petwisp.pack_import`（也随应用分发，见文末"离线可用性"）。

## 支持的三种来源

| 来源 | 判定依据 | 说明 |
| --- | --- | --- |
| Petdex 形象 | 目录里有 `pet.json` | 精灵图集 + 每行一套动作，规格固定（见下） |
| 通用精灵表 | 目录里有 `petwisp-pack.json` | 任意网格，用描述文件讲清"哪一格是什么动作" |
| 视频素材树 | 目录里有 `.webm` / `.gif` | **不转码**，只按类别规整成原生素材树 |

`info` / `check` / `build` 都会自动识别来源，不用手选。

## 快速开始

```bash
# 1. 先看看认出来什么、帧表长什么样（只读，不改任何文件）
python -m petwisp.pack_import info  ~/Downloads/some-pet

# 2. 只校验，不合格时退出码非零（可以直接挂 CI）
python -m petwisp.pack_import check ~/Downloads/some-pet

# 3. 转成形象包，产物落在 <out>/<id>/<版本>-<变体>-640x360/
python -m petwisp.pack_import build ~/Downloads/some-pet --out ./out --version 1.0.0

# 4. 想省掉手动安装，加 --install 直接装进用户角色目录并设为当前角色
python -m petwisp.pack_import build ~/Downloads/some-pet --out ./out --install
```

`--install` 做两件事：把产物复制到 `%APPDATA%\petwisp\characters\<id>\<版本目录>\`，
并写 `active.json` 指针把它设为当前角色。手装也可以，但**必须自己写指针**——
运行时只认指针，不按版本号自动选包（装了什么不等于用哪个），只复制目录会看到
"装好了但形象没变"。

```bash
# 视频素材树：只规整、不转码
python -m petwisp.pack_import video ./my-clips --out ./out --id mypet --install

# 生成一份通用精灵表的描述模板（填好再 build）
python -m petwisp.pack_import new ./mypack --id mypet
```

常用开关：

| 开关 | 作用 |
| --- | --- |
| `--out` | 输出根目录（build/video 必填） |
| `--version` / `--variant` | 产物目录名的前两段，默认 `1.0.0` / `base` |
| `--id` / `--name` / `--fps` | 覆盖来源里读到的身份与帧率 |
| `--allow-missing-turn` | 源里没有转向动作时，用左右移动帧近似（见 v1 说明） |
| `--allow-residue` | 容忍透明区的残留 RGB（**落盘时清零**） |
| `--overwrite` | 允许覆盖同版本目录（默认拒绝，防手滑） |
| `--install` | 生成后直接装进用户角色目录（build 会写 `active.json` 指针） |
| `--force` | 校验没过也强行 build（调试用，不建议） |
| `--json` | 额外打一段机器可读 JSON，方便脚本处理 |

退出码：`0` 通过，`1` 不合格，`2` 用法错误或读不了源。

## Petdex

规格是硬的，导入器**不猜**：

| 版本 | 整图尺寸 | 网格 | 单格 |
| --- | --- | --- | --- |
| v1 | 1536×1872 | 8 列 × 9 行 | 192×208 |
| v2 | 1536×2288 | 8 列 × 11 行 | 192×208 |

`pet.json` 里的 `spriteVersionNumber` 必须是整数 `1` 或 `2`，写成字符串 `"2"`
会直接报错（宁可停下，也不要读错网格导致整只宠物错位）。

每行对应一个状态，状态到 PetWisp 动作的映射：

| Petdex 状态 | 动作名 | 类别 | 分件 |
| --- | --- | --- | --- |
| `idle` | `idle_loop` | 待机 | core |
| `running-right` | `move_right` | 移动 | core |
| `running-left` | `move_left` | 移动 | core |
| `waving` | `click_wave` | 点击 | core |
| `jumping` | `drag_hop` | 拖拽 | core |
| `failed` / `waiting` / `running` / `review` | `idle_fail` / `idle_wait` / `idle_busy` / `idle_review` | 随机小动作 | extra |

- **v2 的转向**：第 9、10 行是 16 个注视方向，拼成一段 `turn_gaze`，每帧 120ms
- **v1 没有转向**：core 会缺 `turn`，运行时不会激活。两种处理——`--allow-missing-turn`
  用左右移动帧近似，或干脆不用 v1 素材
- **空洞检查**：某一行声明了 N 帧，但其中一格全透明 → 报错并指出行列。这类图集
  通常是导出中断，硬转过去会让宠物在动作中途"消失一下"
- **残影检查**：透明像素上残留了 RGB（抠图没清干净）→ 报错；加 `--allow-residue`
  可以放行，落盘时会把这部分 RGB 清零（否则缩放插值时会出现毛边）

## 通用精灵表

放一张精灵表 + 一份 `petwisp-pack.json`：

```json
{
  "schema": 1,
  "id": "mypet",
  "name": "我的形象",
  "image": "spritesheet.webp",
  "grid": {"cell_w": 192, "cell_h": 208, "columns": 8, "rows": 11},
  "fps": 24,
  "license": "CC0-1.0",
  "actions": [
    {"name": "idle_loop", "category": "idle", "row": 0, "frames": 4, "frame_ms": 120},
    {"name": "move_walk", "category": "move", "row": 1, "frames": 4, "frame_ms": 120},
    {"name": "turn_peek", "category": "turn", "row": 2, "frames": 2, "frame_ms": 160},
    {"name": "click_happy", "category": "click", "row": 3, "frames": 4, "frame_ms": 120},
    {"name": "drag_wobble", "category": "drag", "row": 4, "frames": 2, "frame_ms": 140}
  ]
}
```

- 动作**从该行第一列开始**连续取 `frames` 格；超出列数会报错
- 时长两种写法二选一：`frame_ms`（统一帧长）或 `durations`（逐帧数组，长度须等于 `frames`）
- 类别只能用 `idle` / `move` / `turn` / `click` / `drag` / `random`
- 前五类（缺 `random`）凑齐了才算 core 闭环；`random` 默认归 extra
- `part` 只对非核心类别有意义（核心五类恒为 `core`）

## 视频素材树

只做**规整**，不做转码：

```
my-clips/
├── idle/    idle_float.webm
├── move/    move_walk.webm
├── turn/    turn_peek.webm
├── click/   click_happy.webm
└── drag/    drag_stretch.webm
```

- 类别优先看**所在目录名**，其次看文件名里的关键词
- 推不出类别会直接报错，用 `--as idle` 指定（整批同一个类别时才这么干）
- 只做容器头校验（EBML / GIF 头），坏文件当场拦下
- 产物带 `manifest.json`；运行时**首次加载**时才把视频转成帧序列，需要本机有 ffmpeg

## 产物的时间语义（重要）

运行时的帧序列契约只有 `fps` + `frames` 两个字段，**没有变帧率**。所以导入时会把
时长不同的源帧**重采样**到 24fps：源里 120ms 一帧的慢速动作会摊成 3 帧，280ms 的
停顿帧会摊成 7 帧。这样播放速度是对的，代价是帧数变多（体积经验值：257 帧 ≈ 0.9MB
无损 WebP）。

缩放是**整段统一**的，贴底是**逐帧**做的：前者防呼吸帧越缩越大，后者防蹲下、
抬手这类内容变矮的帧悬空。

## 已知限制

- 一个包只支持**一套网格**，不支持"每行格子大小不同"的图集
- 没有音频通道，也没有表情/换装配
- 图集里若是多个角色拼在一张图，导入器不会帮你切（请先自己切好）
- 视频来源在公开仓里**转不了帧序列**：转换器依赖本机 ffmpeg，且不进仓库

## 许可红线

导入器只是工具，**不改变素材的授权状态**。导入并分发包之前自己确认：

- 素材是自制的，或授权允许再分发
- 保留原作者署名与许可声明（导入产物里的 `license` / `author` 字段会被写进
  `character.json`，别为了省事填 `CC0`）

来源不明、标注"禁止再分发"的同人角色，只在自己机器上用，别提交进仓库。

## 排错

| 报错 | 原因 | 处理 |
| --- | --- | --- |
| `spriteVersionNumber` 必须是整数 | `pet.json` 写成了字符串 | 改成 `2` |
| 画布尺寸不符 | 图集被裁剪或放大过 | 按规格重新导出 |
| 第 X 行第 Y 列为空 | 图集导出中断 | 回源修图，或删掉该行动作 |
| 透明区有残留 RGB | 抠图没清干净 | 回源修，或 `--allow-residue` 放行 |
| 目标已存在 | 同版本目录已在 | 抬版本号，或 `--overwrite` |
| 装好了但形象没变 | 只复制了目录，没写 `active.json` 指针 | 用 `--install` 重装，或手写 `{"use": "<版本目录名>"}` |
| 这条动作没有被激活 | core 缺 `turn` 等必需动作 | 补齐动作，或 `--allow-missing-turn` |

## 离线可用性

导入器只依赖 Pillow（图集切片与无损 WebP 编码），**不需要** Qt、ffmpeg 或网络；
运行时侧的编码器档位（`ENCODER_DESC` / `unblend` 标记）直接从源码常量读取，
因此导入器在没有 Qt 的机器上也能跑，产物里的元信息仍与运行时一致。

覆盖这套行为的测试见 `tests/test_pack_import.py`（26 例，全部用合成样本，不联网；
安装相关用例把 `APPDATA` 指到临时目录，不碰真实用户数据）。
