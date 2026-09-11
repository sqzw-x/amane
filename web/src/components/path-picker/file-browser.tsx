import {
  Alert,
  Button,
  Checkbox,
  Group,
  Loader,
  ScrollArea,
  Stack,
  Text,
  TextInput,
  Tooltip,
  UnstyledButton,
} from "@mantine/core";
import { IconChevronRight, IconEye, IconEyeOff, IconFile, IconFolder } from "@tabler/icons-react";
import { useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { FileItem } from "@/client/types.gen";
import { useFileList } from "@/hooks/use-files";
import { canonicalAncestor, splitCanonicalPath } from "@/lib/canonical-path";

interface FileBrowserProps {
  /** 起始浏览路径 (仅作首次请求起点; 渲染由服务端规范 path 驱动). */
  initialPath?: string;
  /** Callback when selection is confirmed. */
  onSelect: (paths: string[]) => void;
  /** Allow multiple selections. */
  allowMultiple?: boolean;
  /** What types can be selected: file, directory, or mixed. */
  selectionType?: "file" | "directory" | "mixed";
  /** Show the path input field. */
  showPathInput?: boolean;
}

export function FileBrowser({
  initialPath = ".",
  onSelect,
  allowMultiple = false,
  selectionType = "mixed",
  showPathInput = true,
}: FileBrowserProps) {
  const { t } = useTranslation(["fileBrowser", "common"]);
  const [path, setPath] = useState(initialPath);
  const [pathInputValue, setPathInputValue] = useState(initialPath);
  // 相对路径输入时的基准目录 (当前浏览位置); 绝对导航时清空, 由服务端缺省回退.
  const [base, setBase] = useState<string | null>(null);
  const [selectedPaths, setSelectedPaths] = useState(new Set<string>());
  const [showHidden, setShowHidden] = useState(false);

  const { data, error, isLoading, refetch } = useFileList({
    path,
    base: base ?? undefined,
    show_hidden: showHidden,
  });
  const items = data?.items ?? [];
  const total = data?.total;

  // 报错时 react-query 会清空 data (keepPreviousData 仅在挂起期生效), 记录最近一次
  // 成功的规范路径 (render 期状态调整), 保证面包屑在错误态仍可点击返回.
  const [lastGoodPath, setLastGoodPath] = useState<string | null>(null);
  if (data?.path && data.path !== lastGoodPath) {
    setLastGoodPath(data.path);
  }

  const isItemSelectable = (item: FileItem) => {
    if (selectionType === "mixed") return true;
    return item.type === selectionType;
  };

  const handleItemClick = (item: FileItem) => {
    if (item.type === "directory") {
      setPath(item.path);
      setPathInputValue(item.path);
      setBase(null);
      return;
    }
    if (isItemSelectable(item)) {
      if (allowMultiple) {
        handleToggleSelection(item.path);
      } else {
        setSelectedPaths(new Set([item.path]));
      }
    }
  };

  const handleToggleSelection = (itemPath: string) => {
    setSelectedPaths((prev) => {
      const next = new Set(prev);
      if (next.has(itemPath)) {
        next.delete(itemPath);
      } else {
        next.add(itemPath);
      }
      return next;
    });
  };

  const handleSelectAll = () => {
    setSelectedPaths((prev) => {
      const next = new Set(prev);
      for (const item of items.filter(isItemSelectable)) {
        next.add(item.path);
      }
      return next;
    });
  };

  const handleClearAll = () => setSelectedPaths(new Set());

  const handleConfirm = () => {
    if (!allowMultiple && selectedPaths.size === 0 && selectionType !== "file") {
      // 回传规范路径: 服务器 resolve 后的绝对形态 (选目录时回退)
      onSelect([data?.path ?? path]);
    } else {
      onSelect(Array.from(selectedPaths));
    }
  };

  const handlePathInputSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const newPath = pathInputValue.trim();
    if (newPath) {
      // 相对输入以当前浏览目录 (data.path) 为基准; 绝对输入不携带 base (服务端忽略)
      const isRelative = splitCanonicalPath(newPath).root === "";
      setBase(isRelative ? (data?.path ?? null) : null);
      setPath(newPath);
    }
  };

  const handleBreadcrumbClick = (targetPath: string) => {
    setPath(targetPath);
    setPathInputValue(targetPath);
    setBase(null);
  };

  const handleReset = () => {
    setPath(initialPath);
    setPathInputValue(initialPath);
    setBase(null);
  };

  // Breadcrumb 由服务端规范路径 (根 + 段) 驱动, 三类前缀互斥, 无平台猜测.
  // load 期间 data 保留上一次响应 (keepPreviousData), 面包屑不闪烁.
  const parts = splitCanonicalPath(data?.path ?? lastGoodPath ?? "");
  const rows = parts.root ? [parts.root, ...parts.segments] : [];
  const rowLen = rows.length;

  // Dynamic breadcrumb collapse: always show first 2 + last 2, hide middle segments on overflow
  const MIN_FIRST = 2;
  const MIN_LAST = 2;
  const [collapseCount, setCollapseCount] = useState(0);
  const breadcrumbRef = useRef<HTMLDivElement>(null);
  const prevLenRef = useRef(rowLen);

  // Reset collapse when path changes, then incrementally collapse until nav fits
  useLayoutEffect(() => {
    const len = rowLen;
    if (prevLenRef.current !== len) {
      prevLenRef.current = len;
      setCollapseCount(0);
      return;
    }
    const nav = breadcrumbRef.current;
    const maxCollapse = Math.max(0, len - MIN_FIRST - MIN_LAST);
    if (!nav || maxCollapse === 0) return;
    if (collapseCount >= maxCollapse) return;
    if (nav.scrollWidth > nav.clientWidth) {
      setCollapseCount((c) => c + 1);
    }
  }, [collapseCount, rowLen]);

  const shouldCollapse = collapseCount > 0;

  return (
    <Stack gap="sm" style={{ minWidth: 0 }}>
      {/* Path input */}
      {showPathInput && (
        <Group component="form" gap="xs" wrap="nowrap" onSubmit={handlePathInputSubmit}>
          <TextInput
            value={pathInputValue}
            onChange={(e) => setPathInputValue(e.target.value)}
            placeholder="Enter path..."
            style={{ flex: 1, minWidth: 0 }}
          />
          <Tooltip label={t("toggleHidden")}>
            <Button
              type="button"
              variant={showHidden ? "light" : "outline"}
              size="sm"
              onClick={() => setShowHidden(!showHidden)}
              aria-label={t("toggleHidden")}
            >
              {showHidden ? <IconEye size={16} /> : <IconEyeOff size={16} />}
            </Button>
          </Tooltip>
          <Button type="submit" size="sm">
            Go
          </Button>
        </Group>
      )}

      {/* Breadcrumbs */}
      <Group ref={breadcrumbRef} gap={2} wrap="nowrap" style={{ overflowX: "auto" }}>
        {rows.map((row, index) => {
          // Always show first MIN_FIRST + last MIN_LAST; hide middle segments on overflow
          const len = rowLen;
          const isHiddenSegment =
            shouldCollapse &&
            index >= MIN_FIRST &&
            index < MIN_FIRST + collapseCount &&
            index < len - MIN_LAST;
          if (isHiddenSegment) {
            if (index === MIN_FIRST) {
              return (
                <Group key="ellipsis" gap={2} wrap="nowrap" style={{ flexShrink: 0 }}>
                  <IconChevronRight size={12} style={{ flexShrink: 0, opacity: 0.6 }} />
                  <Text size="sm" c="dimmed" px={4}>
                    ...
                  </Text>
                </Group>
              );
            }
            return null;
          }
          const targetPath = canonicalAncestor(parts, index);
          return (
            <Group key={targetPath} gap={2} wrap="nowrap" style={{ flexShrink: 0 }}>
              {index > 0 && <IconChevronRight size={12} style={{ flexShrink: 0, opacity: 0.6 }} />}
              <UnstyledButton
                onClick={() => handleBreadcrumbClick(targetPath)}
                px={4}
                style={{ borderRadius: "var(--mantine-radius-sm)", whiteSpace: "nowrap" }}
              >
                <Text size="sm" c="dimmed">
                  {row}
                </Text>
              </UnstyledButton>
            </Group>
          );
        })}
      </Group>

      {/* Truncation warning */}
      {total != null && total > items.length && (
        <Alert color="yellow" variant="light" py={8}>
          {t("tooManyEntries", {
            shown: items.length,
            total,
          })}
        </Alert>
      )}

      {/* File list area - fixed height to prevent layout shift */}
      <Stack
        h={384}
        style={{
          border: "1px solid var(--mantine-color-default-border)",
          borderRadius: "var(--mantine-radius-sm)",
          position: "relative",
        }}
      >
        {isLoading && (
          <Group justify="center" align="center" style={{ position: "absolute", inset: 0 }}>
            <Loader size="sm" />
          </Group>
        )}

        {error && (
          <Group justify="center" align="center" p="md" style={{ position: "absolute", inset: 0 }}>
            <Alert color="red" variant="light">
              <Group gap="sm" wrap="nowrap">
                <Text size="sm">
                  {error instanceof Error ? error.message : JSON.stringify(error)}
                </Text>
                <Button variant="subtle" size="compact-xs" onClick={() => refetch()}>
                  {t("common:actions.retry")}
                </Button>
                <Button variant="subtle" size="compact-xs" onClick={handleReset}>
                  {t("common:actions.reset")}
                </Button>
              </Group>
            </Alert>
          </Group>
        )}

        {!isLoading && !error && (
          <ScrollArea h="100%">
            <Stack gap={0}>
              {items.length === 0 ? (
                <Group justify="center" py="xl">
                  <Text size="sm" c="dimmed">
                    {t("empty")}
                  </Text>
                </Group>
              ) : (
                items.map((item, idx) => {
                  const isSelected = selectedPaths.has(item.path);
                  const selectable = isItemSelectable(item);
                  const disabled = !selectable && item.type !== "directory";
                  return (
                    <UnstyledButton
                      key={item.path}
                      onClick={() => handleItemClick(item)}
                      disabled={disabled}
                      px="sm"
                      py={6}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        borderTop:
                          idx === 0 ? undefined : "1px solid var(--mantine-color-default-border)",
                        backgroundColor: isSelected ? "var(--mantine-color-blue-light)" : undefined,
                        opacity: disabled ? 0.5 : 1,
                        cursor: disabled ? "not-allowed" : "pointer",
                      }}
                    >
                      {allowMultiple && selectable && (
                        <Checkbox
                          checked={isSelected}
                          onChange={() => handleToggleSelection(item.path)}
                          onClick={(e) => e.stopPropagation()}
                        />
                      )}
                      {item.type === "directory" ? (
                        <IconFolder
                          size={16}
                          color="var(--mantine-color-blue-6)"
                          style={{ flexShrink: 0 }}
                        />
                      ) : (
                        <IconFile
                          size={16}
                          color="var(--mantine-color-dimmed)"
                          style={{ flexShrink: 0 }}
                        />
                      )}
                      <Text
                        size="sm"
                        style={{
                          flex: 1,
                          minWidth: 0,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                          textAlign: "left",
                        }}
                      >
                        {item.name}
                      </Text>
                      {item.type === "directory" && (
                        <IconChevronRight
                          size={14}
                          color="var(--mantine-color-dimmed)"
                          style={{ flexShrink: 0 }}
                        />
                      )}
                    </UnstyledButton>
                  );
                })
              )}
            </Stack>
          </ScrollArea>
        )}
      </Stack>

      {/* Actions */}
      <Group justify="space-between">
        <Group gap={6}>
          {allowMultiple && (
            <>
              <Button
                variant="outline"
                size="compact-xs"
                onClick={handleSelectAll}
                disabled={
                  isLoading ||
                  !!error ||
                  items.filter(isItemSelectable).every((i) => selectedPaths.has(i.path))
                }
              >
                {t("selectAll")}
              </Button>
              <Button
                variant="outline"
                size="compact-xs"
                onClick={handleClearAll}
                disabled={selectedPaths.size === 0}
              >
                {t("common:actions.clear")}
              </Button>
            </>
          )}
        </Group>
        <Button
          size="sm"
          onClick={handleConfirm}
          disabled={
            isLoading ||
            !!error ||
            (selectedPaths.size === 0 && (allowMultiple || selectionType === "file"))
          }
        >
          {!allowMultiple && selectedPaths.size === 0 && selectionType !== "file"
            ? t("selectDir")
            : `${t("common:actions.confirm")}${selectedPaths.size > 1 ? ` (${selectedPaths.size})` : ""}`}
        </Button>
      </Group>
    </Stack>
  );
}
