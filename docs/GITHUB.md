# 发布到 GitHub

## 先做安全检查

本项目的运行数据、账号文件、网盘配置、媒体服务配置和下载内容不属于源码，已经由根目录 `.gitignore` 排除。不要执行 `git add -f` 强行加入这些目录。

如果任何 token、密码或私钥曾经被提交到 Git 历史，必须先在对应服务后台撤销并重新生成；仅从工作目录删除文件不能清除 Git 历史中的密钥。

## 创建仓库

建议第一次创建为 **Private** repository。确认代码和文档已经脱敏后，再按需要改为 Public。

在 GitHub 创建一个空仓库，例如 `aurora-media-hub`，不要勾选自动生成 README、`.gitignore` 或 License，避免首次合并冲突。

## 使用 SSH 推送

在本机生成 GitHub 专用密钥：

```bash
ssh-keygen -t ed25519 -C "your-email@example.com"
cat ~/.ssh/id_ed25519.pub
```

把公钥添加到 GitHub：`Settings -> SSH and GPG keys -> New SSH key`，然后验证：

```bash
ssh -T git@github.com
```

## 初始化并推送

```bash
cd /opt/aurora
git init -b main
git add .
git status --short
git diff --cached --check
git commit -m "Initial public release"
git remote add origin git@github.com:lengxiv/aurora.git
git push -u origin main
```

首次 `git status` 要确认没有出现以下内容：

```text
backend/.auth
backend/.secret
backend/data/
qbit/config/
qbit/downloads/
jellyfin/config/
frontend/node_modules/
backend/static/assets/
```

## 后续更新

```bash
cd /opt/aurora
git status
git add backend frontend docs deploy README.md FEATURES.md compose.yaml scripts tests .env.example .gitignore
git diff --cached --check
git commit -m "Describe the change"
git push
```

不要把生产服务器上的 `.env`、`backend/data`、`qbit`、`jellyfin` 或 `.auth` 复制到仓库。新服务器部署时，复制 `.env.example` 为私有环境文件，再单独恢复运行数据。

## 发版（Release）

发版规则与完整 checklist 见 [`CONTRIBUTING.md`](../CONTRIBUTING.md) 第 4、5 节。核心流程：

1. 更新根目录 `VERSION` 与 `frontend/package.json` 的 `version`（两者必须一致，CI 校验）。
2. 在 `CHANGELOG.md` 顶部新增 `## YYYY-MM-DD` 小节。
3. CI 全绿后打标签并推送：

```bash
git tag v0.4.1
git push origin main v0.4.1
```

4. Release 流水线（`.github/workflows/release.yml`）自动执行：
   - 校验 tag 与 `VERSION` 一致（不一致直接失败）；
   - 构建多架构镜像并推送 `ghcr.io/lengxiv/aurora:0.4.1` 与 `:0.4`；
   - 用 `CHANGELOG.md` 最新小节创建 GitHub Release。

首次发布前，在仓库 Settings → Actions → General 把 Workflow permissions 设为 "Read and write permissions"，否则推 GHCR 和创建 Release 会 403。镜像包默认私有，如需公开拉取，在包设置里把 visibility 改为 public。
