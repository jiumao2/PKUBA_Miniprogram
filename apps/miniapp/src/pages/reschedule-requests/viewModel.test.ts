import { describe, expect, it } from "vitest";

import { targetVenueLabel } from "./viewModel";

const request = {
  status: "WAITING_OPPONENT",
  is_terminal: false,
  game: { venue_name: "五四东二" },
};

describe("reschedule target venue visibility", () => {
  it("hides the internally reserved venue while a request is active", () => {
    expect(targetVenueLabel(request)).toBe("场地已内部预留，生效后公布");
  });

  it("does not reveal a released venue for an unsuccessful terminal request", () => {
    expect(targetVenueLabel({ ...request, status: "REJECTED", is_terminal: true }))
      .toBe("申请未生效，场地未公布");
  });

  it("shows only the formal game venue after approval", () => {
    expect(targetVenueLabel({ ...request, status: "APPROVED", is_terminal: true }))
      .toBe("五四东二");
  });
});
