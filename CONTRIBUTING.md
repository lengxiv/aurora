# 贡献指南（CONTRIBUTING）

本文件是 Aurora 的工程维护规则。提交代码前请通读；这些规则同时被 CI 部分强制执行。

## 1. 环境与常用命令

- Node.js ≥ 20.19（建议 22）、Python 3.11、Docker（可选，用于容器部署）。
- 本机没有 Node/Python 时，可用 Docker 跑测试与构建：

```bash
# 后端测试
docker run --rm -v "$(pwd)":/work -w /work python:3.11-slim \
  bash -c "pip install -q -r backend/requirements.txt \
  && AURORA_STATE_DIR=/tmp/s AURORA_DATA_DIR=/tmp/d AURORA_LOCAL_MOUNT=/tmp/m \
  python -m unittest discover -s tests -p 'test_*.py'"

# 前端 lint + 构建
docker run --rm -v "$(pwd)":/work -w /work/frontend node:22-alpine \
  sh -c "npm ci && npm run lint && npm run build"
```

- 前端构建产物写入 `backend/static/`，由 FastAPI 托管，前后端不分开部署。

## 2. 代码规范

- 前端：`npm run lint`（oxlint）与 `npm run build`（tsc -b）必须零错误；不引入 CI 无法通过的新依赖类型。
- 后端：新增真实服务对接必须有在线/离线空态、超时、错误提示和后端测试（沿用 `tests/test_backend.py` 的 FakeProvider 模式）。
- 破坏性操作默认保护文件、可预览、可审计、可重试；优先调用 qBittorrent/rclone/Jellyfin 等成熟 API，不重复造轮子。
- 视觉规范：暗色极光主题、仅 Lucide 图标、全程零 emoji。
- 注释只写「代码本身说不清的约束」（安全取舍、坑、协议约定），不写流水账。

## 3. 提交规范（Conventional Commits）

格式：`<type>(<scope>): <描述>`，描述用中文或英文均可，一句话说清做了什么。

| type | 用途 |
|------|------|
| `feat` | 新功能 |
| `fix` | 缺陷修复 |
| `docs` | 仅文档 |
| `refactor` | 不改行为的重构 |
| `test` | 仅测试 |
| `chore` | 构建/工具/依赖 |
| `ci` | 流水线配置 |
| `perf` | 性能优化 |

示例：`feat(settings): 设置页增加在线检查更新`、`fix(api): 回收站同名恢复返回 409`。

- 一次提交只做一件事；混合多类改动时拆分提交。
- 破坏性变更必须在正文标注 `BREAKING CHANGE:` 并说明迁移方式。

## 4. 版本规范（SemVer）

版本号单一来源是仓库根目录的 `VERSION` 文件（`MAJOR.MINOR.PATCH`），运行链路全部从它派生：

- 后端启动时读取 `VERSION`（可用环境变量 `AURORA_VERSION` 覆盖），暴露在 `/api/health`、`/api/info`；
- 前端构建时由 Vite 注入 `__APP_VERSION__`，显示在设置页；
- `frontend/package.json` 的 `version` 字段必须与 `VERSION` 一致（CI 校验）；
- 容器镜像通过 `build-arg APP_VERSION` 写入 OCI label。

升级规则：

| 变更 | 升级 | 示例 |
|------|------|------|
| 不兼容的配置/接口变更、需要数据迁移 | MAJOR | 1.0.0 → 2.0.0 |
| 向后兼容的新功能（如新增 API、新页面） | MINOR | 0.4.0 → 0.5.0 |
| 缺陷修复、文档、内部重构 | PATCH | 0.4.0 → 0.4.1 |

`0.x` 阶段允许在 MINOR 中携带破坏性变更，但必须在 CHANGELOG 标注迁移说明。

## 5. 发布流程（checklist）

1. 按 SemVer 更新根目录 `VERSION` 与 `frontend/package.json` 的 `version`（两者一致）。
2. 在 `CHANGELOG.md` 顶部新增 `## YYYY-MM-DD` 小节，写面向用户的变更与验证结果。
3. 同步 `FEATURES.md`（功能现状）与 `TODO.md`（只留未完成事项）。
4. 确认 CI 全绿（后端测试、前端 lint/build、版本一致性、镜像构建冒烟）。
5. 提交并打标签：`git tag vX.Y.Z && git push origin main vX.Y.Z`。
6. Release 流水线自动：校验 tag 与 VERSION 一致 → 构建多架构镜像推送 GHCR（`ghcr.io/lengxiv/aurora:0.Y.Z`、`:0.Y`）→ 用 CHANGELOG 最新小节创建 GitHub Release。
7. 部署机更新：`docker compose pull aurora && docker compose up -d aurora`（或 systemd 方式 `git pull` + 重新构建，见 `docs/DEPLOYMENT.md`）。

## 6. 分支与 PR

- `main` 保持可部署：任何时刻 CI 应该是绿的。
- 功能开发用短命分支 + PR（即使单人维护），PR 描述写清动机、方案取舍和验证结果。
- 一个 PR 只解决一个问题；超过 ~500 行的重构先开 issue 对齐方案。

## 7. 文档同步义务

- 每完成一个功能/修复，同步更新 `CHANGELOG.md`、`FEATURES.md`（若是新功能）和 `TODO.md`（勾掉完成项）。
- 新增环境变量、端口、目录或部署方式，必须同步 `.env.example`、`docs/DEPLOYMENT.md`、`compose.yaml` 注释。
- 引入新外部依赖需在 PR 中说明理由与替代方案。

## 8. 安全红线

- 任何 token、密码、`.auth`、`.secret`、`backend/data`、`qbit/`、`jellyfin/` 不进 Git（详见 `docs/GITHUB.md` 与 `docs/SECURITY.md`）。
- 端口暴露约定：Aurora/qBittorrent/Jellyfin 管理端口只绑 `127.0.0.1`，公网只经 Nginx 443；BT 对等端口除外。
- 依赖升级注意审查供应链；锁定版本的变更必须在 CHANGELOG 记录。

## 9. 容器与部署规范

- 镜像内不保存状态：状态在 `/var/lib/aurora`，媒体在挂载卷；重建容器不丢数据。
- 基础镜像按大版本钉住（`python:3.11-slim`、`node:22-alpine`）；应用镜像按 semver 标签发布，**生产不要跟 `:latest`**。
- 修改 `Dockerfile`、`compose.yaml` 或依赖后，本地先 `docker compose build aurora` 验证，CI 也会做镜像冒烟测试。
