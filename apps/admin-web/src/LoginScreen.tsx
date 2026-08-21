import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  type AdminAccount,
  type AdminWebLoginChallenge,
  type createAdminClient,
} from "@pkuba/api-client";
import logoUrl from "@pkuba/design-tokens/pkuba-logo.png";
import * as QRCode from "qrcode";

type AdminClient = ReturnType<typeof createAdminClient>;
type LoginMode = "wechat" | "password";
type QrState = "loading" | "pending" | "confirmed" | "expired" | "error";

export function LoginScreen({
  client,
  onLogin,
}: {
  client: AdminClient;
  onLogin: (account: AdminAccount) => void;
}) {
  const [mode, setMode] = useState<LoginMode>("wechat");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [webChallenge, setWebChallenge] = useState<AdminWebLoginChallenge | null>(null);
  const [qrImage, setQrImage] = useState("");
  const [qrState, setQrState] = useState<QrState>("loading");
  const [qrError, setQrError] = useState<string | null>(null);
  const [remainingSeconds, setRemainingSeconds] = useState(0);
  const generationRef = useRef(0);
  const consumingRef = useRef(false);
  const challengeRequestRef = useRef<Promise<AdminWebLoginChallenge> | null>(null);

  const createQrChallenge = async () => {
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    consumingRef.current = false;
    setWebChallenge(null);
    setQrImage("");
    setQrState("loading");
    setQrError(null);
    const request = challengeRequestRef.current ?? client.createWebLoginChallenge();
    challengeRequestRef.current = request;
    try {
      const challenge = await request;
      const svg = await QRCode.toString(challenge.scan_payload, {
        type: "svg",
        margin: 2,
        errorCorrectionLevel: "M",
        color: { dark: "#171614", light: "#ffffff" },
      });
      if (generationRef.current !== generation) return;
      setWebChallenge(challenge);
      setQrImage(`data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`);
      setRemainingSeconds(challenge.expires_in);
      setQrState("pending");
    } catch (reason: unknown) {
      if (generationRef.current !== generation) return;
      setQrState("error");
      setQrError(reason instanceof Error ? reason.message : "无法生成登录二维码");
    } finally {
      if (challengeRequestRef.current === request) challengeRequestRef.current = null;
    }
  };

  useEffect(() => {
    if (mode !== "wechat") return;
    void createQrChallenge();
    return () => {
      generationRef.current += 1;
    };
  }, [mode]);

  useEffect(() => {
    if (!webChallenge || mode !== "wechat" || qrState === "expired") return;
    const expiresAt = new Date(webChallenge.expires_at).getTime();
    const updateRemaining = () => {
      const remaining = Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000));
      setRemainingSeconds(remaining);
      if (remaining === 0) setQrState("expired");
    };
    updateRemaining();
    const interval = window.setInterval(updateRemaining, 1000);
    return () => window.clearInterval(interval);
  }, [mode, qrState, webChallenge]);

  useEffect(() => {
    if (!webChallenge || mode !== "wechat" || qrState === "expired") return;
    let stopped = false;
    let timer: number | undefined;

    const schedule = (delay: number) => {
      if (!stopped) timer = window.setTimeout(() => void poll(), delay);
    };
    const poll = async () => {
      try {
        const status = await client.getWebLoginStatus();
        if (stopped) return;
        if (
          status.status === "EXPIRED" ||
          status.status === "CONSUMED" ||
          status.status === "MISSING"
        ) {
          setQrState("expired");
          return;
        }
        if (status.status === "CONFIRMED") {
          setQrState("confirmed");
          if (consumingRef.current) return;
          consumingRef.current = true;
          try {
            const account = await client.consumeWebLogin(webChallenge.browser_token);
            if (!stopped) onLogin(account);
            return;
          } catch (reason: unknown) {
            if (stopped) return;
            consumingRef.current = false;
            setQrError(reason instanceof Error ? reason.message : "登录确认失败");
          }
        }
        schedule(1500);
      } catch (reason: unknown) {
        if (stopped) return;
        setQrError(reason instanceof Error ? reason.message : "无法读取扫码状态");
        schedule(3000);
      }
    };

    schedule(600);
    return () => {
      stopped = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [client, mode, onLogin, webChallenge]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setPasswordError(null);
    try {
      const challenge = await client.getLoginChallenge();
      const account = await client.passwordLogin(username, password, challenge.challenge);
      onLogin(account);
    } catch (reason: unknown) {
      setPasswordError(reason instanceof Error ? reason.message : "登录失败");
    } finally {
      setSubmitting(false);
    }
  };

  const selectMode = (nextMode: LoginMode) => {
    setMode(nextMode);
    setPasswordError(null);
    setQrError(null);
  };

  return (
    <main className="login-shell">
      <div className="login-layout">
        <header className="login-context">
          <img className="login-brand" src={logoUrl} alt="北大篮协 PKUBA·1997" />
          <div>
            <p className="eyebrow">PKUBA 赛事管理</p>
            <h1>内部赛事工作台</h1>
            <p>赛季、赛程、调赛与比赛资料统一管理。</p>
          </div>
          <span className="login-context-mark">PEKING UNIVERSITY BASKETBALL ASSOCIATION</span>
        </header>

        <section className="login-panel" aria-labelledby="login-title">
          <div className="login-heading">
            <p className="eyebrow">管理员入口</p>
            <h2 id="login-title">登录工作台</h2>
          </div>
          <div className="login-tabs" role="tablist" aria-label="登录方式">
            <button
              aria-selected={mode === "wechat"}
              className={mode === "wechat" ? "active" : ""}
              onClick={() => selectMode("wechat")}
              role="tab"
              type="button"
            >
              微信扫码
            </button>
            <button
              aria-selected={mode === "password"}
              className={mode === "password" ? "active" : ""}
              onClick={() => selectMode("password")}
              role="tab"
              type="button"
            >
              密码登录
            </button>
          </div>

          {mode === "wechat" ? (
            <div className="qr-login" role="tabpanel">
              <div className={`qr-frame qr-${qrState}`}>
                {qrImage ? (
                  <img src={qrImage} alt="管理员网页登录二维码" />
                ) : (
                  <span className="qr-placeholder" />
                )}
                {qrState === "confirmed" && <span className="qr-overlay">正在登录…</span>}
                {qrState === "expired" && <span className="qr-overlay">二维码已失效</span>}
              </div>
              {webChallenge && (
                <div className="verification-code">
                  <span>浏览器校验码</span>
                  <strong>{webChallenge.verification_code}</strong>
                </div>
              )}
              <p className="qr-instruction">
                打开 PKUBA 小程序，在“我的”中选择“扫码登录管理后台”，扫码后核对校验码并确认。
              </p>
              <p className="qr-status" aria-live="polite">
                {qrState === "loading" && "正在生成二维码…"}
                {qrState === "pending" && `等待扫码确认 · ${formatCountdown(remainingSeconds)}`}
                {qrState === "confirmed" && "已在小程序确认，正在建立安全会话…"}
                {qrState === "expired" && "二维码已过期，请刷新后重新扫码。"}
                {qrState === "error" && "二维码暂时无法使用。"}
              </p>
              {qrError && <div className="form-error">{qrError}</div>}
              {(qrState === "expired" || qrState === "error") && (
                <button
                  className="text-action qr-refresh"
                  onClick={() => void createQrChallenge()}
                  type="button"
                >
                  刷新二维码
                </button>
              )}
            </div>
          ) : (
            <form
              className="password-login"
              onSubmit={(event) => void submit(event)}
              role="tabpanel"
            >
              <p className="login-intro">使用个人管理员账号登录。</p>
              <label>
                用户名
                <input
                  autoComplete="username"
                  autoFocus
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
              {passwordError && <div className="form-error">{passwordError}</div>}
              <button className="primary-action login-submit" disabled={submitting} type="submit">
                {submitting ? "正在验证…" : "登录"}
              </button>
              <p className="login-footnote">
                首次注册后的初始密码与当时的赛季邀请码相同；登录后可在右上角修改。
              </p>
            </form>
          )}
        </section>
      </div>
    </main>
  );
}

function formatCountdown(totalSeconds: number) {
  const minutes = Math.floor(totalSeconds / 60).toString().padStart(2, "0");
  const seconds = Math.max(0, totalSeconds % 60).toString().padStart(2, "0");
  return `${minutes}:${seconds} 后失效`;
}
