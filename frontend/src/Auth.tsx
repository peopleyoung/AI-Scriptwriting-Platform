import { useState, type FormEvent } from "react";
import {
  ArrowRight,
  Eye,
  EyeOff,
  LockKeyhole,
  Sparkles,
  Feather,
} from "lucide-react";
import { api } from "./studio";
import type { User } from "./types";
import { Brand, Button, ErrorMessage, Field } from "./ui";

export default function Auth({
  onLogin,
  onDemo,
  initialUser,
}: {
  onLogin: (user: User) => Promise<void>;
  onDemo: () => void;
  initialUser?: User | null;
}) {
  const [user, setUser] = useState(initialUser);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [visible, setVisible] = useState(false);
  const change = user?.must_change_password;
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusy(true);
    setError("");
    try {
      if (change) {
        if (data.get("new_password") !== data.get("confirm_password"))
          throw new Error("两次输入的新密码不一致。");
        const updated = await api<User>("/auth/change-password", "POST", {
          current_password: data.get("current_password"),
          new_password: data.get("new_password"),
        });
        await onLogin(updated);
      } else {
        const response = await api<{ user: User }>("/auth/login", "POST", {
          username: String(data.get("username")).trim(),
          password: data.get("password"),
        });
        if (response.user.must_change_password) {
          setUser(response.user);
          form.reset();
        } else await onLogin(response.user);
      }
    } catch (error) {
      setError(error instanceof Error ? error.message : "登录失败，请重试。");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="auth-shell">
      <section className="auth-story">
        <Brand onClick={onDemo} />
        <div className="auth-copy">
          <span className="eyebrow">A LITTLE SPARK. A WHOLE NEW STORY.</span>
          <h1 className="serif">
            让每一个灵感，
            <br />
            都有下一幕。
          </h1>
          <p>
            一个安静的创作空间，
            <br />
            陪你把故事写到最后。
          </p>
          <div className="auth-art">
            <img src="/art/rain.svg" alt="雨夜，旧书店的窗户透出暖光" />
            <div>
              <span>SCENE 01</span>
              <p className="serif">故事，从你开始。</p>
            </div>
          </div>
        </div>
        <p className="auth-bottom">幕间 · AI 剧本创作空间</p>
      </section>
      <section className="auth-form-side">
        <div className="auth-form-wrap">
          <span className="auth-symbol">
            <Feather />
          </span>
          <p className="eyebrow">WELCOME TO YOUR NEXT SCENE</p>
          <h2 className="serif">
            {change ? "先为你的账号换一把钥匙" : "欢迎回到，故事开始的地方。"}
          </h2>
          <p className="auth-description">
            {change
              ? `${user.username}，首次登录需要修改初始密码。`
              : "使用管理员为你创建的账号，继续你的创作。"}
          </p>
          <form onSubmit={submit}>
            {change ? (
              <>
                <Field label="初始密码">
                  <input
                    name="current_password"
                    type="password"
                    autoComplete="current-password"
                    required
                    disabled={busy}
                  />
                </Field>
                <Field label="新密码" hint="至少 8 位，与初始密码不同。">
                  <input
                    name="new_password"
                    type="password"
                    autoComplete="new-password"
                    minLength={8}
                    required
                    disabled={busy}
                  />
                </Field>
                <Field label="再次输入新密码">
                  <input
                    name="confirm_password"
                    type="password"
                    autoComplete="new-password"
                    minLength={8}
                    required
                    disabled={busy}
                  />
                </Field>
              </>
            ) : (
              <>
                <Field label="账号">
                  <input
                    name="username"
                    placeholder="输入你的账号"
                    autoComplete="username"
                    required
                    disabled={busy}
                    autoFocus
                  />
                </Field>
                <Field label="密码">
                  <span className="password-field">
                    <input
                      name="password"
                      type={visible ? "text" : "password"}
                      placeholder="输入密码"
                      autoComplete="current-password"
                      required
                      disabled={busy}
                    />
                    <button
                      type="button"
                      className="icon-button"
                      aria-label={visible ? "隐藏密码" : "显示密码"}
                      onClick={() => setVisible(!visible)}
                    >
                      {visible ? <EyeOff /> : <Eye />}
                    </button>
                  </span>
                </Field>
              </>
            )}
            <ErrorMessage message={error} />
            <Button
              type="submit"
              primary
              className="auth-submit"
              disabled={busy}
            >
              {busy ? "正在连接…" : change ? "修改密码并进入" : "进入创作空间"}
              <ArrowRight />
            </Button>
          </form>
          <div className="auth-note">
            <LockKeyhole size={14} />
            作品默认私有，只有授权成员可见。
          </div>
          <div className="auth-demo">
            <span>想先看看幕间？</span>
            <Button small onClick={onDemo} disabled={busy}>
              <Sparkles />
              体验示例作品
            </Button>
          </div>
        </div>
      </section>
    </div>
  );
}
