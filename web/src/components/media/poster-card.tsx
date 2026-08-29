import { AspectRatio, Box, Card, Center, Group, Stack, Text } from "@mantine/core";
import { IconCalendar, IconStar, IconUser } from "@tabler/icons-react";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { MetadataResponse } from "@/client/types.gen";
import { ageAtRelease } from "@/lib/format-birthday";
import { formatReleaseYearMonth } from "@/lib/format-release";
import { proxyImageUrl } from "@/lib/utils";
import { FilePhaseBadges } from "@/components/media/file-phase-badges";
import { ProxyImage } from "@/components/media/proxy-image";

interface PosterCardProps {
  item: MetadataResponse;
  /** 传入时按影片发行日标注演员当时年龄 (演员详情出演墙). */
  actorBirthday?: string | null;
}

const OVERLAY_CHIP = {
  zIndex: 1,
  borderRadius: "var(--mantine-radius-xl)",
  background: "rgba(0, 0, 0, 0.72)",
  backdropFilter: "blur(6px)",
} as const;

/** 海报卡片 - 图片 + 番号 + 标题/演员/年月, 点击跳转详情. 加载失败退化为番号占位块. */
export function PosterCard({ item, actorBirthday }: PosterCardProps) {
  const { t } = useTranslation("metadata");
  const [errored, setErrored] = useState(false);
  const posterUrl = proxyImageUrl(item.poster_url ?? item.poster_urls?.[0]);
  const showImage = posterUrl && !errored;
  const actors = item.actors ?? [];
  const actorsLabel = actors.length > 0 ? actors.join(" / ") : null;
  const releaseLabel = formatReleaseYearMonth(item.release);
  const ageThen = ageAtRelease(actorBirthday, item.release);

  return (
    <Link
      to="/meta/$metadataId"
      params={{ metadataId: String(item.id) }}
      style={{ textDecoration: "none", color: "inherit", display: "block" }}
    >
      <Card padding={0} radius="md" withBorder style={{ overflow: "hidden" }}>
        <Box pos="relative">
          <AspectRatio ratio={0.7}>
            {showImage ? (
              <ProxyImage
                src={posterUrl}
                alt={item.number}
                loading="lazy"
                referrerPolicy="no-referrer"
                style={{ display: "block" }}
                onError={() => setErrored(true)}
              />
            ) : (
              <Center bg="var(--mantine-color-default-hover)" h="100%">
                <Text size="sm" ff="monospace" c="dimmed" ta="center" px="xs">
                  {item.number}
                </Text>
              </Center>
            )}
          </AspectRatio>
          <Box pos="absolute" top={6} left={6} style={{ zIndex: 1, maxWidth: "68%" }}>
            <FilePhaseBadges phase={item.file_phase} />
          </Box>
          {item.score != null && (
            <Group
              gap={3}
              wrap="nowrap"
              pos="absolute"
              top={6}
              right={6}
              px={6}
              py={3}
              style={OVERLAY_CHIP}
            >
              <IconStar
                size={11}
                color="var(--mantine-color-yellow-4)"
                fill="var(--mantine-color-yellow-4)"
              />
              <Text size="xs" c="white" fw={700} lh={1}>
                {item.score.toFixed(1)}
              </Text>
            </Group>
          )}
          {ageThen != null && (
            <Group
              gap={4}
              wrap="nowrap"
              pos="absolute"
              bottom={6}
              left={6}
              px={7}
              py={3}
              style={OVERLAY_CHIP}
            >
              <Text size="xs" c="white" fw={700} lh={1}>
                {t("actors.ageShort", { age: ageThen })}
              </Text>
            </Group>
          )}
          {releaseLabel && (
            <Group
              gap={4}
              wrap="nowrap"
              pos="absolute"
              bottom={6}
              right={6}
              px={7}
              py={3}
              style={OVERLAY_CHIP}
            >
              <IconCalendar size={12} color="var(--mantine-color-cyan-3)" />
              <Text size="xs" c="white" fw={700} lh={1} ff="monospace" lts={0.3}>
                {releaseLabel}
              </Text>
            </Group>
          )}
        </Box>
        <Stack gap={4} p="xs">
          <Text size="sm" fw={600} ff="monospace" lineClamp={1}>
            {item.number}
          </Text>
          {item.title && (
            <Text size="xs" c="dimmed" lineClamp={2} title={item.title}>
              {item.title}
            </Text>
          )}
          {actorsLabel && (
            <Group gap={5} wrap="nowrap" align="center" title={actorsLabel}>
              <Box
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  flexShrink: 0,
                  width: 18,
                  height: 18,
                  borderRadius: "var(--mantine-radius-sm)",
                  background: "var(--mantine-color-grape-filled)",
                  color: "var(--mantine-color-white)",
                }}
              >
                <IconUser size={11} stroke={2.2} />
              </Box>
              <Text
                size="xs"
                fw={600}
                lineClamp={1}
                style={{
                  minWidth: 0,
                  flex: 1,
                  color: "light-dark(var(--mantine-color-grape-7), var(--mantine-color-grape-3))",
                }}
              >
                {actorsLabel}
              </Text>
            </Group>
          )}
        </Stack>
      </Card>
    </Link>
  );
}
