# 02: 作品创建与统一访问控制

**What to build:** 让已登录用户创建默认私有的作品并成为作品所有者；让所有者通过邀请管理查看者和编辑者；让所有作品资源统一遵守成员授权，且回收站作品不再对普通业务查询可见。

**Type:** task

**Blocked by:** 01: 基础工程与账号会话

**Status:** resolved

- [x] 用户可以创建作品并自动成为唯一作品所有者。
- [x] 作品默认只有所有者可访问；平台管理员没有绕过作品成员授权的读取权限。
- [x] 所有者可以邀请查看者或编辑者；邀请未接受前不授予作品读取权。
- [x] 受邀用户接受后获得对应成员权限；所有者可以撤销成员并立即使访问失效。
- [x] 所有者可以在查看者和编辑者之间调整角色；所有成员操作产生审计记录。
- [x] 所有权只能转移给已启用且已经是成员的账号，原所有者转为编辑者。
- [x] 编辑者不能管理成员、转移所有权或执行仅所有者操作；查看者不能修改作品资料。
- [x] 作品详情、对话、设定、大纲、正文、版本和发布记录全部经过统一作品访问检查。
- [x] 作品进入回收站后不出现在普通列表和资源接口；未授权访问返回稳定错误。
- [x] API 测试覆盖管理员无授权访问、邀请未接受、撤销后访问、角色矩阵、所有权转移和回收站隔离。

## Comments

2026-10-12: Implemented works, explicit memberships, invitation acceptance, role changes, ownership transfer, trash/restore isolation, centralized member authorization, audit events, Alembic migration `0002_works_access`, and API black-box tests. Account owners cannot be disabled until ownership is transferred. Full suite: 14 passed; fresh SQLite migration upgrade verified.
