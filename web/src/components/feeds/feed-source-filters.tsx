import { Collapse, Group } from "@mantine/core";
import { useTranslation } from "react-i18next";
import { TriStateSegment } from "@/components/common/tri-state-segment";
import type { FeedSourceFilters } from "@/lib/feeds/groups";

export function FeedSourceFilterControls({
  opened,
  values,
  onChange,
}: {
  opened: boolean;
  values: FeedSourceFilters;
  onChange: (next: FeedSourceFilters) => void;
}) {
  const { t } = useTranslation("feeds");
  return (
    <Collapse expanded={opened}>
      <Group gap="lg" align="flex-end" wrap="wrap">
        <TriStateSegment
          label={t("fields.enabled")}
          value={values.enabled}
          anyLabel={t("filter.any")}
          yesLabel={t("filter.enabled")}
          noLabel={t("filter.disabled")}
          onChange={(enabled) => onChange({ ...values, enabled })}
        />
        <TriStateSegment
          label={t("fields.autoEnqueue")}
          value={values.auto_enqueue}
          anyLabel={t("filter.any")}
          yesLabel={t("filter.autoEnqueue")}
          noLabel={t("filter.discoverOnly")}
          onChange={(auto_enqueue) => onChange({ ...values, auto_enqueue })}
        />
      </Group>
    </Collapse>
  );
}
