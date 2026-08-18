import { useMemo, useState } from "react";
import {
  type AdminAccount,
  type Game,
  type ScheduleImport,
  type Season,
  type createAdminClient,
} from "@pkuba/api-client";

type AdminClient = ReturnType<typeof createAdminClient>;

interface Assignment {
  date?: string;
  period_code?: string;
  venue_code?: string;
  cell?: string;
}

function assignmentsFrom(batch: ScheduleImport | null): Record<string, Assignment> {
  const value = batch?.summary.assignments;
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return value as Record<string, Assignment>;
}

function summaryCount(batch: ScheduleImport | null, key: string): number {
  const value = batch?.summary[key];
  return typeof value === "number" ? value : 0;
}

export function ScheduleImportPage({
  account,
  client,
  games,
  season,
  onConfirmed,
}: {
  account: AdminAccount;
  client: AdminClient;
  games: Game[];
  season: Season;
  onConfirmed: () => Promise<void>;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [batch, setBatch] = useState<ScheduleImport | null>(null);
  const [policies, setPolicies] = useState<Record<string, boolean>>({});
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const assignments = useMemo(() => assignmentsFrom(batch), [batch]);
  const hasErrors = summaryCount(batch, "error_count") > 0;
  const isSuperadmin = account.role === "SUPERADMIN";

  const download = async () => {
    setBusy(true);
    setError(null);
    try {
      const blob = await client.downloadScheduleTemplate(season.id);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `PKUBA_${season.year}_赛程模板.xlsx`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "模板下载失败");
    } finally {
      setBusy(false);
    }
  };

  const upload = async () => {
    if (!file) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const nextBatch = await client.uploadSchedule(season.id, file);
      setBatch(nextBatch);
      setPolicies(
        Object.fromEntries(
          Object.keys(assignmentsFrom(nextBatch)).map((code) => [
            code,
            games.find((game) => game.code === code)?.leader_adjustable ?? true,
          ]),
        ),
      );
      setAcknowledged(false);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "上传校验失败");
    } finally {
      setBusy(false);
    }
  };

  const confirm = async () => {
    if (!batch || hasErrors || !acknowledged) return;
    setBusy(true);
    setError(null);
    try {
      const confirmed = await client.confirmScheduleImport(batch.id, {
        expected_season_version: season.version,
        leader_adjustable_by_game: policies,
      });
      setBatch(confirmed);
      setMessage("赛程已原子写入，比赛 ID 保持不变，审计日志已生成。");
      await onConfirmed();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "确认写入失败");
    } finally {
      setBusy(false);
    }
  };

  if (!isSuperadmin) {
    return (
      <section className="state-panel error">
        <h2>需要超级管理员权限</h2>
        <p>普通管理员可查看导入批次，但不能下载、上传或确认正式赛程。</p>
      </section>
    );
  }

  return (
    <div className="import-workflow">
      <section className="panel import-intro">
        <div>
          <p className="eyebrow">步骤 1 · 下载并离线填写</p>
          <h2>赛季签名模板</h2>
          <p>
            模板按当前赛季、容量、场地与比赛代码即时生成。只填写场地格；不要修改日期、时段或隐藏元数据。
          </p>
        </div>
        <button
          className="secondary-action"
          disabled={busy}
          onClick={() => void download()}
          type="button"
        >
          下载 XLSX 模板
        </button>
      </section>

      <section className="panel upload-panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">步骤 2 · 上传到暂存批次</p>
            <h2>服务器重新校验</h2>
          </div>
          <span className="subtle">最大 10 MB · 仅 XLSX</span>
        </div>
        <div className="upload-row">
          <input
            accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            aria-label="选择赛程 XLSX"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            type="file"
          />
          <button
            className="primary-action"
            disabled={!file || busy}
            onClick={() => void upload()}
            type="button"
          >
            {busy ? "正在处理…" : "上传并校验"}
          </button>
        </div>
        <p className="subtle">上传不会直接改动正式赛程；宏、外部链接、公式、模板篡改会被拒绝。</p>
      </section>

      {batch && (
        <section className="panel validation-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">步骤 3 · 检查差异与锁定政策</p>
              <h2>{hasErrors ? "存在阻止确认的错误" : "校验完成"}</h2>
            </div>
            <span className={hasErrors ? "status error-badge" : "status ready"}>
              {summaryCount(batch, "error_count")} 错误 · {summaryCount(batch, "warning_count")} 警告
            </span>
          </div>

          <div className="import-summary">
            <Summary label="应有" value={summaryCount(batch, "expected")} />
            <Summary label="实际唯一" value={summaryCount(batch, "actual_unique")} />
            <Summary
              label="修改"
              value={Array.isArray(batch.summary.modified) ? batch.summary.modified.length : 0}
            />
            <Summary
              label="移除"
              value={Array.isArray(batch.summary.removed) ? batch.summary.removed.length : 0}
            />
          </div>

          {batch.issues.length > 0 && (
            <div className="issue-list" aria-label="导入问题">
              {batch.issues.map((issue, index) => (
                <div
                  className={`issue ${issue.severity.toLowerCase()}`}
                  key={`${issue.code}-${issue.cell}-${index}`}
                >
                  <strong>{issue.severity === "ERROR" ? "错误" : "提醒"}</strong>
                  <span>{issue.message}</span>
                  {issue.cell && <code>{issue.cell}</code>}
                </div>
              ))}
            </div>
          )}

          {!hasErrors && (
            <>
              <div className="policy-table" role="table" aria-label="逐场调赛政策">
                <div className="policy-row policy-head" role="row">
                  <span>比赛代码</span>
                  <span>目标位置</span>
                  <span>领队可申请调赛</span>
                </div>
                {Object.entries(assignments).map(([code, assignment]) => (
                  <div className="policy-row" role="row" key={code}>
                    <strong>{code}</strong>
                    <span>
                      {assignment.date} · {assignment.period_code} · {assignment.venue_code}
                    </span>
                    <label className="policy-toggle">
                      <input
                        checked={policies[code] ?? true}
                        onChange={(event) =>
                          setPolicies((current) => ({
                            ...current,
                            [code]: event.target.checked,
                          }))
                        }
                        type="checkbox"
                      />
                      {policies[code] ?? true ? "允许" : "锁定"}
                    </label>
                  </div>
                ))}
              </div>
              <label className="confirmation-check">
                <input
                  checked={acknowledged}
                  onChange={(event) => setAcknowledged(event.target.checked)}
                  type="checkbox"
                />
                我已逐场确认不可调政策，并核对场次数、容量、场地与修改差异。
              </label>
              <button
                className="danger-action"
                disabled={!acknowledged || busy || batch.status === "CONFIRMED"}
                onClick={() => void confirm()}
                type="button"
              >
                {batch.status === "CONFIRMED" ? "已写入正式赛程" : "二次确认并原子写入"}
              </button>
            </>
          )}
        </section>
      )}

      {error && <div className="form-error">{error}</div>}
      {message && <div className="form-success">{message}</div>}
    </div>
  );
}

function Summary({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
