# 05: 模型配置、供应商适配与外发确认

**What to build:** 让管理员安全维护供应商和不可变模型配置版本，让平台通过统一适配器调用 OpenAI 兼容与 Anthropic 供应商；让作品所有者针对具体输入快照、模型版本和数据类别授权创作对话外发。

**Type:** task

**Blocked by:** 01: 基础工程与账号会话；02: 作品创建与统一访问控制

**Status:** resolved

- [x] 管理员可以创建、启用、停用供应商并维护数据保留、删除和故障处理说明。
- [x] 缺少完整供应商说明时不能启用供应商；作品所有者授权前可以看到这些说明。
- [x] 管理员修改供应商、模型标识、参数或凭证引用时创建新的不可变模型配置版本。
- [x] 模型凭证服务端加密保存，原始凭证不出现在普通 API 响应、日志、审计记录或备份清单。
- [x] 普通用户只能看到管理员已启用模型的非敏感元数据。
- [x] OpenAI 兼容和 Anthropic 适配器遵守统一的请求、流式响应、停止原因、用量、超时、取消和错误分类契约。
- [x] 作品所有者可以针对作品输入 revision、模型配置版本和数据类别创建或撤销外发确认。
- [x] 输入 revision、模型配置、数据类别或作品进入回收站后，原外发确认立即失效。
- [x] 用户可以在有效外发确认下进行创作对话；对话保存为作品资料但不产生生成版本。
- [x] 模型调用记录只保存供应商、模型、时间、状态、用量、耗时、错误代码和追踪 ID。
- [x] 契约和安全测试验证适配器错误分类、凭证加密、授权生命周期和敏感内容不落日志。

## Comments

2026-10-13: Implemented provider disclosures, encrypted credentials, immutable model configuration versions, adapter contracts, consent lifecycle checks, creative conversations, and invocation records.
