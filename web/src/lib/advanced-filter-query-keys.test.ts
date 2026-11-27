import assert from "node:assert/strict";
import { test } from "node:test";
import { QueryClient } from "@tanstack/react-query";
import { listActorsQueryKey, listMetadataQueryKey } from "@/client/@tanstack/react-query.gen";
import {
  actorAdvancedFilterQueryKey,
  metadataAdvancedFilterQueryKey,
} from "./advanced-filter-query-keys";

test("组合筛选分页缓存随列表变更失效且不影响另一实体", async () => {
  const client = new QueryClient();
  const metadataList = metadataAdvancedFilterQueryKey(
    "list",
    "metadata-condition",
    { version: 1 },
    20,
    0,
  );
  const metadataGrid = metadataAdvancedFilterQueryKey(
    "grid",
    "metadata-condition",
    { version: 1 },
    30,
  );
  const actorList = actorAdvancedFilterQueryKey("list", "actor-condition", { version: 1 }, 20, 0);
  const actorGrid = actorAdvancedFilterQueryKey("grid", "actor-condition", { version: 1 }, 30);
  for (const key of [metadataList, metadataGrid, actorList, actorGrid])
    client.setQueryData(key, { total: 1 });

  await client.invalidateQueries({ queryKey: listMetadataQueryKey() });
  assert.equal(client.getQueryState(metadataList)?.isInvalidated, true);
  assert.equal(client.getQueryState(metadataGrid)?.isInvalidated, true);
  assert.equal(client.getQueryState(actorList)?.isInvalidated, false);
  assert.equal(client.getQueryState(actorGrid)?.isInvalidated, false);

  await client.invalidateQueries({ queryKey: listActorsQueryKey() });
  assert.equal(client.getQueryState(actorList)?.isInvalidated, true);
  assert.equal(client.getQueryState(actorGrid)?.isInvalidated, true);
  client.clear();
});
