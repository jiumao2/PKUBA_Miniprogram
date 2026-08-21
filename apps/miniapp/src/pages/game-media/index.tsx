import { Button, Image, Switch, Text, View } from "@tarojs/components";
import Taro, { useDidShow, useRouter } from "@tarojs/taro";
import { useMemo, useState } from "react";
import type { Game, GameMediaAsset, GameMediaCollection } from "@pkuba/api-client";

import {
  absoluteMediaUrl,
  api,
  replaceGameMedia,
  uploadGameMedia,
  type GameMediaKind,
} from "../../api";
import { exchangeCurrentWeChat, getMiniAppSession } from "../../auth";
import { formatDate } from "../../format";
import "./index.css";

const MEDIA_GROUPS: ReadonlyArray<{ kind: GameMediaKind; label: string }> = [
  { kind: "SCORESHEET", label: "记录表" },
  { kind: "GROUP_PHOTO", label: "比赛合照" },
  { kind: "GAME_PHOTO", label: "其他照片" },
];

export default function GameMediaPage() {
  const router = useRouter();
  const gameId = router.params.id ?? "";
  const [game, setGame] = useState<Game | null>(null);
  const [collection, setCollection] = useState<GameMediaCollection | null>(null);
  const [needsProfile, setNeedsProfile] = useState(false);
  const [scoresheetConfirmed, setScoresheetConfirmed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [uploadingKind, setUploadingKind] = useState<GameMediaKind | null>(null);
  const [progress, setProgress] = useState(0);
  const [message, setMessage] = useState("");

  const load = async () => {
    if (!gameId) {
      setMessage("比赛参数无效。");
      setLoading(false);
      return;
    }
    setLoading(true);
    setMessage("");
    try {
      const currentGame = await api.getGame(gameId);
      setGame(currentGame);
      let token = getMiniAppSession();
      if (!token) {
        const exchanged = await exchangeCurrentWeChat();
        if (exchanged.requires_profile) {
          setNeedsProfile(true);
          setCollection(null);
          return;
        }
        token = getMiniAppSession();
      }
      setNeedsProfile(false);
      setCollection(await api.getGameMedia(gameId, token));
    } catch (reason: unknown) {
      setMessage(reason instanceof Error ? reason.message : "比赛资料读取失败");
    } finally {
      setLoading(false);
    }
  };

  useDidShow(() => {
    void load();
  });

  const allUrls = useMemo(
    () => collection?.assets.map((asset) => absoluteMediaUrl(asset.content_url)) ?? [],
    [collection],
  );

  const chooseAndUpload = async (kind: GameMediaKind) => {
    const token = getMiniAppSession();
    if (!token || !game) return;
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
          game.id,
          file.tempFilePath,
          kind,
          scoresheetConfirmed,
          token,
          (value) => setProgress(Math.round(((index + value / 100) / selected.tempFiles.length) * 100)),
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
      });
      if (!confirmation.confirm) return;
      confirmed = true;
    }
    setMessage("");
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

  return (
    <View className="page media-page">
      <Text className="page-title">比赛资料</Text>
      {game && (
        <View className={`media-game-heading ${game.division_gender === "WOMEN" ? "is-women" : ""}`}>
          <Text className="media-division">{game.division_name} · {formatDate(game.date)} {game.start_time}</Text>
          <View className="media-matchup">
            <Text>{game.home_name}</Text>
            <Text className="media-score">{scoreLabel(game)}</Text>
            <Text>{game.away_name}</Text>
          </View>
          <Text className="media-venue">{game.venue_name}</Text>
        </View>
      )}

      {loading && <View className="state"><Text className="state-detail">正在核对身份和资料…</Text></View>}
      {!loading && needsProfile && (
        <View className="media-access">
          <Text className="media-section-title">登录后查看</Text>
          <Text className="media-detail">当季领队和管理员可以查看比赛资料。</Text>
          <Button className="media-primary" onClick={() => Taro.navigateTo({ url: "/pages/auth/index" })}>微信登录</Button>
        </View>
      )}

      {!loading && collection?.can_upload && (
        <View className="media-upload-section">
          <Text className="media-section-title">上传记录表</Text>
          <Text className="media-detail">请上传完整、清晰且可辨认的照片；管理员会查看原图并审核。</Text>
          <View className="scoresheet-confirmation">
            <Text>已核对所有应填写项目、最终比分和签字，整张表无缺角、无遮挡</Text>
            <Switch
              checked={scoresheetConfirmed}
              color="#c91f26"
              onChange={(event) => setScoresheetConfirmed(event.detail.value)}
            />
          </View>
          <Button
            className="media-primary"
            disabled={uploadingKind !== null}
            onClick={() => void chooseAndUpload("SCORESHEET")}
          >
            {uploadingKind === "SCORESHEET" ? `正在上传 ${progress}%` : "选择记录表原图"}
          </Button>

          <View className="media-subsection">
            <Text className="media-section-title">比赛合照</Text>
            <Button
              className="media-secondary"
              disabled={uploadingKind !== null}
              onClick={() => void chooseAndUpload("GROUP_PHOTO")}
            >
              {uploadingKind === "GROUP_PHOTO" ? `正在上传 ${progress}%` : "选择比赛合照"}
            </Button>
          </View>

          <View className="media-subsection">
            <Text className="media-section-title">其他照片</Text>
            <Button
              className="media-secondary"
              disabled={uploadingKind !== null}
              onClick={() => void chooseAndUpload("GAME_PHOTO")}
            >
              {uploadingKind === "GAME_PHOTO" ? `正在上传 ${progress}%` : "选择其他照片"}
            </Button>
          </View>
        </View>
      )}

      {!loading && collection && !collection.can_upload && (
        <Text className="media-permission-note">比赛资料仅由管理员上传；领队只能查看本队比赛当前已发布记录表原图。</Text>
      )}

      {collection && (
        <View className="media-library">
          <View className="media-library-heading">
            <Text className="media-section-title">已上传资料</Text>
            <Text className="media-count">{collection.assets.length} 张</Text>
          </View>
          {collection.assets.length === 0 ? (
            <Text className="media-empty">尚未上传比赛资料。</Text>
          ) : (
            <View className="media-groups">
              {MEDIA_GROUPS.map((group) => {
                const assets = collection.assets.filter((asset) => asset.kind === group.kind);
                if (assets.length === 0) return null;
                return (
                  <View className="media-library-group" key={group.kind}>
                    <View className="media-group-heading">
                      <Text className="media-group-title">{group.label}</Text>
                      <Text className="media-count">{assets.length} 张</Text>
                    </View>
                    <View className="media-grid">
                      {assets.map((asset) => (
                        <MediaAsset
                          asset={asset}
                          canReplace={collection.can_review}
                          key={asset.id}
                          onPreview={() => {
                            const current = absoluteMediaUrl(asset.content_url);
                            void Taro.previewImage({ current, urls: allUrls });
                          }}
                          onReplace={() => void replaceAsset(asset)}
                        />
                      ))}
                    </View>
                  </View>
                );
              })}
            </View>
          )}
        </View>
      )}
      {message && <View className="media-feedback">{message}</View>}
    </View>
  );
}

function MediaAsset({
  asset,
  canReplace,
  onPreview,
  onReplace,
}: {
  asset: GameMediaAsset;
  canReplace: boolean;
  onPreview: () => void;
  onReplace: () => void;
}) {
  return (
    <View className="media-asset">
      <View className="media-preview" onClick={onPreview}>
        <Image className="media-image" src={absoluteMediaUrl(asset.content_url)} mode="aspectFill" />
        <View className="media-asset-meta">
          <Text className="media-kind">{mediaKindLabel(asset.kind)}</Text>
          <Text className={`media-status status-${asset.review_status.toLowerCase()}`}>
            {reviewLabel(asset.review_status)}
          </Text>
        </View>
        <Text className="media-preview-label">查看原图</Text>
        <Text className="media-dimensions">{asset.width}×{asset.height}</Text>
        {asset.review_note && <Text className="media-review-note">{asset.review_note}</Text>}
      </View>
      {canReplace && (
        <Button className="media-replace" onClick={onReplace}>重新上传</Button>
      )}
    </View>
  );
}

function scoreLabel(game: Game) {
  return game.home_score === null || game.away_score === null
    ? "vs"
    : `${game.home_score} : ${game.away_score}`;
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
