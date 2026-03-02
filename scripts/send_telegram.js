const fs = require("node:fs");
const path = require("node:path");

const TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const CHAT_ID = process.env.TELEGRAM_CHAT_ID;
let BASE_URL = process.env.PUBLIC_BASE_URL;
const HTML_ROOT_DIR = process.env.HTML_ROOT_DIR;

if (!TOKEN || !CHAT_ID || !BASE_URL || !HTML_ROOT_DIR) {
  console.error("Missing env variables");
  process.exit(1);
}

BASE_URL = BASE_URL.replace(/\/+$/, "");
const ROOT = path.resolve(HTML_ROOT_DIR);

/**
 * public klasöründen URL prefix çıkarır
 * ör:
 * /var/task/public/summaries -> summaries
 */
function urlPathFromHtmlRootDir(htmlRootDir) {
  const s = String(htmlRootDir ?? "")
    .replace(/\\/g, "/")
    .replace(/\/+$/, "")
    .trim();

  if (!s) return "";

  const parts = s.split("/").filter(Boolean);
  const idx = parts.lastIndexOf("public");

  if (idx !== -1) return parts.slice(idx + 1).join("/");

  return parts[parts.length - 1];
}

const URL_PREFIX = urlPathFromHtmlRootDir(HTML_ROOT_DIR);

/**
 * kategori -> ikon map
 */
const CATEGORY_ICON_MAP = {
  react: "⚛️",
  next: "▲",
  nextjs: "▲",
  "next.js": "▲",
  angular: "🅰️",

  openai: "🤖",
  anthropic: "🧠",
  claude: "🧠",

  google: "🔎",
  deepmind: "🧠",

  huggingface: "🤗",

  java: "☕",

  dotnet: "🟣",
  ".net": "🟣",
  "c#": "🟣",

  arxiv: "📄",
  research: "📄",

  news: "📰",
};

/**
 * kategori adına göre ikon bulur
 */
function iconForCategory(category) {
  const key = clean(category).toLowerCase();

  if (CATEGORY_ICON_MAP[key]) return CATEGORY_ICON_MAP[key];

  if (key.includes("react")) return "⚛️";
  if (key.includes("next")) return "▲";
  if (key.includes("angular")) return "🅰️";

  if (key.includes("openai")) return "🤖";
  if (key.includes("anthropic") || key.includes("claude")) return "🧠";

  if (key.includes("java")) return "☕";

  if (
    key.includes("dotnet") ||
    key.includes(".net") ||
    key.includes("c#")
  )
    return "🟣";

  if (key.includes("deepmind")) return "🧠";
  if (key.includes("google")) return "🔎";

  if (key.includes("hugging")) return "🤗";

  if (key.includes("arxiv") || key.includes("research"))
    return "📄";

  if (key.includes("news")) return "📰";

  return "🧩";
}

function clean(s) {
  return String(s ?? "").replace(/\s+/g, " ").trim();
}

function escapeHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * HTML içinden title okur
 */
function readTitleFromHtml(html) {
  const text = String(html ?? "");

  let m = text.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
  if (m?.[1]) return clean(m[1]);

  m = text.match(
    /<meta[^>]+property=["']og:title["'][^>]+content=["']([^"']+)["']/i
  );
  if (m?.[1]) return clean(m[1]);

  m = text.match(/<h1[^>]*>([\s\S]*?)<\/h1>/i);
  if (m?.[1]) {
    return clean(m[1].replace(/<[^>]+>/g, ""));
  }

  return "";
}

/**
 * filename'dan title üretir fallback olarak
 */
function titleFromFilename(file) {
  let t = file.replace(/\.html$/i, "");

  t = t.replace(/-[a-f0-9]{6,}$/i, "");
  t = t.replace(/[-_]+/g, " ");

  t = clean(t);

  if (!t) return "";

  return t.charAt(0).toUpperCase() + t.slice(1);
}

function listDirs(p) {
  if (!fs.existsSync(p)) return [];

  return fs
    .readdirSync(p, { withFileTypes: true })
    .filter((d) => d.isDirectory())
    .map((d) => d.name);
}

function listFiles(p) {
  if (!fs.existsSync(p)) return [];

  return fs
    .readdirSync(p, { withFileTypes: true })
    .filter((f) => f.isFile() && f.name.endsWith(".html"))
    .map((f) => f.name);
}

/**
 * Telegram mesajını oluşturur
 */
function buildMessage() {
  const categories = listDirs(ROOT).sort();

  let message = "🗞️ <b>Tech Summary</b>\n\n";

  for (const category of categories) {
    const files = listFiles(path.join(ROOT, category)).sort();

    if (!files.length) continue;

    const icon = iconForCategory(category);

    message += `${icon} <b>${escapeHtml(
      category.toUpperCase()
    )}</b>\n`;

    for (const file of files) {
      const htmlPath = path.join(ROOT, category, file);

      let html;

      try {
        html = fs.readFileSync(htmlPath, "utf8");
      } catch (e) {
        console.error("HTML read failed:", htmlPath);
        continue;
      }

      const title =
        readTitleFromHtml(html) ||
        titleFromFilename(file);

      const url =
        `${BASE_URL}` +
        `/${encodeURIComponent(URL_PREFIX)}` +
        `/${encodeURIComponent(category)}` +
        `/${encodeURIComponent(file)}`;

      message +=
        `• <a href="${escapeHtml(url)}">` +
        `${escapeHtml(title)}</a>\n`;
    }

    message += "\n";
  }

  return message.trim();
}

/**
 * Telegram max 4096 char limit fix
 */
function splitTelegramText(text, limit = 4096) {
  const parts = [];

  let current = "";

  for (const line of text.split("\n")) {
    const next =
      current.length === 0
        ? line
        : current + "\n" + line;

    if (next.length > limit) {
      if (current) parts.push(current);
      current = line;
    } else {
      current = next;
    }
  }

  if (current) parts.push(current);

  return parts;
}

async function sendTelegram(text) {
  const res = await fetch(
    `https://api.telegram.org/bot${TOKEN}/sendMessage`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        chat_id: CHAT_ID,
        text,
        parse_mode: "HTML",
        disable_web_page_preview: true,
      }),
    }
  );

  const json = await res.json().catch(() => null);

  if (!res.ok || !json?.ok) {
    throw new Error(
      `Telegram error: ${
        json
          ? JSON.stringify(json)
          : `HTTP ${res.status}`
      }`
    );
  }
}

async function main() {
  const message = buildMessage();

  if (!message) {
    console.log("Nothing to send");
    return;
  }

  const parts = splitTelegramText(message);

  for (const part of parts) {
    await sendTelegram(part);
  }

  console.log(
    `Telegram message sent (${parts.length} part)`
  );
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
