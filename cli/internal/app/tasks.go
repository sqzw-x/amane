package app

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strconv"
	"time"

	"github.com/sqzw-x/amane/cli/internal/api"
	"github.com/sqzw-x/amane/cli/internal/wait"
)

// cmdWait 轮询任务到终态.
//
// 目录里没有取任务详情的工具 (task-ops 只有 submit_task / cancel_task / retry_task /
// get_task_submission_schema), 因此直接读 GET /api/tasks/{id}, 与 api 命令同一条路径.
// 终态为 done 与 failed: 取消的任务也是 failed, 没有 cancelled.
func (r *runner) cmdWait(ctx context.Context) error {
	id, err := r.requireArg(0, "<task-id>")
	if err != nil {
		return err
	}
	if _, convErr := strconv.Atoi(id); convErr != nil {
		return usagef("task-id 须为整数, 收到 %q", id)
	}
	client, err := r.client()
	if err != nil {
		return err
	}
	path := "/api/tasks/" + id
	fetch := func(ctx context.Context) ([]byte, error) {
		resp, err := client.Get(ctx, path)
		if err != nil {
			return nil, err
		}
		if api.IsAuthFailure(resp.Status) {
			return nil, connf("鉴权失败 (HTTP %d): %s", resp.Status, detailOf(resp.Body))
		}
		// 非 2xx 不能交给终态判断: 解不出 status 会被当成「已完成」, 502 的 HTML 与
		// 路径写错被回退的页面都会让调用方以为任务真的到终态了.
		if resp.Status < 200 || resp.Status >= 300 {
			return nil, &cliError{code: ExitServer, msg: fmt.Sprintf("读取任务失败 (HTTP %d): %s", resp.Status, detailOf(resp.Body))}
		}
		if wait.StatusOf(resp.Body) == "" {
			return nil, &cliError{code: ExitServer, msg: fmt.Sprintf("响应里没有 status 字段, 不是任务详情: %s", detailOf(resp.Body))}
		}
		return resp.Body, nil
	}

	deadline := time.Duration(0)
	if r.p.timeoutSet {
		deadline = r.p.timeout
	}
	body, err := wait.Run(ctx, fetch, wait.Options{
		Interval: r.p.interval,
		Timeout:  deadline,
		Now:      r.env.Now,
		Sleep:    r.env.Sleep,
	})
	if err != nil {
		var te *wait.TimeoutError
		if errors.As(err, &te) {
			if len(te.Last) > 0 {
				_ = r.writeBody(te.Last)
			}
			return connf("%v", te)
		}
		return r.connErr(client.BaseURL, err)
	}
	return r.writeBody(body)
}

// cmdAPI 直通 REST 请求.
//
// REST 供实例自己的界面使用, 不为智能体设计: 形状随版本变化, 也不保证易用.
// 这里是工具目录覆盖不到时的兜底, 方法与路径原样送出, 响应不解释.
func (r *runner) cmdAPI(ctx context.Context) error {
	raw, err := r.requireArg(0, "<path>")
	if err != nil {
		return err
	}
	path, err := api.APIPath(raw)
	if err != nil {
		return usagef("%v", err)
	}
	body, err := r.readRequestBody()
	if err != nil {
		return err
	}
	method := r.p.method
	if method == "" {
		method = http.MethodGet
	}
	client, err := r.client()
	if err != nil {
		return err
	}
	resp, err := client.Do(ctx, method, path, body)
	if err != nil {
		return r.connErr(client.BaseURL, err)
	}
	if err := r.checkStatus(resp); err != nil {
		return err
	}
	return r.writeDownloadable(resp)
}

// readRequestBody 读取 api 请求体: 只认 --file, 传 - 时读标准输入; 未给来源时不带请求体.
func (r *runner) readRequestBody() ([]byte, error) {
	if !r.p.fileSet {
		return nil, nil
	}
	var (
		data []byte
		err  error
	)
	if r.p.file == "-" {
		data, err = io.ReadAll(r.env.Stdin)
	} else {
		data, err = os.ReadFile(r.p.file)
	}
	if err != nil {
		return nil, usagef("读取请求体失败: %v", err)
	}
	trimmed := bytes.TrimSpace(data)
	if len(trimmed) == 0 {
		return nil, usagef("请求体为空: %s", r.p.file)
	}
	return trimmed, nil
}
