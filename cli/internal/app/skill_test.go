package app

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestSkillInstallWithDir(t *testing.T) {
	srv := httptest.NewServer(jsonHandler(t, http.StatusOK, catalogBody, nil))
	defer srv.Close()

	h := newHarness(t, srv.URL)
	parent := filepath.Join(h.dir, "skill")
	target := filepath.Join(parent, "amane")
	if code := h.run("skill", "--profile", "default", "install", "--dir", parent); code != ExitOK {
		t.Fatalf("退出码为 %d, stderr: %s", code, h.errOut())
	}
	for _, name := range []string{"SKILL.md", "version"} {
		if _, err := os.Stat(filepath.Join(target, name)); err != nil {
			t.Fatalf("缺少 %s: %v", name, err)
		}
	}
	if !strings.Contains(h.out(), target) || !strings.Contains(h.out(), "0.22.1") {
		t.Fatalf("输出为 %q", h.out())
	}
	if strings.Contains(h.out()+h.errOut(), testToken) {
		t.Fatal("token 不应出现在输出中")
	}

	main, err := os.ReadFile(filepath.Join(target, "SKILL.md"))
	if err != nil {
		t.Fatalf("读取失败: %v", err)
	}
	if !strings.Contains(string(main), "amane catalog") {
		t.Fatalf("SKILL.md 未提示取工具索引: %s", main)
	}
}

func TestSkillInstallKeepsNewerInstalled(t *testing.T) {
	srv := httptest.NewServer(jsonHandler(t, http.StatusOK, catalogBody, nil))
	defer srv.Close()

	h := newHarness(t, srv.URL)
	parent := filepath.Join(h.home, "skills")
	dir := filepath.Join(parent, "amane")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatalf("准备目录失败: %v", err)
	}
	// 模拟已从更新的实例装过 Skill: 不能被这次 0.22.1 覆盖.
	if err := os.WriteFile(filepath.Join(dir, "version"), []byte("0.30.0\n"), 0o644); err != nil {
		t.Fatalf("写入版本文件失败: %v", err)
	}
	if err := os.WriteFile(filepath.Join(dir, "SKILL.md"), []byte("新版本内容\n"), 0o644); err != nil {
		t.Fatalf("写入 SKILL.md 失败: %v", err)
	}

	if code := h.run("skill", "--profile", "default", "install", "--dir", parent); code != ExitOK {
		t.Fatalf("退出码为 %d, stderr: %s", code, h.errOut())
	}
	version, err := os.ReadFile(filepath.Join(dir, "version"))
	if err != nil {
		t.Fatalf("读取版本失败: %v", err)
	}
	if strings.TrimSpace(string(version)) != "0.30.0" {
		t.Fatalf("版本被降级为 %q", strings.TrimSpace(string(version)))
	}
	body, err := os.ReadFile(filepath.Join(dir, "SKILL.md"))
	if err != nil {
		t.Fatalf("读取 SKILL.md 失败: %v", err)
	}
	if !strings.Contains(string(body), "新版本内容") {
		t.Fatal("SKILL.md 被旧实例覆盖")
	}
	if !strings.Contains(h.out(), "保留不动") {
		t.Fatalf("输出为 %q", h.out())
	}
}

func TestSkillInstallUpgradesOlderInstalled(t *testing.T) {
	srv := httptest.NewServer(jsonHandler(t, http.StatusOK, catalogBody, nil))
	defer srv.Close()

	h := newHarness(t, srv.URL)
	parent := filepath.Join(h.home, "skills")
	dir := filepath.Join(parent, "amane")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatalf("准备目录失败: %v", err)
	}
	if err := os.WriteFile(filepath.Join(dir, "version"), []byte("0.21.0\n"), 0o644); err != nil {
		t.Fatalf("写入版本文件失败: %v", err)
	}

	if code := h.run("skill", "--profile", "default", "install", "--dir", parent); code != ExitOK {
		t.Fatalf("退出码为 %d, stderr: %s", code, h.errOut())
	}
	version, err := os.ReadFile(filepath.Join(dir, "version"))
	if err != nil {
		t.Fatalf("读取版本失败: %v", err)
	}
	if strings.TrimSpace(string(version)) != "0.22.1" {
		t.Fatalf("未升级, 版本为 %q", strings.TrimSpace(string(version)))
	}
}

func TestSkillInstallDefaultDir(t *testing.T) {
	srv := httptest.NewServer(jsonHandler(t, http.StatusOK, catalogBody, nil))
	defer srv.Close()

	h := newHarness(t, srv.URL)
	if code := h.run("skill", "--profile", "default", "install"); code != ExitOK {
		t.Fatalf("退出码为 %d, stderr: %s", code, h.errOut())
	}
	// 父目录不必预先存在: 默认位置是确定的, 不是探测出来的.
	want := filepath.Join(h.home, ".agents", "skills", "amane", "SKILL.md")
	if _, err := os.Stat(want); err != nil {
		t.Fatalf("未写入默认位置 %s: %v", want, err)
	}
}

func TestSkillInstallDirIsParent(t *testing.T) {
	srv := httptest.NewServer(jsonHandler(t, http.StatusOK, catalogBody, nil))
	defer srv.Close()

	h := newHarness(t, srv.URL)
	parent := filepath.Join(h.home, "custom-skills")
	if code := h.run("skill", "--profile", "default", "install", "--dir", parent); code != ExitOK {
		t.Fatalf("退出码为 %d, stderr: %s", code, h.errOut())
	}
	// --dir 给的是父目录, 与默认位置同构: 文件落在 amane 子目录下, 不污染父目录.
	if _, err := os.Stat(filepath.Join(parent, "amane", "SKILL.md")); err != nil {
		t.Fatalf("未写入 %s: %v", filepath.Join(parent, "amane"), err)
	}
	if _, err := os.Stat(filepath.Join(parent, "SKILL.md")); err == nil {
		t.Fatal("SKILL.md 不应直接写在父目录下")
	}
}

func TestSkillInstallDirAlreadyTarget(t *testing.T) {
	srv := httptest.NewServer(jsonHandler(t, http.StatusOK, catalogBody, nil))
	defer srv.Close()

	h := newHarness(t, srv.URL)
	target := filepath.Join(h.home, "skills", "amane")
	if code := h.run("skill", "--profile", "default", "install", "--dir", target); code != ExitOK {
		t.Fatalf("退出码为 %d, stderr: %s", code, h.errOut())
	}
	if _, err := os.Stat(filepath.Join(target, "SKILL.md")); err != nil {
		t.Fatalf("未写入 %s: %v", target, err)
	}
	if _, err := os.Stat(filepath.Join(target, "amane")); err == nil {
		t.Fatal("重复指定 amane 时不应多套一层")
	}
}

func TestSkillShowWritesNothing(t *testing.T) {
	srv := httptest.NewServer(jsonHandler(t, http.StatusOK, catalogBody, nil))
	defer srv.Close()

	h := newHarness(t, srv.URL)
	if code := h.run("skill", "--profile", "default", "show"); code != ExitOK {
		t.Fatalf("退出码为 %d, stderr: %s", code, h.errOut())
	}
	for _, want := range []string{"SKILL.md", "version", "审批礼仪", "amane catalog"} {
		if !strings.Contains(h.out(), want) {
			t.Errorf("输出缺少 %q", want)
		}
	}
	entries, err := os.ReadDir(h.home)
	if err != nil {
		t.Fatalf("读取主目录失败: %v", err)
	}
	if len(entries) != 0 {
		t.Fatalf("skill show 不应写文件: %v", entries)
	}
}

func TestSkillUnknownSubcommand(t *testing.T) {
	h := newHarness(t, "http://127.0.0.1:1")
	if code := h.run("skill", "--profile", "default", "frob"); code != ExitUsage {
		t.Fatalf("退出码为 %d, 期望 %d", code, ExitUsage)
	}
}
