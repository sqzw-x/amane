import {
  ActionIcon,
  Box,
  FileButton,
  Group,
  Loader,
  Stack,
  Text,
  TextInput,
  UnstyledButton,
} from "@mantine/core";
import { IconPhotoOff, IconPlus, IconTrash, IconUpload } from "@tabler/icons-react";
import type { AnyFieldApi } from "@tanstack/react-form";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { HintedActionIcon } from "@/components/common/hinted-action-icon";
import { FanartLightbox } from "@/components/media/fanart-lightbox";
import { ProxyImage } from "@/components/media/proxy-image";
import { useUploadImage } from "@/hooks/use-upload-image";
import { proxyImageUrl } from "@/lib/utils";
import type { ArrayFieldProps, TextJSONSchema } from "../schema";
import { useFieldDomId } from "./dict-entry-form";
import { FieldChrome } from "./field-chrome";

/** 与后端 `media/images.py::detect_upload_image` 收的格式一致; 其余格式未必走得完裁剪 / 水印. */
const IMAGE_ACCEPT = "image/jpeg,image/png,image/webp";

/** 缩略图与 `SortableImageList` 的卡片同尺寸. */
const THUMB: { width: number; height: number } = { width: 80, height: 108 };

/**
 * 单图字段 (x-image-upload): 界面只维护生效的那一张.
 *
 * 字段值是图片 URL 列表 — 展示与整理都只取首项, 其余是刮削留下的多来源候选 (见
 * `docs/dev/data-model.md`), 因此这里上传 / 粘贴 / 删除一律整体替换成单元素列表或空列表,
 * 不提供候选排序. 上传只落 Resource, 字段仍由表单保存时提交.
 */
export function ImageField({
  name,
  label,
  description,
  form,
  variant,
}: ArrayFieldProps<TextJSONSchema>) {
  const id = useFieldDomId(name);

  return (
    <form.Field name={name}>
      {(field: AnyFieldApi) => {
        const urls = Array.isArray(field.state.value) ? (field.state.value as string[]) : [];
        return (
          <FieldChrome variant={variant} htmlFor={id} label={label} description={description}>
            <ImageBody
              id={id}
              url={urls[0] ?? null}
              onChange={(url) => field.setValue(url === null ? [] : [url])}
            />
          </FieldChrome>
        );
      }}
    </form.Field>
  );
}

interface ImageBodyProps {
  id: string;
  url: string | null;
  onChange: (url: string | null) => void;
}

function ImageBody({ id, url, onChange }: ImageBodyProps) {
  const { t } = useTranslation("common");
  const upload = useUploadImage();
  const resetRef = useRef<() => void>(null);
  const [newUrl, setNewUrl] = useState("");
  const [lightboxOpen, setLightboxOpen] = useState(false);

  const uploadFile = async (file: File | null) => {
    resetRef.current?.();
    if (file === null) return;
    try {
      onChange((await upload.mutateAsync({ body: { file } })).url);
    } catch {
      // useAppMutation 已提示失败原因; 保留原有图片.
    }
  };

  const applyUrl = () => {
    const next = newUrl.trim();
    if (!next) return;
    onChange(next);
    setNewUrl("");
  };

  return (
    <Stack gap="xs">
      <Group gap="sm" align="center" wrap="nowrap">
        {url === null ? (
          <Box
            style={{
              width: THUMB.width,
              height: THUMB.height,
              display: "grid",
              placeItems: "center",
              border: "1px dashed var(--mantine-color-default-border)",
              borderRadius: "var(--mantine-radius-sm)",
            }}
          >
            <Stack gap={2} align="center">
              <IconPhotoOff size={16} opacity={0.5} />
              <Text size="xs" c="dimmed">
                {t("imageField.empty")}
              </Text>
            </Stack>
          </Box>
        ) : (
          <UnstyledButton
            onClick={() => setLightboxOpen(true)}
            aria-label={t("imageField.preview")}
            style={{
              lineHeight: 0,
              overflow: "hidden",
              borderRadius: "var(--mantine-radius-sm)",
              border: "1px solid var(--mantine-color-default-border)",
            }}
          >
            <ProxyImage
              src={proxyImageUrl(url) ?? url}
              alt=""
              referrerPolicy="no-referrer"
              style={{
                display: "block",
                width: THUMB.width,
                height: THUMB.height,
                objectFit: "cover",
              }}
              placeholder={
                <span
                  style={{ display: "block", width: THUMB.width, height: THUMB.height }}
                  aria-hidden
                />
              }
            />
          </UnstyledButton>
        )}
        <Group gap={6} wrap="nowrap">
          <FileButton
            resetRef={resetRef}
            onChange={(file) => void uploadFile(file)}
            accept={IMAGE_ACCEPT}
          >
            {(props) => (
              <HintedActionIcon
                {...props}
                variant="light"
                size="lg"
                label={url === null ? t("imageField.upload") : t("imageField.replace")}
                disabled={upload.isPending}
              >
                {upload.isPending ? <Loader size={12} /> : <IconUpload size={16} />}
              </HintedActionIcon>
            )}
          </FileButton>
          {url !== null && (
            <HintedActionIcon
              variant="light"
              color="red"
              size="lg"
              label={t("imageField.remove")}
              onClick={() => onChange(null)}
            >
              <IconTrash size={16} />
            </HintedActionIcon>
          )}
        </Group>
      </Group>

      <Group gap={6} wrap="nowrap">
        <TextInput
          id={id}
          size="xs"
          style={{ flex: 1 }}
          value={newUrl}
          onChange={(e) => setNewUrl(e.currentTarget.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              applyUrl();
            }
          }}
          placeholder={t("imageField.urlPlaceholder")}
        />
        <ActionIcon
          variant="default"
          size="md"
          aria-label={t("imageField.addUrl")}
          disabled={!newUrl.trim()}
          onClick={applyUrl}
        >
          <IconPlus size={14} />
        </ActionIcon>
      </Group>

      {lightboxOpen && url !== null && (
        <FanartLightbox images={[url]} onClose={() => setLightboxOpen(false)} />
      )}
    </Stack>
  );
}
