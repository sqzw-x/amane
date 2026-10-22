// Command fmtcheck 检查 Go 源码是否已按 gofmt 格式化.
//
// gofmt 只有 -l 能列出未格式化的文件, 结果不体现在退出码上; 这里把列表转成退出码,
// recipe 因此只剩一行, 在 sh 与 pwsh 下写法一致.
package main

import (
	"bytes"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run() error {
	gofmt, err := gofmtPath()
	if err != nil {
		return err
	}

	var stdout, stderr bytes.Buffer
	cmd := exec.Command(gofmt, "-l", ".")
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("执行 %s 失败: %v\n%s", gofmt, err, stderr.String())
	}

	files := strings.Fields(stdout.String())
	if len(files) == 0 {
		return nil
	}
	return fmt.Errorf("以下文件未格式化:\n  %s", strings.Join(files, "\n  "))
}

// gofmtPath 先找 PATH, 再退回 GOROOT/bin: go run 的场景下 PATH 未必包含 GOROOT.
func gofmtPath() (string, error) {
	if path, err := exec.LookPath("gofmt"); err == nil {
		return path, nil
	}
	if root := runtime.GOROOT(); root != "" {
		path := filepath.Join(root, "bin", "gofmt")
		if _, err := os.Stat(path); err == nil {
			return path, nil
		}
	}
	return "", fmt.Errorf("找不到 gofmt: 它应当随 Go 工具链一起安装")
}
