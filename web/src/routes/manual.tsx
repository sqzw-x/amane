import {
  Alert,
  Badge,
  Button,
  Code,
  CopyButton,
  Divider,
  Group,
  Paper,
  SimpleGrid,
  Skeleton,
  Stack,
  Tabs,
  Text,
  ThemeIcon,
  Title,
  Tooltip,
} from "@mantine/core";
import { IconAlertTriangle, IconCheck, IconCopy } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { notifications } from "@mantine/notifications";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import {
  getTaskOptions,
  listTasksOptions,
  listTasksQueryKey,
  semanticDiagnosticsOptions,
  submitTaskMutation,
} from "@/client/@tanstack/react-query.gen";
import { extractErrorMessage } from "@/lib/api-error";
import { formatRelativeTime } from "@/lib/format-relative-time";
import { formatFileSize } from "@/lib/utils";

const MANUAL_TABS = ["agent", "semantic"] as const;

const manualSearchSchema = z.object({
  tab: z.enum(MANUAL_TABS).catch("agent").default("agent"),
});

export const Route = createFileRoute("/manual")({
  validateSearch: manualSearchSchema,
  component: ManualPage,
});

/**
 * 接入地址只取决于当前 origin, 因此浏览器侧拼出即可, 不必请求后端.
 * 路径与 `src/amane/api/routes/bootstrap.py` 的端点一致: 免鉴权, 只回固定文本.
 */
const GUIDE_PATH = "/api/agent-guide";

/**
 * 用户手册. 运行时诊断与配置指引按主题分标签页, 内容宽度与其他信息页一致.
 */
function ManualPage() {
  const { t } = useTranslation("manual");
  const { tab } = Route.useSearch();
  const navigate = Route.useNavigate();

  return (
    <Stack gap="lg" maw={1120} mx="auto" w="100%">
      <Title order={2}>{t("title")}</Title>
      <Tabs
        value={tab}
        onChange={(value) => {
          const next = MANUAL_TABS.find((candidate) => candidate === value);
          if (next != null) void navigate({ search: (prev) => ({ ...prev, tab: next }) });
        }}
        keepMounted={false}
      >
        <Tabs.List>
          <Tabs.Tab value="agent">{t("tabs.agent")}</Tabs.Tab>
          <Tabs.Tab value="semantic">{t("tabs.semantic")}</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="agent" pt="lg">
          <ConnectSection />
        </Tabs.Panel>
        <Tabs.Panel value="semantic" pt="lg">
          <SemanticSection />
        </Tabs.Panel>
      </Tabs>
    </Stack>
  );
}

function ConnectSection() {
  const { t } = useTranslation("manual");
  const address = `${window.location.origin}${GUIDE_PATH}`;
  // 复制的是这句话而不是裸地址: 用户往往直接把剪贴板内容贴进对话框, 裸 URL 没有任何动作指引.
  const instruction = t("connect.instruction", { address });

  return (
    <Stack gap="lg">
      <Stack gap="xs">
        <Text size="sm" fw={600}>
          {t("connect.copyHint")}
        </Text>
        <Group gap="xs" wrap="nowrap" align="center">
          {/* pre 默认不换行, 窄屏上地址会被裁掉; 长 URL 也只能按字符断开. */}
          <Code block style={{ flex: 1, whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
            {instruction}
          </Code>
          <CopyButton value={instruction} timeout={2000}>
            {({ copied, copy }) => (
              <Tooltip label={t("connect.copy")} withArrow>
                <Button
                  variant={copied ? "light" : "filled"}
                  color={copied ? "teal" : undefined}
                  leftSection={copied ? <IconCheck size={16} /> : <IconCopy size={16} />}
                  onClick={copy}
                >
                  {copied ? t("connect.copied") : t("connect.copy")}
                </Button>
              </Tooltip>
            )}
          </CopyButton>
        </Group>
        <Text size="sm" c="dimmed">
          {t("connect.effect")}
        </Text>
      </Stack>

      <Paper withBorder p="md">
        <Text size="sm" fw={600} mb={4}>
          {t("connect.token.title")}
        </Text>
        <Text size="sm" c="dimmed">
          {t("connect.token.body")}
        </Text>
      </Paper>

      <Paper withBorder p="md">
        <Text size="sm" fw={600} mb={4}>
          {t("connect.permission.title")}
        </Text>
        <Text size="sm" c="dimmed">
          {t("connect.permission.body")}
        </Text>
      </Paper>
    </Stack>
  );
}

/** 语义检索的运行时状态: 模型与索引各自一张卡片, 只保留不重复的数字. */
function SemanticSection() {
  const { t, i18n } = useTranslation("manual");
  // 视觉塔与文本塔会随任务与闲置超时加载或释放, 面板须定期刷新才能反映当前状态.
  const { data: status } = useQuery({ ...semanticDiagnosticsOptions(), refetchInterval: 30_000 });

  if (status == null) {
    return (
      <Stack gap="lg">
        <SimpleGrid cols={{ base: 1, md: 2 }}>
          <Skeleton height={260} radius="md" />
          <Skeleton height={260} radius="md" />
        </SimpleGrid>
        <Skeleton height={140} radius="md" />
      </Stack>
    );
  }

  if (!status.enabled) {
    return (
      <Alert color="gray" title={t("semantic.unavailable")} maw={720}>
        {t("semantic.intro")}
      </Alert>
    );
  }

  const counts = status.current_model != null ? status.models?.[status.current_model] : undefined;
  const covers = counts?.covers ?? 0;
  const stills = counts?.stills ?? 0;
  const images = covers + stills;
  const entries = status.indexed_metadata ?? 0;
  const hasModel = (status.model_bytes ?? 0) > 0;
  const state = status.search_ready
    ? { color: "teal", label: t("semantic.model.ready") }
    : hasModel
      ? { color: "red", label: t("semantic.model.missing") }
      : { color: "gray", label: t("semantic.model.notDownloaded") };

  return (
    <Stack gap="lg">
      <Text size="sm" c="dimmed">
        {t("semantic.intro")}
      </Text>

      <SimpleGrid cols={{ base: 1, md: 2 }}>
        <Paper withBorder p="lg" radius="md">
          <Group justify="space-between" mb="md">
            <Title order={4}>{t("semantic.model.title")}</Title>
            <Badge color={state.color} variant="light">
              {state.label}
            </Badge>
          </Group>

          <Group gap={48} align="flex-start" mih={68}>
            {hasModel ? (
              <Metric
                label={t("semantic.model.disk")}
                value={formatFileSize(status.model_bytes ?? 0)}
              />
            ) : (
              <Metric
                label={t("semantic.model.download")}
                value={formatFileSize(status.download_size ?? 0)}
                hint={t("semantic.model.downloadHint")}
              />
            )}
          </Group>

          <Divider my="md" />
          <Stack gap={8}>
            <InfoRow label={t("semantic.model.name")} value={status.current_model_label ?? "-"} />
            <InfoRow label={t("semantic.model.backend")} value={status.backend_label ?? "-"} />
            <InfoRow
              label={t("semantic.model.visionTower")}
              value={t("semantic.model.towerState", {
                state: status.vision_loaded
                  ? t("semantic.model.loaded")
                  : t("semantic.model.notLoaded"),
                size: formatFileSize(status.vision_size ?? 0),
              })}
            />
            <InfoRow
              label={t("semantic.model.textTower")}
              value={t("semantic.model.towerState", {
                state: status.text_loaded
                  ? t("semantic.model.loaded")
                  : t("semantic.model.notLoaded"),
                size: formatFileSize(status.text_size ?? 0),
              })}
            />
            <InfoRow label={t("semantic.model.path")} value={status.model_path ?? "-"} mono />
          </Stack>
        </Paper>

        <Paper withBorder p="lg" radius="md">
          <Group justify="space-between" mb="md">
            <Title order={4}>{t("semantic.index.title")}</Title>
            <Badge color={images > 0 ? "teal" : "gray"} variant="light">
              {images > 0 ? t("semantic.index.created") : t("semantic.index.empty")}
            </Badge>
          </Group>

          <Group gap={48} align="flex-start" mih={68}>
            <Metric label={t("semantic.index.entries")} value={String(entries)} />
            <Metric
              label={t("semantic.index.images")}
              value={String(images)}
              hint={t("semantic.index.breakdown", { covers, stills })}
            />
          </Group>

          <Divider my="md" />
          <Stack gap={8}>
            <InfoRow
              label={t("semantic.index.updated")}
              value={
                status.indexed_at != null
                  ? t("semantic.index.updatedAt", {
                      relative: formatRelativeTime(
                        status.indexed_at,
                        i18n.language,
                        t("semantic.index.justNow"),
                      ),
                      exact: new Date(status.indexed_at).toLocaleString(),
                    })
                  : "-"
              }
            />
            <InfoRow
              label={t("semantic.index.disk")}
              value={formatFileSize(status.embeddings_bytes ?? 0)}
            />
            <InfoRow label={t("semantic.index.path")} value={status.embeddings_path ?? "-"} mono />
          </Stack>
        </Paper>
      </SimpleGrid>

      <BenchmarkCard />

      <Paper withBorder p="md" radius="md">
        <Group gap="xs" mb="sm">
          <ThemeIcon color="yellow" variant="light" size="sm">
            <IconAlertTriangle size={16} />
          </ThemeIcon>
          <Title order={5}>{t("semantic.tips.title")}</Title>
        </Group>
        <Stack gap={6}>
          {(["elements", "simple", "language", "positive"] as const).map((key) => (
            <Text key={key} size="sm" c="dimmed">
              · {t(`semantic.tips.${key}`)}
            </Text>
          ))}
        </Stack>
      </Paper>
    </Stack>
  );
}

/** 手动触发一次性能测试, 并展示最近一次完成的结果; 触发期间禁用按钮, 避免重复入队. */
function BenchmarkCard() {
  const { t } = useTranslation("manual");
  const queryClient = useQueryClient();
  // 最近一次任务决定按钮与失败提示; 结果取最近一次成功的任务, 列表项不包含 result, 只能再查询详情.
  const latest = useQuery({
    ...listTasksOptions({ query: { type: ["semantic_benchmark"], limit: 1 } }),
    refetchInterval: 3_000,
  });
  const lastDone = useQuery({
    ...listTasksOptions({ query: { type: ["semantic_benchmark"], status: ["done"], limit: 1 } }),
    refetchInterval: 10_000,
  });
  const doneId = lastDone.data?.items[0]?.id;
  const detail = useQuery({
    ...getTaskOptions({ path: { task_id: doneId ?? 0 } }),
    enabled: doneId != null,
  });
  const submit = useMutation({
    ...submitTaskMutation(),
    onSuccess: () => {
      notifications.show({ message: t("semantic.benchmark.running"), color: "blue" });
      void queryClient.invalidateQueries({ queryKey: listTasksQueryKey() });
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("semantic.benchmark.failed")),
        color: "red",
      }),
  });
  const latestTask = latest.data?.items[0];
  const running = latestTask?.status === "queued" || latestTask?.status === "running";
  const failure =
    latestTask?.status === "failed" ? (latestTask.error ?? t("semantic.benchmark.failed")) : null;
  const result = detail.data?.result;
  const benchmark = result?.type === "semantic_benchmark" ? result : null;
  const busy = running || submit.isPending;

  return (
    <Paper withBorder p="lg" radius="md">
      <Group justify="space-between" mb="md" align="flex-start" wrap="nowrap">
        <Stack gap={4}>
          <Title order={4}>{t("semantic.benchmark.title")}</Title>
          <Text size="sm" c="dimmed">
            {t("semantic.benchmark.intro")}
          </Text>
        </Stack>
        <Button
          variant="light"
          loading={busy}
          disabled={busy}
          onClick={() => submit.mutate({ body: { type: "semantic_benchmark" } })}
        >
          {busy ? t("semantic.benchmark.runningButton") : t("semantic.benchmark.start")}
        </Button>
      </Group>

      {benchmark == null ? (
        <Text size="sm" c={failure != null ? "red" : "dimmed"}>
          {running ? t("semantic.benchmark.running") : (failure ?? t("semantic.benchmark.empty"))}
        </Text>
      ) : (
        <>
          <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="lg">
            <Metric
              label={t("semantic.benchmark.imagesPerSecond")}
              value={t("semantic.benchmark.imagesPerSecondValue", {
                value: benchmark.images_per_second.toFixed(1),
              })}
            />
            <Metric
              label={t("semantic.benchmark.queryMs")}
              value={t("semantic.benchmark.queryMsValue", { value: benchmark.query_ms.toFixed(1) })}
            />
            <Metric
              label={t("semantic.benchmark.loadMs")}
              value={t("semantic.benchmark.loadMsValue", {
                value: (benchmark.load_ms / 1000).toFixed(1),
              })}
            />
          </SimpleGrid>
          <Text size="xs" c="dimmed" mt="md">
            {t("semantic.benchmark.backend", {
              backend: benchmark.backend,
              samples: benchmark.samples,
            })}
            {" · "}
            {t("semantic.benchmark.measuredAt", {
              time: new Date(benchmark.measured_at).toLocaleString(),
            })}
          </Text>
          {failure != null ? (
            <Text size="xs" c="red" mt={4}>
              {failure}
            </Text>
          ) : null}
        </>
      )}
    </Paper>
  );
}

function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Stack gap={2}>
      <Text size="xs" c="dimmed" fw={600} tt="uppercase">
        {label}
      </Text>
      <Text fz={28} fw={700} lh={1.05}>
        {value}
      </Text>
      {hint != null ? (
        <Text size="xs" c="dimmed">
          {hint}
        </Text>
      ) : null}
    </Stack>
  );
}

function InfoRow({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <Group gap="xs" align="flex-start" wrap="nowrap">
      <Text size="sm" c="dimmed" w={96} style={{ flexShrink: 0 }}>
        {label}
      </Text>
      {mono ? (
        <Code style={{ whiteSpace: "pre-wrap", wordBreak: "break-all", fontSize: 12 }}>
          {value}
        </Code>
      ) : (
        <Text size="sm">{value}</Text>
      )}
    </Group>
  );
}
