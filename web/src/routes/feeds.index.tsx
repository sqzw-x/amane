import { Box, Button, Drawer, Group, Text, Title } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { IconList } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { createFileRoute, Link, stripSearchParams } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { z } from "zod";
import { listFeedsOptions } from "@/client/@tanstack/react-query.gen";
import type { FeedItemReadState, FeedItemState } from "@/client/types.gen";
import { FeedReader } from "@/components/feeds/feed-reader";
import { FeedSidebar } from "@/components/feeds/feed-sidebar";
import { APP_SHELL_MAIN_HEIGHT } from "@/components/layout/app-shell-metrics";
import { UNGROUPED_GROUP } from "@/lib/feeds/groups";

const feedsSearchSchema = z.object({
  feed: z.coerce.number().int().positive().optional(),
  group: z.string().optional(),
  q: z.string().optional(),
  state: z.enum(["active", "ignored", "all"]).catch("active").default("active"),
  read: z.enum(["unread", "read", "all"]).catch("unread").default("unread"),
  page: z.coerce.number().int().min(1).catch(1).default(1),
  nodedupe: z.union([z.literal("1"), z.literal("true"), z.literal(true)]).optional(),
});

export const Route = createFileRoute("/feeds/")({
  validateSearch: feedsSearchSchema,
  search: { middlewares: [stripSearchParams({ page: 1, state: "active", read: "unread" })] },
  component: FeedsPage,
});

function FeedsPage() {
  const { t } = useTranslation(["feeds", "common"]);
  const navigate = Route.useNavigate();
  const search = Route.useSearch();
  const { data, isLoading } = useQuery(listFeedsOptions());
  const feeds = data?.items ?? [];
  const [mobileNav, mobileNavHandlers] = useDisclosure(false);

  function patchSearch(
    patch: Partial<{
      feed: number | undefined;
      group: string | undefined;
      q: string | undefined;
      state: FeedItemState;
      read: FeedItemReadState;
      page: number;
      nodedupe: true | undefined;
    }>,
  ) {
    void navigate({ search: (prev) => ({ ...prev, ...patch }) });
  }

  const sidebar = (
    <FeedSidebar
      feeds={feeds}
      feedId={search.feed}
      group={search.group}
      onSelectAll={() => patchSearch({ feed: undefined, group: undefined, page: 1 })}
      onSelectUngrouped={() => patchSearch({ feed: undefined, group: UNGROUPED_GROUP, page: 1 })}
      onSelectGroup={(path) => patchSearch({ feed: undefined, group: path, page: 1 })}
      onSelectFeed={(feed) => {
        mobileNavHandlers.close();
        patchSearch({ feed: feed.id, group: undefined, page: 1 });
      }}
    />
  );

  return (
    <Box
      style={{
        height: APP_SHELL_MAIN_HEIGHT,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
      }}
    >
      <Group justify="space-between" wrap="wrap" mb="sm">
        <Group gap="sm">
          <Button
            hiddenFrom="sm"
            variant="default"
            size="sm"
            leftSection={<IconList size={16} />}
            onClick={mobileNavHandlers.open}
          >
            {t("sidebar.title")}
          </Button>
          <Title order={2}>{t("title")}</Title>
        </Group>
      </Group>

      {!isLoading && feeds.length === 0 ? (
        <Box ta="center" py="xl">
          <Text c="dimmed" size="sm">
            {t("empty")}
          </Text>
          <Button component={Link} to="/feeds/sources" mt="md" size="sm">
            {t("goToManage")}
          </Button>
        </Box>
      ) : (
        <Group align="stretch" gap={0} wrap="nowrap" style={{ flex: 1, minHeight: 0 }}>
          <Box visibleFrom="sm" w={260} style={{ minHeight: 0, flexShrink: 0 }}>
            {sidebar}
          </Box>
          <Box style={{ flex: 1, minWidth: 0, minHeight: 0, display: "flex" }} pl={{ sm: "md" }}>
            <FeedReader
              feeds={feeds}
              feedId={search.feed}
              group={search.group}
              q={search.q}
              state={search.state}
              read={search.read}
              page={search.page}
              dedupe={search.nodedupe == null}
              onQueryChange={(q) => patchSearch({ q, page: 1 })}
              onStateChange={(state) => patchSearch({ state, page: 1 })}
              onReadChange={(read) => patchSearch({ read, page: 1 })}
              onPageChange={(page) => patchSearch({ page })}
              onDedupeChange={(dedupe) => patchSearch({ nodedupe: dedupe ? undefined : true })}
              onOpenFeed={(feed) => patchSearch({ feed: feed.id, group: undefined, page: 1 })}
            />
          </Box>
        </Group>
      )}

      <Drawer
        opened={mobileNav}
        onClose={mobileNavHandlers.close}
        title={t("sidebar.title")}
        size="xs"
        hiddenFrom="sm"
      >
        {sidebar}
      </Drawer>
    </Box>
  );
}
