# 命名与品牌（Branding）

本文记录产品名、中文名方案、以及"哪些地方必须用哪种写法"的硬约定。
改名批次（P0）已把全仓代码、目录、进程名、数据目录统一到 `petwisp`，本文是后续
文档、商店页、README、安装包文案的唯一口径来源。

---

## 一、产品名

| 项 | 值 | 说明 |
| --- | --- | --- |
| 英文名 | **PetWisp** | 文档标题、商店页、README 首现用此大小写 |
| 标识符 | `petwisp` | 包名 / 目录名 / 进程名 / pip 名 / 命令，全小写 |
| 发音 | /ˈpet.wɪsp/ | wisp 同 "whisper" 的元音，不读 "威斯普" |
| 词源 | pet + wisp | *wisp*：一缕轻烟、一点幽光；正好是"桌面上一缕会动的小光点" |

变体（构建形态）沿用后缀约定：`petwisp-webm-chat`、`petwisp-webm`、`petwisp-gif-chat`、`petwisp-gif`。

---

## 二、中文名方案

英文名不翻译，中文名只作为**面向中文用户的展示名**。三个候选与推荐：

| 候选 | 语义对应 | 优点 | 风险 |
| --- | --- | --- | --- |
| **微光**（推荐） | wisp = 一点幽光 | 两字、好念好记；和"陪伴/深夜学习"的场景气质一致；不锁定形象（换任何角色都成立） | "微光"作为通用词被大量产品使用，不适合做商标主张 |
| 雾灵 | wisp = 一缕轻烟 | 更有"灵"的拟人感，形象联想强 | 三字偏"仙侠"；与某些游戏内角色名接近 |
| 小幽 | 幽光 | 萌系、口语化 | 容易和"幽灵"联想，家长/学校场景略敏感 |

**推荐口径**：产品名对外写 `PetWisp（微光）`，后续单独出现时用"微光"。
未选中的候选不进入文档，避免同一产品多个中文名并行。

**待确认**：中文名属品牌决策，本仓默认按"微光"落笔；若后续改定，只需改 README、
`docs/` 与安装包文案，代码侧不依赖中文名（托盘/窗口标题读取配置，见下）。

### 与占位形象的关系

占位形象叫**墨点**（程序化绘制的 CC0 小猫），是*形象名*，不是产品名。
两者独立：换掉形象不影响产品名；产品名也不出现在形象包目录里。
界面上"产品名 + 形象名"同时出现时写作：`微光 · 墨点`。

---

## 三、写法约定（硬约束）

| 位置 | 写法 | 例 |
| --- | --- | --- |
| 文档标题、首段 | `PetWisp（微光）` | `# PetWisp（微光）· 开源学习陪伴桌宠` |
| 正文提到产品 | 微光 | "微光会在你走神时提醒" |
| 命令、包名、import | `petwisp` | `python -m petwisp`、`import petwisp` |
| 数据目录 | `%APPDATA%\petwisp\`（变体为 `%APPDATA%\petwisp-<variant>\`） | |
| 进程 / exe | `petwisp-<variant>.exe` | `petwisp-webm-chat.exe` |
| 环境变量 | `PETWISP_*` | `PETWISP_INSTANCE` |
| 安装包 / 快捷方式显示名 | 微光（英文环境 PetWisp） | 安装包内部 AppName 走 ASCII：`PetWisp (WebM Chat)`，避免 Inno Setup 读非 ASCII 脚本的编码坑；中文名体现在 `GREEN-PACK-README.txt` 与商店页文案 | |
| 包内 Python 包目录 | `petwisp/` | 不是 `pet/` |

**禁止**：新代码、新文档里出现 `dsh-pet`、`dsh_pet`、`DSH_PET_` 作为*新增*约定。
旧名只允许出现在两类地方，且必须带注释说明是兼容用途：

1. 迁移/清理逻辑（`config.py` 的 `_LEGACY_APP_DIR_PREFIX`、`autostart.py` 的旧自启项名单、
   `catalog.py` 的旧角色目录回退、`agent_link` 的旧安装点扫描）；
2. 环境变量兼容（`env_compat.py`、hermes 桥接的旧变量回退）。

---

## 四、改名兼容策略

改名（dsh-pet → petwisp）不能让老用户丢配置。已实现的兼容点：

- **数据目录**：首次启动时把 `%APPDATA%\dsh-pet-standalone{,-<variant>}` 迁移到
  `%APPDATA%\petwisp{,-<variant>}`（`config.py`）。迁移后旧目录留空壳，不删用户文件。
- **自启项**：注册表 Run 里旧名的值会被识别并清理，然后写入新名（`autostart.py`）。
- **环境变量**：`DSH_PET_*` 在包初始化时镜像为 `PETWISP_*`（`env_compat.py`），
  新名优先、旧名兜底；显式设成空串的新名不会被旧值覆盖。
- **角色素材**：`assets/characters/` 下找不到时回退到旧数据目录里的 `characters/`。
- **卸载判定**：扫描"其他安装点"时同时认 `petwisp*` 与 `dsh-pet-standalone*`，
  避免因为老目录识别不到而误删仍在使用的全局 hooks。

---

## 五、未采用的候选（留档，避免重复讨论）

| 候选 | 未采用原因 |
| --- | --- |
| petpack | 生态里已有 `.petpack` 形象包扩展名（duzexu/desktop-pet），撞车会导致"包格式"语义混乱 |
| petforge | GitHub 已有 15 个同名仓库，其中含同类框架，搜索不可达 |
| petstage / petcanvas / petpond | PyPI / npm / GitHub 三处均为空，但与"wisp=幽光"的气质相比过于功能化，无记忆点 |
