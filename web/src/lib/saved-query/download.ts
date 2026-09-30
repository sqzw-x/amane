import { notifications } from "@mantine/notifications";
import { getSavedQueryResult } from "@/client/sdk.gen";
import { extractErrorMessage } from "@/lib/api-error";

const DOWNLOAD_LIMIT = 5000;

/** 下载预设结果为 JSON 文件; 结果过大时只含上限行数. */
export async function downloadSavedQueryResult(
  queryId: number,
  failureMessage: string,
): Promise<void> {
  const { data, error } = await getSavedQueryResult({
    path: { query_id: queryId },
    query: { offset: 0, limit: DOWNLOAD_LIMIT },
  });
  if (error || !data) {
    notifications.show({ color: "red", message: extractErrorMessage(error, failureMessage) });
    return;
  }
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `saved-query-${queryId}.json`;
  a.click();
  URL.revokeObjectURL(url);
}
