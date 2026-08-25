import { useEffect, useRef } from "react";

type DirtySource = {
  isDirty: () => boolean;
  drain?: () => Promise<boolean>;
};

const sources = new Map<string, DirtySource>();

export function useAdminDirtySource(
  key: string,
  dirty: boolean,
  drain?: () => Promise<boolean>,
) {
  const dirtyRef = useRef(dirty);
  const drainRef = useRef(drain);
  dirtyRef.current = dirty;
  drainRef.current = drain;

  useEffect(() => {
    const source: DirtySource = {
      isDirty: () => dirtyRef.current,
    };
    if (drainRef.current) {
      source.drain = () => drainRef.current?.() ?? Promise.resolve(false);
    }
    sources.set(key, source);
    return () => {
      sources.delete(key);
    };
  }, [key]);
}

export function hasUnsavedAdminWork() {
  return [...sources.values()].some((source) => source.isDirty());
}

export async function confirmAdminNavigation() {
  const dirty = [...sources.values()].filter((source) => source.isDirty());
  const unresolved: DirtySource[] = [];
  for (const source of dirty) {
    if (!source.drain) {
      unresolved.push(source);
      continue;
    }
    try {
      if (!(await source.drain())) unresolved.push(source);
    } catch {
      // Keep the source registered as dirty. The shared confirmation below
      // lets the user stay and inspect the page-specific save error.
      unresolved.push(source);
    }
  }
  if (!unresolved.some((source) => source.isDirty())) return true;
  return window.confirm("当前页面仍有未保存修改。确定放弃这些修改并离开吗？");
}
