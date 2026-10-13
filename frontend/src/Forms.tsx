import { useState, type FormEvent } from "react";
import {
  Plus,
  LockKeyhole,
  UserRound,
  Trash2,
  Smartphone,
  Monitor,
} from "lucide-react";
import {
  definitionFields,
  stageNames,
  type Character,
  type Definition,
  type NewWork,
  type Rule,
  type Stage,
  type Template,
  type Work,
} from "./types";
import { Button, ErrorMessage, Field, Modal } from "./ui";

export const labels: Record<string, string> = {
  logline: "一句话故事",
  theme: "核心主题",
  tone: "叙事风格",
  target_audience: "目标观众",
  world_setting: "故事世界",
  name: "人物姓名",
  role: "身份与角色",
  traits: "人物特质",
  goal: "人物目标",
  relationship: "人物关系",
  premise: "故事前提",
  beginning: "起 · 故事开始",
  middle: "承转 · 真相浮现",
  ending: "合 · 最后的选择",
  episode_summaries: "分集梗概",
  episode_count: "计划集数",
  episode_duration_minutes: "单集时长 / 分钟",
  episode_word_target: "单集字数目标",
  genre_tags: "题材标签",
  output_format: "首选导出格式",
};
const defaultValue = (rule: Rule): unknown =>
  rule.type === "enum"
    ? rule.values?.[0] || ""
    : rule.type === "array"
      ? []
      : rule.type === "object"
        ? {}
        : rule.type === "integer" || rule.type === "number"
          ? rule.min || 1
          : "";
export function RuleInput({
  name,
  rule,
  value,
  onChange,
  disabled,
}: {
  name: string;
  rule: Rule;
  value: unknown;
  onChange: (value: unknown) => void;
  disabled?: boolean;
}) {
  const [raw, setRaw] = useState(() =>
    rule.type === "array" && (!rule.items?.type || rule.items.type === "string")
      ? Array.isArray(value)
        ? value.join("\n")
        : ""
      : JSON.stringify(value || (rule.type === "array" ? [] : {}), null, 2),
  );
  if (rule.type === "enum")
    return (
      <select
        name={name}
        value={String(value || rule.values?.[0] || "")}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
      >
        {rule.values?.map((item) => (
          <option key={item} value={item}>
            {item === "markdown"
              ? "Markdown"
              : item === "docx"
                ? "Word 文档（DOCX）"
                : item}
          </option>
        ))}
      </select>
    );
  if (rule.type === "integer" || rule.type === "number")
    return (
      <input
        type="number"
        name={name}
        min={rule.min}
        max={rule.max}
        step={rule.type === "integer" ? 1 : "any"}
        value={Number(value || rule.min || 1)}
        onChange={(event) =>
          onChange(
            event.target.value === "" ? undefined : Number(event.target.value),
          )
        }
        disabled={disabled}
        required={rule.required}
      />
    );
  if (
    rule.type === "array" &&
    (!rule.items?.type || rule.items.type === "string")
  )
    return (
      <textarea
        name={name}
        rows={4}
        value={raw}
        placeholder="每行填写一项"
        onChange={(event) => {
          setRaw(event.target.value);
          onChange(
            event.target.value
              .split("\n")
              .map((item) => item.trim())
              .filter(Boolean),
          );
        }}
        disabled={disabled}
        required={rule.required}
      />
    );
  if (rule.type === "object" || rule.type === "array")
    return (
      <textarea
        name={name}
        rows={4}
        value={raw}
        onChange={(event) => {
          setRaw(event.target.value);
          try {
            const parsed = JSON.parse(event.target.value);
            if (
              rule.type === "array"
                ? !Array.isArray(parsed)
                : !parsed || typeof parsed !== "object" || Array.isArray(parsed)
            )
              throw new Error();
            onChange(parsed);
            event.target.setCustomValidity("");
          } catch {
            event.target.setCustomValidity("请填写有效的结构化内容。");
          }
        }}
        disabled={disabled}
        required={rule.required}
      />
    );
  if (name === "name" || name === "role")
    return (
      <input
        name={name}
        value={String(value || "")}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
        maxLength={rule.max_length}
        required={rule.required}
      />
    );
  return (
    <textarea
      name={name}
      value={String(value || "")}
      rows={name === "logline" ? 4 : 3}
      onChange={(event) => onChange(event.target.value)}
      disabled={disabled}
      maxLength={rule.max_length}
      required={rule.required}
    />
  );
}
export function CreateWork({
  templates,
  initialTemplate,
  onCreate,
  onClose,
}: {
  templates: Template[];
  initialTemplate?: string;
  onCreate: (input: NewWork) => Promise<void>;
  onClose: () => void;
}) {
  const [templateId, setTemplateId] = useState(
    initialTemplate || templates[0]?.id || "",
  );
  const [values, setValues] = useState<Record<string, unknown>>({
    episode_count: 12,
    episode_duration_minutes: 3,
    episode_word_target: 600,
    genre_tags: [],
    output_format: "markdown",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const template = templates.find((item) => item.id === templateId);
  const fields = definitionFields(
    template?.published_version?.definition || {},
    "settings",
  );
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const title = String(new FormData(event.currentTarget).get("title")).trim();
    if (!title) {
      setError("请为作品填写名称。");
      return;
    }
    const settings = Object.fromEntries(
      Object.entries(fields).map(([key, rule]) => {
        const value = values[key] ?? defaultValue(rule);
        return [
          key,
          rule.type === "integer" && typeof value === "number"
            ? Math.max(rule.min ?? value, Math.min(rule.max ?? value, value))
            : value,
        ];
      }),
    );
    setBusy(true);
    setError("");
    try {
      await onCreate({
        title,
        template_id: templateId,
        settings: settings as Work["settings"],
      });
    } catch (error) {
      setError(error instanceof Error ? error.message : "创建失败。");
      setBusy(false);
    }
  }
  return (
    <Modal
      title="一个新故事，就从这里开始"
      description="选择剧本模板，让灵感有一个清晰的起点。"
      onClose={onClose}
      busy={busy}
      footer={
        <>
          <Button onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button
            type="submit"
            form="create-work"
            primary
            disabled={busy || !template}
          >
            <Plus />
            {busy ? "正在创建…" : "创建作品"}
          </Button>
        </>
      }
    >
      <form id="create-work" onSubmit={submit}>
        <Field label="作品名称">
          <input
            name="title"
            placeholder="给你的故事起个名字"
            maxLength={256}
            required
            autoFocus
            disabled={busy}
          />
        </Field>
        <div className="field">
          <span>剧本模板</span>
          <div className="template-choices">
            {templates.map((item) => (
              <label
                className={`template-choice ${templateId === item.id ? "selected" : ""}`}
                key={item.id}
              >
                <input
                  type="radio"
                  name="template"
                  checked={templateId === item.id}
                  onChange={() => setTemplateId(item.id)}
                  disabled={busy}
                />
                {item.slug.includes("vertical") ? <Smartphone /> : <Monitor />}
                <span>
                  <b>{item.name}</b>
                  <small>
                    {item.slug.includes("vertical")
                      ? "强冲突 · 快节奏"
                      : "完整叙事 · 人物弧光"}
                  </small>
                </span>
              </label>
            ))}
          </div>
        </div>
        <div className="settings-form">
          {Object.entries(fields).map(([key, rule]) => (
            <Field key={key} label={labels[key] || key}>
              <RuleInput
                name={key}
                rule={rule}
                value={values[key] ?? defaultValue(rule)}
                disabled={busy}
                onChange={(value) =>
                  setValues((previous) => ({ ...previous, [key]: value }))
                }
              />
            </Field>
          ))}
        </div>
        <p className="hint">
          <LockKeyhole size={14} />
          作品默认私有，创建后可邀请编辑者或查看者。
        </p>
        <ErrorMessage message={error} />
      </form>
    </Modal>
  );
}
export function CreativeEditor({
  work,
  stage,
  onSave,
  onClose,
}: {
  work: Work;
  stage: Stage;
  onSave: (content: unknown) => Promise<void>;
  onClose: () => void;
}) {
  const fields = definitionFields(work.template_definition || {}, stage);
  const empty = () =>
    Object.fromEntries(
      Object.entries(fields).map(([key, rule]) => [key, defaultValue(rule)]),
    );
  const [value, setValue] = useState<unknown>(
    structuredClone(
      work.creative[stage] || (stage === "characters" ? [empty()] : empty()),
    ),
  );
  const [personIndex, setPersonIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const locked = Boolean(work.creative.confirmed[stage]);
  const people = stage === "characters" ? (value as Character[]) : [];
  const current =
    stage === "characters"
      ? people[personIndex] || {}
      : (value as Record<string, unknown>);
  function update(key: string, item: unknown) {
    setValue((previous: unknown) =>
      stage === "characters"
        ? (previous as Character[]).map((person, index) =>
            index === personIndex ? { ...person, [key]: item } : person,
          )
        : { ...(previous as Record<string, unknown>), [key]: item },
    );
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    if (
      stage === "characters" &&
      people.some((person) => !person.name?.trim())
    ) {
      setError("请为每个人物填写姓名。");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await onSave(value);
    } catch (error) {
      setError(error instanceof Error ? error.message : "保存失败。");
      setBusy(false);
    }
  }
  return (
    <Modal
      title={`${locked ? "提出" : "编辑"}${stageNames[stage]}${locked ? "变更" : ""}`}
      description={
        locked
          ? "提案经作品所有者接受后，才会更新已确认设定。"
          : "保存草稿后，由所有者按阶段确认。"
      }
      onClose={onClose}
      busy={busy}
      footer={
        <>
          <Button onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button
            type="submit"
            form="creative-editor"
            primary
            disabled={busy || !Object.keys(fields).length}
          >
            {busy ? "正在保存…" : locked ? "提交变更提案" : "保存设定草稿"}
          </Button>
        </>
      }
    >
      <form id="creative-editor" onSubmit={submit}>
        {stage === "characters" && (
          <div className="person-picker">
            <Field label="当前编辑的人物">
              <select
                value={personIndex}
                onChange={(event) => setPersonIndex(Number(event.target.value))}
                disabled={busy}
              >
                {people.map((person, index) => (
                  <option value={index} key={index}>
                    {person.name || `新人物 ${index + 1}`}
                  </option>
                ))}
              </select>
            </Field>
            <Button
              small
              onClick={() => {
                setValue([...people, empty()]);
                setPersonIndex(people.length);
              }}
              disabled={busy}
            >
              <Plus />
              添加人物
            </Button>
            <Button
              small
              onClick={() => {
                setValue(people.filter((_, index) => index !== personIndex));
                setPersonIndex(Math.max(0, personIndex - 1));
              }}
              disabled={busy || people.length < 2}
            >
              <Trash2 />
              移除
            </Button>
          </div>
        )}
        {Object.entries(fields).map(([key, rule]) => (
          <Field
            label={labels[key] || key}
            key={`${stage}-${personIndex}-${key}`}
          >
            <RuleInput
              name={key}
              rule={rule}
              value={current[key]}
              disabled={busy}
              onChange={(item) => update(key, item)}
            />
          </Field>
        ))}
        {!Object.keys(fields).length && (
          <p className="hint">
            暂时无法读取这部作品的模板字段，请重新打开作品。
          </p>
        )}
        <ErrorMessage message={error} />
      </form>
    </Modal>
  );
}
export function CreativeContent({ work, stage }: { work: Work; stage: Stage }) {
  const value = work.creative[stage];
  if (!value || (Array.isArray(value) && !value.length))
    return <p className="hint">这项设定还没有填写，先从一个清晰的想法开始。</p>;
  if (stage === "characters")
    return (
      <div className="characters">
        {(value as Character[]).map((person, index) => (
          <article className="person" key={index}>
            <div className="row">
              <span className="avatar">
                {person.name?.slice(0, 1) || <UserRound />}
              </span>
              <div>
                <h3>{person.name}</h3>
                <small>{person.role as string}</small>
              </div>
            </div>
            {Object.entries(person)
              .filter(([key]) => key !== "name" && key !== "role")
              .map(([key, item]) => (
                <p key={key}>
                  <span className="muted">{labels[key] || key}：</span>
                  {formatValue(item)}
                </p>
              ))}
          </article>
        ))}
      </div>
    );
  return (
    <div>
      {Object.entries(value).map(([key, item]) => (
        <div className="creative-field" key={key}>
          <span>{labels[key] || key}</span>
          <p>{formatValue(item)}</p>
        </div>
      ))}
    </div>
  );
}
function formatValue(value: unknown): string {
  return Array.isArray(value)
    ? value.map((item) => String(item)).join("\n")
    : typeof value === "object"
      ? JSON.stringify(value, null, 2)
      : String(value || "尚未填写");
}
