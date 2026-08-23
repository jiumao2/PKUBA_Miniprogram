import type { MiniAppMe, WeChatExchange } from "@pkuba/api-client";

export interface ResolvedMiniAppIdentity {
  me: MiniAppMe | null;
  token: string;
  requiresProfile: boolean;
}

export interface MiniAppIdentityAdapter {
  readToken(): string;
  clearToken(): void;
  getMe(token: string): Promise<MiniAppMe>;
  exchange(): Promise<WeChatExchange>;
}

export async function resolveMiniAppIdentityWith(
  adapter: MiniAppIdentityAdapter,
): Promise<ResolvedMiniAppIdentity> {
  const savedToken = adapter.readToken();
  if (savedToken) {
    try {
      return {
        me: await adapter.getMe(savedToken),
        token: savedToken,
        requiresProfile: false,
      };
    } catch {
      adapter.clearToken();
    }
  }

  const exchanged = await adapter.exchange();
  return {
    me: exchanged.requires_profile ? null : (exchanged.me ?? null),
    token: exchanged.session_token ?? adapter.readToken(),
    requiresProfile: exchanged.requires_profile,
  };
}
