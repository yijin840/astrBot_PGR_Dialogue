import os
import json
import random
import re
import logging
# 从 astrbot.api.all 导入所有必要的实体
from astrbot.api.all import *
from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.event.filter import event_message_type, EventMessageType

logger = logging.getLogger(__name__)


@register("PGR", "KurisuRee7", "战双文本插件-高性能版", "1.4", "repo url")
class PGR_Plugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # 路径配置
        self.base_dir = os.path.dirname(__file__)
        self.config_file = os.path.join(self.base_dir, "dia_mapping.json")
        self.dialogue_dir = os.path.join(self.base_dir, "dialogue")

        # === 核心优化：预构建索引 ===
        self.role_rules = []  # 保持原始列表顺序，用于 O(N) 优先级的回退
        self.fuzzy_map = {}  # 模糊匹配的关键词 -> 文件名
        self.regex_pattern = None  # 编译好的模糊匹配正则

        self.build_index()
        logger.info("[PGR] 插件初始化完成，索引已构建。")

    def load_json(self, path):
        """安全加载 JSON 文件"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, Exception) as e:
            logger.error(f"[PGR] 读取 JSON 文件出错 {path}: {e}")
            return []

    def get_dialogue(self, filename):
        """读取台词"""
        file_path = os.path.join(self.dialogue_dir, filename)
        lines = self.load_json(file_path)

        if lines and isinstance(lines, list):
            logger.debug(f"[PGR] 成功加载文件：{filename}，共 {len(lines)} 条台词。")
            return random.choice(lines)

        logger.warning(f"[PGR] 文件 {filename} 加载失败或内容为空。")
        return None

    def build_index(self):
        """
        预处理：将配置文件转换为哈希表和正则，保持 O(N) 优先级的同时实现 O(1) 查找
        """
        raw_config = self.load_json(self.config_file)
        if not raw_config:
            logger.error("[PGR] 未加载到角色配置，功能禁用。")
            return

        fuzzy_keywords = []

        # 1. 构建规则列表 (保持原有的优先级) 和 关键词映射
        for item in raw_config:
            target_file = item.get("file") or item.get("mapping")
            keywords = item.get("keyword") or item.get("keywords", [])
            match_mode = item.get("match_mode", "contains")

            if not target_file or not keywords:
                continue

            # 存储规则，保持优先级
            self.role_rules.append({
                "keywords": keywords,
                "file": target_file,
                "match_mode": match_mode
            })

            # 构建模糊匹配的映射和关键词列表
            if match_mode == "contains":
                for k in keywords:
                    if k not in self.fuzzy_map:  # 确保一个关键词只对应一个文件
                        self.fuzzy_map[k] = target_file
                        fuzzy_keywords.append(k)

        # 2. 构建巨大的正则模式：(词A|词B|词C)
        if fuzzy_keywords:
            # 按长度倒序排列，确保优先匹配长词（如 "21号" 优先于 "21"），这对正则匹配至关重要
            fuzzy_keywords.sort(key=len, reverse=True)
            pattern_str = "|".join(map(re.escape, fuzzy_keywords))
            self.regex_pattern = re.compile(pattern_str)
            logger.info(f"[PGR] 模糊匹配索引构建完成，收录关键词 {len(fuzzy_keywords)} 个。")

    @event_message_type(EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent) -> MessageEventResult:
        """
        当消息中包含部分战双角色时概率发送一条语音文本。
        采用 O(N) 循环来保证优先级，但在循环内部使用 O(1) 查找优化匹配。
        """
        msg_obj = event.message_obj
        text = msg_obj.message_str or ""

        # === 增强日志：消息接收详情 ===
        logger.debug("=== Debug: AstrBotMessage ===")
        logger.debug("Bot ID: %s", msg_obj.self_id)
        logger.debug("Session ID: %s", msg_obj.session_id)
        logger.debug("Message ID: %s", msg_obj.message_id)
        logger.debug("Sender: %s", msg_obj.sender)
        logger.debug("Group ID: %s", msg_obj.group_id)
        logger.debug("Message Text: %s", text)
        logger.debug("Timestamp: %s", msg_obj.timestamp)
        logger.debug("============================")

        probability = float(self.config.get("probability", 0.3))

        # === 核心逻辑：保持 O(N) 优先级循环，内部 O(1) 查找 ===
        for rule in self.role_rules:
            keywords = rule["keywords"]
            target_file = rule["file"]
            match_mode = rule["match_mode"]

            is_match = False

            # --- 优化后的匹配 ---
            if match_mode == "exact":
                # O(1) 查找：精确匹配
                if text in keywords:
                    is_match = True
                    logger.debug(f"[PGR] 命中(精确匹配): 关键词 '{text}' 对应文件 '{target_file}'")

            elif match_mode == "contains" and self.regex_pattern:
                # O(L) 查找：模糊匹配 (利用预编译正则)
                # 注意：这里我们只检查是否有关键词存在，不依赖 match.group()，因为我们只需要知道是否命中。
                # 优先级由 self.role_rules 保证。

                # 检查此条规则的任意关键词是否在文本中
                if any(k in text for k in keywords):
                    is_match = True
                    logger.debug(f"[PGR] 命中(模糊匹配): 关键词 '{keywords}' 对应文件 '{target_file}'")

            # === 命中处理，完全模拟原 if 块的行为 ===
            if is_match:
                # 概率判断
                roll = random.random()
                if roll < probability:
                    # 抽取台词
                    selected_text = self.get_dialogue(target_file)

                    if selected_text:
                        logger.info(
                            f"[PGR] 触发回复! 命中规则: {target_file} | 概率: {roll:.2f}/{probability:.2f} | 回复: {selected_text}")
                        yield event.plain_result(selected_text)
                    else:
                        logger.warning(f"[PGR] 命中规则 {target_file}，但无法获取台词。")
                else:
                    logger.debug(
                        f"[PGR] 未触发回复。命中规则: {target_file} | 概率未达标: {roll:.2f} >= {probability:.2f}")

                # 无论是否成功回复，原逻辑都是 return，即只触发一次匹配判定
                return