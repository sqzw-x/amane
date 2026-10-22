// Command amane 是 Amane 实例的外部智能体接口客户端.
package main

import (
	"os"

	"github.com/sqzw-x/amane/cli/internal/app"
)

// version 由构建期 -ldflags "-X main.version=..." 注入.
var version = "dev"

func main() {
	env := &app.Env{Version: version, StdinIsTerminal: stdinIsTerminal()}
	os.Exit(app.Run(os.Args[1:], env))
}

// stdinIsTerminal 判断标准输入是否来自终端, 用于决定 call 无参数时是否读取管道.
func stdinIsTerminal() bool {
	info, err := os.Stdin.Stat()
	if err != nil {
		return false
	}
	return info.Mode()&os.ModeCharDevice != 0
}
