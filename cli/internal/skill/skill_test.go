package skill

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/sqzw-x/amane/cli/internal/api"
)

func testCatalog() *api.Catalog {
	return &api.Catalog{
		Version: "0.22.1",
		Paths: api.CatalogPaths{
			DataDir:    "/data",
			LogDir:     "/data/logs",
			DBPath:     "/data/amane.db",
			ConfigPath: "/data/config.toml",
			Env:        []string{"AMANE_DATA_DIR", "AMANE_TOKEN"},
		},
		Tools: []api.ToolSummary{
			{Name: "update_metadata", Group: "metadata-ops", Summary: "修改番号条目", Signature: "number: string"},
			{Name: "sql_explore", Group: "sql", Summary: "执行只读 SQL", Signature: "sql: string"},
		},
	}
}

func TestRenderIncludesPaths(t *testing.T) {
	files := Render(testCatalog(), "0.1.0")
	for _, name := range []string{MainFile, VersionFile} {
		if _, ok := files[name]; !ok {
			t.Fatalf("缺少文件 %s", name)
		}
	}
	if got := strings.TrimSpace(string(files[VersionFile])); got != "0.22.1" {
		t.Fatalf("version 文件为 %q", got)
	}

	main := string(files[MainFile])
	for _, want := range []string{"审批礼仪", "amane doctor", "amane wait", "amane catalog", "amane schema", "0.22.1", "/data/logs", "/data/amane.db", "/data/config.toml"} {
		if !strings.Contains(main, want) {
			t.Errorf("SKILL.md 缺少 %q", want)
		}
	}
}

// 工具清单不进 Skill: 它会随实例变化, 且一次会话用不到全部工具.
func TestRenderOmitsToolList(t *testing.T) {
	main := string(Render(testCatalog(), "0.1.0")[MainFile])
	for _, unwanted := range []string{"update_metadata", "sql_explore", "metadata-ops", "number: string"} {
		if strings.Contains(main, unwanted) {
			t.Errorf("SKILL.md 不应包含工具清单内容 %q", unwanted)
		}
	}
}

func TestRenderStable(t *testing.T) {
	first := Render(testCatalog(), "0.1.0")
	second := Render(testCatalog(), "0.1.0")
	for name := range first {
		if string(first[name]) != string(second[name]) {
			t.Fatalf("%s 两次生成结果不一致", name)
		}
	}
}

func TestRenderEmptyCatalog(t *testing.T) {
	files := Render(&api.Catalog{}, "dev")
	if got := strings.TrimSpace(string(files[VersionFile])); got != "未知" {
		t.Fatalf("version 文件为 %q", got)
	}
	if len(files) != 2 {
		t.Fatalf("文件数为 %d, 期望 2", len(files))
	}
}

func TestInstallWritesAllFiles(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "nested", "amane")
	files := Render(testCatalog(), "0.22.1")

	if err := Install(dir, files); err != nil {
		t.Fatalf("未预期的错误: %v", err)
	}
	for name := range files {
		if _, err := os.Stat(filepath.Join(dir, name)); err != nil {
			t.Fatalf("缺少 %s: %v", name, err)
		}
	}
}

func TestDefaultDir(t *testing.T) {
	home := t.TempDir()
	want := filepath.Join(home, ".agents", "skills", "amane")
	if got := DefaultDir(home); got != want {
		t.Fatalf("得到 %q, 期望 %q", got, want)
	}
}

func TestResolveTarget(t *testing.T) {
	cases := []struct {
		name string
		in   string
		want string
	}{
		{name: "父目录补一层", in: "/skills", want: filepath.Join("/skills", "amane")},
		{name: "父目录带尾斜杠", in: "/skills/", want: filepath.Join("/skills", "amane")},
		{name: "已指向 amane 不重复", in: filepath.Join("/skills", "amane"), want: filepath.Join("/skills", "amane")},
		{name: "已指向 amane 带尾斜杠", in: filepath.Join("/skills", "amane") + "/", want: filepath.Join("/skills", "amane")},
		{name: "相对路径", in: "skills", want: filepath.Join("skills", "amane")},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := ResolveTarget(c.in); got != c.want {
				t.Fatalf("得到 %q, 期望 %q", got, c.want)
			}
		})
	}
}
