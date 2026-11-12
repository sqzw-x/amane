// Package version 比较本项目的版本号.
package version

import (
	"strconv"
	"strings"
)

// Compare 按点分数字比较两个版本, 返回 -1 / 0 / 1.
//
// 只处理本项目的版本形态 (如 0.22.1); 任何一侧解析不出数字时返回 ok=false,
// 调用方据此按「无法判断」处理, 不要把它当成相等.
func Compare(a, b string) (int, bool) {
	as, okA := parse(a)
	bs, okB := parse(b)
	if !okA || !okB {
		return 0, false
	}
	for i := 0; i < max(len(as), len(bs)); i++ {
		var av, bv int
		if i < len(as) {
			av = as[i]
		}
		if i < len(bs) {
			bv = bs[i]
		}
		switch {
		case av < bv:
			return -1, true
		case av > bv:
			return 1, true
		}
	}
	return 0, true
}

func parse(v string) ([]int, bool) {
	trimmed := strings.TrimPrefix(strings.TrimSpace(v), "v")
	if trimmed == "" {
		return nil, false
	}
	parts := strings.Split(trimmed, ".")
	out := make([]int, 0, len(parts))
	for _, part := range parts {
		// 带后缀的 (1.2.0-rc1, 1.2.0+dirty) 一律判为无法比较: 只取数字前缀会让预发布
		// 与正式版相等, 于是升级链停在 rc 上, 且没有任何提示.
		if strings.IndexFunc(part, func(r rune) bool { return r < '0' || r > '9' }) >= 0 {
			return nil, false
		}
		n, err := strconv.Atoi(part)
		if err != nil {
			return nil, false
		}
		out = append(out, n)
	}
	return out, true
}
