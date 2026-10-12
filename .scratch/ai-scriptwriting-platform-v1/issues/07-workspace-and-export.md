# 07: 协作工作区与 Markdown/DOCX 导出

**What to build:** 让协作者在一个中文三栏工作区中查看和操作自己有权限的创作资料；让作品所有者从不可变正式稿发布记录导出 Markdown 或 DOCX，并保证下载内容与当前未发布草稿隔离。

**Type:** task

**Blocked by:** 02: 作品创建与统一访问控制；04: 工作草稿、生成版本与正式稿发布；06: 异步生成任务与片段重写

**Status:** resolved

- [x] 工作区展示创作对话、设定/变更提案和正文/版本三个区域，并使用 API 权限结果控制可见操作。
- [x] 查看者可以读取获授权的全部创作资料但不能修改或创建生成/导出任务。
- [x] 编辑者可以编辑草稿、提交提案和查看生成进度，但不能确认设定、审批提案、恢复版本、发布或导出。
- [x] 作品所有者可以查看成员、设定、版本、生成任务和正式稿，并执行所有者操作。
- [x] 作品所有者可以选择任一正式稿发布记录创建 Markdown 或 DOCX 导出任务。
- [x] 导出任务有可查询状态；失败可重试，不会修改作品版本或发布记录。
- [x] Markdown 和 DOCX 从同一结构化剧本正文渲染，包含标题、集和场景层级及剧本块内容。
- [x] 导出不包含创作对话、待审批提案、模型调用记录、工作草稿或模型凭证。
- [x] 下载地址短时有效，并在生成、查询和下载时再次执行作品访问控制。
- [x] 工作区和导出 API/UI 测试覆盖角色矩阵、正式稿绑定、双格式内容一致性、失败重试和短下载链接。

## Comments

2026-10-13: Implemented workspace aggregation, role-aware API permissions, immutable release-bound Markdown/DOCX exports, short-lived downloads, and release selection.
