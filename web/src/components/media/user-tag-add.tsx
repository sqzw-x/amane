import { ActionIcon, Button, Checkbox, Combobox, Group, Text, useCombobox } from "@mantine/core";
import { IconMinus, IconPlus } from "@tabler/icons-react";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

const CREATE_PREFIX = "create:";

/** 与 Badge `sm` 同高; 加宽点击面, 合成一条胶囊以免像两枚小圆点. */
const hitChrome = {
  variant: "subtle",
  color: "gray",
  h: 18,
  w: 28,
  radius: 0,
} as const;

const capsuleStyle = {
  height: 18,
  borderRadius: 1000,
  overflow: "hidden",
  border: "1px solid var(--mantine-color-default-border)",
  background: "var(--mantine-color-default)",
} as const;

function useImeLock() {
  const composing = useRef(false);
  return {
    composing,
    onCompositionStart: () => {
      composing.current = true;
    },
    onCompositionEnd: () => {
      window.setTimeout(() => {
        composing.current = false;
      }, 0);
    },
    blocked: (e: { nativeEvent: { isComposing: boolean; keyCode: number } }) =>
      e.nativeEvent.isComposing || composing.current || e.nativeEvent.keyCode === 229,
  };
}

function OptionRow({ checked, label }: { checked: boolean; label: string }) {
  return (
    <Group gap="xs" wrap="nowrap">
      <Checkbox checked={checked} size="xs" tabIndex={-1} readOnly />
      <Text size="sm">{label}</Text>
    </Group>
  );
}

export function UserTagActions({
  attached,
  candidates,
  onChoose,
  onDetach,
  disabled = false,
}: {
  attached: ReadonlyArray<{ id: number; name: string }>;
  candidates: ReadonlyArray<{ id: number; name: string }>;
  onChoose: (names: string[]) => void;
  onDetach: (ids: number[]) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation("metadata");
  const ime = useImeLock();
  const addBox = useCombobox({
    onDropdownOpen: () => {
      window.setTimeout(() => searchRef.current?.focus(), 0);
    },
    onDropdownClose: () => {
      setSearch("");
      setAddIds(new Set());
      setCreateNames(new Set());
    },
  });
  const removeBox = useCombobox({
    onDropdownClose: () => setRemoveIds(new Set()),
  });

  const [search, setSearch] = useState("");
  const [addIds, setAddIds] = useState(new Set<number>());
  const [createNames, setCreateNames] = useState(new Set<string>());
  const [removeIds, setRemoveIds] = useState(new Set<number>());
  const searchRef = useRef<HTMLInputElement>(null);

  const query = search.trim();
  const filtered = candidates.filter((tag) => tag.name.toLowerCase().includes(query.toLowerCase()));
  const exact = candidates.some((tag) => tag.name === query);
  const canCreate = query.length > 0 && !exact;
  const addCount = addIds.size + createNames.size;
  const removeCount = removeIds.size;
  const empty = attached.length === 0;

  function toggleAddId(id: number) {
    setAddIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleCreate(name: string) {
    setCreateNames((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  function toggleRemove(id: number) {
    setRemoveIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function applyAdd() {
    const names = [
      ...candidates.filter((tag) => addIds.has(tag.id)).map((tag) => tag.name),
      ...createNames,
    ];
    if (names.length === 0) return;
    addBox.closeDropdown();
    onChoose(names);
  }

  function applyRemove() {
    if (removeIds.size === 0) return;
    const ids = [...removeIds];
    removeBox.closeDropdown();
    onDetach(ids);
  }

  function addCurrentQuery() {
    if (query.length === 0) return;
    const match = candidates.find((tag) => tag.name === query);
    if (match) toggleAddId(match.id);
    else toggleCreate(query);
    setSearch("");
  }

  return (
    <Group gap={0} wrap="nowrap" align="center" style={capsuleStyle}>
      <Combobox
        store={addBox}
        withinPortal
        width={260}
        position="bottom-start"
        shadow="md"
        onOptionSubmit={(value) => {
          if (value.startsWith(CREATE_PREFIX)) {
            toggleCreate(value.slice(CREATE_PREFIX.length));
            return;
          }
          toggleAddId(Number(value));
        }}
      >
        <Combobox.Target targetType="button">
          <ActionIcon
            {...hitChrome}
            disabled={disabled}
            aria-label={t("detail.addUserTag")}
            onClick={() => addBox.toggleDropdown()}
          >
            <IconPlus size={12} />
          </ActionIcon>
        </Combobox.Target>
        <Combobox.Dropdown>
          <Combobox.Search
            ref={searchRef}
            value={search}
            placeholder={t("detail.selectUserTag")}
            withKeyboardNavigation={false}
            onChange={(e) => setSearch(e.currentTarget.value)}
            onCompositionStart={ime.onCompositionStart}
            onCompositionEnd={ime.onCompositionEnd}
            onKeyDown={(e) => {
              if (ime.blocked(e)) return;
              if (e.key !== "Enter") return;
              e.preventDefault();
              if (query.length > 0) {
                addCurrentQuery();
                return;
              }
              applyAdd();
            }}
          />
          <Combobox.Options mah={220}>
            {filtered.map((tag) => (
              <Combobox.Option value={String(tag.id)} key={tag.id} active={addIds.has(tag.id)}>
                <OptionRow checked={addIds.has(tag.id)} label={tag.name} />
              </Combobox.Option>
            ))}
            {[...createNames]
              .filter((name) => name.toLowerCase().includes(query.toLowerCase()))
              .map((name) => (
                <Combobox.Option value={`${CREATE_PREFIX}${name}`} key={`c-${name}`} active>
                  <OptionRow checked label={t("detail.createUserTag", { name })} />
                </Combobox.Option>
              ))}
            {canCreate && !createNames.has(query) && (
              <Combobox.Option value={`${CREATE_PREFIX}${query}`}>
                <OptionRow checked={false} label={t("detail.createUserTag", { name: query })} />
              </Combobox.Option>
            )}
            {filtered.length === 0 && createNames.size === 0 && !canCreate && (
              <Combobox.Empty>{t("detail.noUserTags")}</Combobox.Empty>
            )}
          </Combobox.Options>
          <Combobox.Footer>
            <Button size="xs" fullWidth disabled={addCount === 0} onClick={applyAdd}>
              {t("detail.applyAddUserTags", { count: addCount })}
            </Button>
          </Combobox.Footer>
        </Combobox.Dropdown>
      </Combobox>

      <div
        style={{
          width: 1,
          alignSelf: "stretch",
          background: "var(--mantine-color-default-border)",
        }}
      />

      <Combobox
        store={removeBox}
        withinPortal
        width={260}
        position="bottom-start"
        shadow="md"
        onOptionSubmit={(value) => toggleRemove(Number(value))}
      >
        <Combobox.Target targetType="button">
          <ActionIcon
            {...hitChrome}
            disabled={empty || disabled}
            aria-label={t("detail.removeUserTag")}
            onClick={() => removeBox.toggleDropdown()}
          >
            <IconMinus size={12} />
          </ActionIcon>
        </Combobox.Target>
        <Combobox.Dropdown>
          <Combobox.Options mah={220}>
            {attached.map((tag) => (
              <Combobox.Option value={String(tag.id)} key={tag.id} active={removeIds.has(tag.id)}>
                <OptionRow checked={removeIds.has(tag.id)} label={tag.name} />
              </Combobox.Option>
            ))}
          </Combobox.Options>
          <Combobox.Footer>
            <Button
              size="xs"
              fullWidth
              color="red"
              disabled={removeCount === 0}
              onClick={applyRemove}
            >
              {t("detail.applyRemoveUserTags", { count: removeCount })}
            </Button>
          </Combobox.Footer>
        </Combobox.Dropdown>
      </Combobox>
    </Group>
  );
}
