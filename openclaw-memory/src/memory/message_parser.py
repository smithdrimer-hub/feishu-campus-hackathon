"""V1.19 P1-A: 消息解析器 — 统一处理飞书各 msg_type 的文本提取与媒体元数据记录。

不静默丢弃非文本消息。对 image/file/audio/video 等类型：
  - 生成占位文本（如 "[图片消息]"）供提取器消费
  - 记录 media_refs 元数据作为证据链的一部分
  - 不做 OCR / 文件下载 / 内容提取

用法:
    from memory.message_parser import MessageParser
    parser = MessageParser()
    result = parser.parse(event_dict)
    print(result.text)        # 供提取器使用的文本
    print(result.media_refs)  # 媒体元数据列表
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# ParsedContent
# ---------------------------------------------------------------------------


@dataclass
class ParsedContent:
    """消息解析结果。"""

    text: str = ""
    """供提取器消费的文本。非文本类型为占位描述。"""

    msg_type: str = "text"
    """原始 msg_type。"""

    has_unsupported_media: bool = False
    """消息包含当前版本无法解析内容的非文本证据。"""

    media_refs: list[dict[str, Any]] = field(default_factory=list)
    """媒体元数据列表（image_key, file_key, file_name 等）。"""

    mentions: list[dict[str, str]] = field(default_factory=list)
    """V1.19 P1-D: post 消息中 @提及列表 [{"user_id": "ou_xxx", "user_name": "张三"}]。"""

    links: list[dict[str, str]] = field(default_factory=list)
    """V1.19 P1-D: post 消息中链接列表 [{"text": "文档", "url": "https://..."}]。"""


# ---------------------------------------------------------------------------
# Per-type handlers
# ---------------------------------------------------------------------------

# 每个 handler 签名: (content_str, sender_name) -> ParsedContent
# content_str 是飞书消息的 content 字段（可能是纯文本或 JSON 字符串）


def _parse_text(content: str, sender_name: str = "") -> ParsedContent:
    return ParsedContent(text=content, msg_type="text", has_unsupported_media=False)


def _parse_post(content: str, sender_name: str = "") -> ParsedContent:
    """解析 post (富文本) 消息的文本内容。

    V1.19 P1-D: 增强解析 —— 提取 @提及 (user_id + user_name)、链接 (text + url)、
    文档/任务引用、行内媒体。结构化数据通过 ParsedContent.mentions/links/media_refs
    传递给下游，补充 extractor._extract_mentions() 所需的 at_list 数据。

    post content 格式:
      {"text": "...", "title": "...", "content": [[{"tag":"text","text":"..."}, ...], ...]}
    或简化为纯文本段落数组:
      [{"tag":"text","text":"..."}, ...]
    """
    if not content:
        return ParsedContent(msg_type="post")

    try:
        parsed = json.loads(content) if isinstance(content, str) else content
    except (json.JSONDecodeError, TypeError):
        return ParsedContent(text=str(content), msg_type="post")

    # 收集器
    mentions: list[dict[str, str]] = []
    links: list[dict[str, str]] = []
    media_refs: list[dict[str, Any]] = []
    doc_refs: list[dict[str, str]] = []

    if not isinstance(parsed, dict):
        # 可能是段落数组
        if isinstance(parsed, list):
            text = _flatten_post_blocks(
                parsed,
                mentions_out=mentions,
                links_out=links,
                media_refs_out=media_refs,
                doc_refs_out=doc_refs,
            )
            return ParsedContent(
                text=text, msg_type="post",
                mentions=mentions, links=links,
                media_refs=media_refs,
            )
        return ParsedContent(text=str(parsed), msg_type="post")

    title = parsed.get("title", "")
    text = parsed.get("text", "")
    body = parsed.get("content", [])

    parts = []
    if title:
        parts.append(title)
    if text:
        parts.append(text)
    if isinstance(body, list):
        body_text = _flatten_post_blocks(
            body,
            mentions_out=mentions,
            links_out=links,
            media_refs_out=media_refs,
            doc_refs_out=doc_refs,
        )
        if body_text:
            parts.append(body_text)

    # doc_refs 合并到 media_refs（文档引用本质上是无法解析内容的引用证据）
    for dr in doc_refs:
        media_refs.append({
            "media_type": dr.get("task_id", "") and "mention_task" or "mention_doc",
            **(dr if isinstance(dr, dict) else {}),
            "unsupported_reason": "文档/任务引用，当前版本未解析引用内容",
        })

    return ParsedContent(
        text="\n".join(parts),
        msg_type="post",
        mentions=mentions,
        links=links,
        media_refs=media_refs,
    )


def _flatten_post_blocks(
    blocks: list,
    mentions_out: list[dict[str, str]] | None = None,
    links_out: list[dict[str, str]] | None = None,
    media_refs_out: list[dict[str, Any]] | None = None,
    doc_refs_out: list[dict[str, str]] | None = None,
) -> str:
    """将 post 消息的段落数组展平为纯文本，同时收集结构化数据。

    每个 block 可以是一个元素 dict {"tag":"text","text":"..."}
    或是一个段落（元素列表）[[{...}, {...}], ...]。

    可选 collector 参数用于收集 @提及、链接、媒体引用和文档引用。
    """
    parts = []
    for block in blocks:
        if isinstance(block, dict):
            tag = block.get("tag", "")
            if tag == "text":
                parts.append(block.get("text", ""))
            elif tag == "a":
                text = block.get("text", "")
                url = block.get("href", "")
                parts.append(text)
                if links_out is not None and url:
                    links_out.append({"text": str(text), "url": str(url)})
            elif tag == "at":
                uid = block.get("user_id", "")
                name = block.get("user_name", "")
                parts.append(f"@{name or uid}")
                if mentions_out is not None and uid:
                    mentions_out.append({"user_id": str(uid), "user_name": str(name or uid)})
            elif tag == "img":
                img_key = block.get("image_key", "")
                parts.append("[图片]")
                if media_refs_out is not None:
                    ref: dict[str, Any] = {"media_type": "image", "in_post": True}
                    if img_key:
                        ref["image_key"] = img_key
                    ref["unsupported_reason"] = "post 消息中的行内图片，当前版本未解析"
                    media_refs_out.append(ref)
            elif tag == "code_block":
                lang = block.get("language", "")
                code_text = block.get("text", "") or "".join(
                    e.get("text", "") for e in block.get("elements", [])
                    if isinstance(e, dict)
                )
                lang_label = f":{lang}" if lang else ""
                parts.append(f"[代码块{lang_label}]")
                if media_refs_out is not None:
                    media_refs_out.append({
                        "media_type": "code_block",
                        "language": lang,
                        "code_preview": code_text[:120] if code_text else "",
                        "unsupported_reason": "代码块，当前版本未解析代码语义",
                    })
            elif tag == "emotion":
                parts.append(block.get("text", "[表情]"))
            elif tag == "mention_doc":
                doc_url = block.get("url", "") or block.get("token", "")
                doc_title = block.get("title", "")
                label = f": {doc_title}" if doc_title else ""
                parts.append(f"[文档引用{label}]")
                if doc_refs_out is not None:
                    doc_refs_out.append({"doc_url": str(doc_url), "title": str(doc_title)})
            elif tag == "mention_task":
                task_id = block.get("task_id", "")
                task_title = block.get("title", "")
                label = f": {task_title}" if task_title else ""
                parts.append(f"[任务引用{label}]")
                if doc_refs_out is not None:
                    doc_refs_out.append({"task_id": str(task_id), "title": str(task_title)})
            elif tag == "media":
                file_key = block.get("file_key", "")
                file_name = block.get("file_name", "")
                label = f": {file_name}" if file_name else ""
                parts.append(f"[嵌入文件{label}]")
                if media_refs_out is not None:
                    ref = {"media_type": "file", "in_post": True}
                    if file_key:
                        ref["file_key"] = file_key
                    if file_name:
                        ref["file_name"] = file_name
                    ref["unsupported_reason"] = "post 消息中的嵌入文件，当前版本未解析"
                    media_refs_out.append(ref)
            elif tag == "equation":
                latex = block.get("text", "")
                parts.append(f"[公式: {latex[:30]}]" if latex else "[公式]")
        elif isinstance(block, list):
            # 段落（元素列表）
            para_text = _flatten_post_blocks(
                block,
                mentions_out=mentions_out,
                links_out=links_out,
                media_refs_out=media_refs_out,
                doc_refs_out=doc_refs_out,
            )
            if para_text:
                parts.append(para_text)
    return " ".join(parts)


def _parse_image(content: str, sender_name: str = "") -> ParsedContent:
    media_ref: dict[str, Any] = {"media_type": "image"}
    try:
        data = json.loads(content) if isinstance(content, str) and content.strip().startswith("{") else {}
        if isinstance(data, dict):
            image_key = data.get("image_key", "")
            if image_key:
                media_ref["image_key"] = image_key
            width = data.get("width")
            if width:
                media_ref["width"] = width
            height = data.get("height")
            if height:
                media_ref["height"] = height
    except (json.JSONDecodeError, TypeError):
        pass

    media_ref["unsupported_reason"] = "图片消息，当前版本未解析图片内容"
    desc = _build_media_desc(media_ref)
    return ParsedContent(
        text=f"[图片消息{desc}]",
        msg_type="image",
        has_unsupported_media=True,
        media_refs=[media_ref],
    )


def _parse_file(content: str, sender_name: str = "") -> ParsedContent:
    media_ref: dict[str, Any] = {"media_type": "file"}
    mime_type = ""
    file_name = ""
    try:
        data = json.loads(content) if isinstance(content, str) and content.strip().startswith("{") else {}
        if isinstance(data, dict):
            file_key = data.get("file_key", "")
            if file_key:
                media_ref["file_key"] = file_key
            file_name = data.get("file_name", "")
            if file_name:
                media_ref["file_name"] = file_name
            file_size = data.get("file_size")
            if file_size:
                media_ref["file_size"] = file_size
            mime_type = data.get("mime_type", "")
            if mime_type:
                media_ref["mime_type"] = mime_type
    except (json.JSONDecodeError, TypeError):
        pass

    is_text = _is_text_mime(mime_type) or _is_text_extension(file_name)
    if is_text:
        media_ref["unsupported_reason"] = (
            "文本文件，可通过飞书 API 下载内容后提取；当前版本未自动下载"
        )
        media_ref["extractable"] = True
        label = _build_media_desc(media_ref)
        return ParsedContent(
            text=f"[文本文件{label}]",
            msg_type="file",
            has_unsupported_media=False,
            media_refs=[media_ref],
        )
    else:
        media_ref["unsupported_reason"] = "非文本文件，当前版本未解析文件内容"
        label = _build_media_desc(media_ref)
        return ParsedContent(
            text=f"[文件消息{label}]",
            msg_type="file",
            has_unsupported_media=True,
            media_refs=[media_ref],
        )


def _parse_audio(content: str, sender_name: str = "") -> ParsedContent:
    media_ref: dict[str, Any] = {"media_type": "audio"}
    try:
        data = json.loads(content) if isinstance(content, str) and content.strip().startswith("{") else {}
        if isinstance(data, dict):
            file_key = data.get("file_key", "")
            if file_key:
                media_ref["file_key"] = file_key
            duration = data.get("duration", 0)
            if duration:
                media_ref["duration_ms"] = duration
    except (json.JSONDecodeError, TypeError):
        pass

    dur = media_ref.get("duration_ms", 0)
    dur_str = f"{dur // 1000}秒" if dur else ""
    media_ref["unsupported_reason"] = "语音消息，当前版本未做语音转文字"
    return ParsedContent(
        text=f"[语音消息{dur_str}]",
        msg_type="audio",
        has_unsupported_media=True,
        media_refs=[media_ref],
    )


def _parse_video(content: str, sender_name: str = "") -> ParsedContent:
    media_ref: dict[str, Any] = {"media_type": "video"}
    try:
        data = json.loads(content) if isinstance(content, str) and content.strip().startswith("{") else {}
        if isinstance(data, dict):
            file_key = data.get("file_key", "")
            if file_key:
                media_ref["file_key"] = file_key
            image_key = data.get("image_key", "")
            if image_key:
                media_ref["image_key"] = image_key
            duration = data.get("duration", 0)
            if duration:
                media_ref["duration_ms"] = duration
    except (json.JSONDecodeError, TypeError):
        pass

    dur = media_ref.get("duration_ms", 0)
    dur_str = f"{dur // 1000}秒" if dur else ""
    media_ref["unsupported_reason"] = "视频消息，当前版本未解析视频内容"
    return ParsedContent(
        text=f"[视频消息{dur_str}]",
        msg_type="video",
        has_unsupported_media=True,
        media_refs=[media_ref],
    )


def _parse_share_chat(content: str, sender_name: str = "") -> ParsedContent:
    media_ref: dict[str, Any] = {"media_type": "share_chat"}
    try:
        data = json.loads(content) if isinstance(content, str) and content.strip().startswith("{") else {}
        if isinstance(data, dict):
            chat_id = data.get("chat_id", "")
            if chat_id:
                media_ref["chat_id"] = chat_id
            chat_name = data.get("chat_name", "")
            if chat_name:
                media_ref["chat_name"] = chat_name
    except (json.JSONDecodeError, TypeError):
        pass

    chat_name = media_ref.get("chat_name", "")
    label = f": {chat_name}" if chat_name else ""
    media_ref["unsupported_reason"] = "群聊分享，当前版本未解析分享内容"
    return ParsedContent(
        text=f"[群聊分享{label}]",
        msg_type="share_chat",
        has_unsupported_media=True,
        media_refs=[media_ref],
    )


def _parse_share_user(content: str, sender_name: str = "") -> ParsedContent:
    media_ref: dict[str, Any] = {"media_type": "share_user"}
    try:
        data = json.loads(content) if isinstance(content, str) and content.strip().startswith("{") else {}
        if isinstance(data, dict):
            user_id = data.get("user_id", "")
            if user_id:
                media_ref["user_id"] = user_id
            user_name = data.get("user_name", "")
            if user_name:
                media_ref["user_name"] = user_name
    except (json.JSONDecodeError, TypeError):
        pass

    user_name = media_ref.get("user_name", "")
    label = f": @{user_name}" if user_name else ""
    media_ref["unsupported_reason"] = "名片分享，当前版本未解析分享内容"
    return ParsedContent(
        text=f"[名片分享{label}]",
        msg_type="share_user",
        has_unsupported_media=True,
        media_refs=[media_ref],
    )


def _parse_interactive(content: str, sender_name: str = "") -> ParsedContent:
    """交互式卡片消息。尝试提取卡片中的文本，记录完整卡片数据。"""
    media_ref: dict[str, Any] = {"media_type": "interactive"}
    text = ""

    # 尝试从 content 中提取可读文本
    if content:
        if isinstance(content, str) and content.strip().startswith("{"):
            try:
                data = json.loads(content)
                # 提取卡片标题
                title = _extract_card_title(data)
                if title:
                    text = f"[交互消息: {title}]"
                    media_ref["card_title"] = title
                else:
                    text = "[交互消息]"
            except (json.JSONDecodeError, TypeError):
                text = "[交互消息]"
        elif "<card" in str(content):
            # card HTML 格式
            text = "[交互消息]"
            media_ref["card_content"] = str(content)[:200]
        else:
            text = f"[交互消息: {str(content)[:120]}]"

    media_ref["unsupported_reason"] = "交互式卡片消息，当前版本未完整解析卡片内容"
    return ParsedContent(
        text=text,
        msg_type="interactive",
        has_unsupported_media=True,
        media_refs=[media_ref],
    )


def _parse_sticker(content: str, sender_name: str = "") -> ParsedContent:
    media_ref: dict[str, Any] = {"media_type": "sticker"}
    try:
        data = json.loads(content) if isinstance(content, str) and content.strip().startswith("{") else {}
        if isinstance(data, dict):
            file_key = data.get("file_key", "")
            if file_key:
                media_ref["file_key"] = file_key
    except (json.JSONDecodeError, TypeError):
        pass

    media_ref["unsupported_reason"] = "贴纸消息，无文本内容"
    return ParsedContent(
        text="[贴纸消息]",
        msg_type="sticker",
        has_unsupported_media=True,
        media_refs=[media_ref],
    )


def _parse_system(content: str, sender_name: str = "") -> ParsedContent:
    return ParsedContent(text="", msg_type="system", has_unsupported_media=False)


def _parse_unknown(content: str, sender_name: str = "") -> ParsedContent:
    return ParsedContent(
        text=f"[未知消息类型]",
        msg_type="unknown",
        has_unsupported_media=True,
        media_refs=[{
            "media_type": "unknown",
            "content_preview": str(content)[:200],
            "unsupported_reason": "未知消息类型，当前版本未处理",
        }],
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# ── 文件类型判断 ────────────────────────────────────────────────

_TEXT_MIME_TYPES = frozenset({
    "text/plain", "text/markdown", "text/csv", "text/html", "text/xml",
    "text/css", "text/javascript", "text/x-python", "text/x-java",
    "text/x-c", "text/x-c++", "text/x-go", "text/x-rust",
    "application/json", "application/x-yaml", "application/xml",
    "application/javascript", "application/x-sh", "application/x-python",
})

_TEXT_EXTENSIONS = frozenset({
    ".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".xml", ".html", ".htm",
    ".py", ".js", ".ts", ".java", ".go", ".rs", ".c", ".cpp", ".h", ".hpp",
    ".css", ".scss", ".sh", ".bash", ".zsh", ".ps1", ".bat", ".toml", ".ini",
    ".cfg", ".conf", ".log", ".sql", ".r", ".rb", ".php", ".swift", ".kt",
    ".env", ".gitignore", ".dockerfile", ".makefile",
})


def _is_text_mime(mime_type: str) -> bool:
    """判断 MIME 类型是否为可提取的文本类型。"""
    if not mime_type:
        return False
    if mime_type in _TEXT_MIME_TYPES:
        return True
    if mime_type.startswith("text/"):
        return True
    return False


def _is_text_extension(filename: str) -> bool:
    """判断文件扩展名是否属于已知文本类型。"""
    if not filename:
        return False
    ext = Path(filename).suffix.lower()
    return ext in _TEXT_EXTENSIONS


# ── 文件内容提取 ────────────────────────────────────────────────

def try_extract_text_file_content(content_bytes: bytes, mime_type: str,
                                   file_name: str = "",
                                   max_chars: int = 10000) -> str:
    """V1.19: 尝试从文件二进制内容中提取文本。

    对已知文本类型做解码，对非文本类型返回空字符串。
    内容截断至 max_chars 防止事件膨胀。
    """
    if not content_bytes:
        return ""
    if not _is_text_mime(mime_type) and not _is_text_extension(file_name):
        return ""

    # 尝试 UTF-8，失败则尝试 GBK
    for encoding in ("utf-8", "gbk", "latin-1"):
        try:
            text = content_bytes.decode(encoding)
            if len(text) > max_chars:
                text = text[:max_chars] + "\n...[截断]"
            return text
        except (UnicodeDecodeError, LookupError):
            continue
    return ""


def _build_media_desc(ref: dict[str, Any]) -> str:
    """从 media_ref dict 构建人类可读的描述片段。"""
    parts = []
    file_name = ref.get("file_name", "")
    if file_name:
        parts.append(file_name)
    file_size = ref.get("file_size")
    if file_size:
        if file_size >= 1_000_000:
            parts.append(f"{file_size / 1_000_000:.1f}MB")
        elif file_size >= 1000:
            parts.append(f"{file_size / 1000:.0f}KB")
        else:
            parts.append(f"{file_size}B")
    return f": {' · '.join(parts)}" if parts else ""


def _extract_card_title(data: dict) -> str:
    """尝试从卡片 JSON 中提取标题。"""
    header = data.get("header", {})
    if isinstance(header, dict):
        title_obj = header.get("title", {})
        if isinstance(title_obj, dict):
            return title_obj.get("content", "") or title_obj.get("text", "")
    # 也尝试顶层 title
    title = data.get("title", "")
    if isinstance(title, str):
        return title
    return ""


# ---------------------------------------------------------------------------
# MessageParser
# ---------------------------------------------------------------------------

HandlerFunc = Callable[[str, str], ParsedContent]


class MessageParser:
    """按 msg_type 路由的消息解析器。

    用法:
        parser = MessageParser()
        result = parser.parse_content(msg.get("content", ""), msg.get("msg_type", "text"))
        event["text"] = result.text
        event["has_unsupported_media"] = result.has_unsupported_media
        event["media_refs"] = result.media_refs
    """

    def __init__(self) -> None:
        self._handlers: dict[str, HandlerFunc] = {
            "text": _parse_text,
            "post": _parse_post,
            "image": _parse_image,
            "file": _parse_file,
            "audio": _parse_audio,
            "video": _parse_video,
            "share_chat": _parse_share_chat,
            "share_user": _parse_share_user,
            "interactive": _parse_interactive,
            "sticker": _parse_sticker,
            "system": _parse_system,
        }
        self._unknown_handler: HandlerFunc = _parse_unknown

    def register(self, msg_type: str, handler: HandlerFunc) -> None:
        """注册自定义 msg_type 处理器。"""
        self._handlers[msg_type] = handler

    def parse_content(self, content: str, msg_type: str,
                      sender_name: str = "") -> ParsedContent:
        """解析消息 content 字段，返回 ParsedContent。"""
        handler = self._handlers.get(msg_type, self._unknown_handler)
        return handler(content, sender_name)

    def parse_event(self, event: dict) -> ParsedContent:
        """从规范化事件 dict 解析。

        事件 dict 应包含 content、msg_type 字段（如 _normalize_event 的输出）。
        """
        content = event.get("content", "") or event.get("text", "")
        msg_type = event.get("msg_type", "text")
        sender_name = (event.get("sender", {}) or {}).get("name", "")
        return self.parse_content(str(content), str(msg_type), str(sender_name))


# ── 文件事件增强 ────────────────────────────────────────────────

def enrich_file_event(event: dict, adapter=None,
                       data_dir: str = "data/media_cache") -> dict:
    """V1.19: 对文本文件消息尝试下载内容并附加到 event text。

    在事件标准化之后、提取之前调用。下载失败时保持原 placeholder。

    Args:
        event: 已标准化的文件消息事件（需含 message_id 和 media_refs）。
        adapter: LarkCliAdapter 实例（可选，为 None 时跳过下载）。
        data_dir: 媒体缓存目录。

    Returns:
        传入的 event dict（原地修改）。
    """
    if adapter is None:
        return event
    if event.get("msg_type") != "file":
        return event

    media_refs = event.get("media_refs", [])
    if not media_refs:
        return event

    ref = media_refs[0]
    if not ref.get("extractable"):
        return event  # 非文本文件，不下载

    file_key = ref.get("file_key", "")
    message_id = event.get("message_id", "")
    if not file_key or not message_id:
        return event

    import os
    import tempfile
    cache_dir = Path(data_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    # 检查缓存
    cache_name = f"{message_id}_{file_key}"
    for ext in ("", ".txt", ".md", ".json", ".csv"):
        cache_path = cache_dir / (cache_name + ext)
        if cache_path.exists():
            content = cache_path.read_bytes()
            text = try_extract_text_file_content(
                content,
                ref.get("mime_type", "text/plain"),
                ref.get("file_name", ""),
            )
            if text:
                event["text"] = event.get("text", "") + "\n\n--- 文件内容 ---\n" + text
                event["content"] = event["text"]
                ref["content_extracted"] = True
                ref.pop("unsupported_reason", None)
            return event

    # 下载
    try:
        import logging
        logger = logging.getLogger(__name__)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".tmp")
        tmp.close()
        result = adapter.download_resource(message_id, file_key, tmp.name)
        if result.returncode == 0:
            content = Path(tmp.name).read_bytes()
            if content:
                # 缓存
                mime = ref.get("mime_type", "")
                ext = _guess_cache_ext(ref.get("file_name", ""), mime)
                (cache_dir / (cache_name + ext)).write_bytes(content)
                text = try_extract_text_file_content(
                    content, mime, ref.get("file_name", ""),
                )
                if text:
                    event["text"] = event.get("text", "") + "\n\n--- 文件内容 ---\n" + text
                    event["content"] = event["text"]
                    ref["content_extracted"] = True
                    ref.pop("unsupported_reason", None)
    except Exception:
        pass
    finally:
        try:
            Path(tmp.name).unlink(missing_ok=True)
        except Exception:
            pass

    return event


def _guess_cache_ext(file_name: str, mime_type: str) -> str:
    """根据文件名或 MIME 类型推断缓存扩展名。"""
    if file_name:
        ext = Path(file_name).suffix
        if ext:
            return ext
    mime_map = {
        "text/plain": ".txt", "text/markdown": ".md",
        "text/csv": ".csv", "text/html": ".html",
        "application/json": ".json", "application/x-yaml": ".yaml",
    }
    return mime_map.get(mime_type, ".txt")


# 模块级默认实例，方便单行使用
_default_parser: MessageParser | None = None


def get_parser() -> MessageParser:
    global _default_parser
    if _default_parser is None:
        _default_parser = MessageParser()
    return _default_parser
