import {
  type KeyboardEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
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
type LeaderPendingAction =
  | { type: "transfer" }
  | { type: "release"; binding: LeaderBinding };

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
  const [leaderPending, setLeaderPending] = useState<LeaderPendingAction | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const loadGenerationRef = useRef(0);
  const leaderPreviewRef = useRef<HTMLDivElement>(null);

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
    setLeaderPending(null);
    setPending(null);
    setEditingAccount(null);
    setPasswordAccount(null);
    setDialogError(null);
    if (account.role === "SUPERADMIN") void load();
    return () => {
      loadGenerationRef.current += 1;
    };
  }, [account.role, load, seasonId]);

  useEffect(() => {
    if (!leaderPreview) return;
    leaderPreviewRef.current?.focus();
  }, [leaderPreview]);

  const rename = async () => {
    if (!editingAccount) return;
    setBusy(true);
    setDialogError(null);
    setMessage(null);
    try {
      await client.renameAccount(editingAccount.id, editingAccount.version, usernameDraft);
      setEditingAccount(null);
      setUsernameDraft("");
      setMessage("账号昵称已更正；OpenID、稳定账号 ID 和历史记录均未改变。");
      await load();
    } catch (reason: unknown) {
      setDialogError(reason instanceof Error ? reason.message : "昵称更正失败");
    } finally {
      setBusy(false);
    }
  };

  const resetPassword = async () => {
    if (!passwordAccount) return;
    if (newPassword.length < 4 || newPassword !== newPasswordAgain) {
      setDialogError(newPassword.length < 4 ? "网页密码至少需要 4 个字符。" : "两次输入的新密码不一致。");
      return;
    }
    setBusy(true);
    setDialogError(null);
    try {
      await client.resetAdminPassword(passwordAccount.id, passwordAccount.version, newPassword);
      setPasswordAccount(null);
      setNewPassword("");
      setNewPasswordAgain("");
      setMessage("网页密码已重置；旧网页会话将在下次请求时失效，密码未写入审计日志。");
      await load();
    } catch (reason: unknown) {
      setDialogError(reason instanceof Error ? reason.message : "密码重置失败");
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
    setBusy(true);
    setDialogError(null);
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
      setLeaderPending(null);
      setMessage("领队绑定已原子转移，旧绑定保留为历史记录。");
      await onDataChanged();
      await load();
    } catch (reason: unknown) {
      setDialogError(reason instanceof Error ? reason.message : "领队绑定转移失败");
    } finally {
      setBusy(false);
    }
  };

  const releaseLeader = async (binding: LeaderBinding) => {
    setBusy(true);
    setDialogError(null);
    try {
      await client.releaseLeaderBinding(binding.id, binding.version, "超级管理员网页纠错");
      setLeaderPending(null);
      setMessage("领队绑定已释放，历史记录仍可审计。");
      await onDataChanged();
      await load();
    } catch (reason: unknown) {
      setDialogError(reason instanceof Error ? reason.message : "领队绑定释放失败");
    } finally {
      setBusy(false);
    }
  };

  const execute = async () => {
    if (!pending) return;
    setBusy(true);
    setDialogError(null);
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
      setDialogError(reason instanceof Error ? reason.message : "账号操作失败");
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
      {error && <div className="account-toast is-error" role="alert">{error}</div>}
      {message && <div className="account-toast is-success" role="status">{message}</div>}
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
                      onClick={() => { setDialogError(null); setPending({ type: "promote", account: item }); }}
                      type="button"
                    >
                      升级
                    </button>
                  )}
                  {item.role === "SUPERADMIN" && item.id !== account.id && (
                    <button
                      className="text-action destructive"
                      onClick={() => { setDialogError(null); setPending({ type: "demote", account: item }); }}
                      type="button"
                    >
                      降级
                    </button>
                  )}
                  <button
                    className={item.is_active ? "text-action destructive" : "text-action"}
                    onClick={() =>
                      { setDialogError(null); setPending({ type: "active", account: item, active: !item.is_active }); }
                    }
                    type="button"
                  >
                    {item.is_active ? "停用" : "恢复"}
                  </button>
                  <button className="text-action" type="button" onClick={() => { setDialogError(null); setEditingAccount(item); setUsernameDraft(item.username); }}>
                    更正昵称
                  </button>
                  {item.id !== account.id && (item.role === "ADMIN" || item.role === "SUPERADMIN") && (
                    <button className="text-action" type="button" onClick={() => { setDialogError(null); setPasswordAccount(item); setNewPassword(""); setNewPasswordAgain(""); }}>
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
          <div>
            <p className="eyebrow">赛季内身份关系</p>
            <h2>领队绑定纠错</h2>
          </div>
          <label className="leader-season-picker">
            赛季
            <select
              value={seasonId}
              onChange={(event) => {
                setLeaderPreview(null);
                onSeasonChange(event.target.value);
              }}
            >
              {seasons.map((item) => (
                <option key={item.id} value={item.id}>{formatAdminSeasonLabel(item)}</option>
              ))}
            </select>
          </label>
        </div>
        <div className="leader-binding-body">
          <p className="leader-binding-guidance">转移会原子释放同账号或同球队的现行绑定；旧绑定不删除，跨赛季绑定仍被禁止。</p>
          <div className="leader-transfer-grid">
            <label>
              目标账号
              <select value={leaderAccountId} onChange={(event) => { setLeaderAccountId(event.target.value); setLeaderPreview(null); }}>
                <option value="">请选择</option>
                {accounts.filter((item) => item.is_active).map((item) => (
                  <option key={item.id} value={item.id}>{item.username} · {roleLabel(item.role)}</option>
                ))}
              </select>
            </label>
            <label>
              目标球队
              <select value={leaderTeamId} onChange={(event) => { setLeaderTeamId(event.target.value); setLeaderPreview(null); }}>
                <option value="">请选择</option>
                {roster?.teams.filter((team) => team.active).map((team) => (
                  <option key={team.id} value={team.id}>
                    {roster.divisions.find((division) => division.id === team.division_id)?.name ?? "未分组"} · {team.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="leader-reason">
              理由（选填）
              <input maxLength={300} value={leaderReason} onChange={(event) => { setLeaderReason(event.target.value); setLeaderPreview(null); }} />
            </label>
            <button className="secondary-action" type="button" disabled={busy} onClick={() => void previewLeader()}>预览转移影响</button>
          </div>
          {leaderPreview && (
            <div className="leader-preview" ref={leaderPreviewRef} tabIndex={-1}>
              <strong>{leaderPreview.changed ? "将建立新绑定" : "当前绑定已经一致"}</strong>
              <span>{leaderPreview.username} → {leaderPreview.team_name}</span>
              <p>{leaderPreview.release_bindings.length ? `将释放 ${leaderPreview.release_bindings.length} 条现行绑定。` : "无需释放其他绑定。"}</p>
              <button
                className="danger-action"
                type="button"
                disabled={busy || !leaderPreview.changed}
                onClick={() => { setDialogError(null); setLeaderPending({ type: "transfer" }); }}
              >
                确认原子转移
              </button>
            </div>
          )}
          <div className="leader-binding-table" role="table" aria-label="领队绑定">
            <div className="leader-binding-row leader-binding-head" role="row">
              <span role="columnheader">账号</span>
              <span role="columnheader">球队</span>
              <span role="columnheader">状态</span>
              <span role="columnheader">操作</span>
            </div>
            {bindings.map((binding) => (
              <div className={`leader-binding-row ${binding.active ? "active" : "history"}`} role="row" key={binding.id}>
                <strong className="leader-binding-account" role="cell">{binding.username}</strong>
                <span className="leader-binding-team" role="cell">{binding.team_name}</span>
                <small className="leader-binding-status" role="cell">{binding.active ? "现行绑定" : `已释放 · ${binding.released_by ?? "系统"}`}</small>
                <div className="leader-binding-action" role="cell">
                  {binding.active && (
                    <button
                      className="text-action destructive"
                      type="button"
                      disabled={busy}
                      onClick={() => { setDialogError(null); setLeaderPending({ type: "release", binding }); }}
                    >
                      释放
                    </button>
                  )}
                </div>
              </div>
            ))}
            {!bindings.length && <p className="leader-binding-empty">当前赛季尚无领队绑定。</p>}
          </div>
        </div>
      </section>

      {editingAccount && (
        <AccountDialog
          kind="dialog"
          title="更正账号昵称"
          detail="稳定账号 ID、OpenID 和历史引用不会改变。"
          confirmLabel={busy ? "正在提交…" : "确认更正"}
          busy={busy}
          error={dialogError}
          onCancel={() => { setEditingAccount(null); setUsernameDraft(""); setDialogError(null); }}
          onConfirm={() => void rename()}
          initialFocus="field"
        >
          <label>
            新昵称
            <input data-dialog-field maxLength={32} value={usernameDraft} onChange={(event) => setUsernameDraft(event.target.value)} />
          </label>
        </AccountDialog>
      )}
      {passwordAccount && (
        <AccountDialog
          kind="dialog"
          title={`重置 ${accountName(passwordAccount)} 的密码`}
          detail="旧密码、密码哈希和令牌不会显示；提交后旧网页会话将失效。"
          confirmLabel={busy ? "正在提交…" : "确认重置"}
          busy={busy}
          error={dialogError}
          dangerous
          onCancel={() => { setPasswordAccount(null); setNewPassword(""); setNewPasswordAgain(""); setDialogError(null); }}
          onConfirm={() => void resetPassword()}
          initialFocus="field"
        >
          <label>
            新密码
            <input data-dialog-field type="password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} />
          </label>
          <label>
            再次输入
            <input type="password" value={newPasswordAgain} onChange={(event) => setNewPasswordAgain(event.target.value)} />
          </label>
        </AccountDialog>
      )}

      {pending && (
        <AccountDialog
          kind="alertdialog"
          title={confirmationTitle(pending)}
          detail={confirmationDetail(pending)}
          confirmLabel={busy ? "正在提交…" : "确认操作"}
          busy={busy}
          error={dialogError}
          dangerous
          onCancel={() => { setPending(null); setDialogError(null); }}
          onConfirm={() => void execute()}
        />
      )}
      {leaderPending?.type === "transfer" && leaderPreview && (
        <AccountDialog
          kind="alertdialog"
          title="确认原子转移领队绑定？"
          detail={`将把 ${leaderPreview.username} 绑定至 ${leaderPreview.team_name}，并释放预览中的 ${leaderPreview.release_bindings.length} 条冲突绑定；历史记录会保留。`}
          confirmLabel={busy ? "正在提交…" : "确认转移"}
          busy={busy}
          error={dialogError}
          dangerous
          onCancel={() => { setLeaderPending(null); setDialogError(null); }}
          onConfirm={() => void applyLeader()}
        />
      )}
      {leaderPending?.type === "release" && (
        <AccountDialog
          kind="alertdialog"
          title="确认释放领队绑定？"
          detail={`将释放 ${leaderPending.binding.username} 与 ${leaderPending.binding.team_name} 的现行绑定；历史记录仍可审计。`}
          confirmLabel={busy ? "正在提交…" : "确认释放"}
          busy={busy}
          error={dialogError}
          dangerous
          onCancel={() => { setLeaderPending(null); setDialogError(null); }}
          onConfirm={() => void releaseLeader(leaderPending.binding)}
        />
      )}
    </div>
  );
}

function AccountDialog({
  kind,
  title,
  detail,
  confirmLabel,
  busy,
  error,
  dangerous = false,
  initialFocus = "cancel",
  onCancel,
  onConfirm,
  children,
}: {
  kind: "dialog" | "alertdialog";
  title: string;
  detail: string;
  confirmLabel: string;
  busy: boolean;
  error: string | null;
  dangerous?: boolean;
  initialFocus?: "cancel" | "field";
  onCancel: () => void;
  onConfirm: () => void;
  children?: ReactNode;
}) {
  const dialogRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const selector = initialFocus === "field" ? "[data-dialog-field]" : "[data-dialog-cancel]";
    dialogRef.current?.querySelector<HTMLElement>(selector)?.focus();
    return () => previousFocus?.focus();
  }, [initialFocus]);

  const handleKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape" && !busy) {
      event.preventDefault();
      onCancel();
      return;
    }
    if (event.key !== "Tab" || !dialogRef.current) return;
    const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(
      "button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex='-1'])",
    ));
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div
      className="dialog-backdrop account-dialog-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onCancel();
      }}
    >
      <section
        aria-labelledby="account-dialog-title"
        aria-describedby="account-dialog-detail"
        aria-modal="true"
        className="account-dialog"
        onKeyDown={handleKeyDown}
        ref={dialogRef}
        role={kind}
        tabIndex={-1}
      >
        <p className="eyebrow">{kind === "alertdialog" ? "二次确认" : "账号纠错"}</p>
        <h2 id="account-dialog-title">{title}</h2>
        <p className="account-dialog-detail" id="account-dialog-detail">{detail}</p>
        <form
          className="account-dialog-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (!busy) onConfirm();
          }}
        >
          {children}
          {error && <p className="form-error" role="alert">{error}</p>}
          <div className="dialog-actions">
            <button
              className="dialog-secondary"
              data-dialog-cancel
              disabled={busy}
              onClick={onCancel}
              type="button"
            >
              取消
            </button>
            <button className={dangerous ? "danger-action" : "primary-action"} disabled={busy} type="submit">
              {confirmLabel}
            </button>
          </div>
        </form>
      </section>
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
