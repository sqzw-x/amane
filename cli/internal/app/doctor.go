package app

import (
	"context"
	"fmt"
	"sort"

	"github.com/sqzw-x/amane/cli/internal/api"
	"github.com/sqzw-x/amane/cli/internal/config"
	"github.com/sqzw-x/amane/cli/internal/skill"
)

// cmdSkill 生成或打印 Skill.
func (r *runner) cmdSkill(ctx context.Context) error {
	switch sub := r.arg(0); sub {
	case "install":
		return r.skillInstall(ctx)
	case "show":
		return r.skillShow(ctx)
	default:
		return usagef("未知的 skill 子命令 %q, 可用: install, show", sub)
	}
}

func (r *runner) skillInstall(ctx context.Context) error {
	cat, err := r.fetchCatalog(ctx)
	if err != nil {
		return err
	}
	dir := r.p.dir
	if dir == "" {
		home, err := r.homeDir()
		if err != nil {
			return err
		}
		dir = skill.DefaultDir(home)
	} else {
		dir = skill.ResolveTarget(dir)
	}
	// 多实例共存时 Skill 以最新版本为准: 已装的版本不低就保留, 避免被旧实例降级.
	// 判断不了版本高低时照常写入, 与「没装过」同样处理.
	if installed, err := skill.InstalledVersion(dir); err == nil {
		if cmp, ok := skill.CompareVersions(installed, cat.Version); ok && cmp >= 0 {
			fmt.Fprintf(r.env.Stdout, "Skill 已是最新: %s 版本 %s 不低于实例版本 %s, 保留不动\n", dir, installed, cat.Version)
			return nil
		}
	}

	files := skill.Render(cat, r.env.Version)
	if err := skill.Install(dir, files); err != nil {
		return err
	}
	fmt.Fprintf(r.env.Stdout, "Skill 已安装到 %s (实例版本 %s)\n", dir, cat.Version)
	names := make([]string, 0, len(files))
	for name := range files {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		fmt.Fprintf(r.env.Stdout, "- %s\n", name)
	}
	return nil
}

func (r *runner) skillShow(ctx context.Context) error {
	cat, err := r.fetchCatalog(ctx)
	if err != nil {
		return err
	}
	files := skill.Render(cat, r.env.Version)
	for _, name := range []string{skill.MainFile, skill.VersionFile} {
		if _, err := fmt.Fprintf(r.env.Stdout, "<!-- ===== %s ===== -->\n%s\n", name, files[name]); err != nil {
			return err
		}
	}
	return nil
}

// fetchCatalog 取工具目录. 非 2xx 响应在这里转换为错误, 因为生成 Skill 必须有目录.
func (r *runner) fetchCatalog(ctx context.Context) (*api.Catalog, error) {
	client, err := r.client()
	if err != nil {
		return nil, err
	}
	cat, resp, err := client.Catalog(ctx)
	if err != nil {
		return nil, r.connErr(client.BaseURL, err)
	}
	if err := r.checkStatus(resp); err != nil {
		return nil, err
	}
	if cat == nil {
		return nil, connf("目录响应不可用 (HTTP %d): %s", resp.Status, detailOf(resp.Body))
	}
	return cat, nil
}

// doctorCheck 是一条自检结果.
type doctorCheck struct {
	Name   string `json:"name"`
	OK     bool   `json:"ok"`
	Detail string `json:"detail"`
}

// cmdDoctor 依次检查 CLI、实例可达性、token.
//
// 不检查 Skill: 装好之后由智能体自行加载, CLI 不再参与, 反过来验证它只会产生
// 「CLI 检查自己是否被正确安装」这种无意义的耦合.
func (r *runner) cmdDoctor(ctx context.Context) error {
	checks := []doctorCheck{r.checkCLI()}
	server, serverErr := r.server()
	if serverErr != nil {
		checks = append(checks, doctorCheck{Name: "实例", OK: false, Detail: serverErr.Error()})
		checks = append(checks, doctorCheck{Name: "鉴权", OK: false, Detail: "实例地址未知, 无法校验"})
		return r.reportDoctor(checks)
	}

	checks = append(checks, r.checkReachable(ctx, server))

	_, authCheck := r.checkAuth(ctx, server)
	checks = append(checks, authCheck)

	return r.reportDoctor(checks)
}

func (r *runner) checkCLI() doctorCheck {
	exe, err := r.executablePath()
	if err != nil {
		return doctorCheck{Name: "CLI", OK: false, Detail: err.Error()}
	}
	return doctorCheck{
		Name:   "CLI",
		OK:     true,
		Detail: fmt.Sprintf("%s 版本 %s (%s/%s)", exe, r.env.Version, r.env.GOOS, r.env.GOARCH),
	}
}

// checkReachable 用免鉴权的 /api/health 区分网络问题与鉴权问题.
func (r *runner) checkReachable(ctx context.Context, server string) doctorCheck {
	client := &api.Client{BaseURL: server, UserAgent: "amane-cli/" + r.env.Version, HTTP: r.httpClient()}
	resp, err := client.Get(ctx, "/api/health")
	if err != nil {
		return doctorCheck{Name: "实例", OK: false, Detail: fmt.Sprintf("%s 不可达: %v", server, err)}
	}
	if resp.Status != 200 {
		return doctorCheck{Name: "实例", OK: false, Detail: fmt.Sprintf("%s 返回 HTTP %d", server, resp.Status)}
	}
	return doctorCheck{Name: "实例", OK: true, Detail: server + " 可达"}
}

// checkAuth 用目录端点校验 token, 顺带取得实例版本.
func (r *runner) checkAuth(ctx context.Context, server string) (string, doctorCheck) {
	token, source, err := r.token()
	if err != nil {
		return "", doctorCheck{Name: "鉴权", OK: false, Detail: err.Error()}
	}
	client := &api.Client{BaseURL: server, Token: token, UserAgent: "amane-cli/" + r.env.Version, HTTP: r.httpClient()}
	cat, resp, err := client.Catalog(ctx)
	if err != nil {
		return "", doctorCheck{Name: "鉴权", OK: false, Detail: fmt.Sprintf("请求失败: %v", err)}
	}
	if api.IsAuthFailure(resp.Status) {
		return "", doctorCheck{Name: "鉴权", OK: false, Detail: fmt.Sprintf("HTTP %d, token 无效或缺失; 运行 amane login 写入, 或设置 %s", resp.Status, config.EnvToken)}
	}
	if cat == nil {
		return "", doctorCheck{Name: "鉴权", OK: false, Detail: fmt.Sprintf("目录端点返回 HTTP %d", resp.Status)}
	}
	detail := "token 可用"
	if source != "" {
		detail = "token 可用 (来自 " + source + ")"
	} else if token == "" {
		detail = "实例未启用 token 校验"
	}
	return cat.Version, doctorCheck{Name: "鉴权", OK: true, Detail: detail}
}

func (r *runner) reportDoctor(checks []doctorCheck) error {
	ok := true
	for _, check := range checks {
		if !check.OK {
			ok = false
		}
	}
	if r.p.jsonOut {
		if err := r.writeJSON(struct {
			OK     bool          `json:"ok"`
			Checks []doctorCheck `json:"checks"`
		}{OK: ok, Checks: checks}); err != nil {
			return err
		}
	} else {
		for _, check := range checks {
			mark := "通过"
			if !check.OK {
				mark = "失败"
			}
			if _, err := fmt.Fprintf(r.env.Stdout, "[%s] %s: %s\n", mark, check.Name, check.Detail); err != nil {
				return err
			}
		}
	}
	if !ok {
		return &cliError{code: ExitConnection, msg: "自检未通过, 按上面的失败项处理"}
	}
	return nil
}
