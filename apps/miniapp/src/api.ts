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
      timeout: 30_000,
      header: { Accept: "application/json", ...options.headers },
      data: options.body,
      success: (response) =>
        resolve({ status: response.statusCode, data: response.data as T }),
      fail: (error) => reject(new Error(
        error.errMsg.toLowerCase().includes("timeout")
          ? "请求超时，请重试。"
          : error.errMsg,
      )),
    });
  });
}

export const api = createPkubaClient(PKUBA_API_BASE_URL, taroRequest);

export type GameMediaKind = "SCORESHEET" | "GROUP_PHOTO" | "GAME_PHOTO";

// Keep an unresolved upload's key across ordinary page reentry. A different
// account, file, target or source version is a different operation.
const mediaOperationKeys = new Map<string, string>();

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
  idempotencyKey?: string,
): Promise<GameMediaAsset> {
  const operation = JSON.stringify([token, "upload", gameId, filePath, kind, scoresheetCompleteConfirmed]);
  const key = idempotencyKey ?? mediaOperationKeys.get(operation) ?? createIdempotencyKey();
  mediaOperationKeys.set(operation, key);
  return new Promise((resolve, reject) => {
    const start = (attempt: number) => {
      const task = Taro.uploadFile({
        url: `${PKUBA_API_BASE_URL.replace(/\/$/, "")}/api/v1/game-media/games/${gameId}`,
        filePath,
        name: "image",
        header: {
          Authorization: `Bearer ${token}`,
          "Idempotency-Key": key,
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
          if (mediaOperationKeys.get(operation) === key) mediaOperationKeys.delete(operation);
          resolve(data as GameMediaAsset);
        },
        fail: (error) => {
          if (attempt === 0) start(1);
          else reject(new Error(error.errMsg));
        },
      });
      task.onProgressUpdate((event) => onProgress?.(event.progress));
    };
    start(0);
  });
}

export function replaceGameMedia(
  assetId: string,
  expectedVersion: number,
  filePath: string,
  scoresheetCompleteConfirmed: boolean,
  token: string,
  onProgress?: (progress: number) => void,
  idempotencyKey?: string,
): Promise<GameMediaAsset> {
  const operation = JSON.stringify([token, "replace", assetId, expectedVersion, filePath, scoresheetCompleteConfirmed]);
  const key = idempotencyKey ?? mediaOperationKeys.get(operation) ?? createIdempotencyKey();
  mediaOperationKeys.set(operation, key);
  return new Promise((resolve, reject) => {
    const start = (attempt: number) => {
      const task = Taro.uploadFile({
        url: `${PKUBA_API_BASE_URL.replace(/\/$/, "")}/api/v1/game-media/assets/${assetId}/replace`,
        filePath,
        name: "image",
        header: {
          Authorization: `Bearer ${token}`,
          "Idempotency-Key": key,
        },
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
          if (mediaOperationKeys.get(operation) === key) mediaOperationKeys.delete(operation);
          resolve(data as GameMediaAsset);
        },
        fail: (error) => {
          if (attempt === 0) start(1);
          else reject(new Error(error.errMsg));
        },
      });
      task.onProgressUpdate((event) => onProgress?.(event.progress));
    };
    start(0);
  });
}
