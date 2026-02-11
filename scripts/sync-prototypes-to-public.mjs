import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const projectRoot = process.cwd();

const sources = [
  {
    from: resolve(projectRoot, "prototypes/map-v3.html"),
    to: resolve(projectRoot, "public/index.html"),
  },
  {
    from: resolve(projectRoot, "prototypes/explore.html"),
    to: resolve(projectRoot, "public/explore.html"),
  },
];

for (const { from, to } of sources) {
  const raw = await readFile(from, "utf8");
  // Prototypes currently reference ../public/... paths from Netlify-era hosting.
  // Cloudflare serves assets from /public as the root URL path.
  const transformed = raw.replaceAll("../public/", "/");
  await writeFile(to, transformed, "utf8");
  console.log(`Synced ${from} -> ${to}`);
}
