import { createAdminClient } from "@pkuba/api-client";

import { MediaReviewPage } from "./MediaReviewPage";

type AdminClient = ReturnType<typeof createAdminClient>;

export function CompetitionMediaPage({
  client,
  seasonId: _seasonId,
  accountRole: _accountRole,
}: {
  client: AdminClient;
  seasonId: string;
  accountRole: string;
}) {
  return (
    <section className="competition-media-page">
      <div className="media-mode-switch" role="tablist" aria-label="比赛资料类型">
        <button
          className=""
          onClick={() => window.location.assign("/scoresheet.html")}
          role="tab"
          type="button"
        >
          记录表识别与发布
        </button>
        <button
          className="active"
          role="tab"
          type="button"
        >
          合照与其他照片
        </button>
      </div>
      <MediaReviewPage client={client} />
    </section>
  );
}
