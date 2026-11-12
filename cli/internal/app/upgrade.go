package app

import (
	"context"

	"github.com/sqzw-x/amane/cli/internal/httpx"
	"github.com/sqzw-x/amane/cli/internal/upgrade"
)

// cmdUpgrade 从 GitHub Releases 下载同平台产物, 校验后替换自身.
func (r *runner) cmdUpgrade(ctx context.Context) error {
	exe, err := r.executablePath()
	if err != nil {
		return err
	}
	target, resolveErr := upgrade.ResolveTarget(exe)
	if resolveErr != nil {
		if !r.p.dryRun {
			return resolveErr
		}
		// dry-run 只报告计划, 路径解析失败时按原样展示.
		target = exe
	}
	base := r.env.UpgradeBaseURL
	if base == "" {
		base = upgrade.DefaultBaseURL
	}
	latestURL := r.env.UpgradeLatestURL
	if latestURL == "" {
		latestURL = upgrade.DefaultLatestURL
	}
	client := r.httpClient()
	// 下载单独给时限: 版本检查与实例请求仍是 15 秒, 但 10 MB 级的产物传输需要更宽的总时长.
	downloadTimeout := httpx.DownloadTimeout
	if r.p.timeoutSet {
		downloadTimeout = r.p.timeout
	}
	return upgrade.Run(ctx, upgrade.Options{
		BaseURL:         base,
		LatestURL:       latestURL,
		GOOS:            r.env.GOOS,
		GOARCH:          r.env.GOARCH,
		CurrentVersion:  r.env.Version,
		DownloadTimeout: downloadTimeout,
		TargetPath:      target,
		DryRun:          r.p.dryRun,
		HTTP:            client,
		Out:             r.env.Stdout,
		Err:             r.env.Stderr,
	})
}
