import { Button, Group, Text, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconPin, IconPinnedOff } from "@tabler/icons-react";
import { useTranslation } from "react-i18next";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";
import type { NavListDefaultsUpdate } from "@/lib/nav-defaults";
import { useUIStore } from "@/stores/ui";

/**
 * 把当前列表参数写入侧栏条目的默认, 或清除它.
 *
 * 默认只在从侧栏进入时注入 URL (见 `lib/nav-defaults.ts`), 因此这里只负责存取, 不修改当前地址.
 * 不用浮层菜单: 窄屏的底部面板会在浮层交互后关闭 (见 `docs/dev/frontend.md`).
 * `update` 由页面按自己的 search schema 构造, 键与值不可错配.
 */
export function ListDefaultActions({
  update,
  disabledReason,
}: {
  update: NavListDefaultsUpdate;
  disabledReason?: string;
}) {
  const { t } = useTranslation("common");
  const stored = useUIStore((state) => state.listDefaults[update.key]);
  const setListDefault = useUIStore((state) => state.setListDefault);
  const clearListDefault = useUIStore((state) => state.clearListDefault);

  return (
    <Group gap="xs" wrap="nowrap">
      <Tooltip label={disabledReason} disabled={disabledReason == null}>
        <span style={{ display: "inline-flex" }}>
          <Button
            size="sm"
            disabled={disabledReason != null}
            variant={stored == null ? "default" : "light"}
            leftSection={<IconPin size={16} />}
            onClick={() => {
              setListDefault(update);
              notifications.show({ color: "green", message: t("defaults.saved") });
            }}
          >
            {t("defaults.set")}
          </Button>
        </span>
      </Tooltip>
      {disabledReason != null && (
        <Text size="xs" c="dimmed">
          {disabledReason}
        </Text>
      )}
      {stored != null && (
        <HintedActionIcon
          label={t("defaults.clear")}
          variant="transparent"
          color="gray"
          size="sm"
          onClick={() => {
            clearListDefault(update.key);
            notifications.show({ message: t("defaults.cleared") });
          }}
        >
          <IconPinnedOff size={16} />
        </HintedActionIcon>
      )}
    </Group>
  );
}
