# 剧本写作平台开发设计

2026-10-14 更新：模型配置、模型切换、创作对话及外发审批相关规则以[模型配置与创作对话设计](model-chat-design.md)及 [ADR-0011](adr/0011-direct-model-configuration-and-member-invocation.md) 为准；下文相关旧规则作为历史基线保留。新规则已实现；旧供应商与逐次外发确认描述仅作为历史基线保留。

状态：技术评审后基线。本文将已确认的产品规则转换为首版可实现的接口、数据和验收约束。没有既有代码或技术栈，因此技术选型属于建议方案。

## 1. 首版目标

首版服务内部团队，目标不超过约 20 名并发创作者，使用 Docker Compose 单机部署。系统支持：

- 管理员创建和管理账号、剧本模板、模型配置和审计记录。
- 普通用户登录并创建中文短剧作品。
- 竖屏微短剧和网剧短剧两种模板；作品保存模板版本和参数快照。
- 创作简报、人物、故事大纲、确认设定、变更提案和创作对话。
- 按集、场景或片段异步生成，支持选段重写、版本比较和恢复。
- OpenAI 兼容接口与 Claude 原生 Anthropic 接口。
- 外发前由作品所有者确认供应商、模型和数据范围。
- Markdown、DOCX 正式稿导出。

首版不包含公开注册、计费、多租户、Kubernetes、自动发布、自动质量评估和复杂自定义角色。

## 2. 推荐技术栈

| 层 | 选择 | 约束 |
| --- | --- | --- |
| 前端 | React、TypeScript、Vite | 中文界面；对话、设定/提案、正文/版本三栏工作区 |
| API | Python、FastAPI、Pydantic | REST JSON；长任务只创建任务，不在请求内等待模型完成 |
| 数据库 | PostgreSQL、SQLAlchemy 2、Alembic | 事务、外键、唯一约束和乐观锁由数据库保证 |
| 队列 | Redis、ARQ worker | 任务可重试、取消和查询；worker 不直接绕过领域服务写正文 |
| 文件 | 首版使用受控 Docker volume | 正式稿和备份与应用容器分离；未来可替换 S3 兼容存储 |
| 会话 | 服务端会话表 + HttpOnly、Secure、SameSite Cookie | 状态变更使用 CSRF 防护；停用和重置密码立即撤销会话 |
| 密码 | Argon2id | 不保存可逆密码；首次登录必须修改初始密码 |
| 凭证 | AES-256-GCM 或等价 AEAD | 主密钥来自 Docker Secret/受控文件，不进入日志或数据库 |
| 部署 | Docker Compose | `web`、`api`、`worker`、`postgres`、`redis`、`storage`；健康检查和迁移入口固定 |

2026-10-13 前端细化见[页面设计](frontend-page-design.md)与[开发方案](frontend-development-plan.md)。用户已确认完整页面范围、桌面优先且手机可操作、连续结构化正文编辑、编辑者显式保存版本、上游设定变更后的下游重新确认、正文生成期间的作品输入及外部对话锁定，以及作品内预览链接完成异地外发确认；相关 API 行为需补齐。相关 ADR 为 0007-0010，独立开发任务见[前端任务规格](../.scratch/frontend-experience/spec.md)。

## 3. 领域模型与关键约束

### 3.1 账号与权限

- `users`：`id`、登录名、密码哈希、角色（`ADMIN`/`WRITER`）、状态（`ACTIVE`/`DISABLED`）、`must_change_password`、时间戳。
- `sessions`：只存随机会话令牌哈希、用户、过期时间和撤销时间；密码重置、停用和角色变更撤销相关会话。
- `audit_records`：操作者、动作、目标类型/ID、结果、时间、请求追踪 ID；正文、prompt 和模型响应禁止进入审计字段。

平台角色不等于作品成员。管理员没有隐含的作品读取权限。

### 3.2 作品与协作

- `works`：所有者、标题、模板版本、状态（`ACTIVE`/`TRASHED`）、当前内容 `revision`、时间戳。
- `work_members`：作品、用户、成员角色（`OWNER`/`EDITOR`/`VIEWER`）、邀请状态和撤销时间；所有者唯一。
- `work_settings`：创建作品时从模板复制的集数、时长/字数、题材标签、输出格式和模板版本；之后只由作品权限规则修改。
- `script_templates` / `script_template_versions`：管理员维护；发布版本不可变，已有作品永远引用原版本快照。

所有作品查询必须经过 `WorkAccessService`. 资源 API 不接受调用方自带的 owner/member 字段作为授权依据。回收站作品不能被普通列表或生成 API 看到。

### 3.3 创作决策和版本

- `conversations` / `conversation_messages`：作品内对话；消息按作品权限读取，写入时保存作者和时间。
- `creative_briefs`、`characters`、`outlines`：草稿内容和确认状态。
- `locked_decisions`：已确认设定，带来源版本和确认人。
- `change_proposals`：目标设定、提案内容、提交人、状态（`PENDING`/`APPROVED`/`REJECTED`/`WITHDRAWN`）、审批人和审批时间。
- `work_versions`：追加式快照，包含父版本、作者/生成任务、内容摘要、内容文件引用和 `source_revision`。版本不更新、不删除。
- `releases`：所有者确认的正式稿，绑定不可变版本和发布时间；导出只接受 release。

恢复旧版本不是覆盖，而是基于旧版本创建新的 `work_version`，并递增作品 `revision`。任何写操作使用 `expected_revision`；不匹配返回 `409 WORK_REVISION_CONFLICT`。

### 3.4 模型与外发授权

- `model_providers`：`OPENAI_COMPATIBLE` 或 `ANTHROPIC`、基础 URL、启用状态。
- `model_configurations`：供应商、模型标识、展示名称、能力（文本/流式）、参数默认值和加密凭证引用；原始凭证永不返回。
- `external_transmission_consents`：作品、供应商、模型、数据类别、来源 revision、确认人、确认时间、撤销时间和状态。
- `generation_jobs`：作品、目标范围（集/场景/片段）、输入版本、模型配置、幂等键、状态、错误分类、输出版本和取消时间。
- `invocation_records`：供应商、模型、时间、状态、token 用量、耗时、错误代码和追踪 ID；不保存完整请求/响应。

外发确认至少绑定：作品、模型配置版本、发送的数据类别（对话/设定/大纲/正文）、输入 revision。供应商、模型、数据类别、输入 revision 或作品所有者撤销后，确认立即失效。

## 4. API 边界

所有接口使用 `/api/v1` 前缀，错误返回稳定的 `code` 和 `message`，不返回供应商原始错误或密钥。

### 4.1 认证与管理员

- `POST /api/v1/auth/login`
- `POST /api/v1/auth/logout`
- `POST /api/v1/auth/change-password`
- `GET /api/v1/me`
- `POST /api/v1/admin/users`
- `PATCH /api/v1/admin/users/{id}/status`
- `POST /api/v1/admin/users/{id}/reset-password`
- `PATCH /api/v1/admin/users/{id}/role`
- `GET /api/v1/admin/audit-records`

管理员创建用户时生成一次性初始密码或受控临时密码；用户第一次登录只能进入改密流程，不能读取作品或管理页。

### 4.2 作品和权限

- `POST /api/v1/works`
- `GET /api/v1/works`
- `GET /api/v1/works/{workId}`
- `POST /api/v1/works/{workId}/members/invitations`
- `POST /api/v1/works/{workId}/members/{userId}/revoke`
- `POST /api/v1/works/{workId}/transfer-ownership`
- `POST /api/v1/works/{workId}/trash`
- `POST /api/v1/works/{workId}/restore`
- `DELETE /api/v1/works/{workId}`（超过回收站保留期或所有者明确永久删除）

所有者管理协作者；编辑者不能管理成员、转移所有权、确认锁定设定或导出正式稿；查看者只读作品资料。

### 4.3 创作决策和版本

- `GET/POST /api/v1/works/{workId}/conversations`
- `GET/POST /api/v1/works/{workId}/brief`
- `GET/POST /api/v1/works/{workId}/characters`
- `GET/POST /api/v1/works/{workId}/outline`
- `POST /api/v1/works/{workId}/decisions/{id}/confirm`
- `POST /api/v1/works/{workId}/change-proposals`
- `POST /api/v1/works/{workId}/change-proposals/{id}/approve`
- `POST /api/v1/works/{workId}/change-proposals/{id}/reject`
- `GET /api/v1/works/{workId}/versions`
- `POST /api/v1/works/{workId}/versions/{versionId}/restore`
- `POST /api/v1/works/{workId}/releases`

确认和审批接口必须带 `expected_revision`，成功后在同一事务中写入新 revision、审计记录和相关状态变化。

### 4.4 模型和生成

- `GET /api/v1/models`：只返回管理员已启用且可供当前用户选择的模型元数据。
- `POST /api/v1/works/{workId}/external-consents`
- `POST /api/v1/works/{workId}/external-consents/{id}/revoke`
- `POST /api/v1/works/{workId}/generation-jobs`
- `GET /api/v1/works/{workId}/generation-jobs/{jobId}`
- `POST /api/v1/works/{workId}/generation-jobs/{jobId}/cancel`

生成创建请求必须包含目标范围、模型配置 ID、输入 revision、幂等键和所需数据类别。API 只在有效外发确认和权限检查通过后入队。

内部模型适配器接口：

```text
generate(request) -> stream[ModelChunk]
cancel(provider_request_id)
classify_error(error) -> TRANSIENT | RATE_LIMIT | AUTH | INVALID_REQUEST | CONTENT_POLICY | UNKNOWN
```

适配器统一处理流式/非流式响应、停止原因、token 用量、超时、取消和错误分类；重试只允许 `TRANSIENT` 和受控的 `RATE_LIMIT`，并复用同一 generation job 幂等键。

### 4.5 导出

- `POST /api/v1/works/{workId}/exports`：只接受 release ID 和 `MARKDOWN`/`DOCX`。
- `GET /api/v1/works/{workId}/exports/{exportId}`：查询状态和下载地址。

导出任务读取不可变 release，不读取当前草稿；下载地址短时有效，导出文件继承作品访问控制。

## 5. 状态机

### 5.1 GenerationJob

`QUEUED -> RUNNING -> SUCCEEDED | FAILED | CANCELED`

- 只有 `QUEUED` 可取消；`RUNNING` 取消需向供应商发出取消并等待 worker 收口。
- 同一幂等键只能对应一个任务；重复请求返回已有任务。
- 成功提交版本时使用数据库事务检查 `source_revision`；冲突则任务为 `FAILED`，错误码 `WORK_REVISION_CONFLICT`，不产生孤立正文。
- worker 崩溃由租约超时恢复为 `QUEUED`，重试次数有限并记录 attempt。

### 5.2 ChangeProposal

`PENDING -> APPROVED | REJECTED | WITHDRAWN`。审批只允许作品所有者执行；已审批提案只能通过新的提案反向修改，不能直接编辑历史记录。

### 5.3 Work

`ACTIVE -> TRASHED -> ACTIVE`，或 `TRASHED -> PURGED`。普通资源查询过滤 `TRASHED`；回收站恢复和永久删除需要所有者操作和审计记录。每日清理任务只处理超过 30 天的回收站作品。

## 6. Docker Compose 与运维

服务：`web`、`api`、`worker`、`postgres`、`redis`、`storage`。所有服务提供健康检查；API 启动先执行 Alembic 迁移，失败则不接收流量。

备份策略：每日备份 PostgreSQL 和作品文件，备份单独加密并保留 30 天；恢复需要管理员/运维权限和审计记录。备份路径不能挂载为应用可写目录。

必须提供：`.env.example`（只含变量名）、`docker-compose.yml`、`Dockerfile`、迁移命令、备份/恢复脚本、健康检查端点和本地启动文档。真实密钥只通过 Docker Secret 或部署环境注入。

## 7. 测试策略

- 单元测试：授权策略、状态机、模型错误分类、凭证加解密和参数校验。
- API 测试：每种角色的允许/拒绝矩阵；管理员无授权读取作品必须 `403`。
- 并发测试：重复幂等键、两个 editor 同时提交、恢复旧版本、任务重试。
- 合约测试：OpenAI 兼容和 Anthropic 适配器的最小请求、流式响应、usage、错误和取消。
- 安全测试：日志扫描 prompt/密钥、停用会话、首次改密、CSRF、短下载 URL、备份访问。
- 端到端验收：创建账号 → 创建作品 → 确认设定 → 外发确认 → 分段生成 → 版本恢复 → 发布 → Markdown/DOCX 导出 → 回收站恢复。

## 8. 分阶段开发任务

任务应按下面顺序在 AutoWonder 中执行；后续任务依赖前置任务的迁移、接口和测试。

1. **基础工程与部署骨架**：初始化前后端、Docker Compose、健康检查、配置分层、Alembic、CI 基础检查。
2. **认证、账号和审计**：Argon2id、HttpOnly 会话、首次改密、管理员账号生命周期、会话撤销、审计记录。
3. **作品、成员和统一授权**：作品/成员表、邀请/撤销、所有权转移、回收站、`WorkAccessService` 和权限测试。
4. **模板和创作资料**：模板版本、作品参数快照、简报/人物/大纲、确认设定和变更提案。
5. **版本与生成任务核心**：追加式版本、乐观锁、幂等任务、状态机、Redis worker、重试/取消/冲突处理。
6. **模型适配和外发确认**：OpenAI 兼容与 Anthropic 适配器、凭证加密、模型选择、同意生命周期、调用元数据。
7. **协作工作区和导出**：三栏工作区、SSE/轮询任务进度、版本比较/恢复、正式稿发布、Markdown/DOCX 导出。
8. **备份、安全与验收**：加密备份、30 天清理、恢复演练、日志脱敏、权限/并发/供应商合约测试和 Docker 验收。
