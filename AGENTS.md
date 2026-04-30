# 项目执行约束

- 当前项目默认执行后端为飞书官方 `lark-cli`。
- 所有执行相关代码必须通过 `LarkCliAdapter` 调用真实 CLI，不允许直接伪造飞书 API。
- 在 Windows PowerShell 环境中，优先使用 `lark-cli.cmd`，不要默认使用 `lark-cli.ps1`，因为执行策略可能拦截 `.ps1`。

# Memory Engine 架构约束

- Memory Engine 的核心逻辑必须与 `lark-cli` adapter 分离。
- Memory Engine 核心逻辑不得依赖 `lark-cli` 的具体命令字符串。
- 执行层必须抽象为 adapter/interface，以便后续可增加 OpenClaw backend。
- 代码结构必须保留可替换后端的能力，便于后续再适配 OpenClaw。
