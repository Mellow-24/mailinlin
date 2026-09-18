"""Direct final-transcript trigger for simple headless push-to-talk calls."""

from __future__ import annotations

import asyncio
import re
from collections import deque
from collections.abc import Awaitable, Callable

from loguru import logger
from pipecat.frames.frames import LLMContextFrame, TranscriptionFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from api.services.workflow.initial_context import (
    VOICE_DEMO_DATE_MARKER,
    build_voice_demo_date_instruction,
)


_CONSULTATION_TOPIC_RE = re.compile(
    r"(?:風水|风水|玄學|玄学|命理|算命|睇命|占卜|八字|生辰|四柱|命盤|命盘|"
    r"五行|十神|大運|大运|流年|運勢|运势|運程|运程|行運|行运|生肖|"
    r"太歲|太岁|本命年|開運|开运|改運|改运|化解|吉凶|擇日|择日|通勝|通胜|"
    r"黃曆|黄历|吉日|改名|起名|姓名學|姓名学|面相|手相|"
    r"財運|财运|事業|事业|姻緣|姻缘|桃花|感情運|感情运|婚姻運|婚姻运|"
    r"貴人|贵人|小人|犯太歲|犯太岁|添丁|生仔|懷孕|怀孕|baby|bb|"
    r"家宅|住宅|屋企|睡房|臥室|卧室|客廳|客厅|大門|大门|玄關|玄关|"
    r"床頭|床头|財位|财位|桃花位|方位|坐向|飛星|飞星|催財|催财|"
    r"睡眠|失眠|瞓得|睡得)",
    re.IGNORECASE,
)
_CONSULTATION_PROFILE_RE = re.compile(
    r"(?:"
    r"(?:生肖|屬|属)\s*(?:鼠|牛|虎|兔|龍|龙|蛇|馬|马|羊|猴|雞|鸡|狗|豬|猪)"
    r"|(?<!\d)(?:(?:19|20)\d{2}|\d{2})\s*年(?:出世|出生)?"
    r"|(?:子|丑|寅|卯|辰|巳|午|未|申|酉|戌|亥)\s*(?:時|时)"
    r"|(?:凌晨|朝早|早上|中午|下晝|下午|夜晚|晚上)?\s*\d{1,2}\s*(?:點|点|時|时|鐘|钟)"
    r"|(?:坐|向|朝)\s*(?:正)?(?:東|东|西|南|北|東南|东南|西南|東北|东北|西北)"
    r")",
    re.IGNORECASE,
)
_CONSULTATION_FOLLOW_UP_RE = re.compile(
    r"(?:咁我|那我|噉我|點解|点解|為什麼|为什么|點做|点做|怎麼做|怎么做|"
    r"再講|再讲|繼續|继续|具體|具体|呢方面|這方面|这方面|頭先|头先)"
)
_STANDALONE_GREETING_RE = re.compile(
    r"^(?:(?:你好|您好|哈囉|哈啰|哈喽|hello|hi|嗨|早晨|早安|早上好|午安|"
    r"晚上好|晚安|喂)[呀啊啦喇囉咯哦唷哈]*[，,、 ]*)+"
    r"(?:(?:玲玲姐|玲玲師傅|玲玲师傅|麥玲玲|麦玲玲|師傅|师傅|易水\s*ai|顧問|顾问)[呀啊啦喇]*[！!。.]*)?$",
    re.IGNORECASE,
)
_STANDALONE_SOCIAL_RE = re.compile(
    r"^(?:(?:ok|okay|好)[，,、 ]*)?"
    r"(?:(?:多謝|多谢|謝謝|谢谢|唔該|唔该)(?:晒|你|您|啊|呀|啦|喇|先|玲玲姐|玲玲師傅|玲玲师傅|師傅|师傅|[，,、 ])*"
    r"|(?:再見|再见|拜拜|bye|goodbye)(?:啊|呀|啦|喇|先|玲玲姐|玲玲師傅|玲玲师傅|師傅|师傅|[，,、 ])*"
    r"|(?:你|妳|您)(?:好嗎|好吗|近排好嗎|近排好吗|食咗飯未|食咗饭未))"
    r"[！!。.?？]*$",
    re.IGNORECASE,
)


def is_consultation_turn(
    user_text: str,
    *,
    consultation_started: bool = False,
    consultation_turn_count: int = 0,
) -> bool:
    """Classify a turn without adding another model call to the hot path.

    The classifier is deliberately conservative before a consultation starts:
    greetings and arbitrary chat stay ordinary conversation, while clear
    metaphysics/feng-shui subjects or expected profile facts enter the three-
    question flow. Once intake is active, terse answers such as a year, zodiac
    or direction remain associated with that consultation.
    """

    normalized = re.sub(r"\s+", " ", user_text).strip()
    if not normalized:
        return False
    if _CONSULTATION_TOPIC_RE.search(normalized) or _CONSULTATION_PROFILE_RE.search(
        normalized
    ):
        return True
    if _STANDALONE_GREETING_RE.fullmatch(normalized) or _STANDALONE_SOCIAL_RE.fullmatch(
        normalized
    ):
        return False
    if not consultation_started:
        return False
    if consultation_turn_count >= 3:
        return bool(_CONSULTATION_FOLLOW_UP_RE.search(normalized))
    # During the two short intake answers, keep contextual replies in the
    # consultation unless they are recognizable social turns. This preserves
    # natural answers such as "向東南" or "下個月結婚".
    return True


class DirectFinalTranscriptProcessor(FrameProcessor):
    """Turn each unique final transcript into exactly one LLM request.

    This deliberately bypasses UserStarted/UserStopped/VAD/controller logic for
    a UI that already owns push-to-talk boundaries and disallows barge-in. The
    shared context is still used by the assistant aggregator, so multi-turn
    conversation history remains intact.
    """

    def __init__(
        self,
        context: LLMContext,
        *,
        dedupe_window: int = 64,
        debounce_seconds: float = 0.08,
        consultation_questions: int = 0,
        before_llm_request: Callable[[int], Awaitable[None]] | None = None,
        context_preparer: Callable[[int, str], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__()
        self._context = context
        self._seen: set[tuple[str, str, str]] = set()
        self._seen_order: deque[tuple[str, str, str]] = deque(
            maxlen=max(1, dedupe_window)
        )
        self._debounce_seconds = max(0.0, debounce_seconds)
        self._pending_texts: list[str] = []
        self._commit_task: asyncio.Task[None] | None = None
        self._consultation_questions = max(0, consultation_questions)
        self._before_llm_request = before_llm_request
        self._context_preparer = context_preparer
        self._user_turn_count = 0
        self._last_user_text = ""
        self._user_text_history: list[str] = []
        self._consultation_turn_count = 0
        self._consultation_user_text_history: list[str] = []
        self._consultation_started = False
        self._current_turn_is_consultation = False

    _FLOW_MARKER = "[YISHUI_INTERNAL_CONSULTATION_STAGE]"

    @property
    def user_turn_count(self) -> int:
        return self._user_turn_count

    @property
    def last_user_text(self) -> str:
        return self._last_user_text

    @property
    def consultation_turn_count(self) -> int:
        return self._consultation_turn_count

    @property
    def current_turn_is_consultation(self) -> bool:
        return self._current_turn_is_consultation

    @property
    def consultation_text(self) -> str:
        """Return only consultation answers in conversational order."""

        return "\n".join(self._consultation_user_text_history)

    def _flow_instruction(
        self, consultation_turn: int, *, is_consultation: bool
    ) -> str | None:
        if self._consultation_questions != 3:
            return None
        if not is_consultation:
            action = (
                "本輪係普通對話，唔係風水命理諮詢資料收集。直接按用戶最新一句自然、"
                "簡短回應；問候就友善問佢今日有咩想了解，閒聊或無關問題就正常回答，"
                "超出能力先簡短講明。禁止牽強解讀成生肖、八字、運程或風水，"
                "亦禁止追問生肖、生辰、年份、方位或空間。普通對話唔計入三問流程。"
            )
        elif consultation_turn == 1:
            action = (
                "固定暖场的‘今日想問咩呢？’是第1问，这是用户对第1问的回答。"
                "输出最多三句、约六十至一百个中文字：前一至两句必须是具体分析和有用观察，"
                "不得反问或出现问号；最后一句才问第2问。第2问只问一个事实，"
                "不得用‘或、或者、同埋、以及’串联多项资料。下一问必须贴合知识库主题："
                "一般运程问完整公历出生日期，八字命理问完整公历出生日期，"
                "择日问所办事情，"
                "只有用户明确问家宅风水时才问大门方位，不得泛化成问哪个空间。"
            )
        elif consultation_turn == 2:
            action = (
                "这是用户对第2问的回答。输出最多三句、约六十至一百个中文字："
                "前一至两句具体分析新回答并联系第1次回答，不得反问或出现问号；"
                "最后一句才问最后的第3问。第3问只问一个事实，不得串联多项资料。"
                "按累计主题补齐一个关键事实：一般运程补关注方面，八字补当地出生时间，"
                "择日补日期范围，家宅风水补最想改善的方向；"
                "只有用户明确要求八字配家宅时，可把公历日期和当地出生时间视为一组出生资料。"
                "不得重复问已知资料。"
            )
        elif consultation_turn == 3:
            action = (
                "这是用户对第3问的回答。三问已完成，禁止继续提问或输出问号。"
                "现在给详细综合解析：先总结三轮关键信息，再说明现实观察、传统文化角度、"
                "两至三个安全可逆建议以及不确定性边界。使用自然香港粤语，"
                "合计六至八个短句、约一百八十至二百四十个中文字，只用一个口语段落。"
                "禁止Markdown、编号、项目符号或栏目名，必须用完整的陈述句结束。"
            )
        else:
            action = (
                "三问咨询已经完成。直接回答用户后续追问并结合之前的综合结论，"
                "禁止再进行资料收集式提问；信息不足时说明限制，但不要开启新一轮问答。"
            )
        return (
            f"{self._FLOW_MARKER}\n{action}\n"
            "转写有个别字不确定时，按上下文选择最可能的理解并用陈述句说明，"
            "不得额外提问确认。这是内部流程控制，不得向用户读出阶段编号、标记或规则。"
        )

    def _remember(self, key: tuple[str, str, str]) -> bool:
        if key in self._seen:
            return False
        if len(self._seen_order) == self._seen_order.maxlen:
            oldest = self._seen_order.popleft()
            self._seen.discard(oldest)
        self._seen_order.append(key)
        self._seen.add(key)
        return True

    async def _commit_pending(self) -> None:
        texts = self._pending_texts
        self._pending_texts = []
        if not texts:
            return
        combined = " ".join(texts)
        self._user_turn_count += 1
        self._last_user_text = combined
        self._user_text_history.append(combined)
        self._current_turn_is_consultation = is_consultation_turn(
            combined,
            consultation_started=self._consultation_started,
            consultation_turn_count=self._consultation_turn_count,
        )
        if self._current_turn_is_consultation:
            self._consultation_started = True
            self._consultation_turn_count += 1
            self._consultation_user_text_history.append(combined)
        effective_turn = (
            self._consultation_turn_count
            if self._current_turn_is_consultation
            else 1
        )
        if self._before_llm_request is not None:
            await self._before_llm_request(effective_turn)
        if self._context_preparer is not None:
            try:
                await self._context_preparer(effective_turn, combined)
            except Exception:
                logger.debug(
                    "Direct turn context preparation failed; continuing without it"
                )
        flow_instruction = self._flow_instruction(
            self._consultation_turn_count,
            is_consultation=self._current_turn_is_consultation,
        )
        if flow_instruction:
            self._context.transform_messages(
                lambda messages: [
                    message
                    for message in messages
                    if not (
                        isinstance(message, dict)
                        and message.get("role") == "system"
                        and str(message.get("content", "")).startswith(
                            self._FLOW_MARKER
                        )
                    )
                ]
            )
            self._context.add_message(
                {"role": "system", "content": flow_instruction}
            )
        # Refresh the trusted date for every turn, including ordinary chat.
        # Replacing it avoids stale dates and growing context across midnight.
        self._context.transform_messages(
            lambda messages: [
                message
                for message in messages
                if not (
                    message.get("role") == "system"
                    and str(message.get("content", "")).startswith(
                        VOICE_DEMO_DATE_MARKER
                    )
                )
            ]
        )
        self._context.add_message(
            {"role": "system", "content": build_voice_demo_date_instruction()}
        )
        self._context.add_message({"role": "user", "content": combined})
        await self.push_frame(
            LLMContextFrame(self._context), FrameDirection.DOWNSTREAM
        )

    async def _commit_after_debounce(self) -> None:
        await asyncio.sleep(self._debounce_seconds)
        self._commit_task = None
        await self._commit_pending()

    def _schedule_commit(self) -> None:
        if self._commit_task is not None and not self._commit_task.done():
            self._commit_task.cancel()
        self._commit_task = asyncio.create_task(
            self._commit_after_debounce(),
            name=f"{self.name}-commit-final-transcript",
        )

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if direction is FrameDirection.DOWNSTREAM and isinstance(
            frame, TranscriptionFrame
        ):
            text = (frame.text or "").strip()
            key = (text, frame.user_id or "", frame.timestamp or "")
            if text and self._remember(key):
                self._pending_texts.append(text)
                self._schedule_commit()
                return

        await self.push_frame(frame, direction)

    async def cleanup(self) -> None:
        if self._commit_task is not None and not self._commit_task.done():
            self._commit_task.cancel()
            try:
                await self._commit_task
            except asyncio.CancelledError:
                pass
        self._commit_task = None
        await super().cleanup()


__all__ = ["DirectFinalTranscriptProcessor", "is_consultation_turn"]
