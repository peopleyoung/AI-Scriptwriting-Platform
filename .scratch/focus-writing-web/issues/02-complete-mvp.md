# 02: 完整创作 MVP 与 GitHub 交付

Type: task
Status: resolved

## 范围

用户明确要求按审核的 MVP 完整实现并提交 GitHub。在已实现的专注写作界面上补齐真实模型接入、管理员账号与供应商配置、自动保存、生成任务状态及取消、场景生成、成员角色与所有权操作。保留已确认设定、版本、外发确认和作品权限边界；补齐可重复的 CI 和 Docker 验收后推送并建立可审核 PR。

## 验收

- [x] OpenAI 兼容与 Anthropic 调用真实 HTTP；凭证加密且不会返回或写入日志。
- [x] 生成只替换指定范围，校验结构；任务异步执行、取消/重试/冲突不产生孤立版本。
- [x] 对话按确认的数据范围构建输入，保存后使旧输入授权失效。
- [x] 账号、供应商/模型、模板和运行记录有可操作管理入口。
- [x] 自动保存只更新草稿；场景生成、成员角色、所有权和作品参数可操作。
- [x] 后端、模型契约、浏览器、CI 与构建通过，GitHub 代码已推送并有审核入口。

## Answer

已完成专注写作 MVP：React 中文写作工作台、自动保存、三阶段确认/提案、版本与正式稿、MD/DOCX、成员角色和所有权、任务状态/取消/重试、管理员账号/供应商/模型/模板/审计/备份管理。OpenAI 兼容和 Anthropic 接入真实 HTTP；发送按外发确认类别过滤，凭证及备份使用 AES-GCM，生成校验结构后只替换指定范围。备份可恢复到新数据库，恢复递增修订号，旧客户端不能覆盖恢复内容。单进程后台线程处理持久化队列。

验证：46 项后端测试、1 项独立 PostgreSQL 集成测试通过；6 组 Playwright 流程通过，场景选择改进后相关 2 组复测通过。Docker 镜像构建、PostgreSQL 迁移及并发任务/取消、Compose 配置、生产容器健康与静态站点通过。修复旧迁移编号超过 PostgreSQL Alembic 默认长度的问题。构建受本地 PyPI 网络影响，实际 Docker 验证使用可配置 PIP_INDEX_URL 指向清华 PyPI 镜像；代码默认仍使用 PyPI。

GitHub Actions 已加入同等验收。没有使用真实外部供应商凭证进行付费调用；HTTP 合约由本地替身验证。生产部署说明及单进程限制见 [README](../../../README.md)。GitHub 交付分支为 feat/focus-writing-mvp，提交并推送后建立审核 PR。

## GitHub 交付

实现提交 da401cd 已推送到 feat/focus-writing-mvp，审核入口：[PR #1](https://github.com/peopleyoung/AI-Scriptwriting-Platform/pull/1)。本地 Docker/PostgreSQL 预览使用独立测试数据，未改动真实作品。
