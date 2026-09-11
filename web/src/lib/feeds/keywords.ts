/** 表单与批量对话框把关键词显示为多行文本; API 仍是字符串列表. */

export function parseIgnoreKeywordsText(raw: string): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const line of raw.split(/\r?\n/)) {
    const keyword = line.trim();
    if (keyword === "") {
      continue;
    }
    const key = keyword.toLocaleLowerCase();
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    result.push(keyword);
  }
  return result;
}

export function formatIgnoreKeywordsText(keywords: readonly string[]): string {
  return keywords.join("\n");
}
