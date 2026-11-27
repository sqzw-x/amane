import path from "node:path";
import { createServer } from "vite";

const server = await createServer({
  configFile: false,
  optimizeDeps: { noDiscovery: true, include: [] },
  server: { middlewareMode: true },
  resolve: { alias: { "@": path.resolve("src") } },
});

try {
  await server.ssrLoadModule(
    "/src/components/media/advanced-filter-editor/filter-expression.test.ts",
  );
  await server.ssrLoadModule("/src/lib/advanced-filter-query-keys.test.ts");
  await server.ssrLoadModule("/src/lib/browse-filter.test.ts");
  await server.ssrLoadModule("/src/lib/nav-defaults.test.ts");
  await server.ssrLoadModule("/src/components/media/advanced-filter-editor/draft.test.ts");
  await server.ssrLoadModule("/src/components/media/advanced-filter-editor/draft-view.test.ts");
} finally {
  await server.close();
}
