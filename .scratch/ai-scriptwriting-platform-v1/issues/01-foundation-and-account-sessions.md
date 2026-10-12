# 01: 基础工程与账号会话

**What to build:** 让管理员可以启动一个可健康检查的 Docker Compose 应用，创建和管理内部账号；让用户可以安全登录、首次修改密码、退出，并让停用或重置密码立即撤销会话。

**Type:** task

**Blocked by:** None (can start immediately)

**Status:** resolved

- [x] Docker Compose 启动应用、数据库和必要基础服务，并提供健康检查与可重复数据库迁移。
- [x] 部署时可以建立初始管理员；管理员可以创建普通用户和管理员账号。
- [x] 用户可以使用账号密码登录和退出；会话使用安全 Cookie 和服务端会话记录。
- [x] 新账号首次登录只能进入修改初始密码流程，改密成功后才能访问普通功能。
- [x] 管理员可以停用、重新启用账号和重置密码；停用或重置密码立即撤销相关会话。
- [x] 密码使用不可逆哈希保存，原始密码和会话令牌不出现在响应、日志或审计记录中。
- [x] 账号生命周期操作产生不含正文、提示词、模型原文或密钥的审计记录。
- [x] API 层测试覆盖启用、停用、首次改密、会话撤销和未授权访问；健康检查与迁移在干净环境可复现。

## Comments

2026-10-12: Implemented the account/session slice in `app/`, Alembic migration `0001_account_sessions`, Docker Compose deployment, and API black-box tests. Initial passwords are forced through the change-password flow; password reset and account disable revoke all server-side sessions.
