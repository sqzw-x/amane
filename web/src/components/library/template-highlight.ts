/** 与 `organize/template.py` Parser 同一套词法. 未闭合、未知占位符名、非法映射 key 标为 error. */

/** name → 可映射规范 key; 空数组表示该名合法但不校验映射 key. 空表则只标语法错误. */
export type TemplateCatalog = ReadonlyMap<string, readonly string[]>;

export function templateCatalogFromPlaceholders(
  placeholders: readonly { name: string; map_keys?: readonly string[] }[],
): TemplateCatalog {
  return new Map(placeholders.map((item) => [item.name, item.map_keys ?? []]));
}

export const TEMPLATE_TOKEN_KINDS = [
  "text",
  "group",
  "brace",
  "name",
  "punct",
  "mapKey",
  "mapValue",
  "error",
] as const;

export type TemplateTokenKind = (typeof TEMPLATE_TOKEN_KINDS)[number];

export interface TemplateToken {
  kind: TemplateTokenKind;
  text: string;
}

function nameKind(raw: string, catalog: TemplateCatalog | undefined): TemplateTokenKind {
  const name = raw.trim();
  if (name === "") {
    return "error";
  }
  if (catalog == null || catalog.size === 0) {
    return "name";
  }
  return catalog.has(name) ? "name" : "error";
}

function mapKeyKind(
  raw: string,
  placeholder: string,
  catalog: TemplateCatalog | undefined,
): TemplateTokenKind {
  const key = raw.trim();
  const allowed = catalog?.get(placeholder);
  if (allowed == null || allowed.length === 0) {
    return "mapKey";
  }
  if (key === "") {
    return "mapKey";
  }
  return allowed.includes(key) ? "mapKey" : "error";
}

function tokenizeMapping(
  spec: string,
  tokens: TemplateToken[],
  placeholder: string,
  catalog: TemplateCatalog | undefined,
): void {
  if (spec.length === 0) {
    tokens.push({ kind: "error", text: "" });
    return;
  }
  let first = true;
  for (const item of spec.split(",")) {
    if (!first) {
      tokens.push({ kind: "punct", text: "," });
    }
    first = false;
    const eq = item.indexOf("=");
    if (eq < 0) {
      tokens.push({ kind: "error", text: item });
      continue;
    }
    const key = item.slice(0, eq);
    tokens.push({ kind: mapKeyKind(key, placeholder, catalog), text: key });
    tokens.push({ kind: "punct", text: "=" });
    tokens.push({ kind: "mapValue", text: item.slice(eq + 1) });
  }
}

function tokenizePlaceholder(
  body: string,
  tokens: TemplateToken[],
  catalog: TemplateCatalog | undefined,
): void {
  if (body.includes("{")) {
    tokens.push({ kind: "error", text: body });
    return;
  }
  const pipe = body.indexOf("|");
  if (pipe < 0) {
    tokens.push({ kind: nameKind(body, catalog), text: body });
    return;
  }
  const name = body.slice(0, pipe);
  tokens.push({ kind: nameKind(name, catalog), text: name });
  tokens.push({ kind: "punct", text: "|" });
  tokenizeMapping(body.slice(pipe + 1), tokens, name.trim(), catalog);
}

export function tokenizeTemplate(src: string, catalog?: TemplateCatalog): TemplateToken[] {
  const tokens: TemplateToken[] = [];
  let i = 0;
  let buf = "";

  const flush = (): void => {
    if (buf.length > 0) {
      tokens.push({ kind: "text", text: buf });
      buf = "";
    }
  };

  while (i < src.length) {
    if (src.startsWith("[[", i) || src.startsWith("]]", i)) {
      flush();
      tokens.push({ kind: "group", text: src.slice(i, i + 2) });
      i += 2;
      continue;
    }
    if (src[i] === "[" || src[i] === "]") {
      flush();
      tokens.push({ kind: "group", text: src[i] });
      i += 1;
      continue;
    }
    if (src[i] === "{") {
      flush();
      const close = src.indexOf("}", i + 1);
      if (close < 0) {
        tokens.push({ kind: "error", text: src.slice(i) });
        return tokens;
      }
      tokens.push({ kind: "brace", text: "{" });
      tokenizePlaceholder(src.slice(i + 1, close), tokens, catalog);
      tokens.push({ kind: "brace", text: "}" });
      i = close + 1;
      continue;
    }
    buf += src[i];
    i += 1;
  }
  flush();
  return tokens;
}
