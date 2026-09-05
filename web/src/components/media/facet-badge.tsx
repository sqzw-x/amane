import { Badge, type MantineColor } from "@mantine/core";
import { Link } from "@tanstack/react-router";
import type { ActorGender, FacetKind } from "@/client/types.gen";
import { GenderMark } from "@/components/media/gender-mark";
import { metaSearchForFacet } from "@/lib/facets";

const KIND_COLOR: Record<FacetKind, MantineColor> = {
  actor: "grape",
  director: "indigo",
  tag: "teal",
  studio: "blue",
  publisher: "cyan",
  series: "orange",
  user_tag: "pink",
};

/** 去掉锚点行高, 让徽章高度就是 flex 项高度. */
const BADGE_LINK_STYLE = { textDecoration: "none", display: "inline-flex", lineHeight: 1 } as const;

interface FacetBadgeProps {
  kind: FacetKind;
  /** Facet id - 未知 (未建立投影索引) 时为 null/undefined, 渲染为不可点击的纯文本徽章. */
  id: number | null | undefined;
  name: string;
  /** 仅 actor: 来自 Actor 实体. unknown / 缺省不画符号. */
  gender?: ActorGender | null;
  /** "catalog": 分类实体页 (actor → /actors/$id); "meta": 片库列表并附带该 facet 过滤. @default "catalog" */
  mode?: "meta" | "catalog";
  variant?: "filled" | "light" | "outline" | "dot" | "transparent";
  size?: "xs" | "sm" | "md" | "lg";
}

/** 默认为实体页; `mode="meta"` 时进入片库筛选. */
export function FacetBadge({
  kind,
  id,
  name,
  gender,
  mode = "catalog",
  variant = "light",
  size = "sm",
}: FacetBadgeProps) {
  const color = KIND_COLOR[kind];
  const leftSection =
    kind === "actor" && (gender === "female" || gender === "male") ? (
      <GenderMark gender={gender} size={12} />
    ) : undefined;

  if (id == null) {
    return (
      <Badge color={color} variant={variant} size={size} leftSection={leftSection}>
        {name}
      </Badge>
    );
  }

  if (mode === "catalog") {
    if (kind === "actor") {
      return (
        <Link to="/actors/$actorId" params={{ actorId: String(id) }} style={BADGE_LINK_STYLE}>
          <Badge
            component="span"
            color={color}
            variant={variant}
            size={size}
            leftSection={leftSection}
            style={{ cursor: "pointer" }}
          >
            {name}
          </Badge>
        </Link>
      );
    }
    return (
      <Link
        to="/catalog/$kind/$facetId"
        params={{ kind, facetId: String(id) }}
        style={BADGE_LINK_STYLE}
      >
        <Badge
          component="span"
          color={color}
          variant={variant}
          size={size}
          style={{ cursor: "pointer" }}
        >
          {name}
        </Badge>
      </Link>
    );
  }

  return (
    <Link to="/meta" search={metaSearchForFacet(kind, id)} style={BADGE_LINK_STYLE}>
      <Badge
        component="span"
        color={color}
        variant={variant}
        size={size}
        leftSection={leftSection}
        style={{ cursor: "pointer" }}
      >
        {name}
      </Badge>
    </Link>
  );
}
