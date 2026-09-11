import DOMPurify from "dompurify";

let hooksInstalled = false;

function ensureHooks(): void {
  if (hooksInstalled) {
    return;
  }
  hooksInstalled = true;
  DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    if (node instanceof HTMLAnchorElement) {
      node.setAttribute("target", "_blank");
      node.setAttribute("rel", "noopener noreferrer");
    }
  });
}

export function sanitizeFeedHtml(html: string): string {
  ensureHooks();
  return DOMPurify.sanitize(html, {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ["script", "iframe", "object", "embed", "form", "input", "button"],
  });
}

/** 块级标签与 br 收成空格, 避免多行正文在缩略里粘连. */
export function feedHtmlPlainText(html: string): string {
  const clean = sanitizeFeedHtml(html);
  const doc = new DOMParser().parseFromString(clean, "text/html");
  for (const node of doc.body.querySelectorAll("br")) {
    node.replaceWith(" ");
  }
  for (const node of doc.body.querySelectorAll(
    "p, div, li, h1, h2, h3, h4, h5, h6, tr, blockquote, pre",
  )) {
    node.append(" ");
  }
  return (doc.body.textContent ?? "").replace(/\s+/g, " ").trim();
}
