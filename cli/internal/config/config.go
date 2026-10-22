// Package config 读取与写入 CLI 配置文件, 并解析实例地址与 token.
//
// 配置文件位置见 Path (Windows 与桌面应用同目录, macOS / Linux 为 ~/.config/amane), 权限 0600.
// token 的取值顺序为
// profile → 环境变量 AMANE_TOKEN → 本机 token 文件 (AMANE_DATA_DIR / 桌面默认数据目录 / ./data).
package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

// DefaultProfile 是未指定 --profile 且配置为空时使用的 profile 名.
const DefaultProfile = "default"

// FileName 是配置文件的名字.
//
// 不用 config.json: Windows 上它与桌面应用共用数据目录, 那份目录里已经有实例的 config.toml,
// 同名会难以区分. 这里显式标明是 CLI 自己的配置.
const FileName = "cli.json"

// EnvToken 是存放 token 的环境变量名, 与实例读取的同名变量一致.
const EnvToken = "AMANE_TOKEN"

// EnvDataDir 是实例数据目录的环境变量名, 用于定位本机 token 文件.
const EnvDataDir = "AMANE_DATA_DIR"

// Profile 是一个实例的接入配置.
type Profile struct {
	Server string `json:"server"`
	Token  string `json:"token,omitempty"`
}

// File 是配置文件的完整内容.
type File struct {
	Current  string             `json:"current"`
	Profiles map[string]Profile `json:"profiles"`
}

// Path 返回配置文件路径.
//
// 平台约定: Windows 与桌面应用共用数据目录, cli.json 因此与实例的 config.toml / token 并列;
// macOS 与 Linux 遵循 UNIX 约定放在 ~/.config/amane, 不占用平台的应用数据目录.
func Path() (string, error) {
	dir, err := Dir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, FileName), nil
}

// Dir 返回 CLI 的配置目录.
func Dir() (string, error) {
	return dirFor(runtime.GOOS, os.Getenv, os.UserHomeDir, os.UserConfigDir)
}

// dirFor 是 Dir 的平台无关实现, 便于逐平台测试.
func dirFor(goos string, getenv func(string) string, home, configDir func() (string, error)) (string, error) {
	switch goos {
	case "windows":
		local := strings.TrimSpace(getenv("LOCALAPPDATA"))
		if local == "" {
			return "", errors.New("无法确定 LOCALAPPDATA, 不能定位配置目录")
		}
		return filepath.Join(local, "Amane"), nil
	case "darwin":
		// os.UserConfigDir 在 macOS 返回 ~/Library/Application Support, 这里按 UNIX 约定另选位置.
		dir, err := home()
		if err != nil {
			return "", fmt.Errorf("无法确定用户主目录: %w", err)
		}
		return filepath.Join(dir, ".config", "amane"), nil
	default:
		// Linux 等: $XDG_CONFIG_HOME 或 ~/.config, 本身就是 UNIX 约定.
		dir, err := configDir()
		if err != nil {
			return "", fmt.Errorf("无法确定用户配置目录: %w", err)
		}
		return filepath.Join(dir, "amane"), nil
	}
}

// Load 读取配置文件. 文件不存在时返回空配置而不是错误.
func Load(path string) (*File, error) {
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return &File{Current: DefaultProfile, Profiles: map[string]Profile{}}, nil
	}
	if err != nil {
		return nil, fmt.Errorf("读取配置文件失败: %w", err)
	}
	var f File
	if err := json.Unmarshal(data, &f); err != nil {
		return nil, fmt.Errorf("配置文件 %s 不是合法 JSON: %w", path, err)
	}
	f.normalize()
	return &f, nil
}

// Save 写入配置文件, 目录 0700, 文件 0600.
func (f *File) Save(path string) error {
	f.normalize()
	data, err := json.MarshalIndent(f, "", "  ")
	if err != nil {
		return fmt.Errorf("序列化配置失败: %w", err)
	}
	data = append(data, '\n')
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return fmt.Errorf("创建配置目录失败: %w", err)
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return fmt.Errorf("写入配置文件失败: %w", err)
	}
	// WriteFile 的权限受 umask 影响, 显式设置一次.
	if err := os.Chmod(tmp, 0o600); err != nil {
		return fmt.Errorf("设置配置文件权限失败: %w", err)
	}
	if err := os.Rename(tmp, path); err != nil {
		return fmt.Errorf("替换配置文件失败: %w", err)
	}
	return nil
}

func (f *File) normalize() {
	if f.Profiles == nil {
		f.Profiles = map[string]Profile{}
	}
	if f.Current == "" {
		f.Current = DefaultProfile
	}
}

// Select 返回指定 profile 及其是否存在. 传入空名时使用当前 profile.
func (f *File) Select(name string) (string, Profile, bool) {
	if name == "" {
		name = f.Current
	}
	if name == "" {
		name = DefaultProfile
	}
	p, ok := f.Profiles[name]
	return name, p, ok
}

// ResolveToken 按 profile → 环境变量 → 本机 token 文件的顺序取 token,
// 同时返回来源说明, 供 doctor 报告.
func ResolveToken(p Profile, getenv func(string) string) (string, string) {
	if t := strings.TrimSpace(p.Token); t != "" {
		return t, "配置文件"
	}
	if t := strings.TrimSpace(getenv(EnvToken)); t != "" {
		return t, "环境变量 " + EnvToken
	}
	for _, path := range LocalTokenFiles(getenv) {
		if data, err := os.ReadFile(path); err == nil {
			if t := strings.TrimSpace(string(data)); t != "" {
				return t, "本机 token 文件 " + path
			}
		}
	}
	return "", ""
}

// LocalInstance 描述与本机实例有关的凭据, 用于「与实例同机」这条路径:
// 用户没有粘贴过任何东西, 但 token 就在数据目录里.
type LocalInstance struct {
	Token   string
	DataDir string
	Path    string
}

// FindLocalInstance 按 LocalTokenFiles 的顺序找本机实例; 找不到时 ok=false.
func FindLocalInstance(getenv func(string) string) (LocalInstance, bool) {
	for _, path := range LocalTokenFiles(getenv) {
		data, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		if token := strings.TrimSpace(string(data)); token != "" {
			return LocalInstance{Token: token, DataDir: filepath.Dir(path), Path: path}, true
		}
	}
	return LocalInstance{}, false
}

// 桌面应用与源码/Docker 跑的实例用不同的默认端口:
// 桌面壳把端口写进数据目录的 desktop.env (模板默认 18000), 源码与 Docker 用 8000.
const (
	DesktopDefaultPort = "18000"
	DefaultPort        = "8000"
)

// DefaultLocalServer 返回本机实例的默认地址.
func DefaultLocalServer(getenv func(string) string, dataDir string) string {
	return localServer(getenv, dataDir, desktopDataDir(getenv))
}

// localServer 与 DefaultLocalServer 同义, 但把桌面数据目录作为参数: 端口决策因此可以在
// 任何平台上测, 不必依赖运行时的默认目录.
//
// 取值顺序与桌面壳一致: 真实环境变量 > desktop.env > 平台默认. CLI 从终端启动时通常
// 继承不到 AMANE_PORT (那是壳注入给子进程的), 所以 desktop.env 是主要来源.
func localServer(getenv func(string) string, dataDir, desktopDir string) string {
	port := defaultPort(getenv, dataDir, desktopDir)
	host := strings.TrimSpace(getenv("AMANE_HOST"))
	if host == "" {
		host = desktopEnvValue(dataDir, "AMANE_HOST")
	}
	// 监听通配地址时本机仍要连回环.
	switch host {
	case "", "0.0.0.0", "::", "[::]":
		host = "127.0.0.1"
	}
	return "http://" + net.JoinHostPort(host, port)
}

// defaultPort 决定本机实例的端口: 环境变量 > desktop.env > 桌面默认 18000 / 其余 8000.
func defaultPort(getenv func(string) string, dataDir, desktopDir string) string {
	if port := strings.TrimSpace(getenv("AMANE_PORT")); port != "" {
		return port
	}
	if port := desktopEnvValue(dataDir, "AMANE_PORT"); port != "" {
		return port
	}
	if desktopDir != "" && dataDir == desktopDir {
		return DesktopDefaultPort
	}
	return DefaultPort
}

// desktopEnvValue 从数据目录的 desktop.env 读一个键, 没有该文件或该键时返回空串.
//
// 格式是壳定义的子集: 每行 KEY=VALUE, # 开头为注释. 这里只取值, 不做变量展开.
func desktopEnvValue(dataDir, key string) string {
	if dataDir == "" {
		return ""
	}
	data, err := os.ReadFile(filepath.Join(dataDir, "desktop.env"))
	if err != nil {
		return ""
	}
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		name, value, ok := strings.Cut(line, "=")
		if !ok || strings.TrimSpace(name) != key {
			continue
		}
		return strings.Trim(strings.TrimSpace(value), `"'`)
	}
	return ""
}

// LocalTokenFiles 按优先级列出本机实例的 token 文件候选.
//
// 桌面应用把数据目录放在平台默认位置而不是 ./data, 同机场景下这是最常见的一个, 因此必须包含,
// 否则用户明明和实例在同一台机器上也要手动登录一次.
func LocalTokenFiles(getenv func(string) string) []string {
	var dirs []string
	if dir := strings.TrimSpace(getenv(EnvDataDir)); dir != "" {
		dirs = append(dirs, dir)
	}
	if dir := desktopDataDir(getenv); dir != "" {
		dirs = append(dirs, dir)
	}
	dirs = append(dirs, "./data")

	paths := make([]string, 0, len(dirs))
	for _, dir := range dirs {
		paths = append(paths, filepath.Join(dir, "token"))
	}
	return paths
}

// desktopDataDir 返回桌面应用的数据目录, 与壳内置的默认值一致.
//
// macOS 为 ~/Library/Application Support/Amane; Windows 为 %LOCALAPPDATA%\Amane (不是 Roaming).
// 其它平台没有桌面应用, 返回空. 家目录也经 getenv 读取, 使调用方可完整控制输入.
func desktopDataDir(getenv func(string) string) string {
	switch runtime.GOOS {
	case "darwin":
		home := strings.TrimSpace(getenv("HOME"))
		if home == "" {
			return ""
		}
		return filepath.Join(home, "Library", "Application Support", "Amane")
	case "windows":
		local := strings.TrimSpace(getenv("LOCALAPPDATA"))
		if local == "" {
			return ""
		}
		return filepath.Join(local, "Amane")
	default:
		return ""
	}
}

// NormalizeServer 补全 scheme 并去掉末尾斜杠.
func NormalizeServer(raw string) (string, error) {
	s := strings.TrimSpace(raw)
	if s == "" {
		return "", errors.New("实例地址为空")
	}
	if !strings.Contains(s, "://") {
		s = "http://" + s
	}
	u, err := url.Parse(s)
	if err != nil {
		return "", fmt.Errorf("实例地址无法解析: %q", raw)
	}
	if u.Scheme != "http" && u.Scheme != "https" {
		return "", fmt.Errorf("实例地址只支持 http 与 https, 收到 %q", u.Scheme)
	}
	if u.Host == "" {
		return "", fmt.Errorf("实例地址缺少主机名: %q", raw)
	}
	if u.User != nil {
		return "", errors.New("实例地址不能包含用户名或密码: token 由 amane login 单独保存")
	}
	u.Path = strings.TrimRight(u.Path, "/")
	u.RawQuery = ""
	u.Fragment = ""
	return u.String(), nil
}

// PlaintextWarning 判断实例地址是否为非回环的明文 http, 需要提示 token 可能泄露.
func PlaintextWarning(server string) bool {
	u, err := url.Parse(server)
	if err != nil || u.Scheme != "http" {
		return false
	}
	host := u.Hostname()
	if host == "" {
		return false
	}
	if host == "localhost" || host == "::1" {
		return false
	}
	if strings.HasPrefix(host, "127.") {
		return false
	}
	return true
}

// Mask 打码 token: 长度不足 16 时整体打码, 否则保留首尾各 4 个字符.
func Mask(token string) string {
	if token == "" {
		return ""
	}
	if len(token) < 16 {
		return "****"
	}
	return token[:4] + "****" + token[len(token)-4:]
}
