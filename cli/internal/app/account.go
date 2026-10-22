package app

import (
	"bufio"
	"context"
	"errors"
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
	if r.env.StdinIsTerminal {
		// 交互终端里 ReadAll 要等到 EOF 才返回, 用户会以为命令卡住; 读一行即可.
		// token 在终端下必然回显, 想避免就用管道.
		fmt.Fprintln(r.env.Stderr, "请粘贴实例的 API Token 后回车:")
	}
	token, err := readToken(r.env.Stdin, r.env.StdinIsTerminal)
	if err != nil {
		return usagef("读取标准输入失败: %v", err)
	}
	if token == "" {
		// 与实例同机时凭据已在数据目录里, 用户不必粘贴.
		if local, path := config.LocalToken(r.env.Getenv); local != "" {
			token = local
			fmt.Fprintf(r.env.Stderr, "amane: token 取自本机文件 %s\n", path)
		}
	}
	if token == "" {
		return usagef("没有读到 token: 用法 echo <token> | amane login --server <地址>")
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
			// 与实例同机时不需要任何 profile: 地址取默认, token 取本机文件.
			if token, path := config.LocalToken(r.env.Getenv); path != "" {
				return r.writeJSON(struct {
					Profile     string `json:"profile"`
					Server      string `json:"server"`
					Token       string `json:"token"`
					TokenSource string `json:"token_source"`
					ConfigPath  string `json:"config_path"`
				}{
					Server:      config.DefaultLocalServer(r.env.Getenv),
					Token:       config.Mask(token),
					TokenSource: "本机 token 文件 " + path,
					ConfigPath:  r.cfgPath,
				})
			}
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

// readToken 读取 token: 交互终端读一行, 管道读全部.
func readToken(stdin io.Reader, terminal bool) (string, error) {
	if terminal {
		line, err := bufio.NewReader(stdin).ReadString('\n')
		// EOF 只说明没按回车就结束了, 已读到的部分仍然有效.
		if err != nil && !errors.Is(err, io.EOF) {
			return "", err
		}
		return strings.TrimSpace(line), nil
	}
	data, err := io.ReadAll(stdin)
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(data)), nil
}
