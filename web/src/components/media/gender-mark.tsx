import { IconGenderFemale, IconGenderMale } from "@tabler/icons-react";
import type { ParseKeys } from "i18next";
import { useTranslation } from "react-i18next";
import type { ActorGender } from "@/client/types.gen";

const GENDER_LABEL_KEY = {
  female: "browse.person.gender_female",
  male: "browse.person.gender_male",
  unknown: "browse.person.gender_unknown",
} as const satisfies Record<ActorGender, ParseKeys<"metadata">>;

/** 已知性别用与演员墙相同的符号; unknown 不画, 避免名单上铺满灰色标记. */
export function GenderMark({
  gender,
  size = 14,
}: {
  gender: ActorGender | null | undefined;
  size?: number;
}) {
  const { t } = useTranslation("metadata");
  if (gender !== "female" && gender !== "male") return null;
  const Icon = gender === "female" ? IconGenderFemale : IconGenderMale;
  const color = gender === "female" ? "var(--mantine-color-pink-5)" : "var(--mantine-color-blue-5)";
  const label = t(GENDER_LABEL_KEY[gender]);
  return <Icon size={size} color={color} aria-label={label} title={label} />;
}
