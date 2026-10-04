import { notifications } from "@mantine/notifications";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { cropActorAvatarMutation } from "@/client/@tanstack/react-query.gen";
import { ImageCropDialog } from "@/components/media/image-crop-dialog";
import { extractErrorMessage } from "@/lib/api-error";

/** 演员卡片 AspectRatio 为 3:4; 默认锁定该比例. */
const AVATAR_RATIO = 0.75;

export interface ActorAvatarCropDialogProps {
  opened: boolean;
  onClose: () => void;
  actorId: number;
  imageUrl: string;
  onSuccess?: () => void;
}

/** 演员头像裁切: 基准 image_urls[0] 当前本地文件像素, 结果前插为主图并保留原图. */
export function ActorAvatarCropDialog({
  opened,
  onClose,
  actorId,
  imageUrl,
  onSuccess,
}: ActorAvatarCropDialogProps) {
  const { t } = useTranslation(["metadata", "common"]);

  const mutation = useMutation({
    ...cropActorAvatarMutation(),
    onSuccess: () => {
      notifications.show({
        message: t("actors.cropAvatar.success"),
        color: "blue",
      });
      onSuccess?.();
      onClose();
    },
    onError: (err) =>
      notifications.show({
        message: extractErrorMessage(err, t("common:toast.operationFailed")),
        color: "red",
      }),
  });

  return (
    <ImageCropDialog
      opened={opened}
      onClose={onClose}
      imageUrl={imageUrl}
      defaultAspect={AVATAR_RATIO}
      initialAnchor="center"
      submitting={mutation.isPending}
      onSubmit={(box) => mutation.mutate({ path: { actor_id: actorId }, body: box })}
      labels={{
        title: t("actors.cropAvatar.title"),
        confirm: t("detail.cropPoster.confirm"),
        needSelection: t("detail.cropPoster.needSelection"),
        operationFailed: t("common:toast.operationFailed"),
        aspect: t("detail.cropPoster.aspect"),
        lockAspect: t("detail.cropPoster.lockAspect"),
        left: t("detail.cropPoster.left"),
        top: t("detail.cropPoster.top"),
        width: t("detail.cropPoster.width"),
        height: t("detail.cropPoster.height"),
        cancel: t("common:actions.cancel"),
      }}
    />
  );
}
