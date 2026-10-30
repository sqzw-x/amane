/**
 * 扫帚图标. Tabler 图标集没有 broom, 路径取自 lucide-static v1.64.0 的 broom (ISC).
 * 尺寸、线宽与端点与 @tabler/icons 一致, 可直接替换 Tabler 图标使用.
 */
export function BroomIcon({ size = 16 }: { size?: number }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M13.5 10.5 22 2" />
      <path d="M14.734 13.841a2 2 0 00-.314-2.42L12.58 9.58a2 2 0 00-2.421-.314l-7.657 4.461A1 1 0 002.3 15.3l6.403 6.403a1 1 0 001.571-.204z" />
      <path d="m5 18 2-2" />
      <path d="m7.699 10.7 5.602 5.601" />
    </svg>
  );
}
