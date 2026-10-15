import { useCallback, useEffect, useRef, useState } from "react";
import { BookOpen, LogOut, RotateCcw, Sparkles } from "lucide-react";
import Auth from "./Auth";
import Admin, { PasswordForm } from "./Admin";
import Home, { Header } from "./Home";
import Workspace from "./Workspace";
import { CreateWork } from "./Forms";
import { api, ApiError, Studio } from "./studio";
import type { Page, User, Work } from "./types";
import { Button, ErrorMessage, Modal, Spinner } from "./ui";

export default function App() {
  const [studio, setStudio] = useState<Studio | null>(null);
  const [works, setWorks] = useState<Work[]>([]);
  const [workId, setWorkId] = useState<string | null>(null);
  const [page, setPage] = useState<Page>("works");
  const [loading, setLoading] = useState(true);
  const [initialUser, setInitialUser] = useState<User | null>(null);
  const [modal, setModal] = useState<
    "create" | "account" | "help" | "admin" | null
  >(null);
  const [templateId, setTemplateId] = useState<string>();
  const [error, setError] = useState("");
  const [accountBusy, setAccountBusy] = useState(false);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [toast, setToast] = useState("");
  const [dark, setDark] = useState(
    () => localStorage.getItem("mujian-theme") === "dark",
  );
  const toastTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const mounted = useRef(true);
  const notify = useCallback((message: string) => {
    setToast(message);
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(""), 4500);
  }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    localStorage.setItem("mujian-theme", dark ? "dark" : "light");
  }, [dark]);
  const start = useCallback(
    async (
      next: Studio,
      resume = false,
      active: () => boolean = () => true,
    ) => {
      await next.init();
      if (!mounted.current || !active()) return;
      setStudio(next);
      setWorks([...next.works]);
      setInitialUser(null);
      setModal(null);
      setError("");
      sessionStorage.setItem("mujian-mode", next.mode);
      const requested = resume
        ? decodeURIComponent(location.hash.replace(/^#work\//, ""))
        : "";
      setWorkId(
        next.works.some(
          (work) => work.id === requested && work.status === "ACTIVE",
        )
          ? requested
          : null,
      );
      setPage("works");
    },
    [],
  );
  useEffect(() => {
    mounted.current = true;
    let active = true;
    async function init() {
      try {
        if (
          new URLSearchParams(location.search).get("demo") === "1" ||
          sessionStorage.getItem("mujian-mode") === "demo"
        )
          await start(new Studio("demo"), true, () => active);
        else {
          const user = await api<User>("/auth/me");
          await start(new Studio("live", user), true, () => active);
        }
      } catch (error) {
        if (
          active &&
          !(error instanceof ApiError && [401, 403].includes(error.status))
        )
          setError(error instanceof Error ? error.message : "加载失败。");
      } finally {
        if (mounted.current && active) setLoading(false);
      }
    }
    void init();
    return () => {
      active = false;
      mounted.current = false;
      clearTimeout(toastTimer.current);
    };
  }, [start]);
  const current = works.find((work) => work.id === workId);
  function update(work: Work) {
    setWorks((previous) =>
      previous.map((item) => (item.id === work.id ? work : item)),
    );
  }
  function open(work: Work) {
    setWorkId(work.id);
    history.replaceState(null, "", `#work/${encodeURIComponent(work.id)}`);
  }
  function go(next: Page) {
    setWorkId(null);
    setPage(next);
    history.replaceState(null, "", location.pathname + location.search);
  }
  async function demo() {
    setLoading(true);
    try {
      await start(new Studio("demo"));
    } catch (error) {
      notify(String(error));
    } finally {
      setLoading(false);
    }
  }
  async function logout() {
    if (accountBusy || !studio) return;
    setAccountBusy(true);
    setError("");
    try {
      if (studio.mode === "live") await api("/auth/logout", "POST");
      sessionStorage.removeItem("mujian-mode");
      history.replaceState(null, "", location.pathname);
      setStudio(null);
      setWorkId(null);
      setWorks([]);
      setModal(null);
      setInitialUser(null);
    } catch (error) {
      setError(error instanceof Error ? error.message : "退出失败。");
    } finally {
      setAccountBusy(false);
    }
  }
  if (loading) return <Spinner />;
  if (!studio)
    return (
      <>
        <Auth
          initialUser={initialUser}
          onLogin={async (user) => {
            try {
              await start(new Studio("live", user));
            } catch (error) {
              setInitialUser(user);
              throw error;
            }
          }}
          onDemo={demo}
        />
        {error && (
          <div className="connection-note" role="status">
            {error}
          </div>
        )}
      </>
    );
  const headerProps = {
    user: studio.user,
    mode: studio.mode,
    page: current ? undefined : page,
    onPage: go,
    onAccount: () => {
      setError("");
      setPasswordOpen(false);
      setModal("account");
    },
    onHelp: () => setModal("help"),
    dark,
    onTheme: () => setDark(!dark),
  };
  return (
    <div className="application">
      {current ? (
        <Workspace
          key={`${studio.mode}:${current.id}`}
          studio={studio}
          work={current}
          onUpdate={update}
          onBack={go}
          headerProps={headerProps}
          notify={notify}
          onSessionExpired={() => {
            setInitialUser(null);
            setStudio(null);
            setWorkId(null);
            sessionStorage.removeItem("mujian-mode");
          }}
        />
      ) : (
        <>
          <Header {...headerProps} />
          <Home
            works={works}
            templates={studio.templates}
            invitations={studio.invitations}
            page={page}
            onOpen={open}
            onNew={(id) => {
              setTemplateId(id);
              setModal("create");
            }}
            onTrash={async (work) => {
              update(await studio.trash(work.id));
              notify("作品已移入回收站。");
            }}
            onRestore={async (work) => {
              update(await studio.trash(work.id, true));
              notify("作品已恢复，可继续创作。");
            }}
            onAccept={async (id) => {
              await studio.acceptInvitation(id);
              setWorks([...studio.works]);
              notify("邀请已接受，可以开始协作。");
            }}
          />
        </>
      )}
      {modal === "create" && (
        <CreateWork
          templates={studio.templates}
          initialTemplate={templateId}
          onClose={() => setModal(null)}
          onCreate={async (input) => {
            const work = await studio.create(input);
            setWorks([...studio.works]);
            setModal(null);
            open(work);
            notify("作品已创建，从创作简报开始吧。");
          }}
        />
      )}
      {modal === "admin" && (
        <Admin
          studio={studio}
          onClose={() => setModal(null)}
          onRefresh={() => setWorks([...studio.works])}
          onRestored={() => {
            setWorkId(null);
            history.replaceState(null, "", location.pathname + location.search);
          }}
        />
      )}
      {modal === "account" && (
        <Modal
          title="你的创作空间"
          description={`${studio.user.username} · ${studio.user.role === "ADMIN" ? "管理员" : "普通用户"}`}
          onClose={() => {
            setPasswordOpen(false);
            setModal(null);
          }}
          busy={accountBusy}
          footer={
            <>
              <Button
                onClick={() => {
                  setPasswordOpen(false);
                  setModal(null);
                }}
                disabled={accountBusy}
              >
                继续创作
              </Button>
              <Button onClick={logout} disabled={accountBusy}>
                <LogOut />
                {studio.mode === "demo" ? "登录真实账号" : "退出登录"}
              </Button>
            </>
          }
        >
          <div className="notice">
            {studio.mode === "demo"
              ? "当前为示例模式。示例作品仅保存在这个浏览器内，创作回复由本地模拟。"
              : "使用真实账号会话。作品保存到创作服务，操作权限由作品成员角色决定。"}
          </div>
          <div className="summary-row">
            <span>作品数量</span>
            <span>
              {works.filter((work) => work.status === "ACTIVE").length} 部
            </span>
          </div>
          {studio.mode === "demo" && (
            <Button
              small
              onClick={() => {
                studio.resetDemo();
                setWorks([...studio.works]);
                setWorkId(null);
                setModal(null);
                notify("示例作品已重置。");
              }}
            >
              <RotateCcw />
              重置示例作品
            </Button>
          )}
          {studio.mode === "live" && (
            <>
              {studio.user.role === "ADMIN" && (
                <Button onClick={() => setModal("admin")}>平台管理</Button>
              )}
              {!passwordOpen ? (
                <Button onClick={() => setPasswordOpen(true)}>
                  修改登录密码
                </Button>
              ) : (
                <PasswordForm
                  onDone={() => {
                    setPasswordOpen(false);
                    setModal(null);
                    notify("登录密码已更新。");
                  }}
                />
              )}
            </>
          )}
          <ErrorMessage message={error} />
        </Modal>
      )}
      {modal === "help" && (
        <Modal
          title="让故事，一步步成形"
          description="从一个灵感，到一份可以交付的正式稿。"
          onClose={() => setModal(null)}
          footer={
            <Button primary onClick={() => setModal(null)}>
              继续创作
            </Button>
          }
        >
          <div className="guide-step">
            <BookOpen />
            <div>
              <h3>先确认方向</h3>
              <p>
                依次确认创作简报、人物设定、故事大纲，再按集、场景或片段生成正文。
              </p>
            </div>
          </div>
          <div className="guide-step">
            <Sparkles />
            <div>
              <h3>在片段内探索</h3>
              <p>
                点击正文选中片段，写下你的修改方向。成功重写会保留整部作品的生成版本。
              </p>
            </div>
          </div>
          <div className="guide-step">
            <BookOpen />
            <div>
              <h3>让每次交付都有依据</h3>
              <p>
                草稿保存只更新工作草稿。明确保存版本、生成和恢复形成版本；正式稿发布后才能导出。
              </p>
            </div>
          </div>
          <p className="hint">
            快捷键：Ctrl / ⌘ + S 保存草稿；Ctrl / ⌘ + Enter 发送创作对话。
          </p>
        </Modal>
      )}
      {toast && (
        <div className="toast" role="status" aria-live="polite">
          {toast}
        </div>
      )}
    </div>
  );
}
