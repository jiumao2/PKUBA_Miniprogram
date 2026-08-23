import { Button, Image, Switch, Text, View } from "@tarojs/components";
import Taro, { useDidShow, useRouter } from "@tarojs/taro";
import { useMemo, useRef, useState } from "react";
import type {
  GameMediaAsset,
  GameMediaCollection,
  MiniAppMe,
  PublicGameDetail,
  PublicScoresheetStat,
} from "@pkuba/api-client";

import {
  absoluteMediaUrl,
  api,
  replaceGameMedia,
  uploadGameMedia,
  type GameMediaKind,
} from "../../api";
import { getMiniAppSession } from "../../auth";
import { formatDate } from "../../format";
import "./index.css";

const MEDIA_GROUPS: ReadonlyArray<{ kind: GameMediaKind; label: string }> = [
  { kind: "SCORESHEET", label: "记录表" },
  { kind: "GROUP_PHOTO", label: "比赛合照" },
  { kind: "GAME_PHOTO", label: "其他照片" },
];

export default function GameDetailPage() {
  const router = useRouter();
  const gameId = router.params.id ?? "";
  const [detail, setDetail] = useState<PublicGameDetail | null>(null);
  const [me, setMe] = useState<MiniAppMe | null>(null);
  const [collection, setCollection] = useState<GameMediaCollection | null>(null);
  const [publicLoading, setPublicLoading] = useState(true);
  const [privateLoading, setPrivateLoading] = useState(false);
  const [scoresheetConfirmed, setScoresheetConfirmed] = useState(false);
  const [uploadingKind, setUploadingKind] = useState<GameMediaKind | null>(null);
  const [progress, setProgress] = useState(0);
  const [message, setMessage] = useState("");
  const requestVersionRef = useRef(0);

  const loadPrivate = async (token: string, requestVersion: number) => {
    setPrivateLoading(true);
    const [meResult, mediaResult] = await Promise.allSettled([
      api.getMiniAppMe(token),
      api.getGameMedia(gameId, token),
    ]);
    if (requestVersion !== requestVersionRef.current) return;
    setMe(meResult.status === "fulfilled" ? meResult.value : null);
    setCollection(mediaResult.status === "fulfilled" ? mediaResult.value : null);
    setPrivateLoading(false);
  };

  const load = async () => {
    if (!gameId) {
      setMessage("比赛参数无效。");
      setPublicLoading(false);
      return;
    }
    const requestVersion = ++requestVersionRef.current;
    if (!detail) setPublicLoading(true);
    setMessage("");
    try {
      const result = await api.getGameDetail(gameId);
      if (requestVersion !== requestVersionRef.current) return;
      setDetail(result);
      setPublicLoading(false);
      const token = getMiniAppSession();
      if (token) await loadPrivate(token, requestVersion);
      else {
        setMe(null);
        setCollection(null);
      }
    } catch (reason: unknown) {
      if (requestVersion === requestVersionRef.current) {
        setMessage(reason instanceof Error ? reason.message : "比赛详情读取失败");
        setPublicLoading(false);
      }
    }
  };

  useDidShow(() => {
    void load();
  });

  const publicPhotoUrls = useMemo(
    () => detail?.group_photos.map((photo) => absoluteMediaUrl(photo.content_url)) ?? [],
    [detail],
  );
  const privatePhotoUrls = useMemo(
    () => collection?.assets
      .filter((asset) => asset.storage_status === "ONLINE" && asset.content_url)
      .map((asset) => absoluteMediaUrl(asset.content_url)) ?? [],
    [collection],
  );
  const isParticipatingLeader = Boolean(
    me?.leader_binding
      && detail
      && [detail.game.home_team_id, detail.game.away_team_id].includes(me.leader_binding.team_id),
  );

  const chooseAndUpload = async (kind: GameMediaKind) => {
    const token = getMiniAppSession();
    if (!token || !detail) return;
    if (kind === "SCORESHEET" && !scoresheetConfirmed) {
      setMessage("请先确认记录表已正确结表且整张表清晰完整。");
      return;
    }
    setMessage("");
    try {
      const selected = await Taro.chooseMedia({
        count: kind === "SCORESHEET" ? 1 : 9,
        mediaType: ["image"],
        sourceType: ["album", "camera"],
        sizeType: ["original"],
      });
      setUploadingKind(kind);
      for (let index = 0; index < selected.tempFiles.length; index += 1) {
        const file = selected.tempFiles[index];
        await uploadGameMedia(
          detail.game.id,
          file.tempFilePath,
          kind,
          scoresheetConfirmed,
          token,
          (value) => setProgress(Math.round(
            ((index + value / 100) / selected.tempFiles.length) * 100,
          )),
        );
      }
      Taro.showToast({ title: "上传完成", icon: "success" });
      if (kind === "SCORESHEET") setScoresheetConfirmed(false);
      await load();
    } catch (reason: unknown) {
      const text = reason instanceof Error ? reason.message : "图片上传失败";
      if (!text.includes("cancel")) setMessage(text);
    } finally {
      setUploadingKind(null);
      setProgress(0);
    }
  };

  const replaceAsset = async (asset: GameMediaAsset) => {
    const token = getMiniAppSession();
    if (!token) return;
    let confirmed = false;
    if (asset.kind === "SCORESHEET") {
      const confirmation = await Taro.showModal({
        title: "重新上传记录表",
        content: "请确认新照片中的应填项目、最终比分和签字已经完成，整张表无缺角、无遮挡。",
        confirmText: "已核对",
        confirmColor: "#c91f26",
      });
      if (!confirmation.confirm) return;
      confirmed = true;
    }
    try {
      const selected = await Taro.chooseMedia({
        count: 1,
        mediaType: ["image"],
        sourceType: ["album", "camera"],
        sizeType: ["original"],
      });
      const file = selected.tempFiles[0];
      if (!file) return;
      setUploadingKind(asset.kind as GameMediaKind);
      await replaceGameMedia(
        asset.id,
        asset.version,
        file.tempFilePath,
        confirmed,
        token,
        setProgress,
      );
      Taro.showToast({ title: "已重新上传", icon: "success" });
      await load();
    } catch (reason: unknown) {
      const text = reason instanceof Error ? reason.message : "图片替换失败";
      if (!text.includes("cancel")) setMessage(text);
    } finally {
      setUploadingKind(null);
      setProgress(0);
    }
  };

  const reviewAsset = async (asset: GameMediaAsset, approve: boolean) => {
    const token = getMiniAppSession();
    if (!token) return;
    let note = "";
    if (!approve) {
      const reasons = ["图片不清晰", "图片内容与比赛不符", "需要重新上传"];
      try {
        const result = await Taro.showActionSheet({ itemList: reasons });
        note = reasons[result.tapIndex] ?? "需要重新上传";
      } catch {
        return;
      }
    }
    try {
      await api.reviewGameMedia(asset.id, asset.version, approve, note, token);
      await load();
    } catch (reason: unknown) {
      setMessage(reason instanceof Error ? reason.message : "审核失败");
    }
  };

  const deleteAsset = async (asset: GameMediaAsset) => {
    const token = getMiniAppSession();
    if (!token) return;
    const result = await Taro.showModal({
      title: "删除照片",
      content: "删除后将保留审计记录，但普通页面不再显示该照片。",
      confirmText: "删除",
      confirmColor: "#c91f26",
    });
    if (!result.confirm) return;
    try {
      await api.deleteGameMedia(asset.id, asset.version, token);
      await load();
    } catch (reason: unknown) {
      setMessage(reason instanceof Error ? reason.message : "删除失败");
    }
  };

  const moveAsset = async (asset: GameMediaAsset, direction: -1 | 1) => {
    const token = getMiniAppSession();
    if (!token || !collection) return;
    const group = collection.assets.filter((item) => item.kind === asset.kind);
    const index = group.findIndex((item) => item.id === asset.id);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= group.length) return;
    [group[index], group[target]] = [group[target], group[index]];
    try {
      await api.reorderGameMedia(
        gameId,
        asset.kind,
        group.map((item) => ({ id: item.id, expected_version: item.version })),
        token,
      );
      await load();
    } catch (reason: unknown) {
      setMessage(reason instanceof Error ? reason.message : "排序失败");
    }
  };

  if (publicLoading && !detail) {
    return <View className="page game-detail-page"><DetailSkeleton /></View>;
  }
  if (!detail) {
    return <View className="page game-detail-page">
      <Text className="page-title">比赛详情</Text>
      <View className="state"><Text className="state-detail">{message || "比赛详情不可用"}</Text></View>
    </View>;
  }

  const stats = detail.stats as PublicScoresheetStat | null;
  const leaderAssets = collection?.assets.filter((asset) => asset.kind === "SCORESHEET") ?? [];
  return <View className="page game-detail-page">
    <Text className="page-title">比赛详情</Text>
    <GameHeading detail={detail} />

    {!!detail.group_photos.length && <View className="public-photo-section detail-section">
      <Text className="detail-section-title">比赛合照</Text>
      <View className="public-photo-list">
        {detail.group_photos.map((photo, index) => <Image
          className="public-group-photo"
          key={photo.id}
          mode="widthFix"
          src={absoluteMediaUrl(photo.content_url)}
          onClick={() => void Taro.previewImage({
            current: publicPhotoUrls[index],
            urls: publicPhotoUrls,
          })}
        />)}
      </View>
    </View>}

    <View className="detail-section game-stat-section">
      <Text className="detail-section-title">单场数据</Text>
      {stats ? <PublishedGameStats stats={stats} /> : (
        <Text className="detail-empty">本场暂无已发布的球员数据。</Text>
      )}
    </View>

    {isParticipatingLeader && <View className="detail-section leader-material-section">
      <Text className="detail-section-title">领队资料</Text>
      <Text className="detail-copy">参赛领队可查看本场当前正式发布所依据的记录表原图。</Text>
      <PrivateMediaGrid assets={leaderAssets} previewUrls={privatePhotoUrls} readOnly />
    </View>}

    {me?.admin_role && collection && <View className="detail-section admin-media-section">
      <View className="admin-section-heading">
        <Text className="detail-section-title">管理员比赛资料</Text>
        <Text className="admin-role-badge">
          {me.admin_role === "SUPERADMIN" ? "超级管理员" : "普通管理员"}
        </Text>
      </View>
      <Text className="detail-copy">比赛合照上传后立即公开；审核状态仅供内部管理，不控制公众展示。</Text>
      {privateLoading && <Text className="private-refreshing">正在更新资料</Text>}
      <AdminMediaLibrary
        collection={collection}
        previewUrls={privatePhotoUrls}
        onReplace={(asset) => void replaceAsset(asset)}
        onReview={(asset, approve) => void reviewAsset(asset, approve)}
        onDelete={(asset) => void deleteAsset(asset)}
        onMove={(asset, direction) => void moveAsset(asset, direction)}
      />
      {collection.can_upload && <MediaUploader
        scoresheetConfirmed={scoresheetConfirmed}
        uploadingKind={uploadingKind}
        progress={progress}
        onConfirmation={setScoresheetConfirmed}
        onUpload={(kind) => void chooseAndUpload(kind)}
      />}
    </View>}
    {message && <View className="media-feedback"><Text>{message}</Text></View>}
  </View>;
}

function GameHeading({ detail }: { detail: PublicGameDetail }) {
  const game = detail.game;
  return <View className={`detail-game-heading ${game.division_gender === "WOMEN" ? "is-women" : ""}`}>
    <Text className="detail-division">{game.division_name} · {formatDate(game.date)} {game.start_time}</Text>
    <View className="detail-matchup">
      <Text>{game.home_name}</Text>
      <Text className="detail-score">{scoreLabel(game.home_score, game.away_score)}</Text>
      <Text>{game.away_name}</Text>
    </View>
    <Text className="detail-venue">{game.venue_name}</Text>
  </View>;
}

function PublishedGameStats({ stats }: { stats: PublicScoresheetStat }) {
  const players = stats.player_stats.filter(
    (row) => row.appeared || row.points || row.personal_fouls,
  );
  if (!players.length) return <Text className="detail-empty">本场暂无球员数据。</Text>;
  return <View className="published-stats">
    <View className="player-detail-head"><Text>球员</Text><Text>得分</Text><Text>1/2/3分</Text><Text>犯规</Text></View>
    {players.map((player) => <View
      className="player-detail-row"
      key={`${player.team_id}-${player.player_id ?? player.player_name}`}
    >
      <View>
        <Text>#{player.jersey_number || "–"} {player.player_name}</Text>
        <Text>{player.team_name}{player.starter ? " · 首发" : ""}</Text>
      </View>
      <Text>{player.points}</Text>
      <Text>{player.one_point_events}/{player.two_point_events}/{player.three_point_events}</Text>
      <Text>{player.personal_fouls}</Text>
    </View>)}
  </View>;
}

function PrivateMediaGrid({
  assets,
  previewUrls,
  readOnly = false,
}: {
  assets: GameMediaAsset[];
  previewUrls: string[];
  readOnly?: boolean;
}) {
  if (!assets.length) return <Text className="detail-empty">暂无可查看的正式记录表原图。</Text>;
  return <View className="private-media-grid">
    {assets.map((asset) => <View className="private-media-card" key={asset.id}>
      {asset.storage_status === "ONLINE" && asset.content_url ? <Image
        className="private-media-image"
        mode="aspectFill"
        src={absoluteMediaUrl(asset.content_url)}
        onClick={() => void Taro.previewImage({
          current: absoluteMediaUrl(asset.content_url),
          urls: previewUrls,
        })}
      /> : <View className="media-offline-placeholder"><Text>照片已归档至线下备份</Text></View>}
      {!readOnly && <Text className="media-kind">{mediaKindLabel(asset.kind)}</Text>}
    </View>)}
  </View>;
}

function AdminMediaLibrary({
  collection,
  previewUrls,
  onReplace,
  onReview,
  onDelete,
  onMove,
}: {
  collection: GameMediaCollection;
  previewUrls: string[];
  onReplace: (asset: GameMediaAsset) => void;
  onReview: (asset: GameMediaAsset, approve: boolean) => void;
  onDelete: (asset: GameMediaAsset) => void;
  onMove: (asset: GameMediaAsset, direction: -1 | 1) => void;
}) {
  if (!collection.assets.length) return <Text className="detail-empty">尚未上传比赛资料。</Text>;
  return <View className="admin-media-groups">{MEDIA_GROUPS.map((group) => {
    const assets = collection.assets.filter((asset) => asset.kind === group.kind);
    if (!assets.length) return null;
    return <View className="admin-media-group" key={group.kind}>
      <View className="media-group-heading">
        <Text className="media-group-title">{group.label}</Text>
        <Text className="media-count">{assets.length} 张</Text>
      </View>
      <View className="admin-media-list">{assets.map((asset, index) => {
        const online = asset.storage_status === "ONLINE" && Boolean(asset.content_url);
        return <View className="admin-media-card" key={asset.id}>
          {online ? <Image
            className="admin-media-image"
            mode="aspectFill"
            src={absoluteMediaUrl(asset.content_url)}
            onClick={() => void Taro.previewImage({
              current: absoluteMediaUrl(asset.content_url),
              urls: previewUrls,
            })}
          /> : <View className="media-offline-placeholder"><Text>照片已归档至线下备份</Text></View>}
          <View className="admin-media-meta">
            <Text>{asset.width}×{asset.height}</Text>
            <Text className={`media-status status-${asset.review_status.toLowerCase()}`}>
              {reviewLabel(asset.review_status)}
            </Text>
          </View>
          {asset.review_note && <Text className="media-review-note">{asset.review_note}</Text>}
          <View className="media-order-row">
            {collection.can_upload && <Button disabled={index === 0} onClick={() => onMove(asset, -1)}>上移</Button>}
            {collection.can_upload && <Button disabled={index === assets.length - 1} onClick={() => onMove(asset, 1)}>下移</Button>}
            {collection.can_upload && online && <Button onClick={() => onReplace(asset)}>替换</Button>}
          </View>
          {collection.can_review && asset.kind !== "SCORESHEET" && <View className="media-review-row">
            <Button onClick={() => onReview(asset, true)}>通过</Button>
            <Button onClick={() => onReview(asset, false)}>未通过</Button>
            <Button className="danger" onClick={() => onDelete(asset)}>删除</Button>
          </View>}
        </View>;
      })}</View>
    </View>;
  })}</View>;
}

function MediaUploader({
  scoresheetConfirmed,
  uploadingKind,
  progress,
  onConfirmation,
  onUpload,
}: {
  scoresheetConfirmed: boolean;
  uploadingKind: GameMediaKind | null;
  progress: number;
  onConfirmation: (value: boolean) => void;
  onUpload: (kind: GameMediaKind) => void;
}) {
  return <View className="media-uploader">
    <Text className="media-uploader-title">上传比赛资料</Text>
    <View className="scoresheet-confirmation">
      <Text>记录表已正确结表，整张表清晰、完整、无遮挡</Text>
      <Switch
        checked={scoresheetConfirmed}
        color="#c91f26"
        onChange={(event) => onConfirmation(event.detail.value)}
      />
    </View>
    <View className="upload-actions">
      {MEDIA_GROUPS.map((group) => <Button
        className={group.kind === "SCORESHEET" ? "media-primary" : "media-secondary"}
        disabled={uploadingKind !== null}
        key={group.kind}
        onClick={() => onUpload(group.kind)}
      >{uploadingKind === group.kind ? `上传中 ${progress}%` : `上传${group.label}`}</Button>)}
    </View>
  </View>;
}

function DetailSkeleton() {
  return <View className="detail-skeleton" aria-label="正在读取比赛详情">
    <View className="detail-skeleton-title" />
    <View className="detail-skeleton-score" />
    <View className="detail-skeleton-block" />
    <View className="detail-skeleton-block short" />
  </View>;
}

function scoreLabel(homeScore: number | null, awayScore: number | null) {
  return homeScore === null || awayScore === null ? "vs" : `${homeScore} : ${awayScore}`;
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
