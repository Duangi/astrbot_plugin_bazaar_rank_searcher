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
    def __init__(self, context: Context, config: Optional[AstrBotConfig] = None):
        super().__init__(context)
        # 兼容新旧版AstrBot API
        if config is not None:
            self.config = config
        elif hasattr(context, 'config'):
            self.config = context.config
        else:
            # 旧版API，通过其他方式获取配置
            self.config = None
        
        # 使用规范的插件数据目录
        # AstrBot v4.20.0的StarTools.get_data_dir()需要插件名称字符串
        try:
            # 首先尝试传递插件名称字符串
            self.plugin_data_dir = StarTools.get_data_dir("astrbot_plugin_bazaar_rank_searcher")
        except (TypeError, AttributeError):
            try:
                # 如果不行，尝试传递self（某些版本可能需要）
                self.plugin_data_dir = StarTools.get_data_dir(self)
            except:
                # 最后回退方案：使用相对路径
                import os
                self.plugin_data_dir = Path("data/plugin_data/bazaar_rank_searcher")
        
        self.rank_file = self.plugin_data_dir / "bazaar_rank.json"
        self.roster_file = self.plugin_data_dir / "group_roster.json"
        self.binding_file = self.plugin_data_dir / "user_bindings.json"
        
        self.plugin_data_dir.mkdir(parents=True, exist_ok=True)

        # 内存数据缓存
        self.leaderboard_data: List[Dict[str, Any]] = []
        self.name_to_entry: Dict[str, Dict[str, Any]] = {}
        self.roster_data: Dict[str, str] = {}
        self.user_bindings: Dict[str, Dict[str, str]] = {}
        self.last_update_str = "从未更新"
        self.total_entries = 0
        self.last_sync_successful = True  # 新增：同步状态标记
        self.sync_error_message = ""  # 新增：错误信息
        
        # 修复aiohttp.ClientSession反模式（代码审查建议）
        self.session: Optional[aiohttp.ClientSession] = None

        # 加载数据但不启动任务
        self.load_local_data()
        self.fetch_task: Optional[asyncio.Task] = None

    async def on_enable(self):
        """框架生命周期钩子：插件启用时调用"""
        logger.info("大巴扎排名插件开始启用...")
        
        # 初始化aiohttp session（代码审查建议的修复）
        timeout = aiohttp.ClientTimeout(total=30, connect=10, sock_read=20)
        self.session = aiohttp.ClientSession(timeout=timeout)
        logger.info("aiohttp session已初始化")
        
        # 检查必要配置
        if not self.config:
            logger.error("配置对象未初始化，插件无法同步数据！")
            self.sync_error_message = "配置对象未初始化"
            self.last_sync_successful = False
            return
            
        token = self.config.get("token")
        if not token:
            logger.error("未配置token，插件无法同步数据！请在插件配置中设置token")
            self.sync_error_message = "未配置API Token"
            self.last_sync_successful = False
            # 仍然启动任务，但会跳过同步
        else:
            logger.info(f"大巴扎排名插件已启用，Token配置正常（长度: {len(token)}）")
            logger.info(f"赛季ID: {self.config.get('season_id', '11')}")
        
        # 在正确的事件循环中启动后台任务
        self.fetch_task = asyncio.create_task(self.start_fetching())
        logger.info("后台同步任务已启动，将立即尝试第一次数据同步")

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
                    self.total_entries = self._safe_get_int(data, "totalEntries", 0)
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

    def _safe_get_int(self, data: Dict, key: str, default: int = 0) -> int:
        """安全获取整数值，避免None和类型错误"""
        value = data.get(key)
        if value is None:
            return default
        try:
            return int(value)
        except (ValueError, TypeError):
            logger.warning(f"字段 {key} 的值 {value} 无法转换为整数，使用默认值 {default}")
            return default

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
        if not self.config:
            logger.error("配置对象未初始化，无法同步数据！")
            self.last_sync_successful = False
            self.sync_error_message = "配置对象未初始化"
            return
            
        season_id = self.config.get("season_id", "11")  # 暂时保持硬编码，但可以优化
        token = self.config.get("token")
        
        if not token:
            if self.last_sync_successful:  # 只在状态变化时记录
                logger.error("未配置token，无法同步数据！请在插件配置中设置token")
                self.last_sync_successful = False
                self.sync_error_message = "未配置API Token"
            return

        headers = {
            "Authorization": f"{token}",
            "User-Agent": "Mozilla/5.0",
            "x-clientflavor": "Web",
            "x-platform": "Tempo"
        }

        if not self.session:
            logger.error("aiohttp session未初始化")
            return
            
        try:
            async with self.session.get(url, headers=headers, params={"seasonId": season_id}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    entries = data.get("entries", [])
                    
                    if not entries:
                        logger.warning("官方返回数据为空，保持本地缓存不更新。")
                        self.last_sync_successful = False
                        self.sync_error_message = "API返回空数据"
                        return

                    self.leaderboard_data = entries
                    self.total_entries = self._safe_get_int(data, "totalEntries", 0)
                    self.last_update_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self.last_sync_successful = True
                    self.sync_error_message = ""
                    
                    self.rebuild_index()
                    self.save_json(self.rank_file, data)
                    logger.info(f"排行榜数据同步成功，共 {self.total_entries} 条记录")
                else:
                    error_text = await resp.text()
                    logger.error(f"API请求失败，状态码: {resp.status}, 响应: {error_text[:200]}")
                    self.last_sync_successful = False
                    self.sync_error_message = f"API错误: {resp.status}"
        except asyncio.TimeoutError:
            logger.error("API请求超时")
            self.last_sync_successful = False
            self.sync_error_message = "请求超时"
        except aiohttp.ClientError as e:
            logger.error(f"网络请求错误: {e}")
            self.last_sync_successful = False
            self.sync_error_message = f"网络错误: {str(e)[:50]}"
        except Exception as e:
            logger.error(f"排行榜同步异常: {e}")
            self.last_sync_successful = False
            self.sync_error_message = f"同步异常: {str(e)[:50]}"

    async def start_fetching(self):
        """后台数据同步任务，使用固定间隔"""
        try:
            while True:
                start_time = asyncio.get_event_loop().time()
                await self.fetch_leaderboard()
                elapsed = asyncio.get_event_loop().time() - start_time
                
                # 计算剩余等待时间，保持固定间隔
                sleep_time = max(0, 600 - elapsed)  # 确保至少等待600秒
                await asyncio.sleep(sleep_time)
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
        # 检查数据同步状态
        if not self.last_sync_successful and self.sync_error_message:
            yield event.plain_result(
                f"⚠️ 数据同步异常，绑定功能可能受限\n"
                f"错误: {self.sync_error_message}\n"
                f"数据时间: {self.last_update_str}"
            )
        
        user_id = str(event.get_sender_id())
        group_id = str(event.get_group_id())
        sender = event.message_obj.sender
        nickname = sender.nickname if sender.nickname else "N/A"

        normalized_name = game_name.lower()
        
        exists = normalized_name in self.name_to_entry
        
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

        actual_username = self.name_to_entry[normalized_name].get("Username", game_name)
        
        if group_id not in self.user_bindings:
            self.user_bindings[group_id] = {}
        
        self.user_bindings[group_id][user_id] = actual_username
        self.save_json(self.binding_file, self.user_bindings)

        self.roster_data[actual_username] = nickname
        self.save_json(self.roster_file, self.roster_data)

        # 安全获取排名
        player_data = self.name_to_entry[normalized_name]
        position = self._safe_get_int(player_data, "Position", 999999)
        
        yield event.plain_result(
            f"✅ 绑定成功！\n"
            f"🆔 游戏 ID: {actual_username}\n"
            f"🔢 QQ 号: {user_id}\n"
            f"👤 平台昵称: {nickname}\n"
            f"👥 群组: {group_id}\n"
            f"🏆 当前排名: #{position}"
        )

    @filter.llm_tool("query_rank")
    @filter.command("排名")
    async def rank(self, event: AstrMessageEvent, name: Optional[str] = None):
        '''查询排名信息。若查自己则不传参数，若查他人则传名字。

        Args:
            name(string): 可选。仅当用户明确要求查询"某个特定名字"时才填写。如果用户查询"我"或未提姓名，请保持为空。
        '''
        # 检查数据同步状态
        if not self.last_sync_successful:
            error_msg = f"数据同步异常: {self.sync_error_message}" if self.sync_error_message else "数据同步失败"
            yield event.plain_result(
                f"⚠️ 数据可能已过期\n"
                f"错误: {error_msg}\n"
                f"数据时间: {self.last_update_str}\n"
                f"💡 结果仅供参考"
            )
        
        user_id = str(event.get_sender_id())
        group_id = str(event.get_group_id())
        sender = event.message_obj.sender
        current_platform_nickname = sender.nickname if sender.nickname else "N/A"
        
        target_name = name
        is_temporary = True

        if not target_name:
            if group_id in self.user_bindings:
                target_name = self.user_bindings[group_id].get(user_id)
            
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

        normalized_name = target_name.lower()
        player = self.name_to_entry.get(normalized_name)

        if player:
            username = player.get("Username", target_name)
            position = self._safe_get_int(player, "Position", 999999)
            rating = player.get("Rating", "N/A")
            
            if is_temporary:
                yield event.plain_result(
                    f"【临时查询结果】\n"
                    f"🆔 游戏 ID: {username}\n"
                    f"🏆 全服排名: #{position} / {self.total_entries:,}\n" 
                    f"⭐ 天梯分数: {rating}\n"
                    f"🕒 数据时间: {self.last_update_str}"
                )
            else:
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
            if is_temporary:
                yield event.plain_result(f"❌ 未能找到玩家: {target_name}")
            else:
                yield event.plain_result(f"❌ 绑定玩家 {target_name} 未在全量榜单中出现。")

    @filter.llm_tool("group_leaderboard")
    @filter.command("群内排名")
    async def group_rank(self, event: AstrMessageEvent):
        '''查看本群已绑定成员在全服中的内部顺位。'''
        group_id = str(event.get_group_id())
        
        group_bindings = self.user_bindings.get(group_id, {})
        
        if not group_bindings:
            yield event.plain_result("本群还没有人绑定。发送 [/绑定 名字] 参与群排名。")
            return

        group_list = []
        for uid, game_name in group_bindings.items():
            normalized_name = game_name.lower()
            player = self.name_to_entry.get(normalized_name)
            if player:
                # 安全获取所有字段
                p_copy = {
                    'Username': player.get('Username', game_name),
                    'Position': self._safe_get_int(player, 'Position', 999999),
                    'Rating': player.get('Rating', 0),
                    'platform_nick': self.roster_data.get(game_name, "玩家")
                }
                group_list.append(p_copy)
        
        if not group_list:
            yield event.plain_result("当前已绑定的玩家均未出现在全量榜单中。")
            return

        # 安全排序：确保所有Position都是整数
        try:
            group_list.sort(key=lambda x: x['Position'])
        except (TypeError, KeyError) as e:
            logger.error(f"排序失败: {e}")
            # 降级处理：按用户名排序
            group_list.sort(key=lambda x: x['Username'])

        result = [f"📅 群内绑定成员顺位 (共 {len(group_list)}/{len(group_bindings)} 人上榜)"]
        for index, p in enumerate(group_list[:50]):
            icon = "🥇" if index == 0 else "🥈" if index == 1 else "🥉" if index == 2 else f"{index+1}."
            result.append(f"{icon} {p['Username']}({p['platform_nick']}) - #{p['Position']} ({p['Rating']}分)")
        
        if len(group_list) > 50:
            result.append(f"... 等 {len(group_list)} 名玩家")
        
        yield event.plain_result("\n".join(result))

    async def terminate(self):
        """插件终止时的清理工作"""
        # 关闭aiohttp session
        if self.session:
            await self.session.close()
            self.session = None
            logger.info("aiohttp session已关闭")
        
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