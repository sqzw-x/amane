import type { FilePhaseSummary, MetadataResponse, Mosaic } from "@/client/types.gen";

/**
 * 片库与详情页海报角标使用的相位.
 *
 * 自动 (或没有覆盖) 用真实相位; 手动覆盖且水印功能开启时只显示勾选的类别, 与是否真的命中无关 ——
 * 这正是逐片设置的意义. 水印关闭时回到真实相位, 免得留下关不掉的旧选择.
 * 列表筛选与文件行角标仍走真实相位.
 */
export function overlayPhase(
  item: MetadataResponse,
  watermarkEnabled: boolean,
): FilePhaseSummary | null {
  const phase = item.file_phase ?? null;
  if (!watermarkEnabled || item.watermark_auto !== false) return phase;

  const kinds = item.watermark_kinds ?? [];
  const mosaics: Mosaic[] = [];
  if (kinds.includes("cracked")) mosaics.push("cracked");
  if (kinds.includes("leaked")) mosaics.push("leaked");

  return {
    has_subtitle: kinds.includes("subtitle"),
    censored: kinds.includes("censored"),
    uncensored: kinds.includes("uncensored"),
    mosaics,
    definition: kinds.includes("definition") ? (item.watermark_definition ?? null) : null,
  };
}
