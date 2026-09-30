import { Badge, Input, Stack, Text, Textarea, TextInput } from "@mantine/core";
import { useTranslation } from "react-i18next";
import type {
  SavedQueryCreateRequest,
  SavedQueryEntity,
  SavedQueryResponse,
  SavedQueryUpdateRequest,
} from "@/client/types.gen";
import { EnumToggle } from "@/components/common/enum-toggle";
import { SAVED_QUERY_ENTITIES } from "@/lib/exhaustive-maps";
import { SAVED_QUERY_BADGE_COLOR, SAVED_QUERY_ENTITY_LABEL_KEY } from "@/lib/saved-query/display";

export const SAVED_QUERY_FORM_MODAL_SIZE = "min(56rem, 94vw)";
export const SAVED_QUERY_NAME_MAX = 200;
export const SAVED_QUERY_DESCRIPTION_MAX = 2000;

/** 各类型契约提示的翻译 key, 与 SAVED_QUERY_ENTITIES 同步. */
const ENTITY_HINT_KEY = {
  metadata: "entityHintMetadata",
  actor: "entityHintActor",
  data: "entityHintData",
} as const satisfies Record<SavedQueryEntity, string>;

export interface SavedQueryFormState {
  name: string;
  description: string;
  entity: SavedQueryEntity;
  sql: string;
}

export function emptySavedQueryForm(): SavedQueryFormState {
  return { name: "", description: "", entity: "metadata", sql: "" };
}

export function savedQueryFormFromResponse(query: SavedQueryResponse): SavedQueryFormState {
  return {
    name: query.name,
    description: query.description,
    entity: query.entity,
    sql: query.sql,
  };
}

export function savedQueryFormToCreateBody(form: SavedQueryFormState): SavedQueryCreateRequest {
  return {
    name: form.name.trim(),
    description: form.description.trim(),
    entity: form.entity,
    sql: form.sql.trim(),
  };
}

/** 只提交改动字段; 类型不可改, 空对象表示无改动 (保存按钮据此禁用). */
export function savedQueryFormToUpdateBody(
  form: SavedQueryFormState,
  original: SavedQueryResponse,
): SavedQueryUpdateRequest {
  const body: SavedQueryUpdateRequest = {};
  const name = form.name.trim();
  const description = form.description.trim();
  const sql = form.sql.trim();
  if (name !== original.name) body.name = name;
  if (description !== original.description) body.description = description;
  if (sql !== original.sql.trim()) body.sql = sql;
  return body;
}

export function isSavedQueryFormDirty(
  form: SavedQueryFormState,
  original: SavedQueryResponse,
): boolean {
  return Object.keys(savedQueryFormToUpdateBody(form, original)).length > 0;
}

export function SavedQueryFormFields({
  value,
  onChange,
  entityEditable,
}: {
  value: SavedQueryFormState;
  onChange: (next: SavedQueryFormState) => void;
  /** 创建表单可选类型; 编辑表单只读展示. */
  entityEditable: boolean;
}) {
  const { t } = useTranslation("savedQueries");

  return (
    <Stack gap="md">
      <TextInput
        label={t("fieldName")}
        placeholder={t("fieldNamePlaceholder")}
        required
        maxLength={SAVED_QUERY_NAME_MAX}
        value={value.name}
        onChange={(e) => onChange({ ...value, name: e.currentTarget.value })}
      />
      <TextInput
        label={t("fieldDescription")}
        maxLength={SAVED_QUERY_DESCRIPTION_MAX}
        value={value.description}
        onChange={(e) => onChange({ ...value, description: e.currentTarget.value })}
      />
      <Input.Wrapper
        label={t("fieldEntity")}
        description={entityEditable ? t("fieldEntityHint") : undefined}
      >
        {entityEditable ? (
          <EnumToggle
            options={SAVED_QUERY_ENTITIES}
            value={value.entity}
            onChange={(entity) => onChange({ ...value, entity })}
            getLabel={(entity) => t(SAVED_QUERY_ENTITY_LABEL_KEY[entity])}
          />
        ) : (
          <Badge variant="light" color={SAVED_QUERY_BADGE_COLOR[value.entity]} w="max-content">
            {t(SAVED_QUERY_ENTITY_LABEL_KEY[value.entity])}
          </Badge>
        )}
        {entityEditable && (
          <Text size="xs" c="dimmed" mt={6}>
            {t(ENTITY_HINT_KEY[value.entity])}
          </Text>
        )}
      </Input.Wrapper>
      <Textarea
        label={t("fieldSql")}
        description={t("fieldSqlHint")}
        required
        autosize
        minRows={6}
        maxRows={18}
        ff="monospace"
        value={value.sql}
        onChange={(e) => onChange({ ...value, sql: e.currentTarget.value })}
      />
    </Stack>
  );
}
