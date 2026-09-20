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
  IconDeviceMobile,
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
import { navItemClick } from "@/lib/nav-defaults";
import { shellEnvironment } from "@/lib/shell";
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

function NavItemLink({ item, onNavigate }: { item: NavItem; onNavigate?: () => void }) {
  const { t } = useTranslation("common");
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const listDefaults = useUIStore((state) => state.listDefaults);
  const Icon = item.icon;
  const shell = {
    label: t(item.labelKey),
    leftSection: <Icon size={18} stroke={1.6} />,
    active: isNavActive(pathname, item.to, item.end),
    variant: "filled" as const,
    style: { borderRadius: "var(--mantine-radius-md)" },
  };
  // 列表条目携带该页的默认参数: 入口地址不残留搜索词、页码与 agent 深链.
  // 路由的参数类型各不相同, 因此按 `to` 分支给字面量目标. 分类入口是种类索引页, 没有可承载的参数.
  if (item.to === "/meta") {
    return (
      <NavLink
        component={Link}
        to="/meta"
        onClick={(event) =>
          navItemClick(
            event,
            onNavigate,
            () => void navigate({ to: "/meta", search: listDefaults.meta ?? {} }),
          )
        }
        {...shell}
      />
    );
  }
  if (item.to === "/actors") {
    return (
      <NavLink
        component={Link}
        to="/actors"
        onClick={(event) =>
          navItemClick(
            event,
            onNavigate,
            () => void navigate({ to: "/actors", search: listDefaults.actors ?? {} }),
          )
        }
        {...shell}
      />
    );
  }
  if (item.to === "/feeds") {
    return (
      <NavLink
        component={Link}
        to="/feeds"
        onClick={(event) =>
          navItemClick(
            event,
            onNavigate,
            () => void navigate({ to: "/feeds", search: listDefaults.feeds ?? {} }),
          )
        }
        {...shell}
        activeOptions={{ exact: true, includeSearch: false }}
      />
    );
  }
  return (
    <NavLink
      component={Link}
      to={item.to}
      onClick={onNavigate}
      {...shell}
      activeOptions={item.end ? { exact: true, includeSearch: false } : undefined}
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

/**
 * 客户端设置入口: 服务器与登录态属于客户端, 不并入服务端配置. 只在壳内渲染 (判据见 lib/shell.ts).
 */
function ClientSettingsLink({ available }: { available: boolean }) {
  const { t } = useTranslation("common");
  const navigate = useNavigate();
  const { pathname } = useLocation();
  if (!available) return null;

  return (
    <HintedActionIcon
      variant="subtle"
      color={isNavActive(pathname, "/client") ? "brand" : "gray"}
      size="lg"
      onClick={() => void navigate({ to: "/client" })}
      label={t("nav.client")}
    >
      <IconDeviceMobile size={18} />
    </HintedActionIcon>
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
    return <div style={{ flex: 1, minWidth: 0 }} />;
  }

  return (
    // 窄屏顶栏放不下搜索框, 入口回落到片库页内搜索.
    <Box
      component="form"
      visibleFrom="sm"
      style={{ flex: 1, minWidth: 0, maxWidth: 480, marginLeft: 24 }}
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
    </Box>
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
      {/* 窄屏顶栏只保留品牌与图标动作, 版本号在侧栏内呈现; 不加包装元素, 避免改变宽屏下的行内基线. */}
      <VersionMenu visibleFrom="sm" />
    </Group>
  );
}

function GithubLink({ visibleFrom }: { visibleFrom?: "sm" }) {
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
        visibleFrom={visibleFrom}
        aria-label={t("about.github")}
      >
        <IconBrandGithub size={18} />
      </ActionIcon>
    </Tooltip>
  );
}

export function AppShellLayout(): ReactNode {
  const { t } = useTranslation("common");
  const [mobileOpened, { toggle: toggleMobile, close: closeMobile }] = useDisclosure(false);
  const desktopCollapsed = useUIStore((s) => s.navbarCollapsed);
  const toggleDesktop = useUIStore((s) => s.toggleNavbar);
  const shell = shellEnvironment();

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
            <ClientSettingsLink available={shell != null} />
            {/* 窄屏顶栏放不下外链, 该入口收进侧栏底部. */}
            <GithubLink visibleFrom="sm" />
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="sm">
        <ScrollArea
          style={{ flex: 1 }}
          offsetScrollbars
          // 侧栏滚到尽头时不许把滚动传给底下的页面 (触屏上尤其明显).
          viewportProps={{ style: { overscrollBehavior: "contain" } }}
        >
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
                <NavItemLink key={item.to} item={item} onNavigate={closeMobile} />
              ))}
            </div>
          ))}
        </ScrollArea>
        <Divider mb="sm" />
        <NavItemLink
          item={{ to: "/settings", labelKey: "nav.settings", icon: IconSettings }}
          onNavigate={closeMobile}
        />
        {/* 窄屏顶栏放不下版本与外链, 收进侧栏底部. */}
        <Group hiddenFrom="sm" gap="xs" px="xs" pt="sm" wrap="nowrap">
          <VersionMenu />
          <Box ml="auto">
            <GithubLink />
          </Box>
        </Group>
      </AppShell.Navbar>

      <AppShell.Main>
        <Outlet />
      </AppShell.Main>
    </AppShell>
  );
}
