# 玲玲师傅语音演示部署

这套部署只把内部语音演示网关暴露到服务器本机 `127.0.0.1:18080`，并由
独立 Caddy 容器在 `80/443` 为 `<公网IP>.sslip.io` 自动申请 HTTPS 证书。
数据库、缓存、对象存储、API 和 UI 均不开放主机端口。

公网路由经过白名单限制：

- `/`：玲玲师傅演示页（内部渲染 `/voice-demo`）
- `/_next/*`、`/embed/dograh-widget.js`、暖场音频：演示静态资源
- `/api/config/version`：演示运行时配置
- `/api/v1/public/embed/*`：公开嵌入会话接口
- `/api/v1/ws/public/signaling/*`：语音 WebSocket

原 `/voice-demo`、Dograh 首页、登录、设置、普通 API 和 MinIO 管理端均返回
404，不会对公网开放。

## 首次部署

1. 在服务器安装 Docker Engine、Docker Compose v2 和 Git。
2. 克隆仓库到 `/opt/mailinlin`。
3. 检查已纳入版本管理的 `deploy/voice-demo/server.env` 公网参数；换服务器时
   更新该文件。运行 `./deploy/voice-demo/generate-env.sh <服务器公网IP>` 生成
   私密 `.env`，或迁移现有演示的 `.env`。
4. 恢复现有演示数据库，并把当前 `YISHUI_VOICE_DEMO_TOKEN` 与
   `OSS_JWT_SECRET` 安全写入 `deploy/voice-demo/.env`。
5. 确保已加载 `server.env` 中 `DOGRAH_API_IMAGE` 对应的 Dograh API
   基础镜像；该 ECS 使用预加载镜像，避免国内网络直接拉取 GHCR 失败。
6. 运行 `./deploy/voice-demo/deploy.sh`。
7. 确认 `<公网IP>.sslip.io` 可访问。若服务器未来启用共享反向代理，可停用
   `edge` 服务，再把该主机名转发到 `http://127.0.0.1:18080`。

`.env`、数据库备份、私钥和证书都不得提交到 Git。

## 防火墙 / 阿里云安全组

HTTPS 入口需要 TCP `80`、`443`。WebRTC 语音还需要：

- TCP + UDP `3478`
- UDP `49152-49200`

PostgreSQL、Redis、MinIO、API 和 UI 都没有公网端口。

## 更新

```bash
cd /opt/mailinlin
git pull --ff-only
./deploy/voice-demo/deploy.sh
```
