package app

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"sort"
	"strings"

	"github.com/sqzw-x/amane/cli/internal/api"
)

// cmdCatalog 打印工具索引; 指定工具名时打印该工具的完整入参.
//
// 默认输出紧凑文本而不是 JSON: 索引要整个读进上下文, 逐字段的 JSON 结构
// 比一行签名贵一倍多. 需要原文时用 --json.
func (r *runner) cmdCatalog(ctx context.Context) error {
	if name := r.p.arg(0); name != "" {
		return r.cmdToolDetail(ctx, name)
	}
	client, err := r.client()
	if err != nil {
		return err
	}
	resp, err := client.Get(ctx, "/api/tools")
	if err != nil {
		return r.connErr(client.BaseURL, err)
	}
	if err := r.checkStatus(resp); err != nil {
		return err
	}
	if r.p.jsonOut {
		return r.writeBody(resp.Body)
	}
	var cat api.Catalog
	if err := json.Unmarshal(resp.Body, &cat); err != nil {
		return r.writeBody(resp.Body)
	}
	return r.writeRaw([]byte(renderIndex(&cat)))
}

// cmdToolDetail 打印单个工具的完整入参.
func (r *runner) cmdToolDetail(ctx context.Context, name string) error {
	if !api.ValidToolName(name) {
		return usagef("工具名 %q 不合法", name)
	}
	client, err := r.client()
	if err != nil {
		return err
	}
	resp, err := client.Get(ctx, "/api/tools/"+name)
	if err != nil {
		return r.connErr(client.BaseURL, err)
	}
	if err := r.checkStatus(resp); err != nil {
		return err
	}
	if r.p.jsonOut {
		return r.writeBody(resp.Body)
	}
	var detail api.ToolDetail
	if err := json.Unmarshal(resp.Body, &detail); err != nil {
		return r.writeBody(resp.Body)
	}
	return r.writeRaw([]byte(renderDetail(&detail, r.p.pretty)))
}

// renderIndex 把索引排成按域分组的一行制文本.
func renderIndex(cat *api.Catalog) string {
	var b strings.Builder
	fmt.Fprintf(&b, "实例版本 %s, %d 个工具.\n", cat.Version, len(cat.Tools))

	groups := make([]string, 0, 8)
	byGroup := map[string][]api.ToolSummary{}
	for _, tool := range cat.Tools {
		group := tool.Group
		if group == "" {
			group = "未分组"
		}
		if _, ok := byGroup[group]; !ok {
			groups = append(groups, group)
		}
		byGroup[group] = append(byGroup[group], tool)
	}
	sort.Strings(groups)

	for _, group := range groups {
		fmt.Fprintf(&b, "\n%s\n", group)
		for _, tool := range byGroup[group] {
			fmt.Fprintf(&b, "  %s(%s)", tool.Name, tool.Signature)
			if tool.Summary != "" {
				fmt.Fprintf(&b, " — %s", tool.Summary)
			}
			b.WriteString("\n")
		}
	}
	b.WriteString("\n完整入参: amane catalog <tool>\n")
	return b.String()
}

// renderDetail 打印单个工具: 完整描述在前, 入参 Schema 在后.
func renderDetail(detail *api.ToolDetail, pretty bool) string {
	var b strings.Builder
	fmt.Fprintf(&b, "%s (%s)\n\n%s\n", detail.Name, detail.Group, strings.TrimSpace(detail.Description))
	if len(detail.Params) == 0 || string(detail.Params) == "null" {
		return b.String()
	}
	b.WriteString("\n入参:\n")
	var buf bytes.Buffer
	if err := json.Indent(&buf, detail.Params, "", "  "); err == nil {
		b.Write(buf.Bytes())
	} else {
		b.Write(detail.Params)
	}
	b.WriteString("\n")
	return b.String()
}

// cmdSchema 打印库结构摘要.
func (r *runner) cmdSchema(ctx context.Context) error {
	client, err := r.client()
	if err != nil {
		return err
	}
	resp, err := client.Get(ctx, "/api/db-schema")
	if err != nil {
		return r.connErr(client.BaseURL, err)
	}
	if err := r.checkStatus(resp); err != nil {
		return err
	}
	if r.p.jsonOut {
		return r.writeBody(resp.Body)
	}
	var body struct {
		SchemaDoc string `json:"schema_doc"`
	}
	if err := json.Unmarshal(resp.Body, &body); err != nil {
		return r.writeBody(resp.Body)
	}
	return r.writeRaw([]byte(body.SchemaDoc))
}

// cmdCall 派发一次工具调用.
func (r *runner) cmdCall(ctx context.Context) error {
	tool, err := r.requireArg(0, "tool")
	if err != nil {
		return err
	}
	if !api.ValidToolName(tool) {
		return usagef("工具名 %q 不合法", tool)
	}
	args, err := r.readArgs()
	if err != nil {
		return err
	}
	client, err := r.client()
	if err != nil {
		return err
	}
	resp, err := client.CallTool(ctx, tool, args)
	if err != nil {
		return r.connErr(client.BaseURL, err)
	}
	if err := r.checkStatus(resp); err != nil {
		return err
	}
	return r.writeBody(resp.Body)
}

// readArgs 读取工具入参: @文件、- (标准输入)、JSON 字面量, 或无参数时的管道输入.
func (r *runner) readArgs() (json.RawMessage, error) {
	source := r.arg(1)

	var data []byte
	switch {
	case source == "":
		if r.env.StdinIsTerminal {
			return nil, usagef("缺少入参: 用 @文件、- 从标准输入读取, 或直接给出 JSON 字面量")
		}
		read, err := io.ReadAll(r.env.Stdin)
		if err != nil {
			return nil, usagef("读取标准输入失败: %v", err)
		}
		data = read
	case source == "-":
		read, err := io.ReadAll(r.env.Stdin)
		if err != nil {
			return nil, usagef("读取标准输入失败: %v", err)
		}
		data = read
	case strings.HasPrefix(source, "@"):
		path := strings.TrimPrefix(source, "@")
		if path == "" {
			return nil, usagef("@ 后需要文件路径")
		}
		read, err := os.ReadFile(path)
		if err != nil {
			return nil, usagef("读取入参文件失败: %v", err)
		}
		data = read
	default:
		data = []byte(source)
	}

	trimmed := bytes.TrimSpace(data)
	if len(trimmed) == 0 {
		return json.RawMessage("{}"), nil
	}
	var obj map[string]json.RawMessage
	if err := json.Unmarshal(trimmed, &obj); err != nil {
		return nil, usagef("入参须为 JSON 对象: %v", err)
	}
	if obj == nil {
		return nil, usagef("入参须为 JSON 对象, 收到 null")
	}
	return json.RawMessage(trimmed), nil
}
