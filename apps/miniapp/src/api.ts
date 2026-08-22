import {
  ApiError,
  createIdempotencyKey,
  createPkubaClient,
  type GameMediaAsset,
  type RequestAdapter,
  type RequestOptions,
} from "@pkuba/api-client";
import Taro from "@tarojs/taro";

function taroRequest<T>(
  url: string,
  options: RequestOptions = {},
): Promise<{ status: number; data: T }> {
  return new Promise((resolve, reject) => {
    Taro.request({
      url,
      method: options.method ?? "GET",
      header: { Accept: "application/json", ...options.headers },
      data: options.body,
      success: (response) =>
        resolve({ status: response.statusCode, data: response.data as T }),
      fail: (error) => reject(new Error(error.errMsg)),
    });
  });
}

export const api = createPkubaClient(PKUBA_API_BASE_URL, taroRequest);

export type GameMediaKind = "SCORESHEET" | "GROUP_PHOTO" | "GAME_PHOTO";

export function absoluteMediaUrl(path: string) {
  if (/^https?:\/\//.test(path)) return path;
  return `${PKUBA_API_BASE_URL.replace(/\/$/, "")}${path}`;
}

export function uploadGameMedia(
  gameId: string,
  filePath: string,
  kind: GameMediaKind,
  scoresheetCompleteConfirmed: boolean,
  token: string,
  onProgress?: (progress: number) => void,
  idempotencyKey = createIdempotencyKey(),
): Promise<GameMediaAsset> {
  return new Promise((resolve, reject) => {
    const task = Taro.uploadFile({
      url: `${PKUBA_API_BASE_URL.replace(/\/$/, "")}/api/v1/game-media/games/${gameId}`,
      filePath,
      name: "image",
      header: {
        Authorization: `Bearer ${token}`,
        "Idempotency-Key": idempotencyKey,
      },
      formData: {
        kind,
        scoresheet_complete_confirmed: scoresheetCompleteConfirmed ? "true" : "false",
      },
      success: (response) => {
        let data: GameMediaAsset | { message?: string; code?: string };
        try {
          data = JSON.parse(response.data) as typeof data;
        } catch {
          reject(new ApiError("服务器返回了无法识别的上传结果", response.statusCode));
          return;
        }
        if (response.statusCode < 200 || response.statusCode >= 300) {
          const error = data as { message?: string; code?: string };
          reject(new ApiError(error.message ?? "图片上传失败", response.statusCode, error.code));
          return;
        }
        resolve(data as GameMediaAsset);
      },
      fail: (error) => reject(new Error(error.errMsg)),
    });
    task.onProgressUpdate((event) => onProgress?.(event.progress));
  });
}

export function replaceGameMedia(
  assetId: string,
  expectedVersion: number,
  filePath: string,
  scoresheetCompleteConfirmed: boolean,
  token: string,
  onProgress?: (progress: number) => void,
): Promise<GameMediaAsset> {
  return new Promise((resolve, reject) => {
    const task = Taro.uploadFile({
      url: `${PKUBA_API_BASE_URL.replace(/\/$/, "")}/api/v1/game-media/assets/${assetId}/replace`,
      filePath,
      name: "image",
      header: { Authorization: `Bearer ${token}` },
      formData: {
        expected_version: String(expectedVersion),
        scoresheet_complete_confirmed: scoresheetCompleteConfirmed ? "true" : "false",
      },
      success: (response) => {
        let data: GameMediaAsset | { message?: string; code?: string };
        try {
          data = JSON.parse(response.data) as typeof data;
        } catch {
          reject(new ApiError("服务器返回了无法识别的上传结果", response.statusCode));
          return;
        }
        if (response.statusCode < 200 || response.statusCode >= 300) {
          const error = data as { message?: string; code?: string };
          reject(new ApiError(error.message ?? "图片替换失败", response.statusCode, error.code));
          return;
        }
        resolve(data as GameMediaAsset);
      },
      fail: (error) => reject(new Error(error.errMsg)),
    });
    task.onProgressUpdate((event) => onProgress?.(event.progress));
  });
}
