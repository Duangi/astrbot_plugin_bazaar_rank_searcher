# AstrBot Bazaar Rank Searcher 🏆

基于 [AstrBot](https://github.com/Soulter/AstrBot) 的 **The Bazaar (大巴扎)** 游戏全量排名查询插件。支持全服数万名玩家实时检索、账号绑定系统以及群内竞技排行。

[![AstrBot](https://img.shields.io/badge/AstrBot-Plugin-blue)](https://github.com/Soulter/AstrBot)
[![Version](https://img.shields.io/badge/Version-1.8.0-green)](https://github.com/Duangi/astrbot_plugin_bazaar_rank_searcher)

## ✨ 功能特性

- 🌐 **全量数据同步**：每 10 分钟自动从官方 API 同步数万条天梯数据，确保排名实时准确。
- ⚡ **极速内存索引**：采用内存映射技术，即使面对海量玩家数据也能实现 O(1) 复杂度的秒速检索。
- 🔗 **账号绑定系统**：支持将平台账号（如 QQ 号）与游戏 ID 永久绑定，实现“一键查我”。
- 🛡️ **隐私模式设计**：
    - **绑定查询**：展示详细信息（QQ号、平台昵称、全服排名、天梯分数）。
    - **临时查询**：仅展示游戏基础排名数据，保护玩家关联隐私。
- 👥 **群内竞技场**：一键生成本群已绑定成员的内部排名顺位，增强社区互动。
- 🤖 **LLM 智能支持**：原生支持 AstrBot 的函数调用（Function Calling），AI 能够直接理解并协助执行查询和绑定指令。

## 🛠️ 安装方法

在 AstrBot 的控制面板中通过 GitHub 仓库地址安装，或在插件目录下手动克隆：

```bash
cd /你的路径/AstrBot/data/plugins
git clone https://github.com/Duangi/astrbot_plugin_bazaar_rank_searcher.git
```

安装完成后重启 AstrBot 即可自动加载。

## ⚙️ 配置说明

在 AstrBot 管理面板的“插件配置”中填写以下参数：

| 参数 | 必填 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `token` | **是** | - | 官方 API 的身份验证令牌 (Authorization Header) |
| `season_id` | 否 | `11` | 想要查询的赛季 ID |

> **如何获取 Token？**：登录官方排行榜页面，按 F12 打开开发者工具，在网络（Network）选项卡中查找请求 `Leaderboards` 的 Header 中的 `Authorization` 字段。

## 🎮 指令说明

### 1. 绑定账号
将你的平台账号与游戏 ID 关联。
- **指令**：`/绑定 [游戏名]`
- **示例**：`/绑定 Reynad`

### 2. 查询排名
查询指定玩家或自己的实时排名。
- **指令**：`/排名 [可选:游戏名]`
- **用法**：
    - 直接发送 `/排名`：查询自己已绑定的账号排名。
    - 发送 `/排名 名字`：临时查询其他玩家排名，不影响当前绑定。

### 3. 群内排名
查看本群所有已绑定玩家在全服天梯中的内部顺位。
- **指令**：`/群内排名`

## 📂 数据存储
插件会在 `data/plugin_data/bazaar_rank_searcher/` 下生成以下文件进行持久化：
- `bazaar_rank.json`: 缓存的全量排行榜原始数据。
- `user_bindings.json`: 平台 ID 与游戏名的对应绑定关系。
- `group_roster.json`: 玩家昵称与游戏名的名录缓存。

## 📜 开发者信息
- **作者**: Duang
- **版本**: 1.8.0
- **描述**: 大巴扎全量排名：支持隐私模式、极速索引与绑定系统。

---

### 💡 小贴士
* 如果查询提示“未在全量榜单中出现”，请检查游戏名的大小写是否输入正确。
* 插件自带空数据防御机制，若官方接口返回异常，将自动保留最近一次有效的本地缓存。