import { render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  confirmAdminNavigation,
  hasUnsavedAdminWork,
  useAdminDirtySource,
} from "./dirtyGuard";

function Source({
  name,
  dirty,
  drain,
}: {
  name: string;
  dirty: boolean;
  drain?: () => Promise<boolean>;
}) {
  useAdminDirtySource(name, dirty, drain);
  return null;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("admin dirty navigation guard", () => {
  it("blocks a navigation when an explicit form is dirty and the user stays", async () => {
    const confirmation = vi.spyOn(window, "confirm").mockReturnValue(false);
    const view = render(<Source name="form" dirty />);

    expect(hasUnsavedAdminWork()).toBe(true);
    await expect(confirmAdminNavigation()).resolves.toBe(false);
    expect(confirmation).toHaveBeenCalledOnce();

    view.unmount();
    expect(hasUnsavedAdminWork()).toBe(false);
  });

  it("leaves without a prompt after an auto-save source drains successfully", async () => {
    const confirmation = vi.spyOn(window, "confirm").mockReturnValue(false);
    const drain = vi.fn().mockResolvedValue(true);
    const view = render(<Source name="autosave" dirty drain={drain} />);

    await expect(confirmAdminNavigation()).resolves.toBe(true);
    expect(drain).toHaveBeenCalledOnce();
    expect(confirmation).not.toHaveBeenCalled();
    view.unmount();
  });
});
