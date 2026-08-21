import { useState } from "react";
import { createAdminClient } from "@pkuba/api-client";

import { MediaReviewPage } from "./MediaReviewPage";
import { ScoresheetWorkspace } from "./ScoresheetWorkspace";

type AdminClient = ReturnType<typeof createAdminClient>;

export function CompetitionMediaPage({
  client,
  seasonId,
  accountRole,
}: {
  client: AdminClient;
  seasonId: string;
  accountRole: string;
}) {
  const [tab, setTab] = useState<"scoresheets" | "photos">("scoresheets");

  return (
    <section className="competition-media-page">
      <div className="media-mode-switch" role="tablist" aria-label="比赛资料类型">
        <button
          className={tab === "scoresheets" ? "active" : ""}
          onClick={() => setTab("scoresheets")}
          role="tab"
          type="button"
        >
          记录表识别与发布
        </button>
        <button
          className={tab === "photos" ? "active" : ""}
          onClick={() => setTab("photos")}
          role="tab"
          type="button"
        >
          合照与其他照片
        </button>
      </div>
      {tab === "scoresheets" ? (
        <ScoresheetWorkspace
          accountRole={accountRole}
          client={client}
          seasonId={seasonId}
        />
      ) : (
        <MediaReviewPage client={client} />
      )}
    </section>
  );
}
