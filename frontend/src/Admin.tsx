import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, type Studio } from "./studio";
import { Button, ErrorMessage, Field, Modal, formatDate } from "./ui";

type Row = Record<string, unknown> & {
  id: string;
  name?: string;
  username?: string;
  versions?: {
    definition: Record<string, unknown>;
    status: string;
    version: number;
  }[];
};
type Tab = "users" | "models" | "templates" | "operations";
const names: Record<Tab, string> = {
  users: "账号管理",
  models: "模型配置",
  templates: "剧本模板",
  operations: "审计与备份",
};
const paths = {
  users: "/admin/users",
  models: "/admin/model-configs",
  templates: "/admin/templates",
};

export default function Admin({
  studio,
  onClose,
  onRefresh,
  onRestored,
}: {
  studio: Studio;
  onClose: () => void;
  onRefresh: () => void;
  onRestored: () => void;
}) {
  const [tab, setTab] = useState<Tab>("users");
  const [rows, setRows] = useState<Row[]>([]);
  const [backups, setBackups] = useState<Row[]>([]);
  const [editor, setEditor] = useState<Row | "new" | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [restore, setRestore] = useState<string | null>(null);
  const [removeModel, setRemoveModel] = useState<Row | null>(null);
  const lock = useRef(false);
  async function load() {
    if (tab === "operations") {
      const [audit, backup] = await Promise.all([
        api<Row[]>("/admin/audit"),
        api<Row[]>("/admin/backups"),
      ]);
      setRows(audit);
      setBackups(backup);
    } else {
      const next = await api<Row[]>(paths[tab]);
      setRows(tab === "models" ? next.filter((row) => row.status !== "DELETED") : next);
    }
  }
  useEffect(() => {
    let active = true;
    setBusy(true);
    setError("");
    void load()
      .catch((err) => {
        if (active) setError(String(err.message));
      })
      .finally(() => {
        if (active) setBusy(false);
      });
    return () => {
      active = false;
    };
  }, [tab]);
  async function run(task: () => Promise<void>, refresh = true) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await task();
      await load();
      if (refresh) {
        await studio.init();
        onRefresh();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作未完成。");
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  function edit(row: Row | "new") {
    setEditor(row);
    setError("");
    setNotice("");
    if (row === "new")
      setValues({
        adapter: "openai",
        role: "WRITER",
        parameters: '{"temperature": 0.7}',
        definition: JSON.stringify(
          studio.templates[0]?.published_version?.definition || {},
          null,
          2,
        ),
      });
    else
      setValues(
        Object.fromEntries(
          Object.entries({
            ...row,
            api_key: "",
            password: "",
            parameters: JSON.stringify(row.parameters || {}, null, 2),
            definition: JSON.stringify(
              row.versions?.slice().sort((a, b) => b.version - a.version)[0]
                ?.definition || {},
              null,
              2,
            ),
          }).map(([key, value]) => [key, String(value ?? "")]),
        ),
      );
  }
  function input(
    key: string,
    label: string,
    options: { multiline?: boolean; secret?: boolean; optional?: boolean } = {},
  ) {
    return (
      <Field label={label} key={key}>
        {options.multiline ? (
          <textarea
            rows={key === "definition" ? 15 : 3}
            value={values[key] || ""}
            disabled={busy}
            required={!options.optional}
            onChange={(e) => setValues({ ...values, [key]: e.target.value })}
          />
        ) : (
          <input
            type={options.secret ? "password" : "text"}
            autoComplete={options.secret ? "new-password" : "off"}
            value={values[key] || ""}
            disabled={busy}
            required={!options.optional}
            onChange={(e) => setValues({ ...values, [key]: e.target.value })}
          />
        )}
      </Field>
    );
  }
  async function submit(e: FormEvent) {
    e.preventDefault();
    await run(async () => {
      if (tab === "users") {
        if (editor === "new")
          await api(paths.users, "POST", {
            username: values.username,
            password: values.password,
            role: values.role,
          });
        else
          await api(
            `${paths.users}/${(editor as Row).id}/reset-password`,
            "POST",
            { password: values.password },
          );
      } else if (tab === "models") {
        const body = {
          display_name: values.display_name,
          adapter: values.adapter,
          model_name: values.model_name,
          base_url: values.base_url || null,
          parameters: JSON.parse(values.parameters || "{}"),
          ...(values.credential ? { api_key: values.credential } : {}),
        };
        await api(
          editor === "new"
            ? paths.models
            : `${paths.models}/${(editor as Row).id}`,
          editor === "new" ? "POST" : "PATCH",
          body,
        );
      } else if (tab === "templates") {
        const body = {
          name: values.name,
          definition: JSON.parse(values.definition),
        };
        await api(
          editor === "new"
            ? paths.templates
            : `${paths.templates}/${(editor as Row).id}`,
          editor === "new" ? "POST" : "PATCH",
          editor === "new" ? { ...body, slug: values.slug } : body,
        );
      }
      setValues({});
      setEditor(null);
      setNotice("已保存。");
    });
  }
  return (
    <Modal
      title="平台管理"
      description="管理账号、模型与模板。作品访问仍受成员权限约束。"
      wide
      busy={busy}
      onClose={onClose}
      footer={
        <Button disabled={busy} onClick={onClose}>
          返回创作
        </Button>
      }
    >
      <div className="modal-tabs admin-tabs">
        {Object.entries(names).map(([key, name]) => (
          <button
            key={key}
            disabled={busy}
            className={tab === key ? "active" : ""}
            onClick={() => {
              setEditor(null);
              setValues({});
              setTab(key as Tab);
            }}
          >
            {name}
          </button>
        ))}
      </div>
      <ErrorMessage message={error} />
      {notice && (
        <p role="status" className="notice">
          {notice}
        </p>
      )}
      {editor ? (
        <form onSubmit={submit} className="admin-form">
          <h3>
            {editor === "new" ? "新建" : tab === "users" ? "重置密码" : "编辑"}
            {names[tab]}
          </h3>
          {tab === "users" && (
            <>
              {editor === "new" && (
                <>
                  {input("username", "登录名")}
                  <Field label="平台角色">
                    <select
                      disabled={busy}
                      value={values.role}
                      onChange={(e) =>
                        setValues({ ...values, role: e.target.value })
                      }
                    >
                      <option value="WRITER">普通用户</option>
                      <option value="ADMIN">管理员</option>
                    </select>
                  </Field>
                </>
              )}
              {input("password", "初始密码", { secret: true })}
              <p className="hint">用户下次登录必须修改初始密码。</p>
            </>
          )}
          {tab === "models" && (
            <>
              {input("display_name", "显示名称")}
              <Field label="接口类型">
                <select disabled={busy} value={values.adapter} onChange={(e) => setValues({ ...values, adapter: e.target.value })}>
                  <option value="openai">OpenAI 兼容</option>
                  <option value="anthropic">Anthropic 原生</option>
                </select>
              </Field>
              {input("model_name", "模型标识")}
              {input("base_url", "base_url（留空使用官方默认地址）", {
                optional: true,
              })}
              {input("parameters", "调用参数 JSON", { multiline: true })}
              {input("credential", "API key（留空保持现有密钥）", {
                secret: true,
                optional: true,
              })}
              <p className="hint">
                保存创建不可变版本，仅影响后续调用。留空密钥表示保留，填写新值表示轮换。
              </p>
            </>
          )}
          {tab === "templates" && (
            <>
              {input("name", "模板名称")}
              {editor === "new" && input("slug", "模板标识")}
              {input("definition", "模板定义 JSON", { multiline: true })}
              <p className="hint">
                保存为草稿，发布后供新作品使用。已有作品继续使用绑定版本。
              </p>
            </>
          )}
          <div className="row">
            <Button
              disabled={busy}
              onClick={() => {
                setEditor(null);
                setValues({});
              }}
            >
              取消编辑
            </Button>
            <Button primary type="submit" disabled={busy}>
              保存{tab === "templates" ? "模板草稿" : "配置"}
            </Button>
          </div>
        </form>
      ) : (
        <>
          {tab !== "operations" && (
            <Button small disabled={busy} onClick={() => edit("new")}>
              新建{names[tab]}
            </Button>
          )}
          <div className="admin-list">
            {rows.map((row) => (
              <article className="job-row" key={row.id}>
                <div>
                  <strong>
                    {String(
                      row.username ||
                        row.name ||
                        row.display_name ||
                        row.model_name ||
                        row.event_type ||
                        row.id,
                    )}
                  </strong>
                  <small>
                    {tab === "users"
                      ? `${row.role === "ADMIN" ? "管理员" : "普通用户"} · ${row.is_active ? "启用" : "停用"}`
                      : tab === "models"
                          ? `v${row.version} · ${row.status} · ${row.enabled ? "启用" : "停用"} · ${row.model_name}`
                          : tab === "templates"
                            ? `已发布 v${(row.published_version as { version?: number } | null)?.version || "—"}`
                            : formatDate(String(row.created_at))}
                  </small>
                </div>
                {tab !== "operations" && (
                  <Button
                    small
                    disabled={
                      busy || (tab === "models" && row.status !== "ACTIVE")
                    }
                    onClick={() => edit(row)}
                  >
                    {tab === "users" ? "重置密码" : "编辑"}
                  </Button>
                )}
                {tab === "users" && (
                  <Button
                    small
                    disabled={busy || row.id === studio.user.id}
                    onClick={() =>
                      run(async () => {
                        await api(`${paths.users}/${row.id}/status`, "PATCH", {
                          active: !row.is_active,
                        });
                      })
                    }
                  >
                    {row.is_active ? "停用" : "启用"}
                  </Button>
                )}
                {tab === "models" && row.status === "ACTIVE" && (
                  <Button
                    small
                    disabled={busy}
                    onClick={() =>
                      run(async () => {
                        await api(`${paths.models}/${row.id}`, "PATCH", {
                          enabled: !row.enabled,
                        });
                      })
                    }
                  >
                    {row.enabled ? "停用" : "启用"}
                  </Button>
                )}
                {tab === "models" && row.status === "ACTIVE" && (
                  <Button
                    small
                    disabled={busy}
                    onClick={() => setRemoveModel(row)}
                  >
                    删除
                  </Button>
                )}
                {tab === "templates" && (
                  <Button
                    small
                    disabled={
                      busy || !row.versions?.some((v) => v.status === "DRAFT")
                    }
                    onClick={() =>
                      run(async () => {
                        await api(
                          `${paths.templates}/${row.id}/publish`,
                          "POST",
                        );
                        setNotice("模板已发布。");
                      })
                    }
                  >
                    发布模板
                  </Button>
                )}
              </article>
            ))}
          </div>
          {tab === "operations" && (
            <>
              <h3>加密备份</h3>
              <Button
                small
                disabled={busy}
                onClick={() =>
                  run(async () => {
                    await api("/admin/backups", "POST");
                    setNotice("加密备份已生成。");
                  }, false)
                }
              >
                创建备份
              </Button>
              {backups.map((backup) => (
                <div className="job-row" key={backup.id}>
                  <div>
                    <strong>{formatDate(String(backup.created_at))}</strong>
                    <small>
                      {String(backup.checksum).slice(0, 16)} ·{" "}
                      {backup.available ? "可恢复" : "文件不存在"}
                    </small>
                  </div>
                  <Button
                    small
                    disabled={busy || !backup.available}
                    onClick={() => setRestore(backup.id)}
                  >
                    恢复备份
                  </Button>
                </div>
              ))}
            </>
          )}
        </>
      )}
      {restore && (
        <div className="notice">
          <p>恢复备份将覆盖其中的作品内容与成员授权。确认恢复此备份？</p>
          <div className="row">
            <Button disabled={busy} onClick={() => setRestore(null)}>
              取消恢复
            </Button>
            <Button
              primary
              disabled={busy}
              onClick={() =>
                run(async () => {
                  await api(`/admin/backups/${restore}/restore`, "POST");
                  onRestored();
                  setRestore(null);
                  setNotice("备份已恢复，未完成调用已结束。");
                })
              }
            >
              确认恢复备份
            </Button>
          </div>
        </div>
      )}
      {removeModel && (
        <div className="notice">
          <p>
            确定删除模型“{String(removeModel.display_name || removeModel.model_name)}”吗？
            已有对话和历史记录会保留。
          </p>
          <div className="row">
            <Button disabled={busy} onClick={() => setRemoveModel(null)}>
              取消删除
            </Button>
            <Button
              primary
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  await api(`${paths.models}/${removeModel.id}`, "DELETE");
                  setRemoveModel(null);
                  setNotice("模型配置已删除。");
                })
              }
            >
              确认删除模型
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

export function PasswordForm({ onDone }: { onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <form
      className="password-form"
      onSubmit={async (e) => {
        e.preventDefault();
        if (busy) return;
        const data = new FormData(e.currentTarget);
        if (data.get("new") !== data.get("repeat")) {
          setError("两次输入的新密码不一致。");
          return;
        }
        setBusy(true);
        setError("");
        try {
          await api("/auth/change-password", "POST", {
            current_password: data.get("current"),
            new_password: data.get("new"),
          });
          onDone();
        } catch (err) {
          setError(err instanceof Error ? err.message : "密码修改失败。");
        } finally {
          setBusy(false);
        }
      }}
    >
      <h3>修改登录密码</h3>
      <Field label="当前密码">
        <input
          name="current"
          type="password"
          autoComplete="current-password"
          required
          disabled={busy}
        />
      </Field>
      <Field label="新密码">
        <input
          name="new"
          type="password"
          autoComplete="new-password"
          minLength={12}
          required
          disabled={busy}
        />
      </Field>
      <Field label="再次输入新密码">
        <input
          name="repeat"
          type="password"
          autoComplete="new-password"
          required
          disabled={busy}
        />
      </Field>
      <ErrorMessage message={error} />
      <Button type="submit" disabled={busy}>
        修改密码
      </Button>
    </form>
  );
}
