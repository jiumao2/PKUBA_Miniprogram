import { createPkubaClient, type RequestAdapter } from "@pkuba/api-client";
import Taro from "@tarojs/taro";

function taroRequest<T>(url: string): Promise<{ status: number; data: T }> {
  return new Promise((resolve, reject) => {
    Taro.request({
      url,
      header: { Accept: "application/json" },
      success: (response) =>
        resolve({ status: response.statusCode, data: response.data as T }),
      fail: (error) => reject(new Error(error.errMsg)),
    });
  });
}

export const api = createPkubaClient(PKUBA_API_BASE_URL, taroRequest);
