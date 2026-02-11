/**
 * Minimal local HTTP server that replicates Netlify _redirects behavior.
 * Used by Playwright e2e tests so they can test from / with rewrite rules.
 */
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { join, extname } from "node:path";

const ROOT = new URL("..", import.meta.url).pathname;
const PORT = Number(process.env.PORT) || 3999;

const MIME = {
  ".html": "text/html",
  ".json": "application/json",
  ".js": "text/javascript",
  ".css": "text/css",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".svg": "image/svg+xml",
};

// Rewrite rules matching _redirects
const REWRITES = [
  { from: "/", to: "/prototypes/map-v3.html" },
  { from: "/explore.html", to: "/prototypes/explore.html" },
];

async function serveFile(filePath, res) {
  try {
    const data = await readFile(filePath);
    const ext = extname(filePath);
    res.writeHead(200, { "Content-Type": MIME[ext] || "application/octet-stream" });
    res.end(data);
  } catch {
    res.writeHead(404, { "Content-Type": "text/html" });
    res.end("<h1>404 Not Found</h1>");
  }
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  let pathname = url.pathname;

  // Apply rewrite rules (match path without query string)
  for (const rule of REWRITES) {
    if (pathname === rule.from) {
      pathname = rule.to;
      break;
    }
  }

  const filePath = join(ROOT, pathname);
  await serveFile(filePath, res);
});

server.listen(PORT, () => {
  console.log(`Test server running at http://localhost:${PORT}`);
});
