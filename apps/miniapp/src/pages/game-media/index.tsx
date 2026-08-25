import { Button, Image, Text, View } from "@tarojs/components";
import Taro, { useDidShow, useRouter } from "@tarojs/taro";
import { useMemo, useRef, useState } from "react";
import {
  formatOfficialScore,
  type GameMediaAsset,
  type GameMediaCollection,
  type MiniAppMe,
  type PublicGameDetail,
  type PublicScoresheetStat,
} from "@pkuba/api-client";

import {
  absoluteMediaUrl,
  api,
  replaceGameMedia,
  uploadGameMedia,
  type GameMediaKind,
} from "../../api";
import { getMiniAppSession, resolveMiniAppIdentity } from "../../auth";
import { formatDate } from "../../format";
import { mediaAssetActions, mediaGroupPresentation } from "./viewModel";
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
  const [uploadingTarget, setUploadingTarget] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const [message, setMessage] = useState("");
  const [privateError, setPrivateError] = useState("");
  const requestVersionRef = useRef(0);

  const loadPrivate = async (requestVersion: number) => {
    setPrivateLoading(true);
    setPrivateError("");
    try {
      const identity = await resolveMiniAppIdentity();
      if (requestVersion !== requestVersionRef.current) return;
      setMe(identity.me);
      if (!identity.token || !identity.me) {
        setCollection(null);
        return;
      }
      try {
        const media = await api.getGameMedia(gameId, identity.token);
        if (requestVersion === requestVersionRef.current) setCollection(media);
      } catch (reason: unknown) {
        if (requestVersion === requestVersionRef.current) {
          setPrivateError(
            reason instanceof Error ? reason.message : "比赛私有资料读取失败，请重试。",
          );
        }
      }
    } catch (reason: unknown) {
      if (requestVersion === requestVersionRef.current) {
        setPrivateError(
          reason instanceof Error ? reason.message : "身份核对失败，请重试。",
        );
      }
    } finally {
      if (requestVersion === requestVersionRef.current) setPrivateLoading(false);
    }
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
      await loadPrivate(requestVersion);
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
    let confirmed = false;
    if (kind === "SCORESHEET") {
      const confirmation = await Taro.showModal({
        title: "上传记录表",
        content: "请确认记录表已正确结表，应填项目、最终比分和签字完整，整张照片无缺角、无遮挡。",
        confirmText: "已核对",
        confirmColor: "#c91f26",
      });
      if (!confirmation.confirm) return;
      confirmed = true;
    }
    setMessage("");
    try {
      const selected = await Taro.chooseMedia({
        count: kind === "GAME_PHOTO" ? 9 : 1,
        mediaType: ["image"],
        sourceType: ["album", "camera"],
        sizeType: ["original"],
      });
      setUploadingTarget(`new:${kind}`);
      const failures: string[] = [];
      let successCount = 0;
      for (let index = 0; index < selected.tempFiles.length; index += 1) {
        const file = selected.tempFiles[index];
        try {
          await uploadGameMedia(
            detail.game.id,
            file.tempFilePath,
            kind,
            confirmed,
            token,
            (value) => setProgress(Math.round(
              ((index + value / 100) / selected.tempFiles.length) * 100,
            )),
          );
          successCount += 1;
        } catch (reason: unknown) {
          failures.push(
            `第 ${index + 1} 张：${reason instanceof Error ? reason.message : "上传失败"}`,
          );
        }
      }
      if (successCount > 0) await load();
      if (failures.length === 0) {
        Taro.showToast({ title: "上传完成", icon: "success" });
      } else {
        setMessage(`已上传 ${successCount} 张，失败 ${failures.length} 张。${failures.join("；")}`);
      }
    } catch (reason: unknown) {
      const text = reason instanceof Error ? reason.message : "图片上传失败";
      if (!text.includes("cancel")) setMessage(text);
    } finally {
      setUploadingTarget(null);
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
      setUploadingTarget(asset.id);
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
      setUploadingTarget(null);
      setProgress(0);
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
      <Text className="detail-section-title">比赛资料管理</Text>
      {privateLoading && <Text className="private-refreshing">正在更新资料</Text>}
      <AdminMediaLibrary
        collection={collection}
        previewUrls={privatePhotoUrls}
        uploadingTarget={uploadingTarget}
        progress={progress}
        onUpload={(kind) => void chooseAndUpload(kind)}
        onReplace={(asset) => void replaceAsset(asset)}
        onDelete={(asset) => void deleteAsset(asset)}
      />
    </View>}
    {privateError && <View className="private-media-error">
      <Text>{privateError}</Text>
      <Button onClick={() => void loadPrivate(requestVersionRef.current)}>重新读取私有资料</Button>
    </View>}
    {message && <View className="media-feedback"><Text>{message}</Text></View>}
  </View>;
}

function GameHeading({ detail }: { detail: PublicGameDetail }) {
  const game = detail.game;
  const score = formatOfficialScore(game.home_score, game.away_score, " : ");
  return <View className={`detail-game-heading ${game.division_gender === "WOMEN" ? "is-women" : ""}`}>
    <Text className="detail-division">{game.division_name} · {formatDate(game.date)} {game.start_time}</Text>
    <View className="detail-matchup">
      <Text>{game.home_name}</Text>
      {score && <Text className="detail-score">{score}</Text>}
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
  const sideStats = new Map(stats.team_stats.map((row) => [String(row.side ?? ""), row]));
  const teams = [
    { side: "A", name: stats.home_name, score: stats.home_score },
    { side: "B", name: stats.away_name, score: stats.away_score },
  ];
  return <View className="published-stats">
    {teams.map((team) => {
      const teamId = String(sideStats.get(team.side)?.team_id ?? "");
      const teamPlayers = players.filter((player) => (
        teamId ? player.team_id === teamId : player.team_name === team.name
      ));
      return <View className={`team-stat-table side-${team.side.toLowerCase()}`} key={team.side}>
        <View className="team-stat-heading">
          <Text>{team.name}</Text>
          <Text>{team.score} 分</Text>
        </View>
        <View className="player-detail-head"><Text>球员</Text><Text>得分</Text><Text>1/2/3分</Text><Text>犯规</Text></View>
        {teamPlayers.length ? teamPlayers.map((player) => <View
          className="player-detail-row"
          key={`${player.team_id}-${player.player_id ?? player.player_name}`}
        >
          <View>
            <Text className="player-name">#{player.jersey_number || "–"} {player.player_name}</Text>
            {player.starter && <Text className="starter-badge">首发</Text>}
          </View>
          <Text>{player.points}</Text>
          <Text>{player.one_point_events}/{player.two_point_events}/{player.three_point_events}</Text>
          <Text>{player.personal_fouls}</Text>
        </View>) : <Text className="team-stat-empty">本队暂无球员数据</Text>}
      </View>;
    })}
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
  uploadingTarget,
  progress,
  onUpload,
  onReplace,
  onDelete,
}: {
  collection: GameMediaCollection;
  previewUrls: string[];
  uploadingTarget: string | null;
  progress: number;
  onUpload: (kind: GameMediaKind) => void;
  onReplace: (asset: GameMediaAsset) => void;
  onDelete: (asset: GameMediaAsset) => void;
}) {
  const busy = uploadingTarget !== null;
  return <View className="admin-media-groups">{MEDIA_GROUPS.map((group) => {
    const assets = collection.assets.filter((asset) => asset.kind === group.kind);
    const groupPresentation = mediaGroupPresentation(
      group.kind,
      assets.length,
      collection.can_upload,
    );
    return <View className="admin-media-group" key={group.kind}>
      <Text className="media-group-title">{group.label}</Text>
      <View className="admin-media-list">{assets.map((asset, index) => {
        const actions = mediaAssetActions(asset);
        const online = actions.online;
        const active = uploadingTarget === asset.id;
        return <View className="admin-media-row" key={asset.id}>
          {online ? <Image
            className="admin-media-image"
            mode="aspectFill"
            src={absoluteMediaUrl(asset.content_url)}
            onClick={() => void Taro.previewImage({
              current: absoluteMediaUrl(asset.content_url),
              urls: previewUrls,
            })}
          /> : <View className="media-offline-placeholder"><Text>照片已归档至线下备份</Text></View>}
          <View className="admin-media-content">
            <Text className="admin-media-name">
              {group.kind === "GAME_PHOTO" ? `其他照片 ${index + 1}` : group.label}
            </Text>
            <Text className="admin-media-state">
              {online ? "已上传" : "照片已归档至线下备份"}
            </Text>
            {(actions.showReplace || actions.showDelete) && <View className="media-row-actions">
              {actions.showReplace && <Button
                className="media-replace"
                disabled={busy}
                onClick={() => onReplace(asset)}
              >{active ? `上传中 ${progress}%` : "重新上传"}</Button>}
              {actions.showDelete && <Button
                className="media-delete"
                disabled={busy}
                onClick={() => onDelete(asset)}
              >删除</Button>}
            </View>}
            {active && <View className="media-progress-track">
              <View className="media-progress-value" style={{ width: `${progress}%` }} />
            </View>}
          </View>
        </View>;
      })}
      {!assets.length && <View className="admin-media-row is-empty">
        <View className="media-empty-thumb"><Text>暂无</Text></View>
        <View className="admin-media-content">
          <Text className="admin-media-name">尚未上传{group.label}</Text>
          {groupPresentation.showEmptyAction && <Button
            className="media-upload"
            disabled={busy}
            onClick={() => onUpload(group.kind)}
          >{uploadingTarget === `new:${group.kind}` ? `上传中 ${progress}%` : groupPresentation.emptyActionLabel}</Button>}
          {uploadingTarget === `new:${group.kind}` && <View className="media-progress-track">
            <View className="media-progress-value" style={{ width: `${progress}%` }} />
          </View>}
        </View>
      </View>}
      {groupPresentation.showAddMore && <Button
        className="media-add-more"
        disabled={busy}
        onClick={() => onUpload(group.kind)}
      >{uploadingTarget === "new:GAME_PHOTO" ? `上传中 ${progress}%` : "添加其他照片"}</Button>}
      </View>
    </View>;
  })}</View>;
}

function DetailSkeleton() {
  return <View className="detail-skeleton" aria-label="正在读取比赛详情">
    <View className="detail-skeleton-title" />
    <View className="detail-skeleton-score" />
    <View className="detail-skeleton-block" />
    <View className="detail-skeleton-block short" />
  </View>;
}

function mediaKindLabel(kind: string) {
  return ({
    SCORESHEET: "记录表",
    GROUP_PHOTO: "比赛合照",
    GAME_PHOTO: "其他照片",
  } as Record<string, string>)[kind] ?? kind;
}
