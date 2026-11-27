import assert from "node:assert/strict";
import { test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createInstance } from "i18next";
import { I18nextProvider } from "react-i18next";
import common from "@/i18n/locales/zh-CN/common.json";
import metadata from "@/i18n/locales/zh-CN/metadata.json";
import { parseFilterFormula, serializeFilterFormula } from "@/lib/filter-expression";
import { AdvancedFilterEditor } from "./advanced-filter-editor";

async function render(value: unknown): Promise<string> {
  const i18n = createInstance();
  await i18n.init({
    lng: "zh-CN",
    resources: { "zh-CN": { common, metadata } },
    defaultNS: "metadata",
  });
  const queryClient = new QueryClient();
  try {
    return renderToStaticMarkup(
      createElement(
        MantineProvider,
        { env: "test" },
        createElement(
          I18nextProvider,
          { i18n },
          createElement(
            QueryClientProvider,
            { client: queryClient },
            createElement(AdvancedFilterEditor, {
              entity: "metadata",
              value: parseFilterFormula(value, "metadata"),
              onApply: () => {},
            }),
          ),
        ),
      ),
    );
  } finally {
    queryClient.clear();
  }
}

test("画布直接按条件树渲染, 组带括号与否定标记", async () => {
  const html = await render({
    version: 1,
    expression: {
      type: "group",
      op: "or",
      children: [
        { type: "rule", field: "title", operator: "contains", value: "海边" },
        {
          type: "group",
          op: "and",
          negate: true,
          children: [
            { type: "rule", field: "has_files", operator: "eq", value: true },
            { type: "rule", field: "vr", operator: "eq", value: false },
          ],
        },
      ],
    },
  });
  assert.ok(html.includes("海边"));
  assert.ok(html.includes("关联文件"));
  assert.ok(html.includes('data-formula-index="0"'));
  assert.ok(html.includes("("));
  assert.ok(html.includes(")"));
  // 条件以真实取值展示, 不再有字母编号
  assert.equal(/<b[^>]*>A<\/b>/.test(html), false);
  assert.equal(html.includes('role="combobox"'), false);
});

test("仅手动分组显示括号, 隐式分组显示在原连接符位置", async () => {
  for (const implicit of [false, true]) {
    const source = {
      version: 1,
      expression: {
        type: "group",
        op: "or",
        children: [
          { type: "rule", field: "has_files", operator: "eq", value: true },
          {
            type: "group",
            op: "and",
            ...(implicit ? { implicit: true } : {}),
            children: [
              { type: "rule", field: "vr", operator: "eq", value: true },
              { type: "rule", field: "has_subtitle", operator: "eq", value: true },
            ],
          },
        ],
      },
    };
    const html = await render(
      serializeFilterFormula(parseFilterFormula(source, "metadata"), "metadata"),
    );
    assert.deepEqual(
      [...html.matchAll(/data-operator="(and|or)"/g)].map((match) => match[1]),
      ["or", "and"],
    );
    assert.equal(
      html.includes(`aria-label="${metadata.advancedFilter.formula.selectGroup}"`),
      !implicit,
    );
  }
});

test("画布显示根组的否定", async () => {
  const html = await render({
    version: 1,
    expression: {
      type: "group",
      op: "and",
      negate: true,
      children: [{ type: "rule", field: "vr", operator: "eq", value: true }],
    },
  });
  assert.match(html, /data-operator="not"/);
});

test("空条件树只保留新增与应用入口", async () => {
  const html = await render(null);
  assert.ok(html.includes("添加条件"));
  assert.ok(html.includes("应用筛选"));
  assert.equal(html.includes("data-formula-index"), false);
});
