# dsh-pet · 开源学习陪伴桌宠

一只住在 Windows 桌面上的透明小宠物。它会自己走动、发呆、探头、被你拖着玩，
也会在你学习时安静陪着——记你的专注时长、在你走神太久时轻轻提醒、把阶段成果整理成一份可回看的成长档案。

形象、动作、成长数据全部本地存储；除可选的在线语音/大模型能力外，不联网、不上传。

- **形象可换**：动画形象包放在指定目录里，右键「切换角色」即时生效，不重启
- **不锁生态**：转交类功能走可配置的外部助手协议；不填就退化成剪贴板模式，不依赖任何特定软件
- **可离线**：核心功能（形象、动作、成长、提醒）零网络依赖
- **代码 MIT**：可读、可改、可自己加动作

> 本项目 fork 自 [MerZlin/dsh-pet-indesktop](https://github.com/MerZlin/dsh-pet-indesktop)（MIT），
> 在此基础上重构了形象引擎、成长档案与首次使用向导，并剥离了所有第三方角色素材
> 与外部软件强绑定。上游署名与第三方许可详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

---

## 快速开始

**环境**：Windows 10/11、Python 3.11+（开发与验证环境为 3.12）。

```bat
git clone <你的仓库地址> dsh-pet
cd dsh-pet
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m pet
```

也可以直接双击 `run.bat`（内部是 `pythonw -m pet`，不弹控制台）。

首次启动会走一次「首次使用向导」，随后宠物出现在桌面右下角。

> 仓库自带的形象是程序化生成的占位小猫「墨点」（CC0），只为让你 clone 下来就能跑。
> 换成自己的形象见下一节。

---

## 形象包：换一只自己的宠物

形象包就是一个目录，放进 `assets/characters/<角色名>/`（随仓库走），
或 `%APPDATA%\dsh-pet-standalone\characters\<角色名>\`（本机自用，不入库）。

```
assets/characters/<角色名>/
├── videos/
│   ├── idle/    idle_float.webm      # 待机（必需）
│   ├── move/    move_walk.webm       # 走动
│   ├── click/   click_happy.webm     # 被点击
│   ├── drag/    drag_stretch.webm    # 被拖拽
│   ├── turn/    turn_peek.webm       # 边缘探头
│   └── manifest.json
└── (可选) <版本>-<变体>-<WxH>/character.json   # 分包发布的版本目录
```

- 视频规格：VP9 + alpha（透明）webm，逻辑画布 640×360（1280×720 包也支持，按包画布校验）
- 每个动作组可以放多个文件，运行时会随机挑一个；文件名随意，**分类只看所在目录名**
- `manifest.json` 可选，字段见 [docs/CHARACTER-PACK.md](docs/CHARACTER-PACK.md)

放好后右键宠物 →「切换角色」，即时切换，不需要重启。
想更省事：把整个 `assets/characters/placeholder/` 复制一份改名，替换掉里面的 webm 即可。

**关于素材授权**：本仓库只带 CC0 的占位形象。**不要**把来源不明、或标注「禁止再分发」
的同人角色视频提交到这个仓库（`.gitignore` 已硬性忽略 `assets/characters/` 下除
`placeholder/` 以外的一切内容，作为第二道防线）。自己做的、或已取得授权的形象再考虑公开。

---

## 转交外部助手（可选）

宠物攒下的阶段成果可以「交出去」给外部助手继续处理。这里有三种形态：

| 配置 | 行为 |
| --- | --- |
| `handoff_scheme` 留空（默认） | 纯剪贴板模式：把整理好的文本放进剪贴板，自己粘贴到哪里都行 |
| `handoff_scheme = "xxx"` | 拼成 `xxx://...` 深链调用你指定的客户端 |
| `handoff_agent_label` | 界面上按钮显示的名字，例如「交给我的助手」 |

配置写在 `config.json`（位于 `%APPDATA%\dsh-pet-standalone\`）。**本项目不内置任何
特定软件的协议、也不依赖任何特定助手**——填什么就调用什么，不填就只走剪贴板。

---

## 体积与打包

- 源码仓约 25 MB，`git clone` 后即可运行
- 绿色包（PyInstaller onedir）约 60–90 MB，取决于是否包含 OCR 模型
- 打包脚本与参数见 [docs/ONEDIR_PACKAGING.md](docs/ONEDIR_PACKAGING.md)

---

## 已知限制

- **识屏 OCR 需要自备模型**：`resources/rapidocr_models/` 下的三个 ONNX 权重
  （约 31.6 MB）未随本仓库分发。缺失时识屏功能自动降级为不可用，其余功能不受影响。
  需要时按 [docs/ASSET-PIPELINE.md](docs/ASSET-PIPELINE.md) 里的命令取回。
- **游戏帧率段需要 PresentMon**：`resources/presentmon/` 为第三方二进制，同样未随仓库分发，缺失时该功能跳过。
- **在线语音/聊天为可选能力**：未配置凭据时相关入口置灰，不影响本体。

---

## 目录结构

```
pet/            主程序（形象引擎、动作状态机、成长档案、设置与向导）
assets/         形象素材（只有 placeholder/ 随仓库分发）
scripts/        构建、素材生成、校验脚本
tests/          测试套件（pytest）
docs/           设计文档与打包说明
integrations/   外部桥接插件
.github/        CI 工作流
```

## 许可

- 代码：[MIT](LICENSE)（沿用上游版权声明）
- 占位形象 `assets/characters/placeholder/`：CC0-1.0
- 第三方组件：见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
