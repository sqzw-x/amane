package version

import "testing"

func TestCompare(t *testing.T) {
	cases := []struct {
		name string
		a, b string
		want int
		ok   bool
	}{
		{name: "相同", a: "0.22.1", b: "0.22.1", want: 0, ok: true},
		{name: "补丁更高", a: "0.22.2", b: "0.22.1", want: 1, ok: true},
		{name: "次版本更高", a: "0.23.0", b: "0.22.9", want: 1, ok: true},
		{name: "主版本更高", a: "1.0.0", b: "0.99.99", want: 1, ok: true},
		{name: "位缺失按 0", a: "0.22", b: "0.22.0", want: 0, ok: true},
		{name: "v 前缀", a: "v0.22.1", b: "0.22.0", want: 1, ok: true},
		{name: "预发布视为无法比较", a: "0.22.1-rc1", b: "0.22.1", want: 0, ok: false},
		{name: "无法解析", a: "dev", b: "0.22.1", want: 0, ok: false},
		{name: "空版本", a: "", b: "0.22.1", want: 0, ok: false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got, ok := Compare(c.a, c.b)
			if ok != c.ok || (ok && got != c.want) {
				t.Fatalf("Compare(%q, %q) = (%d, %v), 期望 (%d, %v)", c.a, c.b, got, ok, c.want, c.ok)
			}
		})
	}
}
