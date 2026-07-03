# 基于 AstrBot 的米家猜角色小游戏插件

> 在群聊中发送 `/guess` 开始一局看图猜角色游戏，谁先猜对谁获胜

## 玩法

1. 群成员发送 `/guess` 或 `/猜角色` 发起游戏
2. Bot 随机从图片库中抽取一张角色图片，并随机应用一种图片效果（模糊、分块打乱等）
3. 群内任意成员直接回复角色名（支持别名/昵称），先猜对的获胜
4. 超时未猜对自动公布答案

## 食用方法

1. 安装本插件（可使用 AstrBot 插件市场安装）
2. 初次运行请发送 `/guess`，插件会自动 clone 图片仓库到本地
3. 获取完成后即可正常开始游戏

> `/guess`、`/更新猜角色图库`、`/重新获取猜角色图库` 都依赖 git  
> 若网络较差，可在插件配置中选择预设代理，或配置自定义代理

## 功能

- 每群同时只允许一场游戏，多群并发互不影响
- 图片随机应用 6 种可配置效果（高斯模糊、分块打乱、横向/纵向切割、随机截取、像素化）
- 支持别名匹配：角色别名、昵称、外号均可作为正确答案（基于 `alias-map.json`）
- 多角色图片：一张图有多个角色时，猜对任意一个即为正确
- 游戏提示显示所属游戏名、当前效果
- 每局超时自动结束并公布答案
- 游戏结束后支持冷却时间，防止刷屏
- 每日次数限制（可配置关闭）
- 图片处理全内存流式，零磁盘残留

## 指令

| 指令                  | 描述                                                                          |
| :-------------------- | :---------------------------------------------------------------------------- |
| `/guess` / `/猜角色`  | 开始一局猜角色游戏                                                            |
| `/更新猜角色图库`     | git pull 拉取最新图片与角色数据                                               |
| `/重新获取猜角色图库` | 删除本地仓库后从远程强制重新 clone                                            |
| `/测试所有效果`       | 随机选一张图，对所有效果各应用一次并输出（QQ 平台合并转发，其他平台逐条发送） |

## 图片效果

| 效果               | 说明     | 可配参数      |
| :----------------- | :------- | :------------ |
| `blur`             | 高斯模糊 | `blur_radius` |
| `shuffle_blocks`   | 分块打乱 | `block_size`  |
| `horizontal_slice` | 横向切割 | `slice_count` |
| `vertical_slice`   | 纵向切割 | `slice_count` |
| `crop_area`        | 随机截取 | `crop_ratio`  |
| `pixelate`         | 像素化   | `pixel_size`  |

每种效果可独立开关、调整参数

## 插件配置

主要配置项如下：

- 每日游戏次数上限
- 游戏冷却时间
- 单局超时时间
- 图片仓库配置（地址 + 代理方式）
- 图片效果配置（6 种效果各自的开关、参数）

> 代理会同时作用于首次 clone 和后续更新

## 数据文件

- 数据库存放在 `data/plugin_data/astrbot_plugin_mhy_guess/database/runtime.db`
  - 表 `participant_metrics`：记录用户总次数、答对次数、每日额度
- 图片仓库默认存放在 `data/plugin_data/astrbot_plugin_mhy_guess/image_index/`
  - 仓库地址可在配置中更换
  - 索引文件读取自仓库 `dist/` 下的 `image-index.json`、`entity-index.json`、`alias-map.json`

## 图片更新

- 使用 `/更新猜角色图库` 拉取最新图片与角色数据
- 若本地仓库被手动修改、`.git` 目录损坏或普通更新已经无法正常使用，可改用 `/重新获取猜角色图库` 删除本地仓库后重新 clone

> 游戏过程中绝不执行 git pull

## 注意

1. 获取与更新图片仓库使用 GitHub，请确保网络可访问；若网络较差，可优先配置 Git 代理
2. 若执行更新或获取失败，不要连续重复请求；请先检查 git、网络、代理配置或直接尝试 `/重新获取猜角色图库`
3. 首次 `/guess` 触发 clone 时会先发送"正在获取图片库"提示，避免用户误以为卡死
4. 修改图片仓库地址或代理配置后，需执行 `/重新获取猜角色图库` 使新配置生效

## 项目结构

```text
astrbot_plugin_mhy_guess/
├── main.py                       # 插件入口
├── metadata.yaml                 # 插件元信息
├── _conf_schema.json             # 配置 Schema
├── requirements.txt              # 依赖声明
│
├── persistence/                  # 数据持久化层
│   ├── schema.py                 # 表结构 DDL
│   └── storage.py                # SQLite 异步读写
│
├── config/                       # 配置管理
│   └── settings.py               # PluginSettings 类型化配置
│
├── gameplay/                     # 游戏核心
│   ├── room.py                   # 房间状态
│   ├── policies.py               # 纯策略判定（配额/冷却/重复）
│   └── coordinator.py            # 房间生命周期协调器
│
├── services/                     # 独立服务
│   ├── image_repository.py       # 图片仓库（clone/索引/抽题）
│   ├── image_processor.py        # 6 种图片效果
│   ├── metric_service.py         # 统计服务
│   └── cooldown_service.py       # 冷却管理
│
├── handlers/                     # 消息处理（薄层）
│   ├── commands.py               # 指令处理
│   └── listeners.py              # 答案监听
│
└── helpers/                      # 通用工具
    └── answer_matcher.py         # 别名匹配
```

## 相关仓库

- [mhy-image-index](https://github.com/MskTmi/mhy-image-index) — 图片索引仓库

## 开发参考

- [AstrBot 插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
- 图片素材来源于网络，仅供交流学习使用
