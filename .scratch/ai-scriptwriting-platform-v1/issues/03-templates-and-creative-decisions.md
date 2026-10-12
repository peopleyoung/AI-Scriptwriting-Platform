# 03: 剧本模板与阶段化创作资料

**What to build:** 让管理员发布版本化的竖屏微短剧和网剧短剧模板，让用户创建作品时获得模板和作品参数快照，并让协作者编辑创作资料、由作品所有者按阶段确认设定和审批变更提案。

**Type:** task

**Blocked by:** 01: 基础工程与账号会话；02: 作品创建与统一访问控制

**Status:** resolved

- [x] 管理员可以创建、编辑和发布版本化剧本模板；首版提供竖屏微短剧和网剧短剧模板。
- [x] 模板定义创作简报、人物、大纲字段、正文结构、输出格式和校验规则；用户不能增加模板之外的字段。
- [x] 创建作品时复制模板版本和作品参数快照；管理员发布新模板不改变既有作品。
- [x] 作品所有者可以填写并修改模板允许范围内的作品参数；无效参数被拒绝。
- [x] 编辑者可以编辑创作简报、人物和大纲草稿；查看者只能读取。
- [x] 作品所有者必须按创作简报、人物、故事大纲顺序确认；前一阶段未确认时，后一阶段只能保存草稿。
- [x] 确认后的内容形成已确认设定；临时创作指令不能直接覆盖已确认设定。
- [x] 编辑者和所有者可以提交变更提案；同一设定只能存在一个待审批提案。
- [x] 只有作品所有者可以批准或拒绝提案；提案基础发生变化时审批报告稳定冲突并要求重新提交。
- [x] 确认、提案和模板发布操作写入不含正文敏感内容的审计记录。
- [x] API/UI 测试覆盖模板快照、三阶段顺序、角色权限、重复提案和过期提案冲突。

## Answer

已完成模板版本、模板快照、模板参数校验、三阶段创作资料、确认顺序、角色权限、变更提案审批和稳定冲突处理。新增 Alembic 迁移 `0003_templates_creative_decisions` 与黑盒 API 测试 `tests/test_templates_creative.py`。

## Comments

2026-10-12: Implemented issue 03. Full test suite: 18 passed; clean SQLite migration through issue 03 verified. Later-task files in the shared workspace remain uncommitted and are excluded from this issue.
