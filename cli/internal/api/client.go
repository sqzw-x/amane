// Package api 是与 Amane 实例通信的 HTTP 客户端.
//
// 只覆盖 CLI 用到的三个端点: 目录 GET /api/tools, 派发 POST /api/tools/{name},
// 以及直通 GET 请求. 网络层失败与 HTTP 响应分开表示: 前者返回 *TransportError,
// 后者一律返回 *Response, 由调用方按状态码处理.
package api

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
)

// maxBody 是单次响应的读取上限, 避免异常响应耗尽内存.
const maxBody = 32 << 20

// CatalogPaths 是目录端点给出的运行时路径.
type CatalogPaths struct {
	DataDir    string   `json:"data_dir"`
	LogDir     string   `json:"log_dir"`
	DBPath     string   `json:"db_path"`
	ConfigPath string   `json:"config_path"`
	Env        []string `json:"env"`
}

// ToolSummary 是目录索引中的一条: 够选工具, 不够直接构造复杂入参.
type ToolSummary struct {
	Name      string `json:"name"`
	Group     string `json:"group"`
	Summary   string `json:"summary"`
	Signature string `json:"signature"`
}

// ToolDetail 是 GET /api/tools/{name} 的响应, 含完整入参.
type ToolDetail struct {
	Name        string          `json:"name"`
	Group       string          `json:"group"`
	Description string          `json:"description"`
	Params      json.RawMessage `json:"params"`
}

// Catalog 是 GET /api/tools 的响应.
type Catalog struct {
	Version string        `json:"version"`
	Paths   CatalogPaths  `json:"paths"`
	Tools   []ToolSummary `json:"tools"`
}

// Response 是一次已完成传输的响应, 状态码由调用方判断.
type Response struct {
	Status int
	Body   []byte
	Header http.Header
}

// TransportError 表示请求没有拿到响应: 连接失败、超时、地址无法解析等.
type TransportError struct {
	Err error
}

func (e *TransportError) Error() string { return e.Err.Error() }

func (e *TransportError) Unwrap() error { return e.Err }

// IsTransport 判断错误是否为网络层失败.
func IsTransport(err error) bool {
	var te *TransportError
	return errors.As(err, &te)
}

// Client 是实例的 HTTP 客户端.
type Client struct {
	BaseURL   string
	Token     string
	UserAgent string
	HTTP      *http.Client
}

func (c *Client) httpClient() *http.Client {
	if c.HTTP != nil {
		return c.HTTP
	}
	return http.DefaultClient
}

// Get 发起 GET 请求. path 须以 / 开头.
func (c *Client) Get(ctx context.Context, path string) (*Response, error) {
	return c.do(ctx, http.MethodGet, path, nil)
}

// PostJSON 发起 POST 请求, body 为已序列化的 JSON.
func (c *Client) PostJSON(ctx context.Context, path string, body []byte) (*Response, error) {
	return c.do(ctx, http.MethodPost, path, body)
}

func (c *Client) do(ctx context.Context, method, path string, body []byte) (*Response, error) {
	if !strings.HasPrefix(path, "/") {
		return nil, fmt.Errorf("内部错误: 路径须以 / 开头, 收到 %q", path)
	}
	url := strings.TrimRight(c.BaseURL, "/") + path
	var reader io.Reader
	if body != nil {
		reader = bytes.NewReader(body)
	}
	req, err := http.NewRequestWithContext(ctx, method, url, reader)
	if err != nil {
		return nil, &TransportError{Err: err}
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	req.Header.Set("Accept", "application/json")
	if c.Token != "" {
		req.Header.Set("Authorization", "Bearer "+c.Token)
	}
	if c.UserAgent != "" {
		req.Header.Set("User-Agent", c.UserAgent)
	}
	resp, err := c.httpClient().Do(req)
	if err != nil {
		return nil, &TransportError{Err: err}
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, maxBody))
	if err != nil {
		return nil, &TransportError{Err: err}
	}
	return &Response{Status: resp.StatusCode, Body: data, Header: resp.Header}, nil
}

// Catalog 取工具目录.
func (c *Client) Catalog(ctx context.Context) (*Catalog, *Response, error) {
	resp, err := c.Get(ctx, "/api/tools")
	if err != nil {
		return nil, nil, err
	}
	if resp.Status != http.StatusOK {
		return nil, resp, nil
	}
	var cat Catalog
	if err := json.Unmarshal(resp.Body, &cat); err != nil {
		return nil, resp, fmt.Errorf("目录响应不是合法 JSON: %w", err)
	}
	return &cat, resp, nil
}

// CallTool 派发一次工具调用. args 为工具入参对象, 空表示 {}.
func (c *Client) CallTool(ctx context.Context, name string, args json.RawMessage) (*Response, error) {
	if len(args) == 0 {
		args = json.RawMessage("{}")
	}
	payload, err := json.Marshal(struct {
		Args json.RawMessage `json:"args"`
	}{Args: args})
	if err != nil {
		return nil, fmt.Errorf("序列化入参失败: %w", err)
	}
	return c.PostJSON(ctx, "/api/tools/"+name, payload)
}

// APIPath 把用户给出的路径补成 /api 前缀. 已带 /api 或 /public 前缀时原样返回.
func APIPath(p string) (string, error) {
	s := strings.TrimSpace(p)
	if s == "" {
		return "", errors.New("路径为空")
	}
	if strings.Contains(s, "://") {
		return "", fmt.Errorf("只接受实例内的路径, 收到 %q", p)
	}
	if !strings.HasPrefix(s, "/") {
		s = "/" + s
	}
	if s == "/api" || strings.HasPrefix(s, "/api/") {
		return s, nil
	}
	return "/api" + s, nil
}

// ValidToolName 校验工具名, 拒绝空名、路径分隔符与空白, 避免请求打到其它端点.
func ValidToolName(name string) bool {
	if name == "" || strings.ContainsAny(name, "/\\?# \t\n") {
		return false
	}
	return true
}

// IsAuthFailure 判断状态码是否为鉴权失败.
func IsAuthFailure(status int) bool {
	return status == http.StatusUnauthorized || status == http.StatusForbidden
}
