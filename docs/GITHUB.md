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
