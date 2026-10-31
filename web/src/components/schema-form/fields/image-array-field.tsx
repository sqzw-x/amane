import { ActionIcon, Button, FileButton, Group, Stack, Text, TextInput } from "@mantine/core";
import { IconPlus, IconUpload } from "@tabler/icons-react";
import type { AnyFieldApi } from "@tanstack/react-form";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { FanartLightbox } from "@/components/media/fanart-lightbox";
import { SortableImageList } from "@/components/media/sortable-image-list";
import { useNarrowViewport } from "@/hooks/use-narrow-viewport";
import { useUploadImage } from "@/hooks/use-upload-image";
import type { ArrayFieldProps, TextJSONSchema } from "../schema";
import { useFieldDomId } from "./dict-entry-form";
import { FieldChrome } from "./field-chrome";

/** 与后端 `media/images.py::detect_upload_image` 收的格式一致; 其余格式未必走得完裁剪 / 水印. */
const IMAGE_ACCEPT = "image/jpeg,image/png,image/webp";

/**
 * 图片 URL 列表 (x-image-upload): 首项即主图. 本地文件上传后写进列表的值是内部资源 URL
 * (`/api/resources/{hash}`), 与粘贴进来的外链同处一列; 上传只落 Resource, 字段本身仍由表单保存时提交.
 */
export function ImageArrayField({
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
        const value = Array.isArray(field.state.value) ? (field.state.value as string[]) : [];
        // 上传是异步的: 追加按当前值算, 期间删掉的图不会被这次上传重新带回.
        const append = (urls: string[]) => {
          field.setValue((prev: unknown) => {
            const next = Array.isArray(prev) ? (prev as string[]) : [];
            const merged = [...next];
            for (const url of urls) {
              if (!merged.includes(url)) merged.push(url);
            }
            return merged;
          });
        };
        return (
          <FieldChrome variant={variant} htmlFor={id} label={label} description={description}>
            <ImageArrayBody id={id} value={value} onChange={field.handleChange} onAppend={append} />
          </FieldChrome>
        );
      }}
    </form.Field>
  );
}

interface ImageArrayBodyProps {
  id: string;
  value: string[];
  onChange: (value: string[]) => void;
  onAppend: (urls: string[]) => void;
}

function ImageArrayBody({ id, value, onChange, onAppend }: ImageArrayBodyProps) {
  const { t } = useTranslation("common");
  // 触屏设备无法执行 HTML5 拖拽, 窄屏改由左右移动按钮调整顺序.
  const narrow = useNarrowViewport();
  const upload = useUploadImage();
  const resetRef = useRef<() => void>(null);
  const [newUrl, setNewUrl] = useState("");
  const [lightboxIndex, setLightboxIndex] = useState(0);
  const [lightboxOpen, setLightboxOpen] = useState(false);

  const uploadFiles = async (files: File[]) => {
    resetRef.current?.();
    const urls: string[] = [];
    for (const file of files) {
      try {
        urls.push((await upload.mutateAsync({ body: { file } })).url);
      } catch {
        // useAppMutation 已提示失败原因; 单个文件失败不影响其余文件.
      }
    }
    if (urls.length > 0) onAppend(urls);
  };

  const addUrl = () => {
    const url = newUrl.trim();
    if (!url) return;
    onAppend([url]);
    setNewUrl("");
  };

  return (
    <Stack gap="xs">
      {value.length === 0 ? (
        <Text size="xs" c="dimmed">
          {t("imageField.empty")}
        </Text>
      ) : (
        <SortableImageList
          urls={value}
          onChange={onChange}
          onOpen={(index) => {
            setLightboxIndex(index);
            setLightboxOpen(true);
          }}
          primaryLabel={t("imageField.primary")}
          removeLabel={t("imageField.remove")}
          reorderLabel={t("imageField.reorder")}
          onMove={narrow ? onChange : undefined}
        />
      )}
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
              addUrl();
            }
          }}
          placeholder={t("imageField.urlPlaceholder")}
        />
        <ActionIcon
          variant="default"
          size="md"
          aria-label={t("imageField.addUrl")}
          disabled={!newUrl.trim()}
          onClick={addUrl}
        >
          <IconPlus size={14} />
        </ActionIcon>
        <FileButton
          resetRef={resetRef}
          onChange={(files) => void uploadFiles(Array.isArray(files) ? files : [])}
          accept={IMAGE_ACCEPT}
          multiple
        >
          {(props) => (
            <Button
              {...props}
              size="xs"
              variant="light"
              leftSection={<IconUpload size={14} />}
              loading={upload.isPending}
            >
              {t("imageField.upload")}
            </Button>
          )}
        </FileButton>
      </Group>

      {lightboxOpen && value.length > 0 && (
        <FanartLightbox
          images={value}
          initialIndex={lightboxIndex}
          onClose={() => setLightboxOpen(false)}
        />
      )}
    </Stack>
  );
}
