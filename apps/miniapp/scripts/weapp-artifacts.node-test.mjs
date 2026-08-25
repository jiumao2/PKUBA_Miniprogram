import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";

import { validateWeappOutput } from "./weapp-artifacts.mjs";

function fixture(pageJavaScript) {
  const root = mkdtempSync(join(tmpdir(), "pkuba-weapp-artifacts-"));
  const page = "pages/admin/index";
  mkdirSync(dirname(join(root, `${page}.js`)), { recursive: true });
  writeFileSync(join(root, "app.json"), JSON.stringify({ pages: [page] }));
  writeFileSync(join(root, `${page}.json`), "{}");
  writeFileSync(join(root, `${page}.wxml`), '<template is="taro_tmpl"/>');
  writeFileSync(join(root, `${page}.js`), pageJavaScript);
  return root;
}

test("accepts a registered page with a real component module", () => {
  const root = fixture(`Page({data:{title:"管理员工作台"}});${"x".repeat(1100)}`);
  try {
    assert.deepEqual(validateWeappOutput(root), ["pages/admin/index"]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("rejects the empty component module produced by a broken incremental build", () => {
  const root = fixture(
    `(wx["webpackJsonp"]=wx["webpackJsonp"]||[]).push([[1],{1010:function(){},` +
      `2020:function(){Page({})}}]);${"x".repeat(1100)}`,
  );
  try {
    assert.throws(() => validateWeappOutput(root), /组件模块为空/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("rejects missing registered page artifacts", () => {
  const root = fixture(`Page({});${"x".repeat(1100)}`);
  try {
    rmSync(join(root, "pages/admin/index.wxml"));
    assert.throws(() => validateWeappOutput(root), /缺少有效的 \.wxml/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
