// Package httpx 定义 CLI 唯一的 HTTP 超时策略.
//
// 每个出站请求都必须有超时: 对端可能是已断开的局域网地址, 或中间的代理黑洞,
// 无限等待会把智能体的一次会话整个卡住.
package httpx

import (
	"net/http"
	"time"
)

// DefaultTimeout 是单个请求的默认超时, 对实例请求与发布下载一律生效.
// 慢操作靠 `--timeout` 单独放宽, 不在这里另设第二种默认值.
const DefaultTimeout = 15 * time.Second

// DownloadTimeout 是下载发布产物的总时限.
//
// 它必须容纳 10 MB 级的传输, 因此比请求超时宽松; 但「连不上」不该由它兜底 —
// 首字节的等待仍受 DefaultTimeout 约束, 见 Download.
const DownloadTimeout = 2 * time.Minute

// 兜底客户端只在调用方没有注入时使用.
var defaultClient = &http.Client{Timeout: DefaultTimeout}

// Client 返回兜底客户端.
func Client() *http.Client { return defaultClient }

// Download 返回用于下载发布产物的客户端: 首字节限时 DefaultTimeout, 总时长交给 timeout.
//
// http.Client.Timeout 是整段请求的时限, 套用 15 秒会让慢链路上的 10 MB 下载永远失败;
// 而完全放开又会让不可达的地址占住会话. 拆成「首字节等多久」与「总量给多久」两件事.
func Download(base *http.Client, timeout time.Duration) *http.Client {
	if base == nil {
		base = Client()
	}
	if timeout <= 0 {
		timeout = DownloadTimeout
	}
	c := *base
	c.Timeout = timeout
	if c.Transport == nil {
		if transport, ok := http.DefaultTransport.(*http.Transport); ok {
			clone := transport.Clone()
			clone.ResponseHeaderTimeout = DefaultTimeout
			c.Transport = clone
		}
	}
	return &c
}
