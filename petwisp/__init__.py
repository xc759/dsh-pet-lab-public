# -*- coding: utf-8 -*-
"""petwisp 独立桌宠（不依赖 DSH 运行时的 Windows 桌面宠物）。"""

# 版本号唯一来源：updater（更新检测基准）与安装包版本均从这里读取。
__version__ = '4.4.0'

# 旧前缀环境变量（DSH_PET_*）→ 新前缀（PETWISP_*）的一次性镜像。放在包初始化里
# 执行，是为了赶在任何子模块于导入期读取 os.environ 之前完成（screen_capture 的
# 模型目录、island 扩展目录等都是导入期读的）。
from .env_compat import migrate_legacy_env as _migrate_legacy_env  # noqa: E402

_migrate_legacy_env()
