# Aurora Media Hub 前端

这是 Aurora Media Hub 的 React + TypeScript + Vite 前端，负责登录、网盘中转、磁力调度、流媒体播放和运行监控等界面。

## 本地开发

在项目根目录执行：

```bash
npm --prefix frontend install
npm --prefix frontend run dev
```

生产构建：

```bash
npm --prefix frontend run build
```

构建产物会输出到 `backend/static/`。完整部署说明见根目录的 [`docs/DEPLOYMENT.md`](../docs/DEPLOYMENT.md)。

## 目录说明

- `src/views/`：主要业务页面
- `src/components/`：通用界面组件
- `src/lib/api.ts`：后端 API 客户端
- `src/auth.tsx`：登录状态管理
