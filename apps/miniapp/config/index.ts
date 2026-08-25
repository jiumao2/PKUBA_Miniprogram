import { defineConfig } from "@tarojs/cli";

export default defineConfig({
  projectName: "pkuba-miniapp",
  date: "2026-08-19",
  designWidth: 750,
  deviceRatio: {
    640: 2.34 / 2,
    750: 1,
    828: 1.81 / 2,
  },
  sourceRoot: "src",
  outputRoot: process.env.PKUBA_MINIAPP_OUTPUT_ROOT ?? "dist",
  framework: "react",
  compiler: "webpack5",
  cache: { enable: true },
  defineConstants: {
    PKUBA_API_BASE_URL: JSON.stringify(
      process.env.PKUBA_API_BASE_URL ?? "http://localhost:8088",
    ),
    PKUBA_ADMIN_WEB_URL: JSON.stringify(
      process.env.PKUBA_ADMIN_WEB_URL ?? "http://localhost:5173",
    ),
  },
  mini: {
    postcss: {
      pxtransform: { enable: true, config: {} },
      url: { enable: true, config: { limit: 1024 } },
      cssModules: { enable: false, config: { namingPattern: "module", generateScopedName: "[name]__[local]___[hash:base64:5]" } },
    },
  },
});
