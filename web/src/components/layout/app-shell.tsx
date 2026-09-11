import {
  ActionIcon,
  AppShell,
  Box,
  Burger,
  Divider,
  Group,
  Menu,
  NavLink,
  ScrollArea,
  Text,
  TextInput,
  Title,
  Tooltip,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import {
  IconBrandGithub,
  IconCategory,
  IconClock,
  IconFileText,
  IconFolders,
  IconLanguage,
  IconLayoutSidebarLeftCollapse,
  IconLayoutSidebarLeftExpand,
  IconListDetails,
  IconListTree,
  IconMessageChatbot,
  IconMoonStars,
  IconMovie,
  IconPuzzle,
  IconRss,
  IconSearch,
  IconSettings,
  IconSun,
  IconSunMoon,
  IconUsers,
  type Icon,
} from "@tabler/icons-react";
import { Link, Outlet, useLocation, useNavigate } from "@tanstack/react-router";
import type { ParseKeys } from "i18next";
import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";
import { APP_SHELL_HEADER_HEIGHT } from "@/components/layout/app-shell-metrics";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";
import { VersionMenu } from "@/components/layout/version-menu";
import { APP_NAME, GITHUB_URL } from "@/lib/app";
import { useConnectionStore } from "@/stores/connection";
import { useUIStore } from "@/stores/ui";

type CommonKey = ParseKeys<"common">;

interface NavItem {
  to: string;
  labelKey: CommonKey;
  icon: Icon;
  end?: boolean;
}

interface NavGroup {
  key: string;
  labelKey: CommonKey;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    key: "browse",
    labelKey: "nav.groups.browse",
    items: [
      { to: "/", labelKey: "nav.agent", icon: IconMessageChatbot },
      { to: "/meta", labelKey: "nav.meta", icon: IconMovie },
      { to: "/actors", labelKey: "nav.actors", icon: IconUsers },
      { to: "/catalog", labelKey: "nav.catalog", icon: IconCategory },
      { to: "/feeds", labelKey: "nav.feeds", icon: IconRss, end: true },
    ],
  },
  {
    key: "manage",
    labelKey: "nav.groups.manage",
    items: [
      { to: "/libraries", labelKey: "nav.libraries", icon: IconFolders },
      { to: "/plugins", labelKey: "nav.plugins", icon: IconPuzzle },
      { to: "/feeds/sources", labelKey: "nav.feedSources", icon: IconListTree },
    ],
  },
  {
    key: "ops",
    labelKey: "nav.groups.ops",
    items: [
      { to: "/tasks", labelKey: "nav.tasks", icon: IconListDetails },
      { to: "/schedules", labelKey: "nav.schedules", icon: IconClock },
      { to: "/logs", labelKey: "nav.logs", icon: IconFileText },
    ],
  },
];

function isNavActive(pathname: string, to: string, end = false): boolean {
  if (to === "/") return pathname === "/";
  if (end) return pathname === to || pathname === `${to}/`;
  return pathname === to || pathname.startsWith(`${to}/`);
}

function NavItemLink({ item }: { item: NavItem }) {
  const { t } = useTranslation("common");
  const { pathname } = useLocation();
  const Icon = item.icon;
  return (
    <NavLink
      component={Link}
      to={item.to}
      activeOptions={item.end ? { exact: true, includeSearch: false } : undefined}
      label={t(item.labelKey)}
      leftSection={<Icon size={18} stroke={1.6} />}
      active={isNavActive(pathname, item.to, item.end)}
      variant="filled"
      style={{ borderRadius: "var(--mantine-radius-md)" }}
    />
  );
}

function ThemeToggle() {
  const theme = useUIStore((s) => s.theme);
  const setTheme = useUIStore((s) => s.setTheme);
  const { t } = useTranslation("common");

  const next = theme === "light" ? "dark" : theme === "dark" ? "auto" : "light";
  const icon =
    theme === "light" ? (
      <IconSun size={18} />
    ) : theme === "dark" ? (
      <IconMoonStars size={18} />
    ) : (
      <IconSunMoon size={18} />
    );

  return (
    <HintedActionIcon
      variant="subtle"
      color="gray"
      size="lg"
      onClick={() => setTheme(next)}
      label={t(`theme.${theme}`)}
    >
      {icon}
    </HintedActionIcon>
  );
}

function LanguageMenu() {
  const { t, i18n } = useTranslation("common");
  const setLanguage = useUIStore((s) => s.setLanguage);

  return (
    <Menu position="bottom-end" shadow="md">
      <Menu.Target>
        <ActionIcon variant="subtle" color="gray" size="lg" aria-label="language">
          <IconLanguage size={18} />
        </ActionIcon>
      </Menu.Target>
      <Menu.Dropdown>
        <Menu.Item
          onClick={() => {
            setLanguage("zh-CN");
            void i18n.changeLanguage("zh-CN");
          }}
        >
          {t("language.zh-CN")}
        </Menu.Item>
        <Menu.Item
          onClick={() => {
            setLanguage("en");
            void i18n.changeLanguage("en");
          }}
        >
          {t("language.en")}
        </Menu.Item>
      </Menu.Dropdown>
    </Menu>
  );
}

function ConnectionIndicator() {
  const status = useConnectionStore((s) => s.status);
  const { t } = useTranslation("common");
  const color = status === "connected" ? "teal" : status === "reconnecting" ? "yellow" : "red";
  const labelKey =
    status === "connected"
      ? "status.connected"
      : status === "reconnecting"
        ? "status.reconnecting"
        : "status.disconnected";

  return (
    <HintedActionIcon variant="subtle" color="gray" size="lg" label={t(labelKey)}>
      <Box
        w={10}
        h={10}
        bg={`${color}.5`}
        style={{
          borderRadius: "50%",
          boxShadow:
            status === "reconnecting" ? `0 0 0 3px var(--mantine-color-${color}-2)` : undefined,
        }}
      />
    </HintedActionIcon>
  );
}

function HeaderSearch() {
  const { t } = useTranslation("metadata");
  const navigate = useNavigate();
  const location = useLocation();
  const [value, setValue] = useState("");

  // 快捷入口: 已在片库页时隐藏, 避免与页内搜索重复
  if (location.pathname === "/meta" || location.pathname.startsWith("/meta/")) {
    return <div style={{ flex: 1 }} />;
  }

  return (
    <form
      style={{ flex: 1, maxWidth: 480, marginLeft: 24 }}
      onSubmit={(e) => {
        e.preventDefault();
        const q = value.trim();
        setValue("");
        void navigate({ to: "/meta", search: { q: q || undefined } });
      }}
    >
      <TextInput
        value={value}
        onChange={(e) => setValue(e.currentTarget.value)}
        placeholder={t("search.placeholder")}
        leftSection={<IconSearch size={16} />}
        radius="md"
      />
    </form>
  );
}

function HeaderBrand() {
  return (
    <Group gap={8} wrap="nowrap" align="center">
      <Link to="/" style={{ textDecoration: "none", color: "inherit", whiteSpace: "nowrap" }}>
        <Group gap={8} wrap="nowrap" align="center">
          <img
            src="/favicon.svg"
            width={22}
            height={22}
            alt=""
            aria-hidden
            style={{ display: "block" }}
          />
          <Title order={4}>{APP_NAME}</Title>
        </Group>
      </Link>
      <VersionMenu />
    </Group>
  );
}

function GithubLink() {
  const { t } = useTranslation("common");

  return (
    <Tooltip label={t("about.github")}>
      <ActionIcon
        component="a"
        href={GITHUB_URL}
        target="_blank"
        rel="noreferrer"
        variant="subtle"
        color="gray"
        size="lg"
        aria-label={t("about.github")}
      >
        <IconBrandGithub size={18} />
      </ActionIcon>
    </Tooltip>
  );
}

export function AppShellLayout(): ReactNode {
  const { t } = useTranslation("common");
  const [mobileOpened, { toggle: toggleMobile }] = useDisclosure(false);
  const desktopCollapsed = useUIStore((s) => s.navbarCollapsed);
  const toggleDesktop = useUIStore((s) => s.toggleNavbar);

  return (
    <AppShell
      header={{ height: APP_SHELL_HEADER_HEIGHT }}
      navbar={{
        width: 260,
        breakpoint: "sm",
        collapsed: { mobile: !mobileOpened, desktop: desktopCollapsed },
      }}
      padding="md"
    >
      <AppShell.Header>
        <Group h="100%" px="md" gap="sm" wrap="nowrap">
          <Burger opened={mobileOpened} onClick={toggleMobile} hiddenFrom="sm" size="sm" />
          <ActionIcon
            variant="subtle"
            color="gray"
            size="lg"
            visibleFrom="sm"
            onClick={toggleDesktop}
            aria-label="toggle navbar"
          >
            {desktopCollapsed ? (
              <IconLayoutSidebarLeftExpand size={18} />
            ) : (
              <IconLayoutSidebarLeftCollapse size={18} />
            )}
          </ActionIcon>
          <HeaderBrand />
          <HeaderSearch />
          <Group ml="auto" gap="xs" wrap="nowrap">
            <ConnectionIndicator />
            <ThemeToggle />
            <LanguageMenu />
            <GithubLink />
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="sm">
        <ScrollArea style={{ flex: 1 }} offsetScrollbars>
          {NAV_GROUPS.map((group) => (
            <div key={group.key} style={{ marginBottom: 16 }}>
              <Text
                size="xs"
                fw={700}
                c="dimmed"
                px="xs"
                mb={4}
                style={{ textTransform: "uppercase", letterSpacing: 0.5 }}
              >
                {t(group.labelKey)}
              </Text>
              {group.items.map((item) => (
                <NavItemLink key={item.to} item={item} />
              ))}
            </div>
          ))}
        </ScrollArea>
        <Divider mb="sm" />
        <NavItemLink item={{ to: "/settings", labelKey: "nav.settings", icon: IconSettings }} />
      </AppShell.Navbar>

      <AppShell.Main>
        <Outlet />
      </AppShell.Main>
    </AppShell>
  );
}
