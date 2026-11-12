package httpx

import (
	"net/http"
	"testing"
	"time"
)

func TestDownload(t *testing.T) {
	cases := []struct {
		name        string
		base        *http.Client
		timeout     time.Duration
		wantTimeout time.Duration
	}{
		{name: "默认总量", base: nil, timeout: 0, wantTimeout: DownloadTimeout},
		{name: "显式总量", base: nil, timeout: 90 * time.Second, wantTimeout: 90 * time.Second},
		{
			name:        "沿用调用方的传输",
			base:        &http.Client{Transport: roundTripFunc(func(*http.Request) (*http.Response, error) { return nil, nil })},
			timeout:     time.Minute,
			wantTimeout: time.Minute,
		},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			client := Download(c.base, c.timeout)
			if client.Timeout != c.wantTimeout {
				t.Fatalf("Timeout = %s, 期望 %s", client.Timeout, c.wantTimeout)
			}
			// 默认客户端要压住连接与响应头, 不能只有总量.
			if c.base == nil {
				transport, ok := client.Transport.(*http.Transport)
				if !ok {
					t.Fatalf("传输类型为 %T", client.Transport)
				}
				if transport.ResponseHeaderTimeout != DefaultTimeout {
					t.Fatalf("ResponseHeaderTimeout = %s", transport.ResponseHeaderTimeout)
				}
				if transport.TLSHandshakeTimeout != DefaultTimeout {
					t.Fatalf("TLSHandshakeTimeout = %s", transport.TLSHandshakeTimeout)
				}
			}
		})
	}
	if DownloadTimeout <= DefaultTimeout {
		t.Fatalf("下载总量 %s 不应短于请求超时 %s", DownloadTimeout, DefaultTimeout)
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
