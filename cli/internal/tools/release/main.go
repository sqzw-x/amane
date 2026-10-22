// Command release 交叉编译 CLI 并生成 SHA256SUMS.
//
// 用 Go 而不是写进 recipe: 交叉编译要逐平台设置环境变量, 校验和也要跨平台生成,
// 这两件事用 shell 写就得为 sh 与 pwsh 各维护一份.
package main

import (
	"crypto/sha256"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
)

// targets 是发布产物覆盖的平台. 没有产物的组合不要在这里补, 发布页与引导都按实际存在处理.
var targets = []struct{ os, arch string }{
	{"linux", "amd64"},
	{"linux", "arm64"},
	{"darwin", "amd64"},
	{"darwin", "arm64"},
	{"windows", "amd64"},
}

func main() {
	version := flag.String("version", "dev", "注入二进制的版本号")
	out := flag.String("out", "dist", "产物目录")
	clean := flag.Bool("clean", false, "只删除产物目录")
	flag.Parse()

	if err := run(*version, *out, *clean); err != nil {
		fmt.Fprintln(os.Stderr, "release:", err)
		os.Exit(1)
	}
}

func run(version, out string, clean bool) error {
	if err := os.RemoveAll(out); err != nil {
		return err
	}
	if clean {
		return nil
	}
	if err := os.MkdirAll(out, 0o755); err != nil {
		return err
	}

	sumsPath := filepath.Join(out, "SHA256SUMS")
	sums, err := os.Create(sumsPath)
	if err != nil {
		return err
	}
	defer sums.Close()

	for _, target := range targets {
		name := "amane-" + target.os + "-" + target.arch
		if target.os == "windows" {
			name += ".exe"
		}
		path := filepath.Join(out, name)
		fmt.Println("构建", path)

		build := exec.Command("go", "build", "-trimpath", "-ldflags", "-X main.version="+version, "-o", path, "./cmd/amane")
		build.Env = append(os.Environ(), "GOOS="+target.os, "GOARCH="+target.arch, "CGO_ENABLED=0")
		build.Stdout, build.Stderr = os.Stdout, os.Stderr
		if err := build.Run(); err != nil {
			return fmt.Errorf("构建 %s 失败: %w", name, err)
		}

		sum, err := hashFile(path)
		if err != nil {
			return err
		}
		if _, err := fmt.Fprintf(sums, "%x  %s\n", sum, name); err != nil {
			return err
		}
	}

	fmt.Println("产物与校验和位于", out)
	return nil
}

func hashFile(path string) ([]byte, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	sum := sha256.Sum256(data)
	return sum[:], nil
}
