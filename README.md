# AI Scriptwriting Platform

幕间是中文短剧的创作空间，采用“专注写作”布局：故事目录、结构化剧本纸页与可收起的创作对话。Web 前端使用 React、TypeScript 和 Vite，接入现有 FastAPI 服务。

支持作品与模板选择、三阶段设定确认和变更提案、工作草稿编辑、整部作品版本比较与恢复、正式稿发布、Markdown / DOCX 导出、成员邀请及回收站。作品权限由所有者、编辑者、查看者决定，与平台管理员角色独立。

## Local development

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/alembic upgrade head
.venv/bin/python -m pytest -q
.venv/bin/uvicorn app.main:app --reload
```

Set `APP_DATABASE_URL` (or add it to `.env`) to use PostgreSQL. Without it, local development uses `sqlite:///./app.db`. An initial administrator can be created once through `POST /api/v1/admin/bootstrap`, or automatically at startup with `APP_BOOTSTRAP_ADMIN_USERNAME` and `APP_BOOTSTRAP_ADMIN_PASSWORD`.

## Web 前端

需要 Node.js 20+。先按上面的方式启动后端，再启动前端：

```bash
npm --prefix frontend ci
npm --prefix frontend run dev
```

访问 `http://localhost:5173/` 登录真实账号。账号由管理员在“账户设置 → 平台管理”中创建；首次登录会要求修改初始密码。开发服务器将 `/api` 与 `/healthz` 代理到 `http://127.0.0.1:8000`，可用 `API_PROXY_TARGET` 指定其他地址。

访问 `http://localhost:5173/?demo=1#work/demo-0` 体验示例作品。示例数据单独保存在浏览器中，不写入真实账号；可在账户面板重置示例或切换到真实登录。

- 正文按集、场景、片段和剧本块编辑。停止输入 2 秒后自动保存工作草稿。Ctrl / ⌘ + S 保存工作草稿；草稿保存不创建版本。“版本”面板可明确保存整部作品版本。
- 已确认设定通过提案修改。生成前依次确认创作简报、人物设定和故事大纲。所有者与编辑者选择模型后可直接对话、生成和重写，无逐次外发确认弹窗。
- 先将版本发布为正式稿，再导出。之后修改工作草稿不会改变已发布内容。
- 未保存的文字保留在当前浏览器会话中，离开时有保存提示。协作冲突提供两份草稿的比较与选择。
- 手机可切换目录、正文和对话，支持浅色与深色主题。

真实账号的对话与生成使用配置的 OpenAI 兼容或 Anthropic HTTP 接口。示例模式使用本地模拟回复。

管理员可在“账户设置 → 平台管理”创建账号、启停账号与重置密码；直接维护模型名称、接口类型、base_url、API key 与参数；编辑/发布模板；查看审计记录并创建或恢复加密备份。密钥不回显。作品所有者可调整成员角色、转移所有权和修改作品参数。

## 模型接入

1. 在“模型配置”添加显示名称、模型标识、base_url 和 API key，默认使用 OpenAI 兼容协议，也可选 Anthropic 原生。无需先建供应商；同一地址可添加多个独立模型。
2. OpenAI 默认地址为 `https://api.openai.com/v1`，Anthropic 为 `https://api.anthropic.com/v1`，也支持兼容服务地址或完整调用地址。可选参数为 JSON，例如 `{"temperature": 0.7, "max_tokens": 8192}`，参数不能覆盖模型、认证或实际消息。
3. 每个模型独立启停。修改配置创建不可变新版本，编辑密钥留空保留原凭证。正在执行的调用继续使用原版本；主动停用会阻止新调用并结束该模型尚未完成的调用。
4. 在作品对话区切换模型，选择与“包含整部正文”选项按用户和作品记住。切换只影响下次发送；历史保留实际模型名称和版本。所选模型不可用时需手动重新选择。

发送后立即显示消息与回复占位，回复逐步输出。停止或断线保留已保存的部分回复与状态；失败保留输入，只有手动重试才再次外发。模型调用记录只保留必要元数据，原文留在作品对话和固定输入快照中。

发送区可展开上下文：当前消息、最近最多 12 轮完整成功对话、设定和大纲，以及明确选中的集、场景或片段；未选目标不附带正文。“包含整部正文”会明确扩大范围。默认预算为 60000 个字符（`APP_CREATIVE_CONTEXT_MAX_CHARACTERS` 可调整），超限拒绝调用并提示缩小范围，不自动删减已选正文或设定。普通对话期间仍可编辑正文，回复依据发送时快照；同作品其他 AI 调用会被拒绝并保留输入。正文生成排队和执行期间锁定作品输入。
生成按集、场景或片段替换目标，校验结构后才保存整部作品版本。任务面板支持状态查看、取消和失败重试。单机 MVP 使用一个应用进程、一个后台线程串行执行生成任务；请保持 Uvicorn 单 worker，不启动多个实例共享任务表。任务状态持久化，重启会恢复排队任务，将中断任务标记为失败。可重试的连接/限流错误最多尝试 3 次；默认超时 120 秒。取消会关闭上游连接并阻止结果写入；已经发送的内容不能撤回。

模型 HTTP 契约通过本地替身测试；没有使用真实付费供应商账号进行调用。凭证与备份使用 AES-GCM 加密，部署前设置稳定随机的 `APP_EXTERNAL_ENCRYPTION_KEY`。备份不包含模型密钥，跨环境恢复后模型默认停用，需重新配置凭证并启用；原地数据库升级保留密文和历史引用。升级通过 `alembic upgrade head` 完成，不重建数据库。旧 format-1 备份仍可恢复其原有作品和对话，但旧格式从未记录的任务、调用归属和输入快照无法补造；新 format-2 备份包含这些记录，恢复会将未完成调用收口，不恢复运行态。

生产环境可先执行 `npm --prefix frontend run build`，再启动后端。服务会在 `/` 托管 `frontend/dist`，API 保持 `/api/v1`，通过同源 Cookie 会话与 CSRF 校验访问。`APP_FRONTEND_DIST` 可覆盖静态文件目录；作品链接使用 `#work/<id>`，可直接打开和刷新。

## 浏览器验收

```bash
npm --prefix frontend exec -- playwright install chromium
npm --prefix frontend run test:e2e
```

验收自动启动端口 5185 的前端和端口 8015 的临时后端，使用独立 SQLite 数据库，及端口 8016 的模型 HTTP 替身，结束后清理，不读取或修改实际作品，不调用付费服务。覆盖示例写作、真实登录与首次改密、设定确认、生成、版本恢复、发布导出、邀请与角色权限、冲突、自动保存、管理员配置、场景生成、手机和深色布局。已有 Chromium 可通过 `PLAYWRIGHT_CHROMIUM_PATH` 指定。构建后在 `frontend` 目录运行 `PLAYWRIGHT_PRODUCTION=1 npm run test:e2e`，可通过 FastAPI 同源静态服务验收真实流式输出、Cookie 与 CSRF。

## Compose

复制 `.env.example` 为 `.env`，替换管理员密码、初始化密钥与加密密钥后启动。可用 `python -c 'import secrets; print(secrets.token_urlsafe(48))'` 生成加密密钥，长期保留并单独备份。

```bash
docker compose up --build
curl http://localhost:8000/healthz
# Web 界面：http://localhost:8000/
```

Docker 构建会先生成 Web 静态资源，再将它们打包进 Python 镜像。需要使用包镜像时，可指定构建参数 `PIP_INDEX_URL`，默认使用 PyPI。

GitHub Actions 自动执行后端测试、独立 PostgreSQL 迁移/并发取消验收、前端构建、浏览器流程和 Docker 启动检查。PostgreSQL 测试需使用独立测试库，通过 `APP_TEST_POSTGRES_URL` 显式指定。

The application runs `alembic upgrade head` before Uvicorn starts. The bootstrap password and setup secret should be supplied through environment configuration before exposing the service. For local HTTP-only development, set `APP_COOKIE_SECURE=false` explicitly.

## 文档

- [产品规划](docs/product-plan.md)
- [技术评审](docs/technical-review.md)
- [开发设计](docs/development-design.md)
- [前端页面设计](docs/frontend-page-design.md)
- [前端开发方案](docs/frontend-development-plan.md)
- [前端任务规格](.scratch/frontend-experience/spec.md)
- [模型配置与创作对话设计](docs/model-chat-design.md)
- [模型配置与流式创作对话规格](.scratch/model-chat-simplification/spec.md)
- [AutoWonder 开发任务包](docs/development-tasks.md)
- [业务术语表](GLOSSARY.md)
