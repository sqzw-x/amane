// Package upgrade 从 GitHub Releases 下载同平台二进制并替换自身.
//
// 发布产物名为 amane-{os}-{arch} (Windows 加 .exe), 同目录的 SHA256SUMS 给出校验和.
package upgrade

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
)

// DefaultBaseURL 是发布产物的下载根地址.
const DefaultBaseURL = "https://github.com/sqzw-x/amane/releases/latest/download"

// checksumFile 是校验和文件名.
const checksumFile = "SHA256SUMS"

// supported 是发布覆盖的平台.
var supported = map[string][]string{
	"linux":   {"amd64", "arm64"},
	"darwin":  {"amd64", "arm64"},
	"windows": {"amd64"},
}

// AssetName 返回当前平台的发布产物名.
func AssetName(goos, goarch string) (string, error) {
	arches, ok := supported[goos]
	if !ok {
		return "", fmt.Errorf("没有 %s 平台的发布产物", goos)
	}
	found := false
	for _, a := range arches {
		if a == goarch {
			found = true
			break
		}
	}
	if !found {
		return "", fmt.Errorf("没有 %s/%s 的发布产物", goos, goarch)
	}
	name := "amane-" + goos + "-" + goarch
	if goos == "windows" {
		name += ".exe"
	}
	return name, nil
}

// ParseChecksums 从 SHA256SUMS 中取出指定文件的校验和. 行格式为
// "<hex>  <name>" 或 "<hex> *<name>".
func ParseChecksums(data []byte, name string) (string, error) {
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) < 2 {
			continue
		}
		hash := strings.ToLower(fields[0])
		file := strings.TrimPrefix(fields[len(fields)-1], "*")
		if file != name {
			continue
		}
		if len(hash) != 64 {
			return "", fmt.Errorf("校验和长度错误: %q", hash)
		}
		if _, err := hex.DecodeString(hash); err != nil {
			return "", fmt.Errorf("校验和不是十六进制: %q", hash)
		}
		return hash, nil
	}
	return "", fmt.Errorf("校验文件中没有 %s 的记录", name)
}

// VerifySHA256 比对数据的校验和.
func VerifySHA256(data []byte, want string) error {
	sum := sha256.Sum256(data)
	got := hex.EncodeToString(sum[:])
	if !strings.EqualFold(got, want) {
		return fmt.Errorf("校验和不匹配: 得到 %s, 期望 %s", got, want)
	}
	return nil
}

// ResolveTarget 解析实际需要替换的文件路径, 跟随符号链接.
func ResolveTarget(path string) (string, error) {
	resolved, err := filepath.EvalSymlinks(path)
	if err != nil {
		return "", fmt.Errorf("无法解析自身路径: %w", err)
	}
	abs, err := filepath.Abs(resolved)
	if err != nil {
		return "", fmt.Errorf("无法确定自身路径: %w", err)
	}
	return abs, nil
}

// Replace 用 data 替换 target. Windows 上运行中的可执行文件不能被覆盖写,
// 因此先把自身改名为 .old, 再写入新文件.
func Replace(target string, data []byte, goos string) error {
	dir := filepath.Dir(target)
	tmp, err := os.CreateTemp(dir, ".amane-upgrade-*")
	if err != nil {
		return fmt.Errorf("无法在 %s 创建临时文件 (该目录需要可写): %w", dir, err)
	}
	tmpName := tmp.Name()
	cleanup := func() { _ = os.Remove(tmpName) }
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		cleanup()
		return fmt.Errorf("写入临时文件失败: %w", err)
	}
	if err := tmp.Close(); err != nil {
		cleanup()
		return fmt.Errorf("写入临时文件失败: %w", err)
	}
	if err := os.Chmod(tmpName, 0o755); err != nil {
		cleanup()
		return fmt.Errorf("设置可执行权限失败: %w", err)
	}

	if goos != "windows" {
		if err := os.Rename(tmpName, target); err != nil {
			cleanup()
			return fmt.Errorf("替换 %s 失败: %w", target, err)
		}
		return nil
	}

	old := target + ".old"
	_ = os.Remove(old)
	if err := os.Rename(target, old); err != nil {
		cleanup()
		return fmt.Errorf("无法重命名正在运行的 %s: %w", target, err)
	}
	if err := os.Rename(tmpName, target); err != nil {
		_ = os.Rename(old, target)
		return fmt.Errorf("写入 %s 失败: %w", target, err)
	}
	// 旧文件可能仍被系统占用, 删除失败不视为升级失败.
	_ = os.Remove(old)
	return nil
}

// Options 是一次升级的全部输入.
type Options struct {
	BaseURL        string
	GOOS           string
	GOARCH         string
	CurrentVersion string
	TargetPath     string
	DryRun         bool
	HTTP           *http.Client
	Out            io.Writer
	Err            io.Writer
}

// Run 执行升级. DryRun 时只报告将要执行的下载与替换.
func Run(ctx context.Context, opts Options) error {
	base := strings.TrimRight(strings.TrimSpace(opts.BaseURL), "/")
	if base == "" {
		base = DefaultBaseURL
	}
	name, err := AssetName(opts.GOOS, opts.GOARCH)
	if err != nil {
		return err
	}
	assetURL := base + "/" + name
	sumsURL := base + "/" + checksumFile

	if opts.DryRun {
		fmt.Fprintf(opts.Out, "平台: %s/%s\n", opts.GOOS, opts.GOARCH)
		fmt.Fprintf(opts.Out, "当前版本: %s\n", opts.CurrentVersion)
		fmt.Fprintf(opts.Out, "当前路径: %s\n", opts.TargetPath)
		fmt.Fprintf(opts.Out, "下载地址: %s\n", assetURL)
		fmt.Fprintf(opts.Out, "校验文件: %s\n", sumsURL)
		fmt.Fprintf(opts.Out, "将写入: %s\n", opts.TargetPath)
		return nil
	}

	sums, err := fetch(ctx, opts.HTTP, sumsURL)
	if err != nil {
		return fmt.Errorf("下载校验文件失败: %w", err)
	}
	want, err := ParseChecksums(sums, name)
	if err != nil {
		return err
	}
	data, err := fetch(ctx, opts.HTTP, assetURL)
	if err != nil {
		return fmt.Errorf("下载 %s 失败: %w", name, err)
	}
	if err := VerifySHA256(data, want); err != nil {
		return err
	}
	if err := Replace(opts.TargetPath, data, opts.GOOS); err != nil {
		return err
	}
	fmt.Fprintf(opts.Out, "已替换 %s\n", opts.TargetPath)
	return nil
}

func fetch(ctx context.Context, client *http.Client, url string) ([]byte, error) {
	if client == nil {
		client = http.DefaultClient
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("%s 返回 HTTP %d", url, resp.StatusCode)
	}
	return io.ReadAll(io.LimitReader(resp.Body, 256<<20))
}
