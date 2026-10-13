import { demoTemplates, demoUser, sampleScript, seedWorks } from "./demo";
import { stages } from "./types";
import type {
  Consent,
  Creative,
  Invitation,
  Model,
  NewWork,
  Proposal,
  Provider,
  Script,
  Stage,
  Template,
  User,
  Version,
  Work,
} from "./types";

const STORAGE = "mujian-demo-v2";
const uid = () => crypto.randomUUID();
const now = () => new Date().toISOString();
const clone = <T>(value: T): T => structuredClone(value);
const errorMessages: Record<string, string> = {
  INVALID_CREDENTIALS: "账号或密码不正确。",
  ACCOUNT_DISABLED: "账号已停用，请联系管理员。",
  PASSWORD_CHANGE_REQUIRED: "请先修改初始密码。",
  REVISION_CONFLICT:
    "作品已有新修改。请重新打开作品后再操作；当前未保存内容仍保留在编辑区。",
  WORK_REVISION_CONFLICT: "作品已有新修改，请重新打开后再操作。",
  SETTINGS_NOT_CONFIRMED: "请先依次确认创作简报、人物与故事大纲。",
  CONSENT_INVALID: "外发确认已失效，请重新确认。",
  WORK_ACCESS_DENIED: "你没有这部作品的操作权限。",
  CSRF_ORIGIN_REJECTED: "请求来源校验失败，请通过前端代理访问服务。",
  PROPOSAL_ALREADY_PENDING: "这项设定已有待审批提案。",
  GENERATION_ALREADY_RUNNING: "这部作品已有生成任务，请稍后再试。",
  RELEASE_REQUIRED: "请先发布一个正式稿。",
  USER_NOT_FOUND: "该账号不存在，请确认登录名。",
  STAGE_ORDER_INVALID: "请按创作简报、人物、故事大纲的顺序确认。",
};
export class ApiError extends Error {
  constructor(
    message: string,
    public code: string,
    public status: number,
  ) {
    super(message);
  }
}
export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
  headers: Record<string, string> = {},
): Promise<T> {
  const csrf = document.cookie
    .split("; ")
    .find((item) => item.startsWith("scriptwriting_csrf="))
    ?.split("=")
    .slice(1)
    .join("=");
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      method,
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        ...headers,
        ...(csrf ? { "X-CSRF-Token": decodeURIComponent(csrf) } : {}),
      },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
  } catch {
    throw new Error("无法连接服务，请确认后端已启动。");
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const detail = payload.detail;
    const code =
      typeof detail === "object" && !Array.isArray(detail)
        ? detail?.code
        : payload.code;
    throw new ApiError(
      errorMessages[code] ||
        (response.status === 401
          ? "登录已过期，请重新登录。"
          : response.status >= 500
            ? "服务暂时不可用，请稍后重试。"
            : typeof detail === "string"
              ? detail
              : detail?.message || "操作未完成，请检查输入后重试。"),
      code || "REQUEST_FAILED",
      response.status,
    );
  }
  return response.status === 204 ? (undefined as T) : response.json();
}
export class Studio {
  works: Work[] = [];
  templates: Template[] = [];
  invitations: Invitation[] = [];
  private consents = new Map<string, Consent>();
  models: Model[] = [];
  providers: Provider[] = [];
  constructor(
    public mode: "demo" | "live",
    public user: User = demoUser,
  ) {}
  async init() {
    if (this.mode === "demo") {
      try {
        const saved = JSON.parse(localStorage.getItem(STORAGE) || "null");
        this.works =
          Array.isArray(saved) &&
          saved.every(
            (item) =>
              item.id &&
              item.creative &&
              Array.isArray(item.versions) &&
              item.draft,
          )
            ? saved
            : seedWorks();
      } catch {
        this.works = seedWorks();
      }
      this.templates = demoTemplates;
      this.models = [
        {
          id: "demo-model",
          provider_id: "demo-provider",
          model_name: "幕间创作助手（模拟）",
          version: 1,
          parameters: {},
        },
      ];
      this.providers = [
        {
          id: "demo-provider",
          name: "本地示例",
          adapter: "mock",
          retention_description: "所有交互仅在浏览器内模拟，不发送至外部模型。",
          deletion_description:
            "示例内容保存在当前浏览器，可在账户面板中重置。",
          incident_description: "这是交互示例，不产生真实模型调用。",
        },
      ];
    } else {
      [this.templates, this.models, this.providers] = await Promise.all([
        api<Template[]>("/templates"),
        api<Model[]>("/model-configs"),
        api<Provider[]>("/providers"),
      ]);
      const [works, trash, invitations] = await Promise.all([
        api<Partial<Work>[]>("/works"),
        api<Partial<Work>[]>("/trash"),
        api<Invitation[]>("/invitations"),
      ]);
      this.invitations = invitations;
      this.works = [
        ...(await Promise.all(
          works.map((work) => this.fetchWork(work.id!, work)),
        )),
        ...trash.map(
          (work) =>
            ({
              ...work,
              role: "OWNER",
              templateName: "中文短剧",
              art: "rain",
              creative: {
                brief: null,
                characters: null,
                outline: null,
                confirmed: {},
              },
              draft: {},
              versions: [],
              releases: [],
              conversations: [],
              proposals: [],
              members: [],
            }) as Work,
        ),
      ];
    }
    return this.works;
  }
  private persist() {
    if (this.mode === "demo") {
      try {
        localStorage.setItem(STORAGE, JSON.stringify(this.works));
      } catch {
        throw new Error(
          "浏览器存储空间不足。当前修改已保留在会话中，请及时导出正式稿。",
        );
      }
    }
  }
  private async fetchWork(id: string, base?: Partial<Work>): Promise<Work> {
    const [workspace, detail, proposals] = await Promise.all([
      api<{
        work: Partial<Work>;
        creative: Creative;
        draft: Script;
        versions: Work["versions"];
        releases: Work["releases"];
        conversations: Work["conversations"];
      }>(`/works/${id}/workspace`),
      base?.members
        ? Promise.resolve(base)
        : api<Partial<Work>>(`/works/${id}`),
      api<Proposal[]>(`/works/${id}/change-proposals`),
    ]);
    const template = this.templates.find(
      (item) => item.published_version?.id === detail.template_version_id,
    );
    return {
      ...detail,
      ...workspace.work,
      ...workspace,
      ...base,
      ...workspace.work,
      members: detail.members || [],
      settings: detail.settings || base?.settings || {},
      updated_at: detail.updated_at || now(),
      templateName: template?.name || "中文短剧",
      art:
        this.works.find((w) => w.id === id)?.art ||
        (template?.slug === "web-short-drama" ? "ocean" : "rain"),
      template_definition:
        detail.template_definition || template?.published_version?.definition,
      proposals,
    } as Work;
  }
  async refresh(id: string) {
    const work =
      this.mode === "demo"
        ? this.works.find((w) => w.id === id)!
        : await this.fetchWork(id);
    this.works = this.works.map((w) => (w.id === id ? work : w));
    return clone(work);
  }
  private finish(work: Work, bump = true) {
    if (bump) {
      work.revision++;
      work.updated_at = now();
    }
    this.persist();
    return clone(work);
  }
  private work(id: string) {
    const work = this.works.find((w) => w.id === id);
    if (!work) throw new Error("作品不存在。");
    return work;
  }
  async create(input: NewWork) {
    if (this.mode === "live") {
      const base = await api<Work>("/works", "POST", input);
      const work = await this.fetchWork(base.id, base);
      this.works.unshift(work);
      return clone(work);
    }
    const template = this.templates.find((t) => t.id === input.template_id)!;
    const work: Work = {
      ...clone(seedWorks()[0]),
      id: uid(),
      title: input.title,
      settings: input.settings,
      templateName: template.name,
      template_version_id: template.published_version!.id,
      template_definition: template.published_version!.definition,
      art: "rain",
      revision: 0,
      updated_at: now(),
      creative: { brief: null, characters: null, outline: null, confirmed: {} },
      draft: {},
      versions: [],
      releases: [],
      conversations: [],
      proposals: [],
    };
    this.works.unshift(work);
    this.persist();
    return clone(work);
  }
  async saveDraft(
    id: string,
    content: Script,
    revision: number,
    autosave = false,
  ) {
    if (this.mode === "live") {
      await api(`/works/${id}/draft`, "PATCH", {
        content,
        expected_revision: revision,
        autosave,
      });
      return this.refresh(id);
    }
    const work = this.work(id);
    work.draft = clone(content);
    return this.finish(work);
  }
  async saveVersion(
    id: string,
    content: Script,
    revision: number,
    source = "MANUAL_SAVE",
  ) {
    if (this.mode === "live") {
      await api(`/works/${id}/versions`, "POST", {
        content,
        expected_revision: revision,
      });
      return this.refresh(id);
    }
    const work = this.work(id);
    work.draft = clone(content);
    const version: Version = {
      id: uid(),
      source,
      content: clone(content),
      created_at: now(),
      work_revision: work.revision + 1,
    };
    work.versions.push(version);
    return this.finish(work);
  }
  async saveCreative(
    id: string,
    stage: Stage,
    content: unknown,
    confirmed: boolean,
  ) {
    if (this.mode === "live") {
      await api(
        confirmed
          ? `/works/${id}/change-proposals`
          : `/works/${id}/creative/${stage}`,
        confirmed ? "POST" : "PATCH",
        confirmed
          ? { stage, content }
          : { content, expected_revision: this.work(id).revision },
      );
      return this.refresh(id);
    }
    const work = this.work(id);
    if (confirmed) {
      if (
        work.proposals.some((p) => p.stage === stage && p.status === "PENDING")
      )
        throw new Error("这项设定已有待审批提案。");
      work.proposals.push({
        id: uid(),
        stage,
        content: clone(content) as Proposal["content"],
        status: "PENDING",
      });
      return this.finish(work, false);
    }
    Object.assign(work.creative, { [stage]: clone(content) });
    return this.finish(work);
  }
  async confirm(id: string, stage: Stage) {
    if (this.mode === "live") {
      await api(`/works/${id}/creative/${stage}/confirm`, "POST", undefined, {
        "If-Match": String(this.work(id).revision),
      });
      return this.refresh(id);
    }
    const work = this.work(id);
    const index = stages.indexOf(stage);
    if (index && !work.creative.confirmed[stages[index - 1]])
      throw new Error("请按顺序确认创作设定。");
    if (!work.creative[stage])
      throw new Error("请先填写并保存这一阶段的设定。");
    work.creative.confirmed[stage] = clone(work.creative[stage]);
    return this.finish(work);
  }
  async reviewProposal(id: string, proposalId: string, approve: boolean) {
    if (this.mode === "live") {
      await api(
        `/works/${id}/change-proposals/${proposalId}/${approve ? "approve" : "reject"}`,
        "POST",
      );
      return this.refresh(id);
    }
    const work = this.work(id);
    const proposal = work.proposals.find((p) => p.id === proposalId)!;
    proposal.status = approve ? "APPROVED" : "REJECTED";
    if (approve) {
      Object.assign(work.creative, {
        [proposal.stage]: clone(proposal.content),
      });
      work.creative.confirmed[proposal.stage] = clone(proposal.content);
    }
    return this.finish(work, approve);
  }
  async grantConsent(id: string, modelId: string): Promise<Consent> {
    const work = this.work(id);
    const data_categories = ["conversation", "settings", "outline", "script"];
    if (this.mode === "live")
      return api(`/works/${id}/consents`, "POST", {
        model_config_version_id: modelId,
        input_revision: work.revision,
        data_categories,
      });
    const consent = {
      id: uid(),
      input_revision: work.revision,
      model_config_version_id: modelId,
      data_categories,
      status: "ACTIVE",
    };
    this.consents.set(id, consent);
    return consent;
  }
  async existingConsent(
    id: string,
    modelId: string,
  ): Promise<Consent | undefined> {
    if (this.mode === "demo") {
      const consent = this.consents.get(id);
      return consent?.input_revision === this.work(id).revision &&
        consent.model_config_version_id === modelId
        ? consent
        : undefined;
    }
    const consents = await api<Consent[]>(`/works/${id}/consents`);
    return consents.find(
      (c) =>
        c.status === "ACTIVE" &&
        c.input_revision === this.work(id).revision &&
        c.model_config_version_id === modelId &&
        ["conversation", "settings", "outline", "script"].every((category) =>
          c.data_categories.includes(category),
        ),
    );
  }
  async chat(id: string, message: string, consent: Consent) {
    if (this.mode === "live") {
      await api(`/works/${id}/conversations`, "POST", {
        message,
        consent_id: consent.id,
      });
      return this.refresh(id);
    }
    await new Promise((resolve) => setTimeout(resolve, 800));
    const work = this.work(id);
    const reply = /人物|角色/.test(message)
      ? "可以先为每个人物确定三个要素：想得到什么、害怕失去什么、绝不会承认什么。\n\n把人物的目标放在冲突两端，再让他们必须一起解决一个问题。这样，关系的变化就会自然推动情节。你可以在人物设定中记录这些方向，再决定是否确认。"
      : /悬念|冲突|重写/.test(message)
        ? "建议让信息比对白慢一步出现。\n\n角色说“没见过”，手却下意识护住旧照片；角色想要离开，门外偏偏传来熟悉的声音。用一个可见的动作制造矛盾，再让下一场景回答它。\n\n你可以选中正文的一个片段，使用“重写片段”来体验这个流程。"
        : "这个方向可以从“人物的选择”展开。\n\n先给主角一个清晰的目标，再设置一个必须付出代价才能跨越的障碍。每一集解决一个小问题，同时留下一个更大的疑问。\n\n我们可以先把这个想法写入创作简报，再依次完善人物与故事大纲。";
    work.conversations.push({
      id: uid(),
      user_message: message,
      assistant_message: reply,
      created_at: now(),
    });
    return this.finish(work);
  }
  async generate(
    id: string,
    scope: "EPISODE" | "SCENE" | "FRAGMENT",
    episode: number,
    scene: number,
    fragment: string,
    instruction: string,
    consent: Consent,
    onJob?: (id: string) => void,
  ) {
    const work = this.work(id);
    if (!stages.every((stage) => work.creative.confirmed[stage]))
      throw new Error("请先确认全部三个创作阶段。");
    if (this.mode === "live") {
      const job = await api<{
        id: string;
        status: string;
        error_code: string | null;
      }>(`/works/${id}/generation-jobs`, "POST", {
        target_type: scope,
        target_path:
          scope === "EPISODE"
            ? { episode }
            : scope === "SCENE"
              ? { episode, scene }
              : { episode, scene, fragment },
        instruction,
        consent_id: consent.id,
        model_config_version_id: consent.model_config_version_id,
        expected_revision: work.revision,
        input_revision: work.revision,
        idempotency_key: uid(),
        auto_run: true,
      });
      onJob?.(job.id);
      return this.waitForJob(id, job.id);
    }
    await new Promise((resolve) => setTimeout(resolve, 1200));
    const content = clone(work.draft);
    const sample = sampleScript(work.title, episode).episodes![0];
    if (scope === "EPISODE") {
      const index =
        content.episodes?.findIndex((e) => e.number === episode) ?? -1;
      if (index >= 0) content.episodes![index] = sample;
      else {
        content.episodes ||= [];
        content.episodes.push(sample);
        content.episodes.sort((a, b) => a.number - b.number);
      }
    } else if (scope === "SCENE") {
      const target = content.episodes?.find(
        (e) => e.number === episode,
      )?.scenes;
      if (!target) throw new Error("请先选择一集。");
      const generated = { ...sample.scenes[0], number: scene };
      const index = target.findIndex((s) => s.number === scene);
      if (index >= 0) target[index] = generated;
      else target.push(generated);
    } else {
      const target = content.episodes
        ?.find((e) => e.number === episode)
        ?.scenes.find((s) => s.number === scene)
        ?.fragments.find((f) => f.id === fragment);
      if (!target) throw new Error("请先选择一个已有片段。");
      target.blocks = [
        { type: "SCENE_HEADING", text: "外景 · 旧书店 · 夜" },
        {
          type: "ACTION",
          text: "铜铃响了。陆川没有回头，只是把手里的书翻到了最后一页。那一页，夹着一张边角泛黄的照片。",
        },
        { type: "CHARACTER", text: "沈念" },
        { type: "DIALOGUE", text: "我是不是……来过这里？" },
        {
          type: "ACTION",
          text: "陆川合上书，拇指压在照片露出的角上。窗外，一道车灯掠过他的脸。",
        },
        { type: "CHARACTER", text: "陆川" },
        { type: "DIALOGUE", text: "这条街的书店，都差不多。" },
        {
          type: "ACTION",
          text: "沈念没有接话。她看见了他的手——正在轻轻发抖。雨声填满了两人之间的空白。",
        },
      ];
    }
    return this.saveVersion(id, content, work.revision, "GENERATION");
  }
  async jobs(id: string): Promise<import("./types").Job[]> {
    return this.mode === "live" ? api(`/works/${id}/generation-jobs`) : [];
  }
  async cancelJob(id: string, jobId: string) {
    if (this.mode === "live")
      await api(`/works/${id}/generation-jobs/${jobId}/cancel`, "POST");
  }
  async retryJob(id: string, jobId: string) {
    await api(`/works/${id}/generation-jobs/${jobId}/retry`, "POST");
    return this.waitForJob(id, jobId);
  }
  private async waitForJob(id: string, jobId: string) {
    for (;;) {
      const job = await api<import("./types").Job>(
        `/works/${id}/generation-jobs/${jobId}`,
      );
      if (job.status === "SUCCEEDED") return this.refresh(id);
      if (job.status === "CANCELED")
        throw new Error("生成任务已取消，原正文保留。");
      if (job.status === "FAILED")
        throw new Error(
          errorMessages[job.error_code || ""] ||
            "生成失败，原正文保留。请在生成任务中查看或重试。",
        );
      await new Promise((resolve) => setTimeout(resolve, 700));
    }
  }
  async updateSettings(id: string, title: string, settings: Work["settings"]) {
    const work = this.work(id);
    if (this.mode === "live") {
      await api(`/works/${id}/settings`, "PATCH", {
        title,
        settings,
        expected_revision: work.revision,
      });
      return this.refresh(id);
    }
    work.title = title;
    work.settings = structuredClone(settings);
    return this.finish(work);
  }
  async memberRole(id: string, userId: string, role: "EDITOR" | "VIEWER") {
    if (this.mode === "live") {
      await api(`/works/${id}/members/${userId}`, "PATCH", { role });
      return this.refresh(id);
    }
    const work = this.work(id);
    work.members.find((m) => m.user_id === userId)!.role = role;
    return this.finish(work, false);
  }
  async transfer(id: string, userId: string) {
    if (this.mode === "live") {
      await api(`/works/${id}/transfer-ownership`, "POST", { user_id: userId });
      return this.refresh(id);
    }
    const work = this.work(id);
    work.members.find((m) => m.role === "OWNER")!.role = "EDITOR";
    work.members.find((m) => m.user_id === userId)!.role = "OWNER";
    work.owner_id = userId;
    work.role = "EDITOR";
    return this.finish(work);
  }
  async restoreVersion(id: string, versionId: string) {
    const work = this.work(id);
    if (this.mode === "live") {
      await api(
        `/works/${id}/versions/${versionId}/restore?expected_revision=${work.revision}`,
        "POST",
      );
      return this.refresh(id);
    }
    return this.saveVersion(
      id,
      work.versions.find((v) => v.id === versionId)!.content,
      work.revision,
      "RESTORE",
    );
  }
  async publish(id: string, versionId: string) {
    if (this.mode === "live") {
      await api(`/works/${id}/releases`, "POST", { version_id: versionId });
      return this.refresh(id);
    }
    const work = this.work(id);
    const version = work.versions.find((v) => v.id === versionId)!;
    work.releases.forEach((r) => (r.is_current = false));
    work.releases.push({
      id: uid(),
      version_id: versionId,
      is_current: true,
      created_at: now(),
      version: clone(version),
    });
    return this.finish(work, false);
  }
  async invite(id: string, username: string, role: "EDITOR" | "VIEWER") {
    if (this.mode === "live") {
      await api(`/works/${id}/members/invitations`, "POST", { username, role });
      return this.refresh(id);
    }
    const work = this.work(id);
    if (
      work.members.some(
        (m) => m.username === username && m.status !== "REVOKED",
      )
    )
      throw new Error("该成员已经在作品中。");
    work.members.push({
      id: uid(),
      user_id: uid(),
      username,
      role,
      status: "PENDING",
    });
    return this.finish(work, false);
  }
  async revoke(id: string, userId: string) {
    if (this.mode === "live") {
      await api(`/works/${id}/members/${userId}/revoke`, "POST");
      return this.refresh(id);
    }
    const work = this.work(id);
    work.members.find((m) => m.user_id === userId)!.status = "REVOKED";
    return this.finish(work, false);
  }
  async trash(id: string, restore = false) {
    const work = this.work(id);
    if (this.mode === "live") {
      await api(`/works/${id}/${restore ? "restore" : "trash"}`, "POST");
      if (restore) return this.refresh(id);
    }
    work.status = restore ? "ACTIVE" : "TRASHED";
    return this.finish(work);
  }
  async export(id: string, format: "markdown" | "docx", releaseId: string) {
    const work = this.work(id);
    if (this.mode === "live") {
      const result = await api<{ download_url: string | null }>(
        `/works/${id}/exports`,
        "POST",
        { format, release_id: releaseId },
      );
      if (!result.download_url) throw new Error("导出暂未完成，请重试。");
      const url = new URL(result.download_url, window.location.origin);
      const response = await fetch(url.pathname + url.search, {
        credentials: "same-origin",
      });
      if (!response.ok)
        throw new Error("下载失败，链接可能已失效，请重新导出。");
      download(
        await response.blob(),
        `${work.title}.${format === "markdown" ? "md" : "docx"}`,
      );
      return;
    }
    const release = work.releases.find((r) => r.id === releaseId);
    if (!release) throw new Error("请先发布正式稿。");
    if (format === "markdown")
      download(
        new Blob([scriptText(work.title, release.version.content)], {
          type: "text/markdown;charset=utf-8",
        }),
        `${work.title}.md`,
      );
    else {
      const { Document, Packer, Paragraph, TextRun } = await import("docx");
      const paragraphs = scriptText(work.title, release.version.content)
        .split("\n")
        .map(
          (text) =>
            new Paragraph({
              children: [new TextRun(text.replace(/^#+ /, ""))],
            }),
        );
      download(
        await Packer.toBlob(
          new Document({ sections: [{ children: paragraphs }] }),
        ),
        `${work.title}.docx`,
      );
    }
  }
  async acceptInvitation(workId: string) {
    if (this.mode === "live") {
      await api(`/works/${workId}/members/accept`, "POST");
      await this.init();
    }
  }
  resetDemo() {
    this.works = seedWorks();
    this.consents.clear();
    this.persist();
  }
}
export function scriptText(title: string, content: Script): string {
  return [
    `# ${title}`,
    ...(content.episodes || []).flatMap((e) => [
      `\n## 第 ${e.number} 集 · ${e.title || ""}`,
      ...e.scenes.flatMap((s) => [
        `\n### 场景 ${s.number} · ${s.heading || ""}`,
        ...s.fragments.flatMap((f) =>
          f.blocks.map((b) =>
            b.type === "CHARACTER" ? `\n**${b.text}**` : b.text,
          ),
        ),
      ]),
    ]),
  ].join("\n\n");
}
function download(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
