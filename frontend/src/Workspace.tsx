import { useEffect, useRef, useState, type ComponentProps } from "react";
import {
  ArrowLeft,
  Check,
  ChevronRight,
  Download,
  FileText,
  History,
  LockKeyhole,
  MessageSquare,
  PanelRightClose,
  Plus,
  Save,
  Send,
  Settings2,
  Sparkles,
  Trash2,
  Users,
  X,
} from "lucide-react";
import { Header } from "./Home";
import { CreativeContent, CreativeEditor, RuleInput, labels } from "./Forms";
import { ApiError, scriptText, type Studio } from "./studio";
import {
  definitionFields,
  type Job,
  roleNames,
  stageNames,
  stages,
  type Consent,
  type Episode,
  type Fragment,
  type Page,
  type Script,
  type Stage,
  type Work,
} from "./types";
import {
  Button,
  Empty,
  ErrorMessage,
  Field,
  IconButton,
  Modal,
  formatDate,
} from "./ui";

type Props = {
  studio: Studio;
  work: Work;
  onUpdate: (work: Work) => void;
  onBack: (page: Page) => void;
  headerProps: ComponentProps<typeof Header>;
  notify: (message: string) => void;
  onSessionExpired: () => void;
};
type Intent = {
  kind: "chat" | "EPISODE" | "SCENE" | "FRAGMENT";
  instruction: string;
  episode: number;
  scene: number;
  fragment: string;
};
const blockNames: Record<string, string> = {
  SCENE_HEADING: "场景说明",
  ACTION: "动作",
  CHARACTER: "人物",
  DIALOGUE: "对白",
};
const sourceNames: Record<string, string> = {
  MANUAL_SAVE: "手动保存",
  GENERATION: "生成",
  RESTORE: "恢复",
};
const freshFragment = (): Fragment => ({
  id: crypto.randomUUID(),
  blocks: [{ type: "ACTION", text: "" }],
});

export default function Workspace({
  studio,
  work,
  onUpdate,
  onBack,
  headerProps,
  notify,
  onSessionExpired,
}: Props) {
  const recoveryKey = `mujian-draft:${studio.mode}:${studio.user.id}:${work.id}`;
  const [recovery] = useState<{ draft: Script; revision: number } | null>(
    () => {
      try {
        const saved = JSON.parse(sessionStorage.getItem(recoveryKey) || "null");
        return saved?.draft && work.role !== "VIEWER" ? saved : null;
      } catch {
        return null;
      }
    },
  );
  const [draft, setDraft] = useState<Script>(
    () => recovery?.draft || structuredClone(work.draft),
  );
  const [conflict, setConflict] = useState<Work | null>(() =>
    recovery && recovery.revision !== work.revision ? work : null,
  );
  const [episodeNumber, setEpisodeNumber] = useState(
    () => work.draft.episodes?.at(-1)?.number || 1,
  );
  const [selected, setSelected] = useState<{
    scene: number;
    fragment: string;
  } | null>(null);
  const [chatOpen, setChatOpen] = useState(true);
  const [mobilePane, setMobilePane] = useState("paper");
  const [modal, setModal] = useState<
    | "settings"
    | "versions"
    | "export"
    | "members"
    | "generate"
    | "jobs"
    | "parameters"
    | "consent"
    | null
  >(null);
  const [stage, setStage] = useState<Stage>("brief");
  const [editingStage, setEditingStage] = useState<Stage | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [message, setMessage] = useState("");
  const [instruction, setInstruction] = useState("");
  const [scope, setScope] = useState<"EPISODE" | "SCENE" | "FRAGMENT">(
    "EPISODE",
  );
  const [generationScene, setGenerationScene] = useState(1);
  const [modelId, setModelId] = useState(studio.models[0]?.id || "");
  const [pending, setPending] = useState<Intent | null>(null);
  const [checked, setChecked] = useState(false);
  const [versionId, setVersionId] = useState(work.versions.at(-1)?.id || "");
  const [compareId, setCompareId] = useState("");
  const [releaseId, setReleaseId] = useState(
    work.releases.find((r) => r.is_current)?.id || "",
  );
  const [username, setUsername] = useState("");
  const [inviteRole, setInviteRole] = useState<"EDITOR" | "VIEWER">("EDITOR");
  const [destination, setDestination] = useState<Page | null>(null);
  const [restoreId, setRestoreId] = useState<string | null>(null);
  const [sessionExpired, setSessionExpired] = useState(false);
  const [activeJob, setActiveJob] = useState<string | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [cancelBusy, setCancelBusy] = useState(false);
  const [autoSaving, setAutoSaving] = useState(false);
  const [parameterTitle, setParameterTitle] = useState(work.title);
  const [parameterValues, setParameterValues] = useState(work.settings);
  const [transferTarget, setTransferTarget] = useState<string | null>(null);
  const lock = useRef(false);
  const current = useRef(work);
  const local = useRef(draft);
  const editorRef = useRef<HTMLDivElement>(null);
  const chatEnd = useRef<HTMLDivElement>(null);
  current.current = work;
  local.current = draft;
  const dirty = JSON.stringify(draft) !== JSON.stringify(work.draft);
  const owner = work.role === "OWNER";
  const editable = work.role !== "VIEWER";
  const disabled =
    busy ||
    Boolean(activeJob) ||
    modal === "consent" ||
    sessionExpired ||
    Boolean(conflict);
  const episodes = draft.episodes || [];
  const episode = episodes.find((e) => e.number === episodeNumber);
  const confirmedCount = stages.filter(
    (s) => work.creative.confirmed[s],
  ).length;
  const count =
    episode?.scenes.reduce(
      (sum, s) =>
        sum +
        s.fragments.reduce(
          (n, f) =>
            n +
            f.blocks.reduce((m, b) => m + b.text.replace(/\s/g, "").length, 0),
          0,
        ),
      0,
    ) || 0;
  const selectedVersion = work.versions.find((v) => v.id === versionId);
  const comparison = work.versions.find((v) => v.id === compareId);
  const provider = studio.providers.find(
    (p) => p.id === studio.models.find((m) => m.id === modelId)?.provider_id,
  );
  const scriptDefinition = work.template_definition?.script_structure as
    | { blocks?: string[] }
    | undefined;
  const allowedBlocks =
    scriptDefinition?.blocks?.filter((b) => typeof b === "string") ||
    Object.keys(blockNames);

  useEffect(() => {
    if (conflict) return;
    if (dirty && editable) {
      try {
        sessionStorage.setItem(
          recoveryKey,
          JSON.stringify({ draft, revision: work.revision }),
        );
      } catch {
        /* Manual save remains available. */
      }
    } else sessionStorage.removeItem(recoveryKey);
  }, [draft, dirty, editable, recoveryKey, work.revision, conflict]);
  useEffect(() => {
    const handler = (event: BeforeUnloadEvent) => {
      if (dirty) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);
  useEffect(() => {
    chatEnd.current?.scrollIntoView({ block: "nearest" });
  }, [work.conversations.length, busy]);

  function apply(next: Work, replaceDraft = false) {
    current.current = next;
    onUpdate(next);
    if (replaceDraft) {
      local.current = structuredClone(next.draft);
      setDraft(local.current);
      sessionStorage.removeItem(recoveryKey);
    }
  }
  async function savePending() {
    if (JSON.stringify(local.current) !== JSON.stringify(current.current.draft))
      apply(
        await studio.saveDraft(
          work.id,
          local.current,
          current.current.revision,
        ),
        true,
      );
  }
  async function run(
    task: () => Promise<void>,
    saveFirst = true,
    automatic = false,
  ): Promise<boolean> {
    if (lock.current || sessionExpired) return false;
    lock.current = true;
    if (automatic) setAutoSaving(true);
    else setBusy(true);
    setError("");
    try {
      if (saveFirst) await savePending();
      await task();
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作未完成，请重试。");
      if (err instanceof ApiError && err.status === 401)
        setSessionExpired(true);
      if (
        err instanceof ApiError &&
        ["REVISION_CONFLICT", "WORK_REVISION_CONFLICT"].includes(err.code)
      ) {
        try {
          setConflict(await studio.refresh(work.id));
        } catch {
          /* Keep the local draft if the remote copy cannot be read. */
        }
      }
      return false;
    } finally {
      lock.current = false;
      setBusy(false);
      setAutoSaving(false);
    }
  }
  useEffect(() => {
    if (!dirty || !editable || disabled || modal || autoSaving || error) return;
    const timer = setTimeout(() => {
      if (document.querySelector("dialog[open]")) return;
      void run(
        async () => {
          const snapshot = structuredClone(local.current);
          const next = await studio.saveDraft(
            work.id,
            snapshot,
            current.current.revision,
            true,
          );
          apply(next);
          setStatus("已自动保存");
        },
        false,
        true,
      );
    }, 2000);
    return () => clearTimeout(timer);
  }, [draft, work.revision, disabled, modal, autoSaving, error]);
  useEffect(() => {
    if (studio.mode !== "live") return;
    let disposed = false;
    async function loadJobs() {
      try {
        const next = await studio.jobs(work.id);
        if (disposed) return;
        setJobs(next);
        const running = next.find((job) =>
          ["QUEUED", "RUNNING"].includes(job.status),
        );
        if (running) setActiveJob(running.id);
        else if (activeJob && !lock.current) {
          setActiveJob(null);
          const finished = next.find((job) => job.id === activeJob);
          if (finished?.status === "SUCCEEDED")
            apply(await studio.refresh(work.id), true);
        }
      } catch (err) {
        if (!disposed)
          setError(err instanceof Error ? err.message : "任务读取失败。");
      }
    }
    void loadJobs();
    const timer = setInterval(() => {
      if (activeJob || modal === "jobs") void loadJobs();
    }, 900);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, [activeJob, modal, work.revision]);
  async function cancelGeneration() {
    if (!activeJob || cancelBusy) return;
    setCancelBusy(true);
    try {
      await studio.cancelJob(work.id, activeJob);
      notify("已取消生成，原正文保留。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "取消未完成。");
    } finally {
      setCancelBusy(false);
    }
  }
  async function save(version = false) {
    if (
      !version &&
      JSON.stringify(local.current) === JSON.stringify(current.current.draft)
    ) {
      setStatus("工作草稿已保存");
      return;
    }
    await run(async () => {
      const next = version
        ? await studio.saveVersion(
            work.id,
            local.current,
            current.current.revision,
          )
        : await studio.saveDraft(
            work.id,
            local.current,
            current.current.revision,
          );
      apply(next, true);
      setStatus(version ? "已保存整部作品版本" : "工作草稿已保存");
      notify(version ? "已保存整部作品版本。" : "工作草稿已保存。");
      if (version) setVersionId(next.versions.at(-1)!.id);
    }, false);
  }
  function navigate(page: Page) {
    if (lock.current || sessionExpired) return;
    if (dirty) setDestination(page);
    else onBack(page);
  }
  function change(mutator: (next: Script) => void) {
    if (!editable || disabled) return;
    setDraft((previous) => {
      const next = structuredClone(previous);
      mutator(next);
      return next;
    });
    setStatus("");
  }
  function editEpisode(mutator: (next: Episode) => void) {
    change((next) => {
      const target = next.episodes?.find((e) => e.number === episodeNumber);
      if (target) mutator(target);
    });
  }
  function addEpisode() {
    const number = Math.max(0, ...episodes.map((e) => e.number)) + 1;
    change((next) => {
      next.episodes ||= [];
      next.episodes.push({
        number,
        title: "",
        scenes: [{ number: 1, heading: "", fragments: [freshFragment()] }],
      });
    });
    setEpisodeNumber(number);
    setSelected(null);
    setMobilePane("paper");
  }
  function selectEpisode(number: number) {
    setEpisodeNumber(number);
    setSelected(null);
    setMobilePane("paper");
    editorRef.current?.scrollTo({ top: 0 });
  }
  async function execute(intent: Intent, consent: Consent) {
    if (intent.kind === "chat") {
      apply(await studio.chat(work.id, intent.instruction, consent));
      setMessage("");
      notify("创作对话已记录。");
    } else {
      setModal(null);
      setPending(null);
      try {
        apply(
          await studio.generate(
            work.id,
            intent.kind,
            intent.episode,
            intent.scene,
            intent.fragment,
            intent.instruction,
            consent,
            setActiveJob,
          ),
          true,
        );
        setSelected(null);
        setEpisodeNumber(intent.episode);
        notify("生成完成，已保留整部作品版本。");
      } finally {
        setActiveJob(null);
      }
    }
    setPending(null);
    setModal(null);
    setInstruction("");
  }
  async function requestAI(intent: Intent) {
    if (!modelId) {
      setError("暂无可用模型，请联系管理员配置模型，或继续手动写作。");
      return;
    }
    if (intent.kind !== "chat" && confirmedCount < 3) {
      setModal("settings");
      setError("请先依次确认三个创作阶段。");
      return;
    }
    if (intent.kind !== "chat" && !intent.instruction.trim()) {
      setError("请填写生成或修改方向。");
      return;
    }
    await run(async () => {
      const consent = await studio.existingConsent(work.id, modelId);
      if (consent) await execute(intent, consent);
      else {
        setPending(intent);
        setChecked(false);
        setModal("consent");
      }
    });
  }
  async function sendMessage() {
    if (message.trim() && editable)
      await requestAI({
        kind: "chat",
        instruction: message.trim(),
        episode: episodeNumber,
        scene: 0,
        fragment: "",
      });
  }
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey)) return;
      if (event.key.toLowerCase() === "s") {
        event.preventDefault();
        if (!document.querySelector("dialog[open]") && editable && !disabled)
          void save();
      }
      if (
        event.key === "Enter" &&
        !document.querySelector("dialog[open]") &&
        editable &&
        !disabled
      ) {
        event.preventDefault();
        void sendMessage();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  });
  function openModal(value: typeof modal) {
    if (lock.current || sessionExpired) return;
    setError("");
    setModal(value);
  }

  return (
    <div className="workspace-shell">
      <Header
        {...headerProps}
        onPage={navigate}
        onAccount={() => {
          if (!busy && !dirty) headerProps.onAccount();
          else notify("请先保存草稿，再打开账户设置。");
        }}
      />
      <div className="workspace-topbar">
        <div className="row">
          <IconButton
            label="返回我的作品"
            onClick={() => navigate("works")}
            disabled={disabled}
          >
            <ArrowLeft />
          </IconButton>
          <div className="workspace-title">
            <h1 className="serif">{work.title}</h1>
            <span>
              {work.templateName} · {roleNames[work.role]}
            </span>
          </div>
          <span
            className={`save-status ${dirty ? "unsaved" : ""}`}
            role="status"
          >
            {activeJob
              ? "正在生成…"
              : autoSaving
                ? "正在自动保存…"
                : busy
                  ? "正在处理…"
                  : dirty
                    ? "有未保存的修改"
                    : status || "工作草稿已保存"}
          </span>
        </div>
        <div className="workspace-actions">
          {studio.mode === "live" && (
            <Button
              small
              aria-label="生成任务"
              onClick={() => {
                setError("");
                setModal("jobs");
              }}
              disabled={busy && !activeJob}
            >
              <History />
              <span>任务</span>
            </Button>
          )}
          {activeJob && editable && (
            <Button small onClick={cancelGeneration} disabled={cancelBusy}>
              取消生成
            </Button>
          )}
          <Button
            small
            aria-label="协作"
            onClick={() => openModal("members")}
            disabled={disabled}
          >
            <Users />
            <span>协作</span>
          </Button>
          <Button
            small
            aria-label="版本"
            onClick={() => {
              setVersionId(work.versions.at(-1)?.id || "");
              openModal("versions");
            }}
            disabled={disabled}
          >
            <History />
            <span>版本</span>
          </Button>
          {editable && (
            <Button
              small
              aria-label="保存草稿"
              onClick={() => save()}
              disabled={disabled || !dirty}
            >
              <Save />
              <span>保存草稿</span>
            </Button>
          )}
          {owner && (
            <Button
              small
              primary
              aria-label="发布与导出"
              onClick={() => openModal("export")}
              disabled={disabled}
            >
              <Download />
              <span>发布与导出</span>
            </Button>
          )}
        </div>
      </div>
      <div className="confirmation-bar">
        <span>
          <LockKeyhole size={14} />
          故事的基础
        </span>
        <div className="confirmation-steps">
          {stages.map((s, index) => (
            <button
              key={s}
              onClick={() => {
                setStage(s);
                openModal("settings");
              }}
              disabled={disabled}
              className={work.creative.confirmed[s] ? "confirmed" : ""}
            >
              {work.creative.confirmed[s] ? (
                <Check size={14} />
              ) : (
                <span className="step-number">{index + 1}</span>
              )}
              {stageNames[s]}
              {index < 2 && <ChevronRight size={12} />}
            </button>
          ))}
        </div>
        <button
          className="text-button"
          onClick={() => openModal("settings")}
          disabled={disabled}
        >
          <Settings2 size={14} />
          查看设定
          {work.proposals.some((p) => p.status === "PENDING") && (
            <span className="dot" />
          )}
        </button>
      </div>
      <div className="mobile-tabs">
        {[
          ["directory", "故事目录"],
          ["paper", "剧本正文"],
          ["chat", "创作对话"],
        ].map(([key, label]) => (
          <button
            key={key}
            className={mobilePane === key ? "active" : ""}
            onClick={() => {
              setMobilePane(key);
              if (key === "chat") setChatOpen(true);
            }}
          >
            {label}
          </button>
        ))}
      </div>
      <div
        className={`studio-layout ${chatOpen ? "" : "chat-hidden"} mobile-${mobilePane}`}
      >
        <aside className="directory">
          <div className="pane-heading">
            <h2>故事目录</h2>
            {editable && (
              <IconButton
                label="添加一集"
                onClick={addEpisode}
                disabled={
                  disabled ||
                  episodes.length >= (work.settings.episode_count || 200)
                }
              >
                <Plus />
              </IconButton>
            )}
          </div>
          <div className="directory-list">
            {episodes.map((e) => (
              <div className="episode-item" key={e.number}>
                <button
                  className={`episode-link ${episodeNumber === e.number ? "active" : ""}`}
                  onClick={() => selectEpisode(e.number)}
                >
                  <span className="episode-num">
                    {String(e.number).padStart(2, "0")}
                  </span>
                  <span>
                    <b>{e.title || "待命名的一集"}</b>
                    <small>{e.scenes.length} 个场景</small>
                  </span>
                  <ChevronRight size={14} />
                </button>
                {episodeNumber === e.number && (
                  <div className="scene-links">
                    {e.scenes.map((s) => (
                      <button
                        key={s.number}
                        onClick={() => {
                          setMobilePane("paper");
                          setTimeout(
                            () =>
                              document
                                .getElementById(`scene-${s.number}`)
                                ?.scrollIntoView({
                                  behavior: "smooth",
                                  block: "start",
                                }),
                            0,
                          );
                        }}
                      >
                        <span className="scene-dot" />
                        {s.heading || `场景 ${s.number}`}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ))}
            {!episodes.length && (
              <p className="hint directory-empty">
                故事尚未开始。添加第一集，或先完善创作设定。
              </p>
            )}
          </div>
          <div className="directory-bottom">
            <p>
              {episodes.length} / {work.settings.episode_count || "—"} 集 ·{" "}
              {confirmedCount === 3 ? "设定已确认" : "设定待完善"}
            </p>
            <Button small onClick={() => openModal("settings")}>
              <FileText />
              故事设定
            </Button>
          </div>
        </aside>
        <main className="writing-pane">
          <div className="editor-toolbar">
            <span>
              <FileText size={15} />
              {episode
                ? `第 ${String(episodeNumber).padStart(2, "0")} 集`
                : "你的下一幕"}
              <span className="toolbar-divider" />
              {count.toLocaleString()} 字
            </span>
            <div className="row">
              {editable && (
                <Button
                  small
                  onClick={() => {
                    setScope(selected?.fragment ? "FRAGMENT" : "EPISODE");
                    setGenerationScene(
                      selected?.scene || episode?.scenes[0]?.number || 1,
                    );
                    setInstruction("");
                    openModal("generate");
                  }}
                  disabled={disabled || !studio.models.length}
                >
                  <Sparkles />
                  {selected ? "重写片段" : "生成本集"}
                </Button>
              )}
              <IconButton
                label={chatOpen ? "收起创作对话" : "展开创作对话"}
                onClick={() => setChatOpen(!chatOpen)}
              >
                {chatOpen ? <PanelRightClose /> : <MessageSquare />}
              </IconButton>
            </div>
          </div>
          {error && !modal && !conflict && (
            <div className="workspace-error">
              <ErrorMessage message={error} />
            </div>
          )}
          <div className="paper-scroll" ref={editorRef}>
            {episode ? (
              <article className="script-paper">
                <div className="paper-meta">
                  <span>{work.title.toUpperCase()}</span>
                  <span>WORKING DRAFT</span>
                </div>
                <div className="paper-title">
                  <p>第 {String(episodeNumber).padStart(2, "0")} 集</p>
                  <input
                    aria-label="本集标题"
                    className="serif"
                    placeholder="为这一集起个名字"
                    value={episode.title || ""}
                    readOnly={!editable}
                    disabled={disabled}
                    onChange={(event) =>
                      editEpisode((e) => {
                        e.title = event.target.value;
                      })
                    }
                  />
                </div>
                {episode.scenes.map((scene) => (
                  <section
                    className="script-scene"
                    id={`scene-${scene.number}`}
                    key={scene.number}
                  >
                    <div className="scene-title">
                      <span>
                        {episode.number}-{scene.number}
                      </span>
                      <input
                        aria-label={`场景 ${scene.number} 标题`}
                        placeholder="地点 · 时间 · 氛围"
                        value={scene.heading || ""}
                        disabled={disabled}
                        readOnly={!editable}
                        onChange={(event) =>
                          editEpisode((e) => {
                            e.scenes.find(
                              (s) => s.number === scene.number,
                            )!.heading = event.target.value;
                          })
                        }
                      />
                    </div>
                    {scene.fragments.map((fragment, fIndex) => (
                      <div
                        className={`script-fragment ${selected?.scene === scene.number && selected.fragment === fragment.id ? "selected" : ""}`}
                        key={fragment.id}
                        onClick={() =>
                          setSelected({
                            scene: scene.number,
                            fragment: fragment.id,
                          })
                        }
                      >
                        <div className="fragment-label">
                          <span>
                            片段 {String(fIndex + 1).padStart(2, "0")}
                          </span>
                          {selected?.scene === scene.number &&
                            selected.fragment === fragment.id && (
                              <span>已选中 · 可独立重写</span>
                            )}
                        </div>
                        {fragment.blocks.map((block, index) => (
                          <div
                            className={`script-block block-${block.type.toLowerCase()}`}
                            key={index}
                          >
                            <div className="block-controls">
                              <select
                                aria-label={`片段 ${fIndex + 1} 剧本块 ${index + 1} 类型`}
                                value={block.type}
                                disabled={!editable || disabled}
                                onChange={(event) =>
                                  editEpisode((e) => {
                                    e.scenes
                                      .find((s) => s.number === scene.number)!
                                      .fragments.find(
                                        (f) => f.id === fragment.id,
                                      )!.blocks[index].type =
                                      event.target.value;
                                  })
                                }
                              >
                                {Array.from(
                                  new Set([...allowedBlocks, block.type]),
                                ).map((type) => (
                                  <option key={type} value={type}>
                                    {blockNames[type] || type}
                                  </option>
                                ))}
                              </select>
                              {editable && (
                                <IconButton
                                  label={`删除剧本块 ${index + 1}`}
                                  disabled={disabled}
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    editEpisode((e) => {
                                      e.scenes
                                        .find((s) => s.number === scene.number)!
                                        .fragments.find(
                                          (f) => f.id === fragment.id,
                                        )!
                                        .blocks.splice(index, 1);
                                    });
                                  }}
                                >
                                  <X size={12} />
                                </IconButton>
                              )}
                            </div>
                            <textarea
                              aria-label={`片段 ${fIndex + 1} 剧本块 ${index + 1} 内容`}
                              rows={Math.max(
                                1,
                                block.text
                                  .split("\n")
                                  .reduce(
                                    (n, line) =>
                                      n +
                                      Math.max(
                                        1,
                                        Math.ceil(
                                          line.length /
                                            (block.type === "DIALOGUE"
                                              ? 24
                                              : 35),
                                        ),
                                      ),
                                    0,
                                  ),
                              )}
                              value={block.text}
                              disabled={disabled}
                              readOnly={!editable}
                              placeholder={
                                blockNames[block.type] || "开始写作…"
                              }
                              onChange={(event) =>
                                editEpisode((e) => {
                                  e.scenes
                                    .find((s) => s.number === scene.number)!
                                    .fragments.find(
                                      (f) => f.id === fragment.id,
                                    )!.blocks[index].text = event.target.value;
                                })
                              }
                            />
                          </div>
                        ))}
                        {editable && (
                          <button
                            className="add-block"
                            disabled={disabled}
                            onClick={() =>
                              editEpisode((e) => {
                                e.scenes
                                  .find((s) => s.number === scene.number)!
                                  .fragments.find((f) => f.id === fragment.id)!
                                  .blocks.push({ type: "ACTION", text: "" });
                              })
                            }
                          >
                            <Plus size={13} />
                            添加剧本块
                          </button>
                        )}
                      </div>
                    ))}
                    {editable && (
                      <button
                        className="add-fragment"
                        disabled={disabled}
                        onClick={() =>
                          editEpisode((e) => {
                            e.scenes
                              .find((s) => s.number === scene.number)!
                              .fragments.push(freshFragment());
                          })
                        }
                      >
                        <Plus size={14} />
                        添加片段
                      </button>
                    )}
                  </section>
                ))}
                {editable && (
                  <Button
                    small
                    className="add-scene"
                    disabled={disabled}
                    onClick={() =>
                      editEpisode((e) => {
                        e.scenes.push({
                          number:
                            Math.max(0, ...e.scenes.map((s) => s.number)) + 1,
                          heading: "",
                          fragments: [freshFragment()],
                        });
                      })
                    }
                  >
                    <Plus />
                    添加场景
                  </Button>
                )}
                <div className="paper-end">✦</div>
              </article>
            ) : (
              <Empty
                title="把脑海中的第一幕，写下来。"
                description="可以先完善故事设定，也可以添加一集，从一个动作或一句对白开始。"
              >
                {editable && (
                  <Button primary onClick={addEpisode} disabled={disabled}>
                    <Plus />
                    添加第一集
                  </Button>
                )}
              </Empty>
            )}
          </div>
          <footer className="editor-footer">
            <span>
              <span className="dot" />
              {editable ? "专注写作" : "只读浏览"} · 每一幕，都有新的可能
            </span>
            <span>
              {selected ? "已选中一个片段" : "点击片段，探索更多可能"}
            </span>
          </footer>
        </main>
        {chatOpen && (
          <aside className="chat-pane">
            <div className="pane-heading">
              <h2>
                <Sparkles size={17} />
                创作助手
              </h2>
              <IconButton
                label="关闭创作对话"
                onClick={() => {
                  setChatOpen(false);
                  setMobilePane("paper");
                }}
              >
                <X />
              </IconButton>
            </div>
            <p className="chat-subtitle">一起，让故事往前走。</p>
            <div className="chat-context">
              <LockKeyhole size={13} />
              {confirmedCount === 3
                ? "以已确认设定为创作依据"
                : "先讨论灵感，再逐步确认设定"}
            </div>
            <div className="chat-messages">
              {!work.conversations.length && (
                <div className="chat-welcome">
                  <Sparkles />
                  <h3 className="serif">从一个念头开始。</h3>
                  <p>聊聊你的人物，探索冲突，或者给下一幕找一个新的方向。</p>
                </div>
              )}
              {work.conversations.map((m) => (
                <div className="conversation" key={m.id}>
                  <div className="chat-user">
                    <span>你 · {formatDate(m.created_at)}</span>
                    <p>{m.user_message}</p>
                  </div>
                  <div className="chat-assistant">
                    <span>
                      <Sparkles size={13} />
                      幕间创作助手
                    </span>
                    <p>{m.assistant_message || "回复尚未完成。"}</p>
                  </div>
                </div>
              ))}
              {busy && (
                <p className="chat-thinking" role="status">
                  正在处理创作内容…
                </p>
              )}
              <div ref={chatEnd} />
            </div>
            <div className="chat-compose">
              <div className="suggestion-chips">
                {["让冲突更有张力", "探索人物的动机"].map((text) => (
                  <button
                    key={text}
                    onClick={() => setMessage(text)}
                    disabled={!editable || disabled}
                  >
                    {text}
                  </button>
                ))}
              </div>
              <textarea
                aria-label="创作对话内容"
                placeholder={
                  editable
                    ? "关于这个故事，你有什么想法？"
                    : "查看者可以阅读创作对话"
                }
                rows={3}
                value={message}
                disabled={!editable || disabled}
                onChange={(event) => setMessage(event.target.value)}
              />
              <div className="row between">
                <select
                  aria-label="创作模型"
                  value={modelId}
                  onChange={(event) => setModelId(event.target.value)}
                  disabled={disabled || !editable}
                >
                  {!studio.models.length && (
                    <option value="">暂无可用模型</option>
                  )}
                  {studio.models.map((m) => (
                    <option value={m.id} key={m.id}>
                      {m.model_name} · v{m.version}
                    </option>
                  ))}
                </select>
                <IconButton
                  label="发送创作对话"
                  onClick={sendMessage}
                  disabled={
                    disabled || !editable || !message.trim() || !modelId
                  }
                >
                  <Send />
                </IconButton>
              </div>
              <p className="chat-disclaimer">
                {studio.mode === "demo"
                  ? "本地示例回复 · 不调用外部模型"
                  : "使用所选模型进行创作 · 每轮需要有效外发确认"}
              </p>
            </div>
          </aside>
        )}
      </div>
      {modal === "settings" && !editingStage && (
        <Modal
          title="故事设定"
          description="让灵感有方向，让每一次创作有依据。"
          wide
          onClose={() => setModal(null)}
          busy={busy}
          footer={
            <>
              <Button onClick={() => setModal(null)} disabled={busy}>
                回到写作
              </Button>
              {editable && (
                <Button
                  onClick={() => setEditingStage(stage)}
                  disabled={
                    busy ||
                    work.proposals.some(
                      (p) => p.stage === stage && p.status === "PENDING",
                    )
                  }
                >
                  {work.creative.confirmed[stage] ? "提出设定变更" : "编辑设定"}
                </Button>
              )}
              {owner && !work.creative.confirmed[stage] && (
                <Button
                  primary
                  disabled={
                    busy ||
                    !work.creative[stage] ||
                    (stage !== "brief" &&
                      !work.creative.confirmed[
                        stages[stages.indexOf(stage) - 1]
                      ])
                  }
                  onClick={() =>
                    run(async () => {
                      apply(await studio.confirm(work.id, stage));
                      notify(`${stageNames[stage]}已确认。`);
                    })
                  }
                >
                  <Check />
                  确认此阶段
                </Button>
              )}
            </>
          }
        >
          {owner && (
            <Button
              small
              disabled={busy}
              onClick={() => {
                setParameterTitle(work.title);
                setParameterValues(structuredClone(work.settings));
                setModal("parameters");
              }}
            >
              编辑作品参数
            </Button>
          )}
          <div className="modal-tabs">
            {stages.map((s) => (
              <button
                className={stage === s ? "active" : ""}
                key={s}
                onClick={() => setStage(s)}
              >
                {stageNames[s]}
                {Boolean(work.creative.confirmed[s]) && <Check size={13} />}
              </button>
            ))}
          </div>
          <div className="notice">
            {work.creative.confirmed[stage]
              ? "此阶段已确认。修改需要提交变更提案，并经所有者审批。"
              : "当前为设定草稿。所有者依次确认创作简报、人物设定与故事大纲后，才能生成正文。"}
          </div>
          <CreativeContent
            work={{
              ...work,
              creative: {
                ...work.creative,
                [stage]: work.creative.confirmed[stage] || work.creative[stage],
              },
            }}
            stage={stage}
          />
          {work.proposals
            .filter((p) => p.stage === stage && p.status === "PENDING")
            .map((p) => (
              <article className="proposal" key={p.id}>
                <h3>待审批的变更提案</h3>
                <CreativeContent
                  work={{
                    ...work,
                    creative: { ...work.creative, [stage]: p.content },
                  }}
                  stage={stage}
                />
                {owner ? (
                  <div className="row">
                    <Button
                      small
                      onClick={() =>
                        run(async () => {
                          apply(
                            await studio.reviewProposal(work.id, p.id, false),
                          );
                          notify("提案已拒绝，已确认设定保持原内容。");
                        })
                      }
                      disabled={busy}
                    >
                      拒绝提案
                    </Button>
                    <Button
                      small
                      primary
                      onClick={() =>
                        run(async () => {
                          apply(
                            await studio.reviewProposal(work.id, p.id, true),
                          );
                          notify("提案已接受，外发确认需要重新授权。");
                        })
                      }
                      disabled={busy}
                    >
                      接受变更
                    </Button>
                  </div>
                ) : (
                  <p className="hint">等待作品所有者审批。</p>
                )}
              </article>
            ))}
          <ErrorMessage message={error} />
        </Modal>
      )}
      {editingStage && (
        <CreativeEditor
          work={work}
          stage={editingStage}
          onClose={() => setEditingStage(null)}
          onSave={async (content) => {
            const ok = await run(async () => {
              apply(
                await studio.saveCreative(
                  work.id,
                  editingStage,
                  content,
                  Boolean(work.creative.confirmed[editingStage]),
                ),
              );
            });
            if (!ok)
              throw new Error("保存未完成，请检查作品状态和网络后重试。");
            setEditingStage(null);
            notify(
              work.creative.confirmed[editingStage]
                ? "设定变更提案已提交。"
                : "设定草稿已保存。",
            );
          }}
        />
      )}
      {modal === "generate" && (
        <Modal
          title={
            scope === "FRAGMENT"
              ? "给这一幕，另一种可能"
              : scope === "SCENE"
                ? "让这一场景开始"
                : "让下一集开始"
          }
          description={
            scope === "FRAGMENT"
              ? "只替换选中的片段，成功后保留整部作品版本。"
              : scope === "SCENE"
                ? "只替换选定场景，其他正文保留。成功后创建整部作品版本。"
                : `生成第 ${episodeNumber} 集。已有正文将被替换，并保留整部作品版本。`
          }
          onClose={() => setModal(null)}
          busy={busy}
          footer={
            <>
              <Button onClick={() => setModal(null)} disabled={busy}>
                取消
              </Button>
              <Button
                primary
                disabled={busy || !instruction.trim() || !modelId}
                onClick={() =>
                  requestAI({
                    kind: scope,
                    instruction,
                    episode: episodeNumber,
                    scene:
                      scope === "SCENE"
                        ? generationScene
                        : selected?.scene || 1,
                    fragment: selected?.fragment || "",
                  })
                }
              >
                <Sparkles />
                {scope === "FRAGMENT"
                  ? "重写选中片段"
                  : scope === "SCENE"
                    ? "生成场景"
                    : "生成本集"}
              </Button>
            </>
          }
        >
          <Field label="生成范围">
            <select
              value={scope}
              disabled={busy}
              onChange={(e) => setScope(e.target.value as typeof scope)}
            >
              <option value="EPISODE">当前整集</option>
              <option value="SCENE" disabled={!episode}>
                选定场景
              </option>
              <option value="FRAGMENT" disabled={!selected?.fragment}>
                选中片段
              </option>
            </select>
          </Field>
          {scope === "SCENE" && (
            <Field label="目标场景">
              <select
                value={generationScene}
                disabled={busy}
                onChange={(e) => setGenerationScene(Number(e.target.value))}
              >
                {episode?.scenes.map((scene) => (
                  <option key={scene.number} value={scene.number}>
                    场景 {scene.number} · {scene.heading}
                  </option>
                ))}
              </select>
            </Field>
          )}
          <Field label="创作方向">
            <textarea
              autoFocus
              rows={5}
              value={instruction}
              onChange={(event) => setInstruction(event.target.value)}
              placeholder="例如：用动作暗示人物之间的秘密，让对白更加克制。"
              disabled={busy}
            />
          </Field>
          <Field label="创作模型">
            <select
              value={modelId}
              onChange={(event) => setModelId(event.target.value)}
              disabled={busy}
            >
              {studio.models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.model_name} · v{m.version}
                </option>
              ))}
            </select>
          </Field>
          <p className="hint">
            {confirmedCount === 3
              ? "本次生成会遵循已确认设定。"
              : "请先依次确认三个创作阶段。"}
          </p>
          <ErrorMessage message={error} />
        </Modal>
      )}
      {modal === "consent" && pending && (
        <Modal
          title="确认本次创作的发送范围"
          description="授权仅适用于当前作品输入和所选模型配置版本。"
          onClose={() => {
            setModal(null);
            setPending(null);
          }}
          busy={busy}
          footer={
            <>
              <Button
                onClick={() => {
                  setModal(null);
                  setPending(null);
                }}
                disabled={busy}
              >
                取消
              </Button>
              {owner && (
                <Button
                  primary
                  disabled={!checked || busy}
                  onClick={() =>
                    run(
                      async () =>
                        execute(
                          pending,
                          await studio.grantConsent(work.id, modelId),
                        ),
                      false,
                    )
                  }
                >
                  确认并继续
                </Button>
              )}
            </>
          }
        >
          <div className="consent-summary">
            <div>
              <span>供应商</span>
              <strong>{provider?.name || "未知供应商"}</strong>
            </div>
            <div>
              <span>模型</span>
              <strong>
                {studio.models.find((m) => m.id === modelId)?.model_name} · v
                {studio.models.find((m) => m.id === modelId)?.version}
              </strong>
            </div>
            <div>
              <span>作品输入</span>
              <strong>当前修订 {work.revision}</strong>
            </div>
            <div>
              <span>发送范围</span>
              <strong>创作对话、设定、故事大纲、剧本正文</strong>
            </div>
          </div>
          <div className="provider-policy">
            <h3>数据保留</h3>
            <p>{provider?.retention_description || "供应商未提供说明。"}</p>
            <h3>数据删除</h3>
            <p>{provider?.deletion_description || "供应商未提供说明。"}</p>
            <h3>故障处理</h3>
            <p>{provider?.incident_description || "供应商未提供说明。"}</p>
          </div>
          {owner ? (
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={checked}
                onChange={(event) => setChecked(event.target.checked)}
                disabled={busy}
              />
              <span>我了解发送范围，允许使用当前输入进行本次创作。</span>
            </label>
          ) : (
            <div className="notice">
              只有作品所有者可以授权外发。请联系所有者为当前作品输入与所选模型进行确认。
            </div>
          )}
          <ErrorMessage message={error} />
        </Modal>
      )}
      {modal === "versions" && (
        <Modal
          title="每一次修改，都有来路"
          description="工作草稿独立保存。下列版本均为整部作品的不可变快照。"
          wide
          onClose={() => setModal(null)}
          busy={busy}
          footer={
            <>
              <Button onClick={() => setModal(null)} disabled={busy}>
                回到写作
              </Button>
              {owner && selectedVersion && (
                <Button
                  onClick={() => setRestoreId(selectedVersion.id)}
                  disabled={busy}
                >
                  恢复此版本
                </Button>
              )}
              {owner && (
                <Button
                  primary
                  onClick={() =>
                    run(async () => {
                      apply(
                        await studio.saveVersion(
                          work.id,
                          local.current,
                          current.current.revision,
                        ),
                        true,
                      );
                      setVersionId(current.current.versions.at(-1)!.id);
                      notify("已保存整部作品版本。");
                    }, false)
                  }
                  disabled={busy}
                >
                  <Save />
                  保存当前版本
                </Button>
              )}
            </>
          }
        >
          <div className="version-selectors">
            <Field label="查看版本">
              <select
                value={versionId}
                onChange={(event) => setVersionId(event.target.value)}
              >
                <option value="">选择版本</option>
                {[...work.versions].reverse().map((v, i) => (
                  <option key={v.id} value={v.id}>
                    版本 {work.versions.length - i} ·{" "}
                    {sourceNames[v.source] || v.source} ·{" "}
                    {formatDate(v.created_at)}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="与另一个版本比较">
              <select
                value={compareId}
                onChange={(event) => setCompareId(event.target.value)}
              >
                <option value="">不比较</option>
                {work.versions
                  .filter((v) => v.id !== versionId)
                  .map((v, i) => (
                    <option key={v.id} value={v.id}>
                      {sourceNames[v.source] || v.source} · 修订{" "}
                      {v.work_revision} · {i + 1}
                    </option>
                  ))}
              </select>
            </Field>
          </div>
          {selectedVersion ? (
            <>
              <p className="hint">
                {comparison
                  ? JSON.stringify(selectedVersion.content) ===
                    JSON.stringify(comparison.content)
                    ? "两个版本的正文内容相同。"
                    : "两个版本的正文有变化，可并排查看。"
                  : `整部作品 · 修订 ${selectedVersion.work_revision}`}
              </p>
              <div
                className={`version-preview ${comparison ? "comparing" : ""}`}
              >
                <div>
                  <h3>选中版本</h3>
                  <pre>{scriptText(work.title, selectedVersion.content)}</pre>
                </div>
                {comparison && (
                  <div>
                    <h3>比较版本</h3>
                    <pre>{scriptText(work.title, comparison.content)}</pre>
                  </div>
                )}
              </div>
            </>
          ) : (
            <Empty
              title="为值得留住的这一刻，保存一个版本。"
              description={
                owner
                  ? "保存当前版本后，可以比较、恢复或发布正式稿。"
                  : "由作品所有者保存版本后，可以在这里查看。"
              }
            />
          )}
          <ErrorMessage message={error} />
        </Modal>
      )}
      {restoreId && (
        <Modal
          title="恢复这个版本？"
          description="恢复会替换整部工作草稿，并产生一个新的恢复版本。"
          onClose={() => setRestoreId(null)}
          busy={busy}
          footer={
            <>
              <Button onClick={() => setRestoreId(null)} disabled={busy}>
                取消
              </Button>
              <Button
                primary
                disabled={busy}
                onClick={() =>
                  run(async () => {
                    apply(
                      await studio.restoreVersion(work.id, restoreId),
                      true,
                    );
                    setRestoreId(null);
                    setVersionId(current.current.versions.at(-1)!.id);
                    setEpisodeNumber(
                      current.current.draft.episodes?.[0]?.number || 1,
                    );
                    setSelected(null);
                    notify("已恢复整部作品，并保留恢复版本。");
                  })
                }
              >
                确认恢复
              </Button>
            </>
          }
        >
          <p>
            如有未保存的修改，会先保存到工作草稿；请先保存一个版本以保留这些修改。
          </p>
          <ErrorMessage message={error} />
        </Modal>
      )}
      {modal === "export" && (
        <Modal
          title="让故事，走向下一幕"
          description="发布绑定保存的版本，导出始终使用正式稿内容。"
          onClose={() => setModal(null)}
          busy={busy}
          footer={
            <Button onClick={() => setModal(null)} disabled={busy}>
              回到写作
            </Button>
          }
        >
          <div className="publish-section">
            <h3>1 · 发布正式稿</h3>
            <Field label="选择要发布的版本">
              <select
                value={versionId}
                disabled={busy}
                onChange={(event) => setVersionId(event.target.value)}
              >
                <option value="">选择已保存的版本</option>
                {[...work.versions].reverse().map((v) => (
                  <option key={v.id} value={v.id}>
                    修订 {v.work_revision} · {sourceNames[v.source] || v.source}{" "}
                    · {formatDate(v.created_at)}
                  </option>
                ))}
              </select>
            </Field>
            <div className="row">
              <Button
                small
                disabled={busy}
                onClick={() =>
                  run(async () => {
                    apply(
                      await studio.saveVersion(
                        work.id,
                        local.current,
                        current.current.revision,
                      ),
                      true,
                    );
                    setVersionId(current.current.versions.at(-1)!.id);
                    notify("当前正文已保存为版本。");
                  }, false)
                }
              >
                <Save />
                保存当前版本
              </Button>
              <Button
                small
                primary
                disabled={!versionId || busy}
                onClick={() =>
                  run(async () => {
                    apply(await studio.publish(work.id, versionId));
                    setReleaseId(
                      current.current.releases.find((r) => r.is_current)!.id,
                    );
                    notify("正式稿已发布，可以导出。");
                  })
                }
              >
                发布选中版本
              </Button>
            </div>
            <p className="hint">
              请选择版本内容进行交付。草稿之后的修改不会改变已发布的正式稿。
            </p>
          </div>
          <div className="publish-section">
            <h3>2 · 导出正式稿</h3>
            <Field label="正式稿">
              <select
                value={releaseId}
                disabled={busy}
                onChange={(event) => setReleaseId(event.target.value)}
              >
                <option value="">选择已发布的正式稿</option>
                {[...work.releases].reverse().map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.is_current ? "当前正式稿" : "历史正式稿"} · 修订{" "}
                    {r.version.work_revision} · {formatDate(r.created_at)}
                  </option>
                ))}
              </select>
            </Field>
            <div className="row">
              <Button
                disabled={!releaseId || busy}
                onClick={() =>
                  run(async () => {
                    await studio.export(work.id, "markdown", releaseId);
                    notify("Markdown 已导出。");
                  }, false)
                }
              >
                <Download />
                Markdown
              </Button>
              <Button
                disabled={!releaseId || busy}
                onClick={() =>
                  run(async () => {
                    await studio.export(work.id, "docx", releaseId);
                    notify("DOCX 已导出。");
                  }, false)
                }
              >
                <Download />
                DOCX
              </Button>
            </div>
          </div>
          <ErrorMessage message={error} />
        </Modal>
      )}
      {modal === "members" && (
        <Modal
          title="一起，把故事写好"
          description="作品成员权限与平台角色独立，邀请被接受后才可访问。"
          onClose={() => setModal(null)}
          busy={busy}
          footer={
            <Button onClick={() => setModal(null)} disabled={busy}>
              完成
            </Button>
          }
        >
          <div className="member-list">
            {work.members
              .filter((m) => m.status !== "REVOKED")
              .map((m) => (
                <div className="member-row" key={m.id}>
                  <span className="avatar">{m.username?.slice(0, 1)}</span>
                  <div>
                    <strong>{m.username}</strong>
                    <small>
                      {roleNames[m.role]} ·{" "}
                      {m.status === "PENDING" ? "等待接受" : "已加入"}
                    </small>
                  </div>
                  {owner && m.role !== "OWNER" && m.status === "ACCEPTED" && (
                    <>
                      <select
                        aria-label={`${m.username}的作品角色`}
                        value={m.role}
                        disabled={busy}
                        onChange={(e) => {
                          void run(async () =>
                            apply(
                              await studio.memberRole(
                                work.id,
                                m.user_id,
                                e.target.value as "EDITOR" | "VIEWER",
                              ),
                            ),
                          );
                        }}
                      >
                        <option value="EDITOR">编辑者</option>
                        <option value="VIEWER">查看者</option>
                      </select>
                      <Button
                        small
                        disabled={busy}
                        onClick={() => setTransferTarget(m.user_id)}
                      >
                        转移所有权
                      </Button>
                    </>
                  )}
                  {owner && m.role !== "OWNER" && (
                    <IconButton
                      label={`撤销${m.username}的访问权限`}
                      disabled={busy}
                      onClick={() =>
                        run(async () => {
                          apply(await studio.revoke(work.id, m.user_id));
                          notify("成员访问权限已撤销。");
                        })
                      }
                    >
                      <Trash2 />
                    </IconButton>
                  )}
                </div>
              ))}
          </div>
          {owner && (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                if (username.trim())
                  void run(async () => {
                    apply(
                      await studio.invite(work.id, username.trim(), inviteRole),
                    );
                    setUsername("");
                    notify("邀请已创建，等待对方接受。");
                  });
              }}
            >
              <Field label="邀请已有账号">
                <input
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  placeholder="输入对方登录名"
                  required
                  disabled={busy}
                />
              </Field>
              <div className="row">
                <select
                  aria-label="邀请角色"
                  value={inviteRole}
                  onChange={(event) =>
                    setInviteRole(event.target.value as "EDITOR" | "VIEWER")
                  }
                  disabled={busy}
                >
                  <option value="EDITOR">编辑者 · 参与创作</option>
                  <option value="VIEWER">查看者 · 只读访问</option>
                </select>
                <Button
                  primary
                  type="submit"
                  disabled={busy || !username.trim()}
                >
                  发送邀请
                </Button>
              </div>
            </form>
          )}
          <ErrorMessage message={error} />
        </Modal>
      )}
      {modal === "jobs" && (
        <Modal
          title="生成任务"
          description="生成成功创建整部作品版本。失败或取消时保留原草稿。"
          onClose={() => setModal(null)}
        >
          {jobs.length === 0 && <p className="hint">还没有生成任务。</p>}
          {jobs
            .slice()
            .reverse()
            .map((job) => (
              <div className="job-row" key={job.id}>
                <div>
                  <strong>
                    {
                      {
                        WORK: "整部作品",
                        EPISODE: "集",
                        SCENE: "场景",
                        FRAGMENT: "片段",
                      }[job.target_type]
                    }{" "}
                    · {job.target_path.episode || ""}
                  </strong>
                  <small>
                    {formatDate(job.created_at)} ·{" "}
                    {
                      {
                        QUEUED: "排队中",
                        RUNNING: "生成中",
                        SUCCEEDED: "已完成",
                        FAILED: "失败",
                        CANCELED: "已取消",
                      }[job.status]
                    }
                  </small>
                  {job.error_code && <p className="hint">{job.error_code}</p>}
                </div>
                {editable && ["QUEUED", "RUNNING"].includes(job.status) && (
                  <Button
                    small
                    disabled={cancelBusy}
                    onClick={cancelGeneration}
                  >
                    取消生成
                  </Button>
                )}
                {editable && job.status === "FAILED" && (
                  <Button
                    small
                    disabled={busy || Boolean(activeJob)}
                    onClick={() =>
                      run(async () => {
                        setActiveJob(job.id);
                        try {
                          apply(await studio.retryJob(work.id, job.id), true);
                        } finally {
                          setActiveJob(null);
                        }
                      })
                    }
                  >
                    重试任务
                  </Button>
                )}
              </div>
            ))}
          <ErrorMessage message={error} />
        </Modal>
      )}
      {modal === "parameters" && (
        <Modal
          title="作品参数"
          description="更新参数后，已有外发确认失效。"
          busy={busy}
          onClose={() => setModal("settings")}
          footer={
            <>
              <Button disabled={busy} onClick={() => setModal("settings")}>
                取消
              </Button>
              <Button
                primary
                disabled={busy || !parameterTitle.trim()}
                onClick={() =>
                  run(async () => {
                    apply(
                      await studio.updateSettings(
                        work.id,
                        parameterTitle.trim(),
                        parameterValues,
                      ),
                    );
                    setModal("settings");
                    notify("作品参数已更新。");
                  })
                }
              >
                保存作品参数
              </Button>
            </>
          }
        >
          <Field label="作品名称">
            <input
              maxLength={256}
              value={parameterTitle}
              disabled={busy}
              onChange={(e) => setParameterTitle(e.target.value)}
            />
          </Field>
          {Object.entries(
            definitionFields(work.template_definition || {}, "settings"),
          ).map(([key, rule]) => (
            <Field key={key} label={labels[key] || key}>
              <RuleInput
                name={key}
                rule={rule}
                value={(parameterValues as Record<string, unknown>)[key]}
                disabled={busy}
                onChange={(value) =>
                  setParameterValues((previous) => ({
                    ...previous,
                    [key]: value,
                  }))
                }
              />
            </Field>
          ))}
          <ErrorMessage message={error} />
        </Modal>
      )}
      {transferTarget && (
        <Modal
          title="转移这部作品的所有权？"
          description="接收成员将成为所有者，你将成为编辑者。"
          busy={busy}
          onClose={() => setTransferTarget(null)}
          footer={
            <>
              <Button disabled={busy} onClick={() => setTransferTarget(null)}>
                取消
              </Button>
              <Button
                primary
                disabled={busy}
                onClick={() =>
                  run(async () => {
                    apply(await studio.transfer(work.id, transferTarget));
                    setTransferTarget(null);
                    notify("作品所有权已转移。");
                  })
                }
              >
                确认转移
              </Button>
            </>
          }
        >
          <p>
            接收账号：
            {work.members.find((m) => m.user_id === transferTarget)?.username}
          </p>
          <ErrorMessage message={error} />
        </Modal>
      )}
      {destination && (
        <Modal
          title="还有一些文字没有保存"
          description="离开前，可以保存当前工作草稿。"
          onClose={() => setDestination(null)}
          busy={busy}
          footer={
            <>
              <Button onClick={() => setDestination(null)} disabled={busy}>
                继续编辑
              </Button>
              <Button
                onClick={() => {
                  sessionStorage.removeItem(recoveryKey);
                  onBack(destination);
                }}
                disabled={busy}
              >
                放弃修改并离开
              </Button>
              <Button
                primary
                disabled={busy}
                onClick={() =>
                  run(async () => {
                    onBack(destination);
                  })
                }
              >
                保存并离开
              </Button>
            </>
          }
        >
          <p>《{work.title}》的最新修改仍在编辑区。保存草稿不会创建版本。</p>
          <ErrorMessage message={error} />
        </Modal>
      )}
      {conflict && (
        <Modal
          title="作品已有新的修改"
          description="你的未保存文字仍然保留。比较两份草稿后，选择接下来使用的内容。"
          wide
          onClose={() => {}}
          busy={busy}
          footer={
            <>
              <Button
                disabled={busy}
                onClick={() => {
                  apply(conflict, true);
                  setConflict(null);
                  setModal(null);
                  setError("");
                  setSelected(null);
                  setEpisodeNumber(conflict.draft.episodes?.[0]?.number || 1);
                  notify("已读取服务器上的最新草稿。");
                }}
              >
                使用服务器草稿
              </Button>
              <Button
                primary
                disabled={busy}
                onClick={() => {
                  apply(conflict);
                  setConflict(null);
                  setModal(null);
                  setError("");
                  notify("你的草稿已保留，请检查后再保存。");
                }}
              >
                保留我的草稿继续编辑
              </Button>
            </>
          }
        >
          <div className="notice">
            选择保留你的草稿后，下一次保存会用这份整部草稿更新服务器内容。请先检查协作者的修改。
          </div>
          <div className="version-preview comparing">
            <div>
              <h3>你的未保存草稿</h3>
              <pre>{scriptText(work.title, draft)}</pre>
            </div>
            <div>
              <h3>服务器最新草稿 · 修订 {conflict.revision}</h3>
              <pre>{scriptText(work.title, conflict.draft)}</pre>
            </div>
          </div>
          <ErrorMessage message={error} />
        </Modal>
      )}
      {sessionExpired && (
        <Modal
          title="登录已过期"
          description="未保存文字已保留在当前浏览器会话中，重新登录后可继续。"
          onClose={() => {}}
          footer={
            <Button primary onClick={onSessionExpired}>
              重新登录
            </Button>
          }
        >
          <p>
            当前修改无法提交。重新登录同一账号并打开这部作品后，编辑区会恢复未保存的内容。
          </p>
        </Modal>
      )}
    </div>
  );
}
