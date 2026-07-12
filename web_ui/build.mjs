import { build, context } from "esbuild";
import { cp, mkdir, rm } from "node:fs/promises";

const watch = process.argv.includes("--watch");
await rm("dist", { recursive: true, force: true });
await mkdir("dist/assets", { recursive: true });
await cp("index.html", "dist/index.html");

const options = {
  entryPoints: ["src/main.tsx"],
  bundle: true,
  minify: !watch,
  sourcemap: watch,
  outfile: "dist/assets/app.js",
  loader: { ".css": "css" },
  jsx: "automatic",
  define: { "process.env.NODE_ENV": JSON.stringify(watch ? "development" : "production") },
};

if (watch) {
  const ctx = await context(options);
  await ctx.watch();
  console.log("Watching web_ui sources; serve the API at http://127.0.0.1:8000");
} else {
  await build(options);
}
