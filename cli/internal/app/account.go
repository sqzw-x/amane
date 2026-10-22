package app

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"sort"
	"strings"

	"github.com/sqzw-x/amane/cli/internal/api"
	"golang.org/x/term"

	"github.com/sqzw-x/amane/cli/internal/config"
)

// cmdLogin 解析实例地址并写入一份 profile.
//
// token 有三个来源, 按「用户可能已经做过什么」排序: 显式给出的 (管道或终端输入)、
// 本机桌面版留下的 token 文件、现场提示用户粘贴. 本机文件这条要当场验一次鉴权 ——
// 实例未必是桌面版, 猜来的凭据写进配置只会让后续每条命令都 401.
func (r *runner) cmdLogin(ctx context.Context) error {
	cfg, err := r.config()
	if err != nil {
		return err
	}
	server, err := config.NormalizeServer(r.p.server)
	if err != nil {
		return usagef("%v", err)
	}
	name := strings.TrimSpace(r.p.profile)

	// 同名 profile 已存在时按地址分两种: 地址一致说明用户想刷新凭据, 先看旧的还能不能
	// 用; 地址不一致多半是重名, 默认不动它, 要覆盖得显式说.
	if existing, ok := cfg.Profiles[name]; ok && !r.p.force {
		stored, err := config.NormalizeServer(existing.Server)
		if err != nil || stored != server {
			return usagef("profile %q 已存在并指向 %s; 要改成 %s 请加 --force", name, existing.Server, server)
		}
		switch err := r.verifyToken(ctx, server, existing.Token); {
		case err == nil:
			fmt.Fprintf(r.env.Stderr, "amane: profile %q 已存在且凭据可用, 无需登录\n", name)
			return nil
		case api.IsTransport(err):
			return r.loginConnectError(server, err)
		}
	}

	if config.PlaintextWarning(server) {
		fmt.Fprintf(r.env.Stderr, "amane: 提示: %s 是明文 http 且不在本机, token 会以明文传输, 建议改用 https 或反向代理\n", server)
	}

	token, verified, err := r.loginToken(ctx, server)
	if err != nil {
		return err
	}
	if token == "" {
		return usagef("没有读到 token: 用管道传入, 或在终端里粘贴")
	}
	// 人给的 token 也验一次: 粘错了当场就知道, 而不是下一条命令才 401. 连不上实例时
	// 验不了, 仍按原样保存 —— 离线或实例尚未启动的场景不该挡住登录.
	var note string
	if !verified {
		switch err := r.verifyToken(ctx, server, token); {
		case err == nil:
		case api.IsTransport(err):
			note = "; 未能验证, 连不上实例"
		default:
			return connf("token 未被实例接受 (%v); 确认复制完整后重试", err)
		}
	}

	cfg.Profiles[name] = config.Profile{Server: server, Token: token}
	if err := cfg.Save(r.cfgPath); err != nil {
		return err
	}
	fmt.Fprintf(r.env.Stderr, "amane: 已保存 profile %q (%s)%s\n", name, server, note)
	return nil
}

// loginToken 选一个 token: 显式给出的优先, 其次本机桌面版留下的, 最后问用户.
//
// 空管道不等于「没有凭据」: 智能体的执行器常把 stdin 接到空 pipe 或 /dev/null,
// 这时仍要继续找本机 token, 否则引导承诺的「本机无需粘贴」对这类调用方直接失效.
func (r *runner) loginToken(ctx context.Context, server string) (string, bool, error) {
	terminal := r.env.StdinIsTerminal
	if !terminal {
		data, err := io.ReadAll(r.env.Stdin)
		if err != nil {
			return "", false, usagef("读取标准输入失败: %v", err)
		}
		if token := strings.TrimSpace(string(data)); token != "" {
			return token, false, nil
		}
	}

	// 地址指向本机时才去找桌面版留下的凭据. 找到要当场验一次: 这个地址上跑的不一定
	// 是桌面版, 属于别处的凭据写进配置只会让后续每条命令都 401.
	if config.IsLoopback(server) {
		if token, _ := config.LocalToken(r.env.Getenv); token != "" {
			if err := r.verifyToken(ctx, server, token); err == nil {
				return token, true, nil
			} else if api.IsTransport(err) {
				return "", false, r.loginConnectError(server, err)
			}
		}
	}

	// 后面的文案要区分「实例在但不认这个凭据」与「这个地址上没有实例」, 否则会引导
	// 用户去给一台不存在的实例登录. 探活走免鉴权的健康检查.
	//
	// 只在非交互时探: 终端里由人当场粘贴, 先探一次只会让人多等一个来回.
	if !terminal {
		if err := r.pingInstance(ctx, server); err != nil {
			return "", false, r.loginConnectError(server, err)
		}
		return "", false, &cliError{
			code: ExitConnection,
			msg:  fmt.Sprintf("此实例无法自动鉴权, 需引导用户手动登录\n示例: amane login --server %s --profile %s", server, r.p.profile),
		}
	}

	fmt.Fprintln(r.env.Stderr, "请粘贴该实例的 API Token 后回车 (输入不回显):")
	token, err := r.readSecret()
	if err != nil {
		return "", false, usagef("读取标准输入失败: %v", err)
	}
	return token, false, nil
}

// readSecret 读取用户粘贴的 token. 终端下关闭回显, 免得凭据留在屏幕上.
func (r *runner) readSecret() (string, error) {
	if file, ok := r.env.Stdin.(*os.File); ok && r.env.StdinIsTerminal {
		if typed, err := term.ReadPassword(int(file.Fd())); err == nil {
			fmt.Fprintln(r.env.Stderr)
			return strings.TrimSpace(string(typed)), nil
		}
	}
	return readToken(r.env.Stdin, r.env.StdinIsTerminal)
}

// pingInstance 用免鉴权的健康检查确认该地址上有实例在跑.
func (r *runner) pingInstance(ctx context.Context, server string) error {
	client := &api.Client{BaseURL: server, UserAgent: "amane-cli/" + r.env.Version, HTTP: r.httpClient()}
	resp, err := client.Get(ctx, "/api/health")
	if err != nil {
		return err
	}
	if resp.Status < 200 || resp.Status >= 300 {
		return fmt.Errorf("健康检查返回 HTTP %d", resp.Status)
	}
	return nil
}

// loginConnectError 给出连不上时的下一步: 该确认地址或启动实例, 不是去登录.
func (r *runner) loginConnectError(server string, err error) error {
	if api.IsRefused(err) {
		return &cliError{
			code: ExitConnection,
			msg:  fmt.Sprintf("连接实例失败 (%s): 端口上没有实例在监听\n请向用户确认实例地址, 或让用户先启动实例", server),
		}
	}
	return &cliError{
		code: ExitConnection,
		msg:  fmt.Sprintf("连接实例失败 (%s): %v\n请向用户确认实例地址与网络", server, err),
	}
}

// verifyToken 用一次需要鉴权的请求确认 token 可用. 传输错误原样返回, 由调用方决定
// 是当作「连不上」还是「凭据不对」.
//
// 端点用 /api/system/desktop: 菜单栏每三秒轮询它, 是历来版本都有的路径; 换成新端点会
// 让旧服务端上的本机登录被判成「凭据不可用」. 只有 401/403 算不可用 —— 其他状态说明不了
// token 的对错, 留给 doctor 报告.
func (r *runner) verifyToken(ctx context.Context, server, token string) error {
	client := &api.Client{BaseURL: server, Token: token, UserAgent: "amane-cli/" + r.env.Version, HTTP: r.httpClient()}
	resp, err := client.Get(ctx, "/api/system/desktop")
	if err != nil {
		return err
	}
	if api.IsAuthFailure(resp.Status) {
		return fmt.Errorf("鉴权失败 (HTTP %d)", resp.Status)
	}
	return nil
}

func (r *runner) cmdConfig(_ context.Context) error {
	cfg, err := r.config()
	if err != nil {
		return err
	}
	switch sub := r.arg(0); sub {
	case "":
		if strings.TrimSpace(r.p.profile) == "" {
			return usagef("缺少 --profile: amane config --profile <名字>, 或 amane config list 查看全部")
		}
		profile, ok := cfg.Profiles[r.p.profile]
		if !ok {
			return usagef("profile %q 不存在; 用 amane config list 查看已有 profile", r.p.profile)
		}
		token, source := config.ResolveToken(profile, r.env.Getenv)
		return r.writeJSON(struct {
			Profile     string `json:"profile"`
			Server      string `json:"server"`
			Token       string `json:"token"`
			TokenSource string `json:"token_source"`
			ConfigPath  string `json:"config_path"`
		}{
			Profile:     r.p.profile,
			Server:      profile.Server,
			Token:       config.Mask(token),
			TokenSource: source,
			ConfigPath:  r.cfgPath,
		})
	case "delete", "rm":
		name, err := r.requireArg(1, "<名字>")
		if err != nil {
			return err
		}
		if _, ok := cfg.Profiles[name]; !ok {
			return usagef("profile %q 不存在; 用 amane config list 查看已有 profile", name)
		}
		delete(cfg.Profiles, name)
		if err := cfg.Save(r.cfgPath); err != nil {
			return err
		}
		fmt.Fprintf(r.env.Stderr, "amane: 已删除 profile %q\n", name)
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
			Profiles []entry `json:"profiles"`
		}{Profiles: entries})
	default:
		return subcommandError("config", sub, configSubcommands...)
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
