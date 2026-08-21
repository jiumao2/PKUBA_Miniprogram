import { useCallback, useEffect, useState } from "react";
import type { GameMediaAsset } from "@pkuba/api-client";

import "./operation-pages.css";

type AdminClient = ReturnType<typeof import("@pkuba/api-client").createAdminClient>;

export function MediaReviewPage({ client }: { client: AdminClient }) {
  const [assets, setAssets] = useState<GameMediaAsset[]>([]);
  const [filter, setFilter] = useState("PENDING");
  const [kindFilter, setKindFilter] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [note, setNote] = useState("");
  const [replacementFile, setReplacementFile] = useState<File | null>(null);
  const [replacementConfirmed, setReplacementConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    setMessage("");
    try {
      const result = (await client.listAdminGameMedia(filter, kindFilter)).filter(
        (asset) => asset.kind !== "SCORESHEET",
      );
      setAssets(result);
      setSelectedId((current) =>
        result.some((asset) => asset.id === current) ? current : (result[0]?.id ?? ""),
      );
    } catch (reason: unknown) {
      setMessage(reason instanceof Error ? reason.message : "无法读取比赛资料");
    }
  }, [client, filter, kindFilter]);

  useEffect(() => { void load(); }, [load]);
  const selected = assets.find((asset) => asset.id === selectedId) ?? null;

  const review = async (approve: boolean) => {
    if (!selected) return;
    if (!approve && !note.trim()) {
      setMessage("未通过时必须填写原因。");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      await client.reviewAdminGameMedia(selected.id, {
        expected_version: selected.version,
        approve,
        note: note.trim(),
      });
      setNote("");
      setMessage(approve ? "图片已通过审核。" : "图片已退回并记录原因。");
      await load();
    } catch (reason: unknown) {
      setMessage(reason instanceof Error ? reason.message : "审核失败");
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!selected || !window.confirm("确认从在线资料中删除这张图片？操作会写入审计日志。")) return;
    setBusy(true);
    try {
      await client.deleteAdminGameMedia(selected.id, selected.version);
      setMessage("图片已从在线资料中删除。");
      await load();
    } catch (reason: unknown) {
      setMessage(reason instanceof Error ? reason.message : "删除失败");
    } finally {
      setBusy(false);
    }
  };

  const replace = async () => {
    if (!selected || !replacementFile) {
      setMessage("请先选择新的图片文件。");
      return;
    }
    if (selected.kind === "SCORESHEET" && !replacementConfirmed) {
      setMessage("重新上传记录表前，请确认已正确结表且照片完整清晰。");
      return;
    }
    if (!window.confirm("确认用新图片替换当前图片？旧文件会保留审计记录，新图片需要重新审核。")) return;
    setBusy(true);
    setMessage("");
    try {
      await client.replaceAdminGameMedia(
        selected.id,
        selected.version,
        replacementConfirmed,
        replacementFile,
      );
      setReplacementFile(null);
      setReplacementConfirmed(false);
      setMessage("新图片已上传并进入待审核，旧图片已从在线资料中移除。");
      await load();
    } catch (reason: unknown) {
      setMessage(reason instanceof Error ? reason.message : "重新上传失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="media-review-layout">
      <div className="media-review-list">
        <div className="operation-heading">
          <div><h2>比赛照片</h2><p>比赛合照和其他照片在这里完成清晰度与内容审核。</p></div>
          <strong>{assets.length} 张</strong>
        </div>
        <div className="review-filter-row">
          <span>状态</span>
          <div className="review-filters">
            {[
              { value: "PENDING", label: "待审核" },
              { value: "APPROVED", label: "已通过" },
              { value: "REJECTED", label: "未通过" },
              { value: "", label: "全部" },
            ].map((item) => (
              <button className={filter === item.value ? "active" : ""} key={item.label} onClick={() => setFilter(item.value)} type="button">{item.label}</button>
            ))}
          </div>
        </div>
        <div className="review-filter-row">
          <span>分类</span>
          <div className="review-filters">
            {[
              { value: "GROUP_PHOTO", label: "比赛合照" },
              { value: "GAME_PHOTO", label: "其他照片" },
              { value: "", label: "全部" },
            ].map((item) => (
              <button className={kindFilter === item.value ? "active" : ""} key={item.label} onClick={() => setKindFilter(item.value)} type="button">{item.label}</button>
            ))}
          </div>
        </div>
        <div className="review-asset-list">
          {assets.map((asset) => (
            <button
              className={selected?.id === asset.id ? "review-asset-row active" : "review-asset-row"}
              key={asset.id}
              onClick={() => {
                setSelectedId(asset.id);
                setNote(asset.review_note);
                setReplacementFile(null);
                setReplacementConfirmed(false);
              }}
              type="button"
            >
              <img src={asset.content_url} alt="" />
              <span>
                <strong>{mediaKindLabel(asset.kind)}</strong>
                <b>{asset.game_label}</b>
                <small>{asset.width}×{asset.height} · {asset.uploaded_by}</small>
              </span>
            </button>
          ))}
          {!assets.length && <p className="operation-empty-copy">当前筛选下没有图片。</p>}
        </div>
      </div>
      <div className="media-review-detail">
        {!selected ? (
          <div className="operation-empty"><h2>审核资料</h2><p>选择左侧图片查看原图和审核状态。</p></div>
        ) : (
          <>
            <div className="operation-heading">
              <div><p className="eyebrow">{mediaKindLabel(selected.kind)}</p><h2>{selected.game_label}</h2></div>
              <span className={`review-state ${selected.review_status.toLowerCase()}`}>{reviewLabel(selected.review_status)}</span>
            </div>
            <a className="review-image-link" href={selected.content_url} rel="noreferrer" target="_blank">
              <img className="review-image" src={selected.content_url} alt={selected.game_label} />
            </a>
            <dl className="review-metadata">
              <div><dt>像素</dt><dd>{selected.width} × {selected.height}</dd></div>
              <div><dt>大小</dt><dd>{formatBytes(selected.byte_size)}</dd></div>
              <div><dt>上传者</dt><dd>{selected.uploaded_by}</dd></div>
              <div><dt>结表确认</dt><dd>{selected.scoresheet_complete_confirmed ? "已确认" : "不适用"}</dd></div>
            </dl>
            <div className="review-replacement" key={selected.id}>
              <strong>重新上传</strong>
              <p>拍错、缺角或模糊时可替换。旧文件保留审计记录，新图片重新进入待审核。</p>
              <input
                accept="image/jpeg,image/png,image/webp"
                disabled={busy}
                onChange={(event) => setReplacementFile(event.target.files?.[0] ?? null)}
                type="file"
              />
              {selected.kind === "SCORESHEET" && (
                <label>
                  <input
                    checked={replacementConfirmed}
                    disabled={busy}
                    onChange={(event) => setReplacementConfirmed(event.target.checked)}
                    type="checkbox"
                  />
                  已核对结表项目、最终比分、签字及整表完整性
                </label>
              )}
              <button disabled={busy || !replacementFile} onClick={() => void replace()} type="button">上传替换图片</button>
            </div>
            <label className="review-note">
              审核说明
              <textarea value={note} onChange={(event) => setNote(event.target.value)} placeholder="未通过时说明缺页、模糊、反光或未结表等问题" />
            </label>
            <div className="review-actions">
              <button className="approve-action" disabled={busy} onClick={() => void review(true)} type="button">通过</button>
              <button className="reject-action" disabled={busy} onClick={() => void review(false)} type="button">退回</button>
              <button className="delete-action" disabled={busy} onClick={() => void remove()} type="button">删除</button>
            </div>
          </>
        )}
        {message && <p className="operation-message" role="status">{message}</p>}
      </div>
    </section>
  );
}

function reviewLabel(status: string) {
  return ({ PENDING: "待审核", APPROVED: "已通过", REJECTED: "未通过" } as Record<string, string>)[status] ?? status;
}

function mediaKindLabel(kind: string) {
  return ({
    SCORESHEET: "记录表",
    GROUP_PHOTO: "比赛合照",
    GAME_PHOTO: "其他照片",
  } as Record<string, string>)[kind] ?? kind;
}

function formatBytes(value: number) {
  return value >= 1024 * 1024
    ? `${(value / 1024 / 1024).toFixed(1)} MB`
    : `${Math.ceil(value / 1024)} KB`;
}
