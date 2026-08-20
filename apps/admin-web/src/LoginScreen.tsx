import { useState, type FormEvent } from "react";
import { type AdminAccount, type createAdminClient } from "@pkuba/api-client";
import logoUrl from "@pkuba/design-tokens/pkuba-logo.png";

type AdminClient = ReturnType<typeof createAdminClient>;

export function LoginScreen({
  client,
  onLogin,
}: {
  client: AdminClient;
  onLogin: (account: AdminAccount) => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const challenge = await client.getLoginChallenge();
      const account = await client.passwordLogin(username, password, challenge.challenge);
      onLogin(account);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "登录失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="login-shell">
      <section className="login-panel" aria-labelledby="login-title">
        <img className="login-brand" src={logoUrl} alt="北大篮协 PKUBA·1997" />
        <p className="eyebrow">内部赛事工作台</p>
        <h1 id="login-title">管理员登录</h1>
        <p className="login-intro">使用个人管理员账号。首次注册后的初始密码与当时的赛季邀请码相同。</p>
        <form onSubmit={(event) => void submit(event)}>
          <label>
            用户名
            <input
              autoComplete="username"
              onChange={(event) => setUsername(event.target.value)}
              required
              value={username}
            />
          </label>
          <label>
            密码
            <input
              autoComplete="current-password"
              onChange={(event) => setPassword(event.target.value)}
              required
              type="password"
              value={password}
            />
          </label>
          {error && <div className="form-error">{error}</div>}
          <button className="primary-action login-submit" disabled={submitting} type="submit">
            {submitting ? "正在验证…" : "登录"}
          </button>
        </form>
        <p className="login-footnote">登录后可在工作台右上角修改个人密码；修改不会影响赛季邀请码。</p>
      </section>
    </main>
  );
}
