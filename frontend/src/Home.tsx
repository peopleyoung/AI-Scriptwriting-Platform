import { useEffect, useState } from "react";
import {
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Clock3,
  Feather,
  Grid2X2,
  List,
  LockKeyhole,
  Monitor,
  Moon,
  Plus,
  Search,
  Smartphone,
  Sparkles,
  Sun,
  Trash2,
  Undo2,
  Users,
} from "lucide-react";
import {
  type Invitation,
  type Page,
  type Template,
  type Work,
  type User,
  roleNames,
} from "./types";
import {
  Brand,
  Button,
  Empty,
  ErrorMessage,
  IconButton,
  Modal,
  relativeTime,
} from "./ui";

export function Header({
  user,
  mode,
  page,
  onPage,
  onAccount,
  onHelp,
  onTheme,
  dark,
}: {
  user: User;
  mode: "demo" | "live";
  page?: Page;
  onPage: (page: Page) => void;
  onAccount: () => void;
  onHelp: () => void;
  onTheme: () => void;
  dark: boolean;
}) {
  return (
    <header className="app-header">
      <Brand onClick={() => onPage("works")} />
      <nav className="main-nav" aria-label="创作空间导航">
        {(
          [
            ["works", "我的作品"],
            ["templates", "剧本模板"],
            ["team", "团队协作"],
            ["trash", "回收站"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            className={page === key ? "active" : ""}
            onClick={() => onPage(key)}
          >
            {label}
          </button>
        ))}
      </nav>
      <div className="header-tools">
        {mode === "demo" && <span className="demo-badge">示例模式</span>}
        <IconButton
          label={dark ? "切换浅色主题" : "切换深色主题"}
          onClick={onTheme}
        >
          {dark ? <Sun /> : <Moon />}
        </IconButton>
        <IconButton label="创作指南" onClick={onHelp}>
          <BookOpen />
        </IconButton>
        <button
          className="avatar profile"
          onClick={onAccount}
          aria-label="账户设置"
        >
          {user.username.slice(0, 1)}
        </button>
      </div>
    </header>
  );
}
export default function Home({
  works,
  templates,
  invitations,
  page,
  onOpen,
  onNew,
  onTrash,
  onRestore,
  onAccept,
}: {
  works: Work[];
  templates: Template[];
  invitations: Invitation[];
  page: Page;
  onOpen: (work: Work) => void;
  onNew: (templateId?: string) => void;
  onTrash: (work: Work) => Promise<void>;
  onRestore: (work: Work) => Promise<void>;
  onAccept: (workId: string) => Promise<void>;
}) {
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [list, setList] = useState(false);
  const [sort, setSort] = useState("recent");
  const [deleting, setDeleting] = useState<Work | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    setFilter("all");
    setSearch("");
    setError("");
  }, [page]);
  const active = works.filter((work) => work.status === "ACTIVE");
  const pageWorks =
    page === "trash"
      ? works.filter((work) => work.status === "TRASHED")
      : page === "team"
        ? active.filter(
            (work) =>
              work.role !== "OWNER" ||
              work.members.some(
                (member) =>
                  member.role !== "OWNER" && member.status === "ACCEPTED",
              ),
          )
        : active;
  const selected = pageWorks
    .filter(
      (work) =>
        (filter !== "writing" || !work.releases.length) &&
        (filter !== "published" || work.releases.length) &&
        (filter !== "joined" || work.role !== "OWNER") &&
        `${work.title}${work.settings.genre_tags?.join("") || ""}`.includes(
          search,
        ),
    )
    .sort((a, b) =>
      sort === "name"
        ? a.title.localeCompare(b.title, "zh-CN")
        : new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
    );
  const resume = active
    .filter((work) => work.role === "OWNER")
    .sort(
      (a, b) =>
        new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
    )[0];
  const episode = resume?.draft.episodes?.at(-1);
  async function action(callback: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await callback();
      setDeleting(null);
    } catch (error) {
      setError(error instanceof Error ? error.message : "操作失败。");
    } finally {
      setBusy(false);
    }
  }
  const names = {
    works: "我的作品",
    team: "团队协作",
    templates: "剧本模板",
    trash: "回收站",
  };
  if (page === "templates")
    return (
      <main className="home-page">
        <div className="page-intro">
          <p className="eyebrow">A FRAME FOR YOUR IMAGINATION</p>
          <h1 className="serif">从适合你的剧本结构开始</h1>
          <p>为不同的故事，选择不同的叙事节奏。</p>
        </div>
        <div className="template-grid">
          {templates.map((template) => (
            <article className="template-card" key={template.id}>
              <div className="template-visual">
                <div
                  className={`screen ${template.slug.includes("vertical") ? "" : "landscape"}`}
                >
                  {template.slug.includes("vertical") ? (
                    <Smartphone />
                  ) : (
                    <Monitor />
                  )}
                  <span />
                  <span />
                  <span />
                </div>
              </div>
              <h2 className="serif">{template.name}</h2>
              <p>
                {template.slug.includes("vertical")
                  ? "快节奏、强冲突，每一集留下一个悬念。适合高密度的都市、爱情与悬疑故事。"
                  : "为人物留出呼吸的空间。用完整的情节铺陈，讲述人物的选择与关系的变化。"}
              </p>
              <div className="template-flow">
                创作简报 → 人物设定 → 故事大纲 → 正文
              </div>
              <Button primary onClick={() => onNew(template.id)}>
                用此模板创作
                <ArrowRight />
              </Button>
            </article>
          ))}
        </div>
        {!templates.length && (
          <Empty
            title="暂时没有可用的剧本模板"
            description="请联系管理员发布模板。"
          />
        )}
      </main>
    );
  return (
    <main className="home-page">
      <div className="page-intro">
        <p className="eyebrow">
          {page === "works"
            ? "YOUR STORIES, YOUR SPACE"
            : page === "team"
              ? "BETTER STORIES, TOGETHER"
              : "A SECOND CHANCE FOR YOUR STORY"}
        </p>
        <h1 className="serif">
          {page === "works" ? "今天，又有什么故事想说？" : names[page]}
        </h1>
        <p>
          {page === "works"
            ? "一个画面，一句对白，一个还没成形的念头。让我们从这里开始。"
            : page === "team"
              ? "让好的故事，在共同创作中发生。"
              : "作品保留 30 天，所有者可在此恢复。"}
        </p>
      </div>
      {page === "works" && (
        <>
          <section className="home-hero">
            <div>
              <p className="eyebrow">A LITTLE SPARK. A WHOLE NEW STORY.</p>
              <h2 className="serif">
                让每一个灵感，
                <br />
                都有<span>下一幕。</span>
              </h2>
              <p>从一个想法，到一部值得讲述的剧本。</p>
              <Button primary onClick={() => onNew()}>
                <Plus />
                开始新作品
              </Button>
            </div>
            <div className="hero-art" aria-hidden="true">
              <div className="orbit" />
              <div className="film-strip">
                <div />
                <div />
                <div />
              </div>
              <span className="hero-spark">
                <Sparkles />
              </span>
              <span className="hero-art-note">THE NEXT SCENE IS YOURS.</span>
            </div>
          </section>
          {resume && (
            <section className="continue-card">
              <span className="continue-icon">
                <Feather />
              </span>
              <div>
                <strong>继续《{resume.title}》的故事</strong>
                <p>
                  {episode
                    ? `第 ${String(episode.number).padStart(2, "0")} 集 · ${episode.title || "正文创作"}`
                    : "从创作简报开始"}
                  <span>上次编辑的地方，等你回来。</span>
                </p>
              </div>
              <Button small onClick={() => onOpen(resume)}>
                继续创作
                <ArrowRight />
              </Button>
            </section>
          )}
        </>
      )}
      {page === "team" && invitations.length > 0 && (
        <section className="invitation-list" aria-label="待接受的邀请">
          <h2>有人邀请你一起创作</h2>
          {invitations.map((invitation) => (
            <div className="invitation" key={invitation.work_id}>
              <Users />
              <div>
                <strong>{invitation.title}</strong>
                <p>邀请你以{roleNames[invitation.role]}身份加入</p>
              </div>
              <Button
                small
                primary
                disabled={busy}
                onClick={() => action(() => onAccept(invitation.work_id))}
              >
                接受邀请
              </Button>
            </div>
          ))}
        </section>
      )}
      <section className="works-section">
        <div className="section-heading">
          <h2>
            {names[page]}
            <span className="count">{pageWorks.length}</span>
          </h2>
          <div className="row">
            <label className="search-box">
              <Search />
              <input
                type="search"
                aria-label="搜索作品"
                placeholder="搜索作品"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </label>
            {page !== "trash" && (
              <Button small onClick={() => onNew()}>
                <Plus />
                新建作品
              </Button>
            )}
          </div>
        </div>
        {page !== "trash" && (
          <div className="filter-bar">
            <div className="filters" aria-label="作品筛选">
              {[
                ["all", "全部作品"],
                ["writing", "创作中"],
                ["published", "已发布"],
                ["joined", "我参与的"],
              ].map(([key, label]) => (
                <button
                  key={key}
                  className={filter === key ? "active" : ""}
                  aria-pressed={filter === key}
                  onClick={() => setFilter(key)}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="row view-controls">
              <select
                aria-label="作品排序"
                value={sort}
                onChange={(event) => setSort(event.target.value)}
              >
                <option value="recent">最近编辑</option>
                <option value="name">作品名称</option>
              </select>
              <IconButton
                label="网格显示"
                onClick={() => setList(false)}
                aria-pressed={!list}
              >
                <Grid2X2 />
              </IconButton>
              <IconButton
                label="列表显示"
                onClick={() => setList(true)}
                aria-pressed={list}
              >
                <List />
              </IconButton>
            </div>
          </div>
        )}
        <ErrorMessage message={error} />
        <div className={list ? "work-list" : "work-grid"}>
          {selected.map((work) => (
            <article className="work-card" key={work.id}>
              <button
                className={`work-cover cover-${work.art}`}
                onClick={() =>
                  page === "trash"
                    ? action(() => onRestore(work))
                    : onOpen(work)
                }
                disabled={busy}
                aria-label={`${page === "trash" ? "恢复" : "打开"}${work.title}`}
              >
                <img src={`/art/${work.art}.svg`} alt="" />
                <span className="cover-format">{work.templateName}</span>
                <span className="cover-title serif">
                  {work.title}
                  <small>
                    {work.title === "雨夜来信"
                      ? "LETTERS IN THE RAIN"
                      : work.title === "第二次心动"
                        ? "LOVE, ONCE MORE"
                        : work.title === "潮汐之间"
                          ? "BETWEEN THE TIDES"
                          : "A STORY BY YOU"}
                  </small>
                </span>
              </button>
              <div className="work-card-body">
                <div className="row between">
                  <span className="genres">
                    {work.settings.genre_tags?.join(" / ") || "题材未设定"}
                  </span>
                  <span className="work-status">
                    {page === "trash"
                      ? "回收站"
                      : work.releases.length
                        ? "已发布"
                        : Object.values(work.creative.confirmed).filter(Boolean)
                              .length === 3
                          ? "正文创作"
                          : "设定中"}
                  </span>
                </div>
                <p className="work-details">
                  {work.settings.episode_count || 1} 集 ·{" "}
                  {work.settings.episode_duration_minutes
                    ? `${work.settings.episode_duration_minutes} 分钟 / 集`
                    : "按字数目标创作"}{" "}
                  · {roleNames[work.role]}
                </p>
                <footer className="work-card-footer">
                  <span>
                    <Clock3 />
                    {relativeTime(work.updated_at)}
                  </span>
                  <div className="row">
                    {page === "trash" ? (
                      <Button
                        small
                        onClick={() => action(() => onRestore(work))}
                        disabled={busy}
                      >
                        <Undo2 />
                        恢复
                      </Button>
                    ) : (
                      <>
                        {work.role === "OWNER" && (
                          <IconButton
                            label={`将${work.title}移入回收站`}
                            onClick={() => setDeleting(work)}
                          >
                            <Trash2 />
                          </IconButton>
                        )}
                        <IconButton
                          label={`打开${work.title}的工作台`}
                          onClick={() => onOpen(work)}
                        >
                          <ArrowUpRight />
                        </IconButton>
                      </>
                    )}
                  </div>
                </footer>
              </div>
            </article>
          ))}
          {!selected.length && (
            <Empty
              title={
                search
                  ? "没有找到相关作品"
                  : page === "trash"
                    ? "回收站是空的"
                    : page === "team"
                      ? "还没有协作作品"
                      : "你的第一个故事，从这里开始"
              }
              description={
                search
                  ? "试试其他作品名称或题材。"
                  : page === "team"
                    ? "接受作品所有者的邀请后，可以一起参与创作。"
                    : undefined
              }
            >
              {page === "works" && (
                <Button primary onClick={() => onNew()}>
                  <Plus />
                  开始新作品
                </Button>
              )}
            </Empty>
          )}
        </div>
      </section>
      <footer className="home-footer">
        <span>每一个好故事，都始于一次勇敢的落笔。</span>
        <span>幕间 · AI 剧本创作空间</span>
      </footer>
      {deleting && (
        <Modal
          title="将作品移入回收站？"
          description="作品在 30 天内可以恢复。"
          onClose={() => setDeleting(null)}
          busy={busy}
          footer={
            <>
              <Button onClick={() => setDeleting(null)} disabled={busy}>
                取消
              </Button>
              <Button
                primary
                onClick={() => action(() => onTrash(deleting))}
                disabled={busy}
              >
                移入回收站
              </Button>
            </>
          }
        >
          <p>
            《{deleting.title}》进入回收站后，协作者无法访问，未完成的模型调用将停止。
          </p>
          <ErrorMessage message={error} />
        </Modal>
      )}
    </main>
  );
}
