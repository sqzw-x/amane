package wait

import (
	"context"
	"errors"
	"testing"
	"time"
)

func TestTerminal(t *testing.T) {
	// 取值来自 TaskStatus; 没有 cancelled, 取消的任务是 failed.
	cases := map[string]bool{
		"queued":    false,
		"running":   false,
		"QUEUED":    false,
		" Running ": false,
		"done":      true,
		"failed":    true,
		"cancelled": true,
		"":          true,
		"weird":     true,
	}
	for status, want := range cases {
		if got := Terminal(status); got != want {
			t.Errorf("%q: 得到 %v, 期望 %v", status, got, want)
		}
	}
}

func TestStatusOf(t *testing.T) {
	cases := []struct {
		name string
		body string
		want string
	}{
		{name: "正常", body: `{"id":1,"status":"done"}`, want: "done"},
		{name: "无字段", body: `{"id":1}`, want: ""},
		{name: "非对象", body: `"OK"`, want: ""},
		{name: "非法 JSON", body: `{`, want: ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := StatusOf([]byte(c.body)); got != c.want {
				t.Fatalf("得到 %q, 期望 %q", got, c.want)
			}
		})
	}
}

// fakeClock 是只在 Sleep 时前进的时钟, 让轮询测试不消耗真实时间.
type fakeClock struct {
	now  time.Time
	step time.Duration
}

func (c *fakeClock) Now() time.Time { return c.now }

func (c *fakeClock) Sleep(_ context.Context, d time.Duration) error {
	c.step += d
	c.now = c.now.Add(d)
	return nil
}

func sequenceFetcher(t *testing.T, bodies []string) (Fetcher, *int) {
	t.Helper()
	calls := 0
	return func(context.Context) ([]byte, error) {
		if calls >= len(bodies) {
			t.Fatalf("轮询次数超出预期: %d", calls+1)
		}
		body := bodies[calls]
		calls++
		return []byte(body), nil
	}, &calls
}

func TestRunUntilTerminal(t *testing.T) {
	fetch, calls := sequenceFetcher(t, []string{
		`{"status":"queued"}`,
		`{"status":"running"}`,
		`{"status":"done","result":{}}`,
	})
	clock := &fakeClock{now: time.Unix(0, 0)}
	body, err := Run(context.Background(), fetch, Options{Interval: 2 * time.Second, Now: clock.Now, Sleep: clock.Sleep})
	if err != nil {
		t.Fatalf("未预期的错误: %v", err)
	}
	if *calls != 3 {
		t.Fatalf("轮询 %d 次, 期望 3 次", *calls)
	}
	if string(body) != `{"status":"done","result":{}}` {
		t.Fatalf("最终响应为 %s", body)
	}
	if clock.step != 4*time.Second {
		t.Fatalf("累计等待 %s, 期望 4s", clock.step)
	}
}

func TestRunStopsAtFirstTerminal(t *testing.T) {
	fetch, calls := sequenceFetcher(t, []string{`{"status":"failed","error":"boom"}`})
	clock := &fakeClock{now: time.Unix(0, 0)}
	body, err := Run(context.Background(), fetch, Options{Now: clock.Now, Sleep: clock.Sleep})
	if err != nil {
		t.Fatalf("未预期的错误: %v", err)
	}
	if *calls != 1 {
		t.Fatalf("轮询 %d 次, 期望 1 次", *calls)
	}
	if string(body) != `{"status":"failed","error":"boom"}` {
		t.Fatalf("最终响应为 %s", body)
	}
}

func TestRunUnknownStatusIsTerminal(t *testing.T) {
	fetch, calls := sequenceFetcher(t, []string{`{"detail":"not found"}`})
	clock := &fakeClock{now: time.Unix(0, 0)}
	if _, err := Run(context.Background(), fetch, Options{Now: clock.Now, Sleep: clock.Sleep}); err != nil {
		t.Fatalf("未预期的错误: %v", err)
	}
	if *calls != 1 {
		t.Fatalf("轮询 %d 次, 期望 1 次", *calls)
	}
}

func TestRunTimeout(t *testing.T) {
	fetch := func(context.Context) ([]byte, error) { return []byte(`{"status":"running"}`), nil }
	clock := &fakeClock{now: time.Unix(0, 0)}
	body, err := Run(context.Background(), fetch, Options{
		Interval: 2 * time.Second,
		Timeout:  5 * time.Second,
		Now:      clock.Now,
		Sleep:    clock.Sleep,
	})
	var te *TimeoutError
	if !errors.As(err, &te) {
		t.Fatalf("期望 TimeoutError, 实际 %v", err)
	}
	if string(body) != `{"status":"running"}` {
		t.Fatalf("超时时应返回最后一次响应, 实际 %q", body)
	}
	if clock.step > 5*time.Second {
		t.Fatalf("累计等待 %s, 超过截止时间", clock.step)
	}
}

func TestRunFetchError(t *testing.T) {
	boom := errors.New("连接中断")
	fetch := func(context.Context) ([]byte, error) { return nil, boom }
	if _, err := Run(context.Background(), fetch, Options{}); !errors.Is(err, boom) {
		t.Fatalf("期望返回取数错误, 实际 %v", err)
	}
}

func TestRunContextCancel(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	fetch := func(context.Context) ([]byte, error) { return []byte(`{"status":"running"}`), nil }
	sleep := func(context.Context, time.Duration) error {
		cancel()
		return ctx.Err()
	}
	if _, err := Run(ctx, fetch, Options{Sleep: sleep, Now: time.Now}); !errors.Is(err, context.Canceled) {
		t.Fatalf("期望取消错误, 实际 %v", err)
	}
}
