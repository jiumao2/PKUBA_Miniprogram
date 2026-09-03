import { useCallback, useEffect, useRef, useState } from "react";
import {
  type AdminAccount,
  type AdminManagedAccount,
  type AdminRegistrationPolicy,
  type AdminSeason,
  type LeaderBinding,
  type LeaderTransferPreview,
  type RosterDataset,
  type createAdminClient,
} from "@pkuba/api-client";

import { useAdminDirtySource } from "./dirtyGuard";
import { formatAdminSeasonLabel } from "./seasonLabel";
import "./admin-accounts.css";

type AdminClient = ReturnType<typeof createAdminClient>;
type PendingAction =
  | { type: "promote"; account: AdminManagedAccount }
  | { type: "demote"; account: AdminManagedAccount }
  | { type: "active"; account: AdminManagedAccount; active: boolean };

export function AdminAccountsPage({
  account,
  client,
  seasons = [],
  seasonId = "",
  onSeasonChange = () => undefined,
  onDataChanged = async () => undefined,
}: {
  account: AdminAccount;
  client: AdminClient;
  seasons?: AdminSeason[];
  seasonId?: string;
  onSeasonChange?: (seasonId: string) => void;
  onDataChanged?: () => Promise<void>;
}) {
  const [accounts, setAccounts] = useState<AdminManagedAccount[]>([]);
  const [invite, setInvite] = useState<AdminRegistrationPolicy | null>(null);
  const [inviteCode, setInviteCode] = useState("");
  const [inviteAgain, setInviteAgain] = useState("");
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [editingAccount, setEditingAccount] = useState<AdminManagedAccount | null>(null);
  const [usernameDraft, setUsernameDraft] = useState("");
  const [passwordAccount, setPasswordAccount] = useState<AdminManagedAccount | null>(null);
  const [newPassword, setNewPassword] = useState("");
  const [newPasswordAgain, setNewPasswordAgain] = useState("");
  const [roster, setRoster] = useState<RosterDataset | null>(null);
  const [bindings, setBindings] = useState<LeaderBinding[]>([]);
  const [leaderAccountId, setLeaderAccountId] = useState("");
  const [leaderTeamId, setLeaderTeamId] = useState("");
  const [leaderReason, setLeaderReason] = useState("");
  const [leaderPreview, setLeaderPreview] = useState<LeaderTransferPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const loadGenerationRef = useRef(0);

  useAdminDirtySource(
    "admin-invite-form",
    Boolean(inviteCode || inviteAgain),
  );
  useAdminDirtySource(
    "admin-account-corrections",
    Boolean(editingAccount || passwordAccount || leaderAccountId || leaderTeamId || leaderReason),
  );

  const load = useCallback(async () => {
    const generation = ++loadGenerationRef.current;
    setLoading(true);
    setError(null);
    try {
      const [nextAccounts, nextInvite, nextRoster, nextBindings] = await Promise.all([
        client.listAdminAccounts(),
        client.getAdminRegistrationPolicy(),
        seasonId ? client.getRosterDataset(seasonId) : Promise.resolve(null),
        seasonId ? client.listLeaderBindings(seasonId, true) : Promise.resolve([]),
      ]);
      if (generation !== loadGenerationRef.current) return;
      setAccounts(nextAccounts);
      setInvite(nextInvite);
      setRoster(nextRoster);
      setBindings(nextBindings);
    } catch (reason: unknown) {
      if (generation !== loadGenerationRef.current) return;
      setError(reason instanceof Error ? reason.message : "无法读取管理员列表");
    } finally {
      if (generation === loadGenerationRef.current) setLoading(false);
    }
  }, [client, seasonId]);

  useEffect(() => {
    loadGenerationRef.current += 1;
    setAccounts([]);
    setInvite(null);
    setInviteCode("");
    setInviteAgain("");
    setRoster(null);
    setBindings([]);
    setLeaderAccountId("");
    setLeaderTeamId("");
    setLeaderReason("");
    setLeaderPreview(null);
    setPending(null);
    setEditingAccount(null);
    setPasswordAccount(null);
    if (account.role === "SUPERADMIN") void load();
    return () => {
      loadGenerationRef.current += 1;
    };
  }, [account.role, load, seasonId]);

  const rename = async () => {
    if (!editingAccount) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await client.renameAccount(editingAccount.id, editingAccount.version, usernameDraft);
      setEditingAccount(null);
      setUsernameDraft("");
      setMessage("账号昵称已更正；OpenID、稳定账号 ID 和历史记录均未改变。");
      await load();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "昵称更正失败");
    } finally {
      setBusy(false);
    }
  };

  const resetPassword = async () => {
    if (!passwordAccount) return;
    if (newPassword.length < 4 || newPassword !== newPasswordAgain) {
      setError(newPassword.length < 4 ? "网页密码至少需要 4 个字符。" : "两次输入的新密码不一致。");
      return;
    }
    if (!window.confirm(`确认重置 ${accountName(passwordAccount)} 的网页密码并撤销其旧网页会话？`)) return;
    setBusy(true);
    setError(null);
    try {
      await client.resetAdminPassword(passwordAccount.id, passwordAccount.version, newPassword);
      setPasswordAccount(null);
      setNewPassword("");
      setNewPasswordAgain("");
      setMessage("网页密码已重置；旧网页会话将在下次请求时失效，密码未写入审计日志。");
      await load();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "密码重置失败");
    } finally {
      setBusy(false);
    }
  };

  const previewLeader = async () => {
    const selectedSeason = seasons.find((item) => item.id === seasonId);
    if (!selectedSeason || !leaderAccountId || !leaderTeamId) {
      setError("请选择赛季、账号和球队。");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setLeaderPreview(await client.previewLeaderTransfer(seasonId, {
        expected_season_version: selectedSeason.version,
        account_id: leaderAccountId,
        team_id: leaderTeamId,
        reason: leaderReason,
      }));
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "领队转移预览失败");
    } finally {
      setBusy(false);
    }
  };

  const applyLeader = async () => {
    const selectedSeason = seasons.find((item) => item.id === seasonId);
    if (!selectedSeason || !leaderPreview) return;
    if (!window.confirm("确认释放预览中列出的旧绑定，并原子建立新绑定？历史绑定会保留。")) return;
    setBusy(true);
    setError(null);
    try {
      await client.transferLeaderBinding(seasonId, {
        expected_season_version: selectedSeason.version,
        account_id: leaderAccountId,
        team_id: leaderTeamId,
        reason: leaderReason,
        impact_hash: leaderPreview.impact_hash,
        confirmed: true,
      });
      setLeaderPreview(null);
      setLeaderAccountId("");
      setLeaderTeamId("");
      setLeaderReason("");
      setMessage("领队绑定已原子转移，旧绑定保留为历史记录。");
      await onDataChanged();
      await load();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "领队绑定转移失败");
    } finally {
      setBusy(false);
    }
  };

  const releaseLeader = async (binding: LeaderBinding) => {
    if (!window.confirm(`确认释放 ${binding.username} 与 ${binding.team_name} 的当前绑定？`)) return;
    setBusy(true);
    setError(null);
    try {
      await client.releaseLeaderBinding(binding.id, binding.version, "超级管理员网页纠错");
      setMessage("领队绑定已释放，历史记录仍可审计。");
      await onDataChanged();
      await load();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "领队绑定释放失败");
    } finally {
      setBusy(false);
    }
  };

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
    if (!invite?.configured) return;
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
      const next = await client.setAdminRegistrationPolicy(inviteCode, invite.version);
      setInvite(next);
      setInviteCode("");
      setInviteAgain("");
      setMessage("全局管理员邀请码已更新，旧邀请码立即失效。");
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
          <h2>账号与权限</h2>
          <p>
            超级管理员可以升级普通管理员，也可以降级其他超级管理员。系统禁止自我降级，并保护最后一个有效超级管理员。
          </p>
        </div>
        <button className="secondary-action" disabled={loading} onClick={() => void load()} type="button">
          刷新列表
        </button>
      </section>

      {invite && (
        <section className="panel invite-panel">
          <div className="invite-copy">
            <p className="eyebrow">全局注册策略</p>
            <h2>管理员邀请码</h2>
            <p>
              管理员注册不依赖赛季状态。系统只保存邀请码摘要；更新后旧邀请码立即失效，已注册管理员不受影响。
            </p>
            <span className="subtle">
              {invite.configured && invite.updated_at
                ? `最近更新：${new Date(invite.updated_at).toLocaleString("zh-CN")}`
                : "尚未初始化；请先由服务器运维执行一次性初始化命令"}
            </span>
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
              disabled={busy || !invite.configured}
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
            <h2>{loading ? "正在读取" : `${accounts.length} 个系统账号`}</h2>
          </div>
          <span className="subtle">所有修改均校验版本并写入审计日志</span>
        </div>
        {!loading && accounts.length === 0 ? (
          <div className="empty-state">系统中尚无账号。</div>
        ) : (
          <div className="account-table" role="table" aria-label="系统账号与权限">
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
                <span>{roleLabel(item.role)}</span>
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
                  <button className="text-action" type="button" onClick={() => { setEditingAccount(item); setUsernameDraft(item.username); }}>
                    更正昵称
                  </button>
                  {item.id !== account.id && (item.role === "ADMIN" || item.role === "SUPERADMIN") && (
                    <button className="text-action" type="button" onClick={() => { setPasswordAccount(item); setNewPassword(""); setNewPasswordAgain(""); }}>
                      重置密码
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="panel leader-binding-panel">
        <div className="panel-heading">
          <div><p className="eyebrow">赛季内身份关系</p><h2>领队绑定纠错</h2></div>
          <label>赛季<select value={seasonId} onChange={(event) => { setLeaderPreview(null); onSeasonChange(event.target.value); }}>{seasons.map((item) => <option key={item.id} value={item.id}>{formatAdminSeasonLabel(item)}</option>)}</select></label>
        </div>
        <p className="subtle">转移会原子释放同账号或同球队的现行绑定；旧绑定不删除，跨赛季绑定仍被禁止。</p>
        <div className="leader-transfer-grid">
          <label>目标账号<select value={leaderAccountId} onChange={(event) => { setLeaderAccountId(event.target.value); setLeaderPreview(null); }}><option value="">请选择</option>{accounts.filter((item) => item.is_active).map((item) => <option key={item.id} value={item.id}>{item.username} · {roleLabel(item.role)}</option>)}</select></label>
          <label>目标球队<select value={leaderTeamId} onChange={(event) => { setLeaderTeamId(event.target.value); setLeaderPreview(null); }}><option value="">请选择</option>{roster?.teams.filter((team) => team.active).map((team) => <option key={team.id} value={team.id}>{roster.divisions.find((division) => division.id === team.division_id)?.name ?? "未分组"} · {team.name}</option>)}</select></label>
          <label className="leader-reason">理由（选填）<input maxLength={300} value={leaderReason} onChange={(event) => { setLeaderReason(event.target.value); setLeaderPreview(null); }} /></label>
          <button className="secondary-action" type="button" disabled={busy} onClick={() => void previewLeader()}>预览转移影响</button>
        </div>
        {leaderPreview && <div className="leader-preview"><strong>{leaderPreview.changed ? "将建立新绑定" : "当前绑定已经一致"}</strong><span>{leaderPreview.username} → {leaderPreview.team_name}</span><p>{leaderPreview.release_bindings.length ? `将释放 ${leaderPreview.release_bindings.length} 条现行绑定。` : "无需释放其他绑定。"}</p><button className="danger-action" type="button" disabled={busy || !leaderPreview.changed} onClick={() => void applyLeader()}>确认原子转移</button></div>}
        <div className="leader-binding-list">
          {bindings.map((binding) => <article className={binding.active ? "active" : "history"} key={binding.id}><div><strong>{binding.username}</strong><span>{binding.team_name}</span></div><small>{binding.active ? "现行绑定" : `已释放 · ${binding.released_by ?? "系统"}`}</small>{binding.active && <button className="text-action destructive" type="button" disabled={busy} onClick={() => void releaseLeader(binding)}>释放</button>}</article>)}
          {!bindings.length && <p className="subtle">当前赛季尚无领队绑定。</p>}
        </div>
      </section>

      {editingAccount && <section className="confirmation-panel" role="dialog" aria-label="更正账号昵称"><div><p className="eyebrow">稳定 ID 不变</p><h2>更正昵称</h2><label>新昵称<input autoFocus maxLength={32} value={usernameDraft} onChange={(event) => setUsernameDraft(event.target.value)} /></label></div><div className="confirmation-actions"><button className="secondary-action" type="button" onClick={() => setEditingAccount(null)}>取消</button><button className="primary-action" disabled={busy} type="button" onClick={() => void rename()}>确认更正</button></div></section>}
      {passwordAccount && <section className="confirmation-panel" role="dialog" aria-label="重置管理员密码"><div><p className="eyebrow">仅重置网页密码</p><h2>重置 {accountName(passwordAccount)} 的密码</h2><p>旧密码、密码哈希和令牌不会显示；旧网页会话将失效。</p><label>新密码<input type="password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} /></label><label>再次输入<input type="password" value={newPasswordAgain} onChange={(event) => setNewPasswordAgain(event.target.value)} /></label></div><div className="confirmation-actions"><button className="secondary-action" type="button" onClick={() => { setPasswordAccount(null); setNewPassword(""); setNewPasswordAgain(""); }}>取消</button><button className="danger-action" disabled={busy} type="button" onClick={() => void resetPassword()}>确认重置</button></div></section>}

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

function roleLabel(role: string): string {
  if (role === "SUPERADMIN") return "超级管理员";
  if (role === "ADMIN") return "普通管理员";
  return "普通用户";
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
