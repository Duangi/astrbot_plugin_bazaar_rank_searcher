import json
import asyncio
import aiohttp
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api import AstrBotConfig
from astrbot.api import logger 

@register("astrbot_plugin_bazaar_rank_searcher", "Duang", "大巴扎全量排名：隐私模式与绑定系统", "1.8.0")
class BazaarRankPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        
        # 1. 使用规范的插件数据目录
        self.plugin_data_dir = StarTools.get_data_dir(self)
        self.rank_file = self.plugin_data_dir / "bazaar_rank.json"
        self.roster_file = self.plugin_data_dir / "group_roster.json"
        self.binding_file = self.plugin_data_dir / "user_bindings.json"
        
        self.plugin_data_dir.mkdir(parents=True, exist_ok=True)

        # 2. 内存数据缓存
        self.leaderboard_data: List[Dict[str, Any]] = []     # 原始全量列表
        self.name_to_entry: Dict[str, Dict[str, Any]] = {}   # 极速索引 {小写游戏名: 数据对象}
        self.roster_data: Dict[str, str] = {}                # {标准化游戏名: 绑定时的平台昵称}
        self.user_bindings: Dict[str, Dict[str, str]] = {}   # {group_id: {user_id: game_name}}
        self.last_update_str = "从未更新"
        self.total_entries = 0

        # 3. 加载数据但不启动任务
        self.load_local_data()
        self.fetch_task: Optional[asyncio.Task] = None

    async def on_enable(self):
        """框架生命周期钩子：插件启用时调用"""
        # 在正确的事件循环中启动后台任务
        self.fetch_task = asyncio.create_task(self.start_fetching())
        logger.info("大巴扎排名插件已启用，后台同步任务已启动")

    def rebuild_index(self):
        """对数万条全量数据建立内存映射索引"""
        self.name_to_entry = {
            e.get("Username", "").lower(): e 
            for e in self.leaderboard_data 
            if e.get("Username")
        }
        logger.info(f"全量索引构建完成，共收录 {len(self.name_to_entry)} 名玩家")

    def load_local_data(self):
        """插件启动时加载本地持久化文件"""
        # 加载排行榜数据
        if self.rank_file.exists():
            try:
                with open(self.rank_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.leaderboard_data = data.get("entries", [])
                    self.total_entries = data.get("totalEntries", 0)
                    ts = self.rank_file.stat().st_mtime
                    self.last_update_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                    self.rebuild_index()
                    logger.info(f"已加载本地排行榜数据，共 {self.total_entries} 条记录")
            except Exception as e:
                logger.error(f"加载排行榜失败: {e}")
                self.leaderboard_data = []
        
        # 加载昵称映射数据
        if self.roster_file.exists():
            try:
                with open(self.roster_file, "r", encoding="utf-8") as f:
                    self.roster_data = json.load(f)
                logger.info(f"已加载昵称映射数据，共 {len(self.roster_data)} 条记录")
            except Exception as e:
                logger.error(f"加载昵称映射失败: {e}")
                self.roster_data = {}

        # 加载用户绑定数据
        if self.binding_file.exists():
            try:
                with open(self.binding_file, "r", encoding="utf-8") as f:
                    self.user_bindings = json.load(f)
                logger.info(f"已加载用户绑定数据，共 {sum(len(v) for v in self.user_bindings.values())} 条绑定")
            except Exception as e:
                logger.error(f"加载用户绑定失败: {e}")
                self.user_bindings = {}

    def save_json(self, file_path: Path, data: Any):
        """通用的数据保存逻辑"""
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            logger.debug(f"数据已保存到 {file_path}")
        except Exception as e:
            logger.error(f"数据保存失败 {file_path}: {e}")

    async def fetch_leaderboard(self):
        """定时获取全量数据，带空数据防御机制"""
        url = "https://www.playthebazaar.com/api/Leaderboards"
        season_id = self.config.get("season_id", "11") 
        token = self.config.get("token")
        if not token:
            logger.warning("未配置token，跳过数据同步")
            return 

        headers = {
            "Authorization": f"{token}",
            "User-Agent": "Mozilla/5.0",
            "x-clientflavor": "Web",
            "x-platform": "Tempo"
        }

        timeout = aiohttp.ClientTimeout(total=30, connect=10, sock_read=20)
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                async with session.get(url, headers=headers, params={"seasonId": season_id}) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        entries = data.get("entries", [])
                        
                        # 【核心逻辑】如果获取到的数据为空，不更新文件，防止覆盖本地有效数据
                        if not entries:
                            logger.warning("官方返回数据为空，保持本地缓存不更新。")
                            return

                        self.leaderboard_data = entries
                        self.total_entries = data.get("totalEntries", 0)
                        self.last_update_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        
                        self.rebuild_index() # 重新构建索引
                        self.save_json(self.rank_file, data)
                        logger.info(f"排行榜数据同步成功，共 {self.total_entries} 条记录")
                    else:
                        logger.error(f"API请求失败，状态码: {resp.status}, 响应: {await resp.text()}")
            except asyncio.TimeoutError:
                logger.error("API请求超时")
            except aiohttp.ClientError as e:
                logger.error(f"网络请求错误: {e}")
            except Exception as e:
                logger.error(f"排行榜同步异常: {e}")

    async def start_fetching(self):
        """后台数据同步任务"""
        try:
            while True:
                await self.fetch_leaderboard()
                await asyncio.sleep(600)  # 每10分钟同步一次
        except asyncio.CancelledError:
            logger.info("后台同步任务已取消")
            raise
        except Exception as e:
            logger.error(f"后台同步任务异常: {e}")

    # ================= 指令区域 =================

    @filter.llm_tool("bind_user")
    @filter.command("绑定")
    async def bind_user(self, event: AstrMessageEvent, game_name: str):
        '''将当前发送者的账号与游戏 ID 永久绑定。

        Args:
            game_name(string): 想要绑定的游戏玩家名称。
        '''
        user_id = str(event.get_sender_id())
        group_id = str(event.get_group_id())
        sender = event.message_obj.sender
        nickname = sender.nickname if sender.nickname else "N/A"

        # 标准化游戏名（统一小写用于查询）
        normalized_name = game_name.lower()
        
        # 检查玩家是否存在
        exists = normalized_name in self.name_to_entry
        
        # 产品洁癖：如果玩家不存在，阻止绑定
        if not exists:
            yield event.plain_result(
                f"❌ 绑定失败！\n"
                f"🆔 游戏 ID: {game_name}\n"
                f"⚠️ 在当前收录的 {self.total_entries} 名玩家中未找到该 ID\n"
                f"💡 请检查：\n"
                f"  1. 大小写是否正确\n"
                f"  2. 是否在游戏中使用此ID\n"
                f"  3. 等待下一次数据同步后重试"
            )
            return

        # 获取标准化后的实际用户名（保持原始大小写）
        actual_username = self.name_to_entry[normalized_name].get("Username", game_name)
        
        # 初始化群绑定字典
        if group_id not in self.user_bindings:
            self.user_bindings[group_id] = {}
        
        # 保存绑定关系（按群隔离）
        self.user_bindings[group_id][user_id] = actual_username
        self.save_json(self.binding_file, self.user_bindings)

        # 保存昵称映射（使用标准化用户名作为key）
        self.roster_data[actual_username] = nickname
        self.save_json(self.roster_file, self.roster_data)

        yield event.plain_result(
            f"✅ 绑定成功！\n"
            f"🆔 游戏 ID: {actual_username}\n"
            f"🔢 QQ 号: {user_id}\n"
            f"👤 平台昵称: {nickname}\n"
            f"👥 群组: {group_id}\n"
            f"🏆 当前排名: #{self.name_to_entry[normalized_name].get('Position', 'N/A')}"
        )

    @filter.llm_tool("query_rank")
    @filter.command("排名")
    async def rank(self, event: AstrMessageEvent, name: Optional[str] = None):
        '''查询排名信息。若查自己则不传参数，若查他人则传名字。

        Args:
            name(string): 可选。仅当用户明确要求查询"某个特定名字"时才填写。如果用户查询"我"或未提姓名，请保持为空。
        '''
        user_id = str(event.get_sender_id())
        group_id = str(event.get_group_id())
        sender = event.message_obj.sender
        current_platform_nickname = sender.nickname if sender.nickname else "N/A"
        
        target_name = name
        is_temporary = True  # 标记是否为临时查询

        # 如果没有传名字，走绑定查询逻辑
        if not target_name:
            # 先尝试从当前群获取绑定
            if group_id in self.user_bindings:
                target_name = self.user_bindings[group_id].get(user_id)
            
            # 如果当前群没有，尝试全局查找（兼容旧数据）
            if not target_name:
                for gid, bindings in self.user_bindings.items():
                    if user_id in bindings:
                        target_name = bindings[user_id]
                        break
            
            is_temporary = False
            if not target_name:
                yield event.plain_result("❌ 您还没有绑定游戏 ID。\n请发送 [/绑定 名字] 进行关联，或 [/排名 名字] 临时查询。")
                return

        if not self.name_to_entry:
            yield event.plain_result("全量名单正在同步中，请稍后再试。")
            return

        # 利用内存索引进行 O(1) 检索
        normalized_name = target_name.lower()
        player = self.name_to_entry.get(normalized_name)

        if player:
            # 安全获取字段，避免KeyError
            username = player.get("Username", target_name)
            position = player.get("Position", "N/A")
            rating = player.get("Rating", "N/A")
            
            if is_temporary:
                # 【临时查询】隐私模式
                yield event.plain_result(
                    f"【临时查询结果】\n"
                    f"🆔 游戏 ID: {username}\n"
                    f"🏆 全服排名: #{position} / {self.total_entries:,}\n" 
                    f"⭐ 天梯分数: {rating}\n"
                    f"🕒 数据时间: {self.last_update_str}"
                )
            else:
                # 【绑定查询】完整模式
                yield event.plain_result(
                    f"【个人绑定查询】\n"
                    f"🆔 游戏 ID: {username}\n"
                    f"🔢 QQ 号: {user_id}\n"
                    f"👤 平台昵称: {current_platform_nickname}\n"
                    f"🏆 全服排名: #{position} / {self.total_entries:,}\n" 
                    f"⭐ 天梯分数: {rating}\n"
                    f"🕒 数据时间: {self.last_update_str}"
                )
        else:
            # 查无此人的输出
            if is_temporary:
                yield event.plain_result(f"❌ 未能找到玩家: {target_name}")
            else:
                yield event.plain_result(f"❌ 绑定玩家 {target_name} 未在全量榜单中出现。")

    @filter.llm_tool("group_leaderboard")
    @filter.command("群内排名")
    async def group_rank(self, event: AstrMessageEvent):
        '''查看本群已绑定成员在全服中的内部顺位。'''
        group_id = str(event.get_group_id())
        
        # 获取当前群的绑定用户
        group_bindings = self.user_bindings.get(group_id, {})
        
        if not group_bindings:
            yield event.plain_result("本群还没有人绑定。发送 [/绑定 名字] 参与群排名。")
            return

        group_list = []
        for uid, game_name in group_bindings.items():
            normalized_name = game_name.lower()
            player = self.name_to_entry.get(normalized_name)
            if player:
                # 安全获取字段
                p_copy = {
                    'Username': player.get('Username', game_name),
                    'Position': player.get('Position', 999999),
                    'Rating': player.get('Rating', 0),
                    'platform_nick': self.roster_data.get(game_name, "玩家")  # 使用标准化用户名
                }
                group_list.append(p_copy)
        
        if not group_list:
            yield event.plain_result("当前已绑定的玩家均未出现在全量榜单中。")
            return

        # 按排名排序
        group_list.sort(key=lambda x: x['Position'])

        result = [f"📅 群内绑定成员顺位 (共 {len(group_list)}/{len(group_bindings)} 人上榜)"]
        for index, p in enumerate(group_list[:50]):  # 限制显示前50名
            icon = "🥇" if index == 0 else "🥈" if index == 1 else "🥉" if index == 2 else f"{index+1}."
            result.append(f"{icon} {p['Username']}({p['platform_nick']}) - #{p['Position']} ({p['Rating']}分)")
        
        if len(group_list) > 50:
            result.append(f"... 等 {len(group_list)} 名玩家")
        
        yield event.plain_result("\n".join(result))

    async def terminate(self):
        """插件终止时的清理工作"""
        if self.fetch_task:
            self.fetch_task.cancel()
            try:
                await self.fetch_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"任务取消时发生错误: {e}")
            self.fetch_task = None
            logger.info("大巴扎排名插件已终止，后台任务已清理")