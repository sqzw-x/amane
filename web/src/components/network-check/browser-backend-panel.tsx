import {
  ActionIcon,
  Anchor,
  Badge,
  Group,
  Paper,
  Skeleton,
  Stack,
  Text,
  Tooltip,
} from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { IconRefresh } from "@tabler/icons-react";
import { useTranslation } from "react-i18next";
import { browserBackendsOptions } from "@/client/@tanstack/react-query.gen";
import type { BrowserBackendItemResponse } from "@/client/types.gen";

/** 后端的展示名; 与 ``BrowserBackendName`` 的取值一一对应, 新增后端时同步补这里与 i18n. */
const BACKEND_LABEL: Record<string, string> = {
  patchright: "Patchright",
  camoufox: "Camoufox",
  bsk: "BrowserSkill",
  solver: "Solver",
};

const BSK_REPO = "https://github.com/Tencent/BrowserSkill";

function BackendHint({ backend }: { backend: string }) {
  const { t } = useTranslation("networkCheck");
  return (
    <Text size="xs" c="dimmed">
      {backend === "patchright" && t("backend.hint.patchright")}
      {backend === "camoufox" && t("backend.hint.camoufox")}
      {backend === "solver" && t("backend.hint.solver")}
      {backend === "bsk" && (
        <>
          {t("backend.hint.bsk")}{" "}
          <Anchor href={BSK_REPO} target="_blank" rel="noreferrer" size="xs">
            {t("backend.hint.bskLink")}
          </Anchor>
        </>
      )}
    </Text>
  );
}

/**
 * 浏览器后端可用性: 回答「这个实例能用哪个后端」, 与下方逐来源的连接性结论互不替代.
 * 探测本身很轻 (模块检查 + 本地协议握手), 进入页面即可自动跑一次.
 */
export function BrowserBackendPanel() {
  const { t } = useTranslation("networkCheck");
  const { data, isPending, isFetching, refetch } = useQuery({
    ...browserBackendsOptions(),
    staleTime: 0,
  });

  const extras = (item: BrowserBackendItemResponse): string => {
    const parts: string[] = [];
    if (item.version != null) {
      parts.push(t("backend.version", { version: item.version }));
    }
    const names = (item.browsers ?? [])
      .filter((browser) => !browser.unresponsive)
      .map((browser) => browser.name);
    if (names.length > 0) {
      parts.push(t("backend.browsers", { names: names.join(", ") }));
    }
    return parts.join(" · ");
  };

  return (
    <Paper withBorder p="md">
      <Group justify="space-between" mb="xs">
        <Stack gap={0}>
          <Text fw={600}>{t("backend.title")}</Text>
          <Text size="xs" c="dimmed">
            {t("backend.description")}
          </Text>
        </Stack>
        <Tooltip label={t("backend.refresh")}>
          <ActionIcon
            variant="subtle"
            aria-label={t("backend.refresh")}
            loading={isFetching && !isPending}
            onClick={() => void refetch()}
          >
            <IconRefresh size={18} />
          </ActionIcon>
        </Tooltip>
      </Group>

      {isPending ? (
        <Skeleton height={72} />
      ) : data == null ? (
        <Text size="sm" c="red">
          {t("backend.loadError")}
        </Text>
      ) : (
        <Stack gap="xs">
          {(data.items ?? []).map((item) => (
            <Stack key={item.backend} gap={2}>
              <Group gap="xs" wrap="nowrap">
                <Text size="sm" fw={500}>
                  {BACKEND_LABEL[item.backend] ?? item.backend}
                </Text>
                {item.backend === data.selected && (
                  <Badge size="xs" variant="light">
                    {t("backend.selected")}
                  </Badge>
                )}
                <Badge size="xs" color={item.available ? "teal" : "gray"} variant="light">
                  {item.available ? t("backend.available") : t("backend.unavailable")}
                </Badge>
                <Text size="xs" c="dimmed" truncate>
                  {[item.detail, extras(item)].filter(Boolean).join(" · ")}
                </Text>
              </Group>
              {!item.available && <BackendHint backend={item.backend} />}
            </Stack>
          ))}
        </Stack>
      )}
    </Paper>
  );
}
