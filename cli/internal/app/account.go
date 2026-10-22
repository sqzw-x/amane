package app

import (
	"context"
	"fmt"
	"io"
	"sort"
	"strings"

	"github.com/sqzw-x/amane/cli/internal/config"
)

// cmdLogin 从标准输入读取 token 并写入配置文件.
func (r *runner) cmdLogin(_ context.Context) error {
	cfg, err := r.config()
	if err != nil {
		return err
	}
	data, err := io.ReadAll(r.env.Stdin)
	if err != nil {
		return usagef("读取标准输入失败: %v", err)
	}
	token := strings.TrimSpace(string(data))
	if token == "" {
		return usagef("标准输入为空: 用法 echo <token> | amane login --server <地址>")
	}

	name := r.p.profile
	if name == "" {
		name = cfg.Current
	}
	var server string
	if raw := strings.TrimSpace(r.p.server); raw != "" {
		server, err = config.NormalizeServer(raw)
		if err != nil {
			return usagef("%v", err)
		}
	} else {
		existing, ok := cfg.Profiles[name]
		if !ok || strings.TrimSpace(existing.Server) == "" {
			return usagef("缺少 --server: amane login --server <地址>")
		}
		server, err = config.NormalizeServer(existing.Server)
		if err != nil {
			return usagef("%v", err)
		}
	}
	if config.PlaintextWarning(server) {
		fmt.Fprintf(r.env.Stderr, "amane: 提示: %s 是明文 http 且不在本机, token 会以明文传输, 建议改用 https 或反向代理\n", server)
	}

	cfg.Profiles[name] = config.Profile{Server: server, Token: token}
	cfg.Current = name
	if err := cfg.Save(r.cfgPath); err != nil {
		return err
	}
	fmt.Fprintf(r.env.Stderr, "amane: 已保存 profile %s (%s), token 写入 %s (权限 0600)\n", name, server, r.cfgPath)
	return nil
}

// cmdConfig 查看配置、切换 profile 或列出全部 profile.
func (r *runner) cmdConfig(_ context.Context) error {
	cfg, err := r.config()
	if err != nil {
		return err
	}
	switch sub := r.arg(0); sub {
	case "":
		name, profile, ok := cfg.Select(r.p.profile)
		if !ok {
			return usagef("profile %q 不存在; 先运行 amane login --server <地址>, 或用 amane config list 查看已有 profile", name)
		}
		token, source := config.ResolveToken(profile, r.env.Getenv)
		return r.writeJSON(struct {
			Profile     string `json:"profile"`
			Server      string `json:"server"`
			Token       string `json:"token"`
			TokenSource string `json:"token_source"`
			ConfigPath  string `json:"config_path"`
		}{
			Profile:     name,
			Server:      profile.Server,
			Token:       config.Mask(token),
			TokenSource: source,
			ConfigPath:  r.cfgPath,
		})
	case "use":
		name, err := r.requireArg(1, "profile")
		if err != nil {
			return err
		}
		if _, ok := cfg.Profiles[name]; !ok {
			return usagef("profile %q 不存在; 先运行 amane login --server <地址> --profile %s 创建", name, name)
		}
		cfg.Current = name
		if err := cfg.Save(r.cfgPath); err != nil {
			return err
		}
		fmt.Fprintf(r.env.Stderr, "amane: 已切换到 profile %s\n", name)
		return nil
	case "list":
		type entry struct {
			Name   string `json:"name"`
			Server string `json:"server"`
			Token  string `json:"token"`
		}
		names := make([]string, 0, len(cfg.Profiles))
		for name := range cfg.Profiles {
			names = append(names, name)
		}
		sort.Strings(names)
		entries := make([]entry, 0, len(names))
		for _, name := range names {
			profile := cfg.Profiles[name]
			token, _ := config.ResolveToken(profile, r.env.Getenv)
			entries = append(entries, entry{Name: name, Server: profile.Server, Token: config.Mask(token)})
		}
		return r.writeJSON(struct {
			Current  string  `json:"current"`
			Profiles []entry `json:"profiles"`
		}{Current: cfg.Current, Profiles: entries})
	default:
		return usagef("未知的 config 子命令 %q, 可用: use, list", sub)
	}
}
