# amane CLI 的构建入口.
#
# 目标:
#   make check   格式检查 + go vet + go test
#   make build   编译当前平台的二进制到 dist/amane
#   make dist    交叉编译五个平台并生成 SHA256SUMS
#   make clean   清理产物
#
# 版本号经 -ldflags 注入, 默认 dev:
#   make dist VERSION=0.22.1

GO ?= go
VERSION ?= dev
DIST ?= dist
LDFLAGS := -X main.version=$(VERSION)
PLATFORMS := linux/amd64 linux/arm64 darwin/amd64 darwin/arm64 windows/amd64

.PHONY: all check fmt vet test build dist clean

all: check build

check:
	@out="$$(gofmt -l .)"; if [ -n "$$out" ]; then echo "以下文件未格式化:"; echo "$$out"; exit 1; fi
	$(GO) vet ./...
	$(GO) test ./...

fmt:
	gofmt -w .

vet:
	$(GO) vet ./...

test:
	$(GO) test ./...

build:
	@mkdir -p $(DIST)
	$(GO) build -trimpath -ldflags "$(LDFLAGS)" -o $(DIST)/amane ./cmd/amane

# 产物命名 amane-{os}-{arch}, Windows 加 .exe; 同目录生成 SHA256SUMS.
dist:
	@rm -rf $(DIST)
	@mkdir -p $(DIST)
	@set -e; for platform in $(PLATFORMS); do \
		os=$${platform%/*}; arch=$${platform#*/}; \
		out=$(DIST)/amane-$$os-$$arch; \
		if [ "$$os" = "windows" ]; then out=$$out.exe; fi; \
		echo "构建 $$out"; \
		GOOS=$$os GOARCH=$$arch CGO_ENABLED=0 \
			$(GO) build -trimpath -ldflags "$(LDFLAGS)" -o $$out ./cmd/amane; \
	done
	@cd $(DIST) && if command -v sha256sum >/dev/null 2>&1; then \
		sha256sum amane-* > SHA256SUMS; \
	else \
		shasum -a 256 amane-* > SHA256SUMS; \
	fi
	@echo "产物与校验和位于 $(DIST)/"

clean:
	rm -rf $(DIST)
