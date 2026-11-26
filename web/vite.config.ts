import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// Brand: branding/brand.local wins over branding/brand.example (see branding/README.md).
// BRAND_DIR overrides both (the web image build sets it).
const brandingRoot = resolve(__dirname, "..", "branding");
const brandDir =
  process.env.BRAND_DIR ??
  (existsSync(resolve(brandingRoot, "brand.local", "brand.json"))
    ? resolve(brandingRoot, "brand.local")
    : resolve(brandingRoot, "brand.example"));
const brand = JSON.parse(readFileSync(resolve(brandDir, "brand.json"), "utf8"));
// Tab icon: favicon.svg (drawn for 16-32 px) if the brand has one, else logo.svg.
const iconPath = ["favicon.svg", "logo.svg"].map((n) => resolve(brandDir, n)).find((p) => existsSync(p));
const favicon = iconPath
  ? `<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,${readFileSync(iconPath).toString("base64")}">`
  : "";

// Dev port and the API's own base URL both come from config.yaml's
// catalog.web section, not hardcoded here — dev.ps1 exports them as
// VITE_* env vars before launching this, so there is exactly one place
// (config.yaml) that knows these values. Falls back to the documented
// defaults so `npm run dev` still works if someone runs it by hand.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const port = Number(env.VITE_DEV_PORT || 5180);

  return {
    plugins: [
      react(),
      { name: "brand-title", transformIndexHtml: (html: string) => html.replace(/%BRAND_NAME%/g, brand.name).replace("</head>", `${favicon}</head>`) },
    ],
    resolve: { alias: { "@brand": brandDir } },
    server: {
      port,
      strictPort: true,
      fs: { allow: [__dirname, brandingRoot] },
    },
    preview: {
      port,
      strictPort: true,
    },
  };
});
