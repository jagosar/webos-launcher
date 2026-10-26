// @vitest-environment node
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { parse } from "acorn";
// @ts-expect-error build.mjs is plain JavaScript without declarations
import { buildLauncher } from "../scripts/build.mjs";
import { BACKGROUND_IDS } from "../src/types";

const PUBLIC_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "public");
const BASE_FILES = [
  "index.html",
  "main.js",
  "style.css",
  "appinfo.json",
  "icon.png",
  "largeIcon.png",
  "assets/fonts/jost.woff2",
  "assets/fonts/OFL.txt",
  "assets/CREDITS.md",
];
const FORBIDDEN_IN_BUNDLE = ["padStart", "Object.values", "Object.entries", ".includes(", "fetch("];

let tmp = "";

function read(name: string): string {
  return fs.readFileSync(path.join(tmp, name), "utf8");
}

describe("AC-019 · Build ships the assets and stays Chromium 53 compatible", () => {
  beforeAll(async () => {
    tmp = fs.mkdtempSync(path.join(os.tmpdir(), "launcher-v2-"));
    await buildLauncher(tmp);
  }, 60000);

  afterAll(() => {
    fs.rmSync(tmp, { recursive: true, force: true });
  });

  it("test_ac_019_emits_all_expected_files", () => {
    for (const f of BASE_FILES) {
      expect(fs.existsSync(path.join(tmp, f)), f).toBe(true);
    }
    for (const id of BACKGROUND_IDS) {
      expect(fs.existsSync(path.join(tmp, "assets", "backgrounds", `${id}.jpg`)), id).toBe(true);
    }
  });

  it("test_ac_019_ships_no_background_outside_the_list", () => {
    expect(BACKGROUND_IDS).toHaveLength(8);
    expect(fs.existsSync(path.join(tmp, "assets", "backgrounds", "godrevy.jpg"))).toBe(false);
    const allowed = BACKGROUND_IDS.map((id) => `${id}.jpg`);
    const jpgs = fs.readdirSync(path.join(tmp, "assets", "backgrounds")).filter((f) => f.toLowerCase().endsWith(".jpg"));
    for (const f of jpgs) {
      expect(allowed.indexOf(f), f).not.toBe(-1);
    }
  });

  it("test_ac_019_appinfo_is_valid", () => {
    const info = JSON.parse(read("appinfo.json"));
    expect(info.disableBackHistoryAPI).toBe(true);
    expect(info.bgColor).toBe("#000000");
    expect(info.version).toBe("2.0.0");
    expect(info.id).toBe("com.jagosar.minimal.launcher");
  });

  it("test_ac_019_html_and_css_are_copied_byte_equal", () => {
    expect(fs.readFileSync(path.join(tmp, "index.html")).equals(fs.readFileSync(path.join(PUBLIC_DIR, "index.html")))).toBe(true);
    expect(fs.readFileSync(path.join(tmp, "style.css")).equals(fs.readFileSync(path.join(PUBLIC_DIR, "style.css")))).toBe(true);
  });

  it("test_ac_019_bundle_is_real_es2016_script", () => {
    const js = read("main.js");
    expect(() => parse(js, { ecmaVersion: 2016, sourceType: "script" })).not.toThrow();
    expect(js).toContain("Configuración inválida");
    expect(js).toContain("api.open-meteo.com");
    for (const banned of FORBIDDEN_IN_BUNDLE) {
      expect(js.indexOf(banned), banned).toBe(-1);
    }
  });

  it("test_ac_019_html_does_not_use_es_modules", () => {
    expect(read("index.html")).not.toContain('type="module"');
  });
});
