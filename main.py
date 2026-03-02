import json
import asyncio
import aiohttp
from datetime import datetime
from pathlib import Path
from typing import Optional
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import AstrBotConfig
from astrbot.api import logger 

@register("bazaar_rank_searcher", "Duang", "大巴扎全量排名：隐私模式与绑定系统", "1.8.0")
class BazaarRankPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        
        # 1. 路径初始化
        self.plugin_data_dir = Path(get_astrbot_data_path()) / "plugin_data" / "bazaar_rank_searcher"
        self.rank_file = self.plugin_data_dir / "bazaar_rank.json"
        self.roster_file = self.plugin_data_dir / "group_roster.json"
        self.binding_file = self.plugin_data_dir / "user_bindings.json"
        
        self.plugin_data_dir.mkdir(parents=True, exist_ok=True)

        # 2. 内存数据缓存
        self.leaderboard_data = []     # 原始全量列表
        self.name_to_entry = {}        # 极速索引 {小写游戏名: 数据对象}
        self.roster_data = {}          # {游戏名: 绑定时的平台昵称}
        self.user_bindings = {}        # {QQ号: 游戏名}
        self.last_update_str = "从未更新"
        self.total_entries = 0

        # 3. 启动逻辑
        self.load_local_data()
        self.fetch_task = asyncio.create_task(self.start_fetching())

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
        if self.rank_file.exists():
            try:
                with open(self.rank_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.leaderboard_data = data.get("entries", [])
                    self.total_entries = data.get("totalEntries", 0)
                    ts = self.rank_file.stat().st_mtime
                    self.last_update_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                    self.rebuild_index()
            except Exception as e: logger.error(f"加载排行榜失败: {e}")
        
        if self.roster_file.exists():
            try:
                with open(self.roster_file, "r", encoding="utf-8") as f:
                    self.roster_data = json.load(f)
            except Exception: self.roster_data = {}

        if self.binding_file.exists():
            try:
                with open(self.binding_file, "r", encoding="utf-8") as f:
                    self.user_bindings = json.load(f)
            except Exception: self.user_bindings = {}

    def save_json(self, file_path, data):
        """通用的数据保存逻辑"""
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e: logger.error(f"数据保存失败: {e}")

    async def fetch_leaderboard(self):
        """定时获取全量数据，带空数据防御机制"""
        url = "https://www.playthebazaar.com/api/Leaderboards"
        season_id = self.config.get("season_id", "11") 
        token = self.config.get("token")
        if not token: return 

        headers = {
            "Authorization": f"{token}",  # 纯 token 认证
            "User-Agent": "Mozilla/5.0",
            "x-clientflavor": "Web",
            "x-platform": "Tempo"
        }

        async with aiohttp.ClientSession() as session:
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
            except Exception as e: logger.error(f"排行榜同步异常: {e}")

    async def start_fetching(self):
        while True:
            await self.fetch_leaderboard()
            await asyncio.sleep(600) # 每10分钟同步一次
    # ================= 指令区域 =================

    @filter.llm_tool("bind_user")
    @filter.command("绑定")
    async def bind_user(self, event: AstrMessageEvent, game_name: str):
        '''将当前发送者的账号与游戏 ID 永久绑定。

        Args:
            game_name(string): 想要绑定的游戏玩家名称。
        '''
        user_id = str(event.get_sender_id())
        sender = event.message_obj.sender
        nickname = sender.nickname if sender.nickname else "N/A"

        # 内存索引中预检查
        exists = game_name.lower() in self.name_to_entry
        
        # 保存持久化绑定关系
        self.user_bindings[user_id] = game_name
        self.save_json(self.binding_file, self.user_bindings)

        # 保存名录（用于群内排名展示）
        self.roster_data[game_name] = nickname
        self.save_json(self.roster_file, self.roster_data)

        yield event.plain_result(
            f"✅ 绑定成功！\n"
            f"🆔 游戏 ID: {game_name}\n"
            f"🔢 QQ 号: {user_id}\n"
            f"👤 平台昵称: {nickname}" +
            ("" if exists else "\n⚠️ 提示：在当前收录的玩家名单中未找到该 ID，请检查大小写。")
        )

    @filter.llm_tool("query_rank")
    @filter.command("排名")
    async def rank(self, event: AstrMessageEvent, name: Optional[str] = None):
        '''查询排名信息。若查自己则不传参数，若查他人则传名字。

        Args:
            name(string): 可选。仅当用户明确要求查询“某个特定名字”时才填写。如果用户查询“我”或未提姓名，请保持为空。
        '''
        user_id = str(event.get_sender_id())
        sender = event.message_obj.sender
        current_platform_nickname = sender.nickname if sender.nickname else "N/A"
        
        target_name = name
        is_temporary = True # 标记是否为临时查询

        # 如果没有传名字，走绑定查询逻辑
        if not target_name:
            target_name = self.user_bindings.get(user_id)
            is_temporary = False
            if not target_name:
                yield event.plain_result("❌ 您还没有绑定游戏 ID。\n请发送 [/绑定 名字] 进行关联，或 [/排名 名字] 临时查询。")
                return

        if not self.name_to_entry:
            yield event.plain_result("全量名单正在同步中，请稍后再试。")
            return

        # 利用内存索引进行 O(1) 检索
        player = self.name_to_entry.get(target_name.lower())

        if player:
            if is_temporary:
                # 【临时查询】隐私模式
                yield event.plain_result(
                    f"【临时查询结果】\n"
                    f"🆔 游戏 ID: {player['Username']}\n"
                    f"🏆 全服排名: #{player['Position']} / {self.total_entries}\n" 
                    f"⭐ 天梯分数: {player['Rating']}\n"
                    f"🕒 数据时间: {self.last_update_str}"
                )
            else:
                # 【绑定查询】完整模式
                yield event.plain_result(
                    f"【个人绑定查询】\n"
                    f"🆔 游戏 ID: {player['Username']}\n"
                    f"🔢 QQ 号: {user_id}\n"
                    f"👤 平台昵称: {current_platform_nickname}\n"
                    f"🏆 全服排名: #{player['Position']} / {self.total_entries:,}\n" 
                    f"⭐ 天梯分数: {player['Rating']}\n"
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
        if not self.user_bindings:
            yield event.plain_result("群内还没有人绑定。发送 [/绑定 名字] 参与群排名。")
            return

        group_list = []
        for uid, game_name in self.user_bindings.items():
            player = self.name_to_entry.get(game_name.lower())
            if player:
                p_copy = player.copy()
                p_copy['platform_nick'] = self.roster_data.get(player['Username'], "玩家")
                group_list.append(p_copy)
        
        if not group_list:
            yield event.plain_result("当前已绑定的玩家均未出现在全量榜单中。")
            return

        group_list.sort(key=lambda x: x['Position'])

        result = [f"📅 群内绑定成员顺位 (共 {len(self.user_bindings)} 人)"]
        for index, p in enumerate(group_list[:200]):
            icon = "🥇" if index == 0 else "🥈" if index == 1 else "🥉" if index == 2 else f"{index+1}."
            result.append(f"{icon} {p['Username']}({p['platform_nick']}) - #{p['Position']} ({p['Rating']}分)")
        
        yield event.plain_result("\n".join(result))
    async def terminate(self):
        if self.fetch_task:
            self.fetch_task.cancel()