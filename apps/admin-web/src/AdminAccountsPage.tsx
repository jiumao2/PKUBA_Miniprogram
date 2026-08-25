import { useCallback, useEffect, useState } from "react";
import {
  type AdminAccount,
  type AdminManagedAccount,
  type AdminSeason,
  type SeasonInvite,
  type createAdminClient,
} from "@pkuba/api-client";

type AdminClient = ReturnType<typeof createAdminClient>;
type PendingAction =
  | { type: "promote"; account: AdminManagedAccount }
  | { type: "demote"; account: AdminManagedAccount }
  | { type: "active"; account: AdminManagedAccount; active: boolean };

export function AdminAccountsPage({
  account,
  client,
  season,
}: {
  account: AdminAccount;
  client: AdminClient;
  season: AdminSeason | null;
}) {
  const [accounts, setAccounts] = useState<AdminManagedAccount[]>([]);
  const [invite, setInvite] = useState<SeasonInvite | null>(null);
  const [inviteCode, setInviteCode] = useState("");
  const [inviteAgain, setInviteAgain] = useState("");
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [nextAccounts, nextInvite] = await Promise.all([
        client.listAdminAccounts(),
        season ? client.getSeasonInvite(season.id) : Promise.resolve(null),
      ]);
      setAccounts(nextAccounts);
      setInvite(nextInvite);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "无法读取管理员列表");
    } finally {
      setLoading(false);
    }
  }, [client, season]);

  useEffect(() => {
    if (account.role === "SUPERADMIN") void load();
  }, [account.role, load]);

  const execute = async () => {
    if (!pending) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      if (pending.type === "promote") {
        await client.promoteAdmin(pending.account.id, pending.account.version);
        setMessage(`${accountName(pending.account)} 已升级为超级管理员。`);
      } else if (pending.type === "demote") {
        await client.demoteSuperadmin(pending.account.id, pending.account.version);
        setMessage(`${accountName(pending.account)} 已降级为普通管理员，审计日志已生成。`);
      } else {
        await client.setAdminActive(
          pending.account.id,
          pending.account.version,
          pending.active,
        );
        setMessage(
          `${accountName(pending.account)} 已${pending.active ? "恢复" : "停用"}，审计日志已生成。`,
        );
      }
      setPending(null);
      await load();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "账号操作失败");
    } finally {
      setBusy(false);
    }
  };

  const rotateInvite = async () => {
    if (!season || !invite || season.status === "ARCHIVED") return;
    if (inviteCode.length < 8) {
      setError("邀请码至少需要 8 个字符。");
      return;
    }
    if (inviteCode !== inviteAgain) {
      setError("两次输入的邀请码不一致。");
      return;
    }
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const next = await client.setSeasonInvite(season.id, inviteCode, invite.version);
      setInvite(next);
      setInviteCode("");
      setInviteAgain("");
      setMessage(`${season.name} 的管理员邀请码已更新，旧邀请码立即失效。`);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "邀请码更新失败");
    } finally {
      setBusy(false);
    }
  };

  if (account.role !== "SUPERADMIN") {
    return (
      <section className="state-panel error">
        <h2>需要超级管理员权限</h2>
        <p>普通管理员不能查看或修改其他管理员账号。</p>
      </section>
    );
  }

  return (
    <div className="account-workflow">
      <section className="panel account-intro">
        <div>
          <p className="eyebrow">权限边界</p>
          <h2>管理员账户</h2>
          <p>
            超级管理员可以升级普通管理员，也可以降级其他超级管理员。系统禁止自我降级，并保护最后一个有效超级管理员。
          </p>
        </div>
        <button className="secondary-action" disabled={loading} onClick={() => void load()} type="button">
          刷新列表
        </button>
      </section>

      {season && invite && (
        <section className="panel invite-panel">
          <div className="invite-copy">
            <p className="eyebrow">赛季元信息</p>
            <h2>管理员邀请码</h2>
            <p>
              当前赛季：{season.name}。系统只保存邀请码摘要；更新后旧邀请码立即失效，已注册管理员不受影响。
            </p>
            <span className="subtle">
              {invite.updated_at
                ? `最近更新：${new Date(invite.updated_at).toLocaleString("zh-CN")}`
                : "尚未设置"}
            </span>
            {invite.uses_default_invite && (
              <span className="form-warning" role="status">
                当前仍使用默认邀请码 PKUBA1997，正式开放前建议轮换。
              </span>
            )}
            {season.status === "ARCHIVED" && (
              <span className="subtle">已归档赛季只读，不能轮换邀请码。</span>
            )}
          </div>
          <div className="invite-form">
            <label>
              新邀请码
              <input type="password" value={inviteCode} onChange={(event) => setInviteCode(event.target.value)} />
            </label>
            <label>
              再次输入
              <input type="password" value={inviteAgain} onChange={(event) => setInviteAgain(event.target.value)} />
            </label>
            <button
              className="primary-action"
              disabled={busy || season.status === "ARCHIVED"}
              onClick={() => void rotateInvite()}
              type="button"
            >
              {busy ? "正在更新…" : "更新邀请码"}
            </button>
          </div>
        </section>
      )}

      <section className="panel account-panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">当前权限状态</p>
            <h2>{loading ? "正在读取" : `${accounts.length} 个管理员账号`}</h2>
          </div>
          <span className="subtle">所有修改均校验版本并写入审计日志</span>
        </div>
        {!loading && accounts.length === 0 ? (
          <div className="empty-state">尚无管理员账号。</div>
        ) : (
          <div className="account-table" role="table" aria-label="管理员账户">
            <div className="account-row account-head" role="row">
              <span>账号</span>
              <span>角色</span>
              <span>状态</span>
              <span>操作</span>
            </div>
            {accounts.map((item) => (
              <div className="account-row" role="row" key={item.id}>
                <div className="account-identity">
                  <strong>{accountName(item)}</strong>
                  <span>@{item.username}{item.id === account.id ? " · 当前账号" : ""}</span>
                </div>
                <span>{item.role === "SUPERADMIN" ? "超级管理员" : "普通管理员"}</span>
                <span className={item.is_active ? "status ready" : "status inactive"}>
                  {item.is_active ? "有效" : "已停用"}
                </span>
                <div className="account-actions">
                  {item.role === "ADMIN" && item.is_active && (
                    <button
                      className="text-action"
                      onClick={() => setPending({ type: "promote", account: item })}
                      type="button"
                    >
                      升级
                    </button>
                  )}
                  {item.role === "SUPERADMIN" && item.id !== account.id && (
                    <button
                      className="text-action destructive"
                      onClick={() => setPending({ type: "demote", account: item })}
                      type="button"
                    >
                      降级
                    </button>
                  )}
                  <button
                    className={item.is_active ? "text-action destructive" : "text-action"}
                    onClick={() =>
                      setPending({ type: "active", account: item, active: !item.is_active })
                    }
                    type="button"
                  >
                    {item.is_active ? "停用" : "恢复"}
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {pending && (
        <section className="confirmation-panel" role="alertdialog" aria-labelledby="account-confirm-title">
          <div>
            <p className="eyebrow">二次确认</p>
            <h2 id="account-confirm-title">{confirmationTitle(pending)}</h2>
            <p>{confirmationDetail(pending)}</p>
          </div>
          <div className="confirmation-actions">
            <button className="secondary-action" disabled={busy} onClick={() => setPending(null)} type="button">
              取消
            </button>
            <button className="danger-action" disabled={busy} onClick={() => void execute()} type="button">
              {busy ? "正在提交…" : "确认操作"}
            </button>
          </div>
        </section>
      )}

      {error && <div className="form-error">{error}</div>}
      {message && <div className="form-success">{message}</div>}
    </div>
  );
}

function accountName(account: AdminManagedAccount): string {
  return account.username;
}

function confirmationTitle(action: PendingAction): string {
  if (action.type === "promote") return `升级 ${accountName(action.account)}？`;
  if (action.type === "demote") return `降级 ${accountName(action.account)}？`;
  return `${action.active ? "恢复" : "停用"} ${accountName(action.account)}？`;
}

function confirmationDetail(action: PendingAction): string {
  if (action.type === "promote") {
    return "升级后该账号可执行赛季、赛程和账号级危险操作。";
  }
  if (action.type === "demote") {
    return "降级后该账号保留普通管理员权限，但不能再执行赛季、赛程和账号级危险操作；最后一个有效超级管理员不能被降级。";
  }
  if (action.active) return "恢复后，该账号将重新获得原有管理员权限。";
  return "停用会立即阻止该账号继续登录；最后一个有效超级管理员不能被停用。";
}
