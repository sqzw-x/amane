package app

import (
	"context"
	"net/http"
	"time"

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
	client := r.env.HTTPClient
	if client == nil {
		client = &http.Client{Timeout: 5 * time.Minute}
	}
	return upgrade.Run(ctx, upgrade.Options{
		BaseURL:        base,
		GOOS:           r.env.GOOS,
		GOARCH:         r.env.GOARCH,
		CurrentVersion: r.env.Version,
		TargetPath:     target,
		DryRun:         r.p.dryRun,
		HTTP:           client,
		Out:            r.env.Stdout,
		Err:            r.env.Stderr,
	})
}
