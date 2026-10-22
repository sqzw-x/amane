// Package wait 轮询任务详情, 直到任务到达终态.
//
// 状态取值以 src/amane/db/models.py 的 TaskStatus 为准, 只有四个: queued / running 为
// 未完成, done / failed 为终态. 没有 cancelled: 取消的任务标成 failed, error 字段写
// 「已由用户取消」, 因此不要在终态集合里匹配 cancelled.
// 未知状态按终态处理, 避免轮询永不结束.
package wait

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"
)

// DefaultInterval 是默认轮询间隔.
const DefaultInterval = 2 * time.Second

// pending 是未完成的状态集合; 其余取值 (含 done / failed) 均为终态.
var pending = map[string]bool{
	"queued":  true,
	"running": true,
}

// Terminal 判断任务状态是否到达终态.
func Terminal(status string) bool {
	return !pending[strings.ToLower(strings.TrimSpace(status))]
}

// StatusOf 从任务详情响应中读取 status 字段. 响应不是 JSON 对象或没有该字段时返回空串.
func StatusOf(body []byte) string {
	var payload struct {
		Status string `json:"status"`
	}
	if err := json.Unmarshal(body, &payload); err != nil {
		return ""
	}
	return payload.Status
}

// TimeoutError 表示在截止时间内没有等到终态.
type TimeoutError struct {
	Timeout time.Duration
	Last    []byte
}

func (e *TimeoutError) Error() string {
	return fmt.Sprintf("等待超时 (%s), 任务仍未到终态", e.Timeout)
}

// Fetcher 取一次任务详情. 返回的字节原样作为最终输出.
type Fetcher func(ctx context.Context) ([]byte, error)

// Options 控制轮询节奏. Interval 与 Timeout 为零时取默认值.
type Options struct {
	Interval time.Duration
	Timeout  time.Duration
	// Now 与 Sleep 供测试注入; 为零时使用真实时钟.
	Now   func() time.Time
	Sleep func(ctx context.Context, d time.Duration) error
}

// Run 轮询到终态并返回最后一次响应. 网络错误立即返回; 超时返回 *TimeoutError.
func Run(ctx context.Context, fetch Fetcher, opts Options) ([]byte, error) {
	interval := opts.Interval
	if interval <= 0 {
		interval = DefaultInterval
	}
	now := opts.Now
	if now == nil {
		now = time.Now
	}
	sleep := opts.Sleep
	if sleep == nil {
		sleep = sleepCtx
	}

	start := now()
	var last []byte
	for {
		body, err := fetch(ctx)
		if err != nil {
			return nil, err
		}
		last = body
		if Terminal(StatusOf(body)) {
			return body, nil
		}
		delay := interval
		if opts.Timeout > 0 {
			remaining := opts.Timeout - now().Sub(start)
			if remaining <= 0 {
				return last, &TimeoutError{Timeout: opts.Timeout, Last: last}
			}
			if remaining < delay {
				delay = remaining
			}
		}
		if err := sleep(ctx, delay); err != nil {
			return last, err
		}
	}
}

func sleepCtx(ctx context.Context, d time.Duration) error {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}
