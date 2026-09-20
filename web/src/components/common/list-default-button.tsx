import { Button, Menu } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconPin, IconPinnedOff } from "@tabler/icons-react";
import { useTranslation } from "react-i18next";
import type { NavListDefaultsUpdate } from "@/lib/nav-defaults";
import { useUIStore } from "@/stores/ui";

/**
 * 把当前列表参数写入侧栏条目的默认.
 *
 * 默认只在从侧栏进入时注入 URL (见 `lib/nav-defaults.ts`), 因此这里只负责存取, 不修改当前地址.
 * `update` 由页面按自己的 search schema 构造, 键与值不可错配.
 */
export function ListDefaultButton({ update }: { update: NavListDefaultsUpdate }) {
  const { t } = useTranslation("common");
  const stored = useUIStore((state) => state.listDefaults[update.key]);
  const setListDefault = useUIStore((state) => state.setListDefault);
  const clearListDefault = useUIStore((state) => state.clearListDefault);

  return (
    <Menu shadow="md" position="bottom-end" withinPortal>
      <Menu.Target>
        <Button
          size="sm"
          variant={stored == null ? "default" : "light"}
          leftSection={<IconPin size={16} />}
        >
          {t("navDefaults.title")}
        </Button>
      </Menu.Target>
      <Menu.Dropdown>
        <Menu.Item
          leftSection={<IconPin size={16} />}
          onClick={() => {
            setListDefault(update);
            notifications.show({ color: "green", message: t("navDefaults.saved") });
          }}
        >
          {t("navDefaults.set")}
        </Menu.Item>
        <Menu.Item
          leftSection={<IconPinnedOff size={16} />}
          disabled={stored == null}
          onClick={() => {
            clearListDefault(update.key);
            notifications.show({ message: t("navDefaults.cleared") });
          }}
        >
          {t("navDefaults.clear")}
        </Menu.Item>
      </Menu.Dropdown>
    </Menu>
  );
}
