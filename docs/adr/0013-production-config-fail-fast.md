# 生产配置 Fail-Fast 校验

状态：已确认，2026-10-13。对生产环境的密钥与引导凭据施加启动期强校验，缺失或占位即拒绝启动。

## 背景与决策

加密密钥一旦使用即绑定历史密文：若生产部署误用默认或占位密钥，之后更换密钥会让已保存的供应商凭证与备份无法解密，属于不可逆损失。引导管理员密码与初始化密钥若停留在示例值，等于把可登录的弱口令暴露在生产。与其在运行期报错或静默降级，不如在进程启动、加载配置的那一刻失败得越早越好。

配置在 `environment == "production"` 时执行下列校验，任一不满足即抛出错误阻止启动：

- `APP_EXTERNAL_ENCRYPTION_KEY`：至少 32 字符，且不等于默认 `development-only-key`、不以 `change-this` 开头。
- `APP_BOOTSTRAP_ADMIN_PASSWORD`（若设置）：至少 12 字符，非 `change-this` 占位、不在常见弱口令清单中。
- `APP_BOOTSTRAP_SECRET`（若设置）：至少 16 字符、不含 `change-this`。

校验使用 `model_validator(mode="after")`，配置对象设 `hide_input_in_errors=True`：错误信息只说明哪个字段不合规及其规则，绝不回显字段值，避免把明文密钥写进启动日志或异常堆栈。

## Consequences

- 仅生产受影响：development / test 保持宽松默认值，本地与 CI 冒烟测试无需注入真实密钥即可启动。
- `docker-compose.yml` 用 `${VAR:?说明}` 强制注入加密密钥、数据库密码、引导密钥与管理员密码，去掉弱默认值；`set -u` 或 `docker compose config` 会在缺失时报错。
- `.env.example` 用 `REPLACE_WITH_*` 占位并注明各字段最小长度与一次性生成方式（`python -c 'import secrets; print(secrets.token_urlsafe(48))'`），示例值本身即会触发生产校验失败，杜绝「照抄示例直接上线」。
- 弱口令清单是保守集合，只用于拦截最常见误用，不替代真实强度策略；部署方仍应使用高熵随机值。
