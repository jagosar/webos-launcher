import { access, cp, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { crc32, deflateSync } from "node:zlib";
import { build } from "esbuild";

const LAUNCHER_DIR = fileURLToPath(new URL("..", import.meta.url));
const ENTRY_FILE = path.join(LAUNCHER_DIR, "src", "entry.ts");
const PUBLIC_DIR = path.join(LAUNCHER_DIR, "public");
const REQUIRED_PUBLIC_FILES = [
  "index.html",
  "appinfo.json",
  "style.css",
  path.join("assets", "fonts", "jost.woff2"),
];
const ICON_COLOR = [0x11, 0x11, 0x11];
const ICONS = [
  { name: "icon.png", size: 80 },
  { name: "largeIcon.png", size: 130 },
];
const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const BIT_DEPTH = 8;
const COLOR_TYPE_RGB = 2;
const BYTES_PER_PIXEL = 3;

function pngChunk(type, data) {
  const typeAndData = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const checksum = Buffer.alloc(4);
  checksum.writeUInt32BE(crc32(typeAndData));
  return Buffer.concat([length, typeAndData, checksum]);
}

function solidPng(size) {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(size, 0);
  header.writeUInt32BE(size, 4);
  header[8] = BIT_DEPTH;
  header[9] = COLOR_TYPE_RGB;
  const rowLength = 1 + size * BYTES_PER_PIXEL;
  const raw = Buffer.alloc(rowLength * size);
  for (let y = 0; y < size; y++) {
    const rowStart = y * rowLength; // filter byte stays 0
    for (let x = 0; x < size; x++) {
      Buffer.from(ICON_COLOR).copy(raw, rowStart + 1 + x * BYTES_PER_PIXEL);
    }
  }
  return Buffer.concat([
    PNG_SIGNATURE,
    pngChunk("IHDR", header),
    pngChunk("IDAT", deflateSync(raw)),
    pngChunk("IEND", Buffer.alloc(0)),
  ]);
}

async function assertInputsExist(files) {
  for (const file of files) {
    try {
      await access(file);
    } catch {
      throw new Error("Missing build input: " + path.relative(LAUNCHER_DIR, file));
    }
  }
}

export async function buildLauncher(outDir = path.join(LAUNCHER_DIR, "dist")) {
  const publicPaths = REQUIRED_PUBLIC_FILES.map((name) => path.join(PUBLIC_DIR, name));
  await assertInputsExist([ENTRY_FILE, ...publicPaths]);
  await mkdir(outDir, { recursive: true });
  await build({
    entryPoints: [ENTRY_FILE],
    outfile: path.join(outDir, "main.js"),
    bundle: true,
    format: "iife",
    target: ["chrome53"],
    minify: true,
    charset: "utf8",
    logLevel: "silent",
  });
  await cp(PUBLIC_DIR, outDir, { recursive: true });
  for (const icon of ICONS) {
    await writeFile(path.join(outDir, icon.name), solidPng(icon.size));
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  buildLauncher().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exit(1);
  });
}
