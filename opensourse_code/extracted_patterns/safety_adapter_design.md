# Safety & Adapter Design — 可借鉴模式

来源：基于 agent-memory-server, openclaw-memory（当前项目）, cognee 的安全与适配器模式分析。

---

## 1. 读写命令分类（当前项目已有 + agent-memory-server 增强）

**推荐设计**：

```python
from enum import Enum

class CommandSafetyLevel(str, Enum):
    SAFE = "safe"              # 只读，无副作用，自动允许
    CONFIRM = "confirm"        # 写入，需要用户确认
    DANGEROUS = "dangerous"    # 高风险，禁止自动执行


class CommandRegistry:
    """飞书 CLI 命令注册与安全分类。"""
    
    # 只读命令（自动允许）
    SAFE_COMMANDS = {
        "doctor",
        "im +chat-search",
        "im +chat-messages-list",
        "im +messages-mget",
        "docs +fetch",
        "task +search",
        "task +tasklist-search",
        "task tasklists tasks --params -",
    }
    
    # 写入命令（需要确认）
    CONFIRM_COMMANDS = {
        "im +messages-send",
        "im +messages-reply",
        "docs +create",
        "docs +update",
        "task +create",
        "task +update",
        "task +complete",
        "task +comment",
        "task +assign",
        "task +followers",
        "task +tasklist-create",
        "task +tasklist-task-add",
    }
    
    # 高风险命令（禁止自动执行）
    DANGEROUS_COMMANDS = {
        "im +chat-dismiss",       # 退出群聊
        "docs +delete",           # 删除文档
        "task +delete",           # 删除任务
        "batch-execute",          # 批量执行
    }
    
    def get_safety_level(self, command: str) -> CommandSafetyLevel:
        """获取命令的安全等级。"""
        # 解析命令（去掉参数）
        base_command = command.split()[0] if " " in command else command
        
        if base_command in self.SAFE_COMMANDS:
            return CommandSafetyLevel.SAFE
        elif base_command in self.CONFIRM_COMMANDS:
            return CommandSafetyLevel.CONFIRM
        elif base_command in self.DANGEROUS_COMMANDS:
            return CommandSafetyLevel.DANGEROUS
        else:
            # 未知命令默认需要确认
            return CommandSafetyLevel.CONFIRM
```

**借鉴来源**：
- `openclaw-memory/src/safety/policy.py` — 当前项目的命令分类
- `openclaw-memory/src/adapters/command_registry.py` — 命令注册
- `agent-memory-server/prompt_security.py` — Prompt 安全校验

---

## 2. 安全策略与确认（当前项目已有）

**推荐设计**：

```python
from dataclasses import dataclass
from typing import Awaitable, Callable

@dataclass
class SafetyPolicy:
    """安全策略配置。"""
    
    # 是否允许自动执行只读命令
    auto_allow_safe: bool = True
    
    # 是否允许自动执行需要确认的命令（通常不允许）
    auto_allow_confirm: bool = False
    
    # 是否允许执行高风险命令（通常不允许）
    allow_dangerous: bool = False
    
    # 写入命令的 dry-run 开关
    dry_run_writes: bool = True
    
    # 最大批量操作数
    max_batch_size: int = 10
    
    def check_command(self, command: str, registry: CommandRegistry) -> tuple[bool, str]:
        """
        检查命令是否允许执行。
        
        Returns:
            (allowed, reason) 元组
        """
        level = registry.get_safety_level(command)
        
        if level == CommandSafetyLevel.SAFE:
            if self.auto_allow_safe:
                return True, "Safe read-only command"
            return False, "Safe commands disabled"
        
        elif level == CommandSafetyLevel.CONFIRM:
            if self.auto_allow_confirm:
                return True, "Confirm command auto-allowed"
            return False, "Requires user confirmation"
        
        elif level == CommandSafetyLevel.DANGEROUS:
            if self.allow_dangerous:
                return True, "Dangerous command allowed"
            return False, "Dangerous commands are disabled"
        
        return False, "Unknown command safety level"


@dataclass
class ConfirmationRequest:
    """用户确认请求。"""
    
    command: str
    description: str
    risk_level: CommandSafetyLevel
    side_effects: list[str]  # 副作用列表
    dry_run_output: str | None = None  # dry-run 输出（如果有）
    
    def format_prompt(self) -> str:
        """格式化确认提示。"""
        lines = [
            f"\n🔐 安全确认",
            f"命令：{self.command}",
            f"描述：{self.description}",
            f"风险等级：{self.risk_level.value}",
            f"副作用：",
        ]
        for effect in self.side_effects:
            lines.append(f"  - {effect}")
        
        if self.dry_run_output:
            lines.append(f"\nDry-run 输出：\n{self.dry_run_output}")
        
        lines.append(f"\n是否继续？(y/n)")
        return "\n".join(lines)
```

**借鉴来源**：
- `openclaw-memory/src/safety/policy.py` — 当前项目的安全策略
- `openclaw-memory/src/safety/confirmation.py` — 确认请求

---

## 3. Adapter 模式（推荐当前项目 + cognee）

**推荐设计**：

```python
from abc import ABC, abstractmethod
from typing import Any, Protocol


class PlatformAdapter(ABC):
    """平台适配器抽象基类。"""
    
    @abstractmethod
    async def fetch_messages(
        self,
        chat_id: str,
        limit: int = 100,
    ) -> list[dict]:
        """获取消息列表。"""
        pass
    
    @abstractmethod
    async def fetch_doc(self, doc_id: str) -> dict:
        """获取文档内容。"""
        pass
    
    @abstractmethod
    async def fetch_tasks(
        self,
        project_id: str,
        status: str | None = None,
    ) -> list[dict]:
        """获取任务列表。"""
        pass
    
    @abstractmethod
    async def send_message(
        self,
        chat_id: str,
        content: str,
        reply_to: str | None = None,
    ) -> bool:
        """发送消息。"""
        pass
    
    @abstractmethod
    async def create_task(
        self,
        project_id: str,
        title: str,
        description: str,
        assignee: str | None = None,
    ) -> str:
        """创建任务，返回任务 ID。"""
        pass


class LarkCliAdapter(PlatformAdapter):
    """飞书 CLI 适配器。"""
    
    def __init__(
        self,
        command_runner: Callable[[str], Awaitable[str]],
        safety_policy: SafetyPolicy,
        registry: CommandRegistry,
    ):
        self.run = command_runner
        self.policy = safety_policy
        self.registry = registry
    
    async def _run_command(
        self,
        command: str,
        require_confirm: bool = False,
    ) -> str:
        """运行命令，检查安全策略。"""
        allowed, reason = self.policy.check_command(command, self.registry)
        
        if not allowed:
            if require_confirm:
                # 抛出需要确认的异常，由上层处理
                raise ConfirmationRequired(command, reason)
            else:
                raise PermissionError(f"Command not allowed: {reason}")
        
        return await self.run(command)
    
    async def fetch_messages(self, chat_id: str, limit: int = 100) -> list[dict]:
        """获取消息（只读，自动允许）。"""
        output = await self._run_command(
            f"im +chat-messages-list --chat-id {chat_id} --limit {limit}"
        )
        return self._parse_messages(output)
    
    async def fetch_doc(self, doc_id: str) -> dict:
        """获取文档（只读，自动允许）。"""
        output = await self._run_command(f"docs +fetch --doc-id {doc_id}")
        return self._parse_doc(output)
    
    async def send_message(
        self,
        chat_id: str,
        content: str,
        reply_to: str | None = None,
    ) -> bool:
        """发送消息（写入，需要确认）。"""
        cmd = f"im +messages-send --chat-id {chat_id} --content '{content}'"
        if reply_to:
            cmd += f" --reply-to {reply_to}"
        
        try:
            output = await self._run_command(cmd, require_confirm=True)
            return self._parse_send_result(output)
        except ConfirmationRequired as e:
            # 抛出给上层，由用户确认后再执行
            raise
    
    def _parse_messages(self, output: str) -> list[dict]:
        """解析消息输出。"""
        # JSON 解析逻辑
        pass
    
    def _parse_doc(self, output: str) -> dict:
        """解析文档输出。"""
        pass
    
    def _parse_send_result(self, output: str) -> bool:
        """解析发送结果。"""
        pass
```

**关键设计**：
1. **适配器与平台解耦**：`PlatformAdapter` 抽象接口，不依赖具体 CLI 命令
2. **安全策略注入**：Adapter 构造函数注入 `SafetyPolicy` + `CommandRegistry`
3. **Dry-run 支持**：写入命令先跑 dry-run，显示结果后用户确认
4. **异常处理**：`ConfirmationRequired` 异常由上层捕获并提示用户

**借鉴来源**：
- `openclaw-memory/src/adapters/lark_cli_adapter.py` — 当前项目的飞书适配器
- `cognee/infrastructure/databases/graph/graph_db_interface.py` — 图数据库抽象接口
- `cognee/infrastructure/databases/vector/vector_db_interface.py` — 向量数据库抽象接口

---

## 4. Dry-run 机制（推荐）

**设计**：

```python
class DryRunExecutor:
    """Dry-run 执行器。"""
    
    def __init__(self, adapter: PlatformAdapter):
        self.adapter = adapter
    
    async def dry_run_write(
        self,
        operation: str,
        params: dict,
    ) -> DryRunResult:
        """
        执行写入命令的 dry-run。
        
        返回模拟结果，不产生实际副作用。
        """
        if operation == "send_message":
            return await self._dry_run_send(params)
        elif operation == "create_task":
            return await self._dry_run_create_task(params)
        elif operation == "update_doc":
            return await self._dry_run_update_doc(params)
        else:
            raise ValueError(f"Unknown operation: {operation}")
    
    async def _dry_run_send(self, params: dict) -> DryRunResult:
        """模拟发送消息。"""
        return DryRunResult(
            success=True,
            simulated_output=f"Message would be sent to {params['chat_id']}",
            side_effects=[
                f"Message will appear in chat {params['chat_id']}",
                f"Recipients will be notified",
            ],
            is_idempotent=False,
        )
    
    async def _dry_run_create_task(self, params: dict) -> DryRunResult:
        """模拟创建任务。"""
        return DryRunResult(
            success=True,
            simulated_output=f"Task would be created in {params['project_id']}",
            side_effects=[
                f"Task will be assigned to {params.get('assignee', 'unassigned')}",
                f"Project members will see the new task",
            ],
            is_idempotent=False,
        )


@dataclass
class DryRunResult:
    """Dry-run 结果。"""
    
    success: bool
    simulated_output: str
    side_effects: list[str]
    is_idempotent: bool
    error: str | None = None
```

**使用流程**：

```
用户触发写入操作
       │
       ▼
┌─────────────────────────┐
│ 1. Dry-run 执行          │
│    - 不产生实际副作用     │
│    - 返回模拟结果         │
└─────────────────────────┘
       │
       ▼
┌─────────────────────────┐
│ 2. 显示 Dry-run 结果      │
│    - 模拟输出             │
│    - 副作用列表           │
│    - 是否幂等             │
└─────────────────────────┘
       │
       ▼
┌─────────────────────────┐
│ 3. 用户确认 (y/n)         │
└─────────────────────────┘
       │
       ├─────┬─────┐
       │     │     │
       ▼     ▼     ▼
     Yes   No   Timeout
       │     │     │
       ▼     │     ▼
┌───────────┴─┐   │
│ 4. 实际执行  │   ▼
│    写入命令  │  取消，无副作用
└─────────────┘
```

---

## 5. Prompt 安全校验（推荐 agent-memory-server）

**问题**：CustomMemoryStrategy 允许用户提供自定义 Prompt，但需要防止注入攻击。

**解决方案**（`agent-memory-server/prompt_security.py`）：

```python
class PromptSecurityError(Exception):
    """Prompt security validation failed."""
    pass


def validate_custom_prompt(prompt: str, strict: bool = True) -> None:
    """
    验证自定义 Prompt 的安全性。
    
    Checks for:
    - System instruction injection
    - Command injection patterns
    - Secret/credential patterns
    - Infinite loops / recursion
    """
    dangerous_patterns = [
        "ignore previous instructions",
        "you are now",
        "system instruction",
        "execute this code",
        "run this command",
        "import os",
        "subprocess",
        "eval(",
        "exec(",
        "__import__",
        "api_key",
        "secret",
        "password",
        "token",
    ]
    
    prompt_lower = prompt.lower()
    for pattern in dangerous_patterns:
        if pattern in prompt_lower:
            raise PromptSecurityError(
                f"Prompt contains dangerous pattern: {pattern}"
            )
    
    # Check for excessive length (potential DoS)
    if len(prompt) > 10000:
        raise PromptSecurityError(
            f"Prompt too long: {len(prompt)} chars (max 10000)"
        )


def secure_format_prompt(
    template: str,
    allowed_vars: set[str],
    **kwargs,
) -> str:
    """
    安全地格式化 Prompt，防止模板注入。
    
    Only allows specified variables to be interpolated.
    """
    import re
    
    # Find all {var} patterns
    found_vars = re.findall(r"\{(\w+)\}", template)
    
    # Check that all found vars are allowed
    for var in found_vars:
        if var not in allowed_vars:
            raise PromptSecurityError(
                f"Template uses disallowed variable: {var}"
            )
    
    # Safe to format
    return template.format(**kwargs)
```

**借鉴来源**：
- `agent-memory-server/prompt_security.py` — `validate_custom_prompt`, `secure_format_prompt`

---

## 6. 推荐飞书 Memory Engine 的安全设计

```python
class FeishuSafetyConfig:
    """飞书 Memory Engine 安全配置。"""
    
    # 只读命令自动允许
    AUTO_ALLOW_SAFE = True
    
    # 写入命令默认 dry-run
    DRY_RUN_WRITES = True
    
    # 需要确认的写入命令
    REQUIRES_CONFIRM = {
        "im +messages-send",
        "im +messages-reply",
        "task +create",
        "task +update",
        "task +complete",
        "task +comment",
        "task +assign",
    }
    
    # 禁止自动执行的命令
    BLOCKED_COMMANDS = {
        "im +chat-dismiss",
        "docs +delete",
        "task +delete",
        "batch-execute",
    }


class FeishuMemoryEngine:
    """飞书 Memory Engine，集成安全策略。"""
    
    def __init__(
        self,
        adapter: PlatformAdapter,
        safety_policy: SafetyPolicy,
    ):
        self.adapter = adapter
        self.policy = safety_policy
        self.confirmation_callback: Callable[[ConfirmationRequest], Awaitable[bool]] | None = None
    
    def set_confirmation_callback(
        self,
        callback: Callable[[ConfirmationRequest], Awaitable[bool]],
    ):
        """设置确认回调。"""
        self.confirmation_callback = callback
    
    async def execute_with_safety(
        self,
        operation: str,
        **params,
    ) -> Any:
        """执行操作，应用安全策略。"""
        
        # 1. 检查操作类型
        is_write = operation in self.REQUIRES_CONFIRM
        is_blocked = operation in self.BLOCKED_COMMANDS
        
        if is_blocked:
            raise PermissionError(f"Operation blocked: {operation}")
        
        # 2. Dry-run（如果配置启用）
        if is_write and self.policy.dry_run_writes:
            dry_run_result = await self._dry_run(operation, params)
            
            # 3. 用户确认
            if self.confirmation_callback:
                confirmed = await self.confirmation_callback(
                    ConfirmationRequest(
                        command=operation,
                        description=f"Execute {operation}",
                        risk_level=CommandSafetyLevel.CONFIRM,
                        side_effects=dry_run_result.side_effects,
                        dry_run_output=dry_run_result.simulated_output,
                    )
                )
                if not confirmed:
                    raise PermissionError("User cancelled operation")
        
        # 4. 实际执行
        return await self._execute(operation, params)
    
    async def _dry_run(self, operation: str, params: dict) -> DryRunResult:
        """执行 dry-run。"""
        executor = DryRunExecutor(self.adapter)
        return await executor.dry_run_write(operation, params)
    
    async def _execute(self, operation: str, params: dict) -> Any:
        """实际执行操作。"""
        if operation == "fetch_messages":
            return await self.adapter.fetch_messages(**params)
        elif operation == "fetch_doc":
            return await self.adapter.fetch_doc(**params)
        elif operation == "send_message":
            return await self.adapter.send_message(**params)
        elif operation == "create_task":
            return await self.adapter.create_task(**params)
        else:
            raise ValueError(f"Unknown operation: {operation}")
```

---

## 7. 总结：关键借鉴点

| 项目 | 安全/适配器设计亮点 | 飞书可借鉴 |
|------|-------------------|-----------|
| **openclaw-memory** | 命令分类（SAFE/CONFIRM/DANGEROUS），ConfirmationRequest | 当前项目已有良好基础 |
| **agent-memory-server** | Prompt 安全校验，secure_format_prompt | Custom Prompt 场景 |
| **cognee** | 数据库抽象接口（Graph/Vector） | Adapter 模式 |

**飞书 Memory Engine 推荐设计**：
1. **保持当前项目的命令分类** — 已经设计良好
2. **增加 Dry-run 机制** — 写入命令先模拟，用户确认后再执行
3. **Prompt 安全校验** — 如果支持自定义提取 Prompt，需要防止注入
4. **Adapter 与平台解耦** — 保持当前设计，不直接依赖 CLI 命令
