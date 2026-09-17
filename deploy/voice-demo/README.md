# 玲玲师傅语音演示部署

这套部署只把语音演示暴露到服务器本机 `127.0.0.1:18080`。公网入口应由
服务器已有的 Nginx、Caddy 或其他反向代理转发到该端口，因此不会占用其他
服务的域名或容器端口。

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
3. 运行 `./deploy/voice-demo/generate-env.sh <服务器公网IP>`。
4. 恢复现有演示数据库，并把当前 `YISHUI_VOICE_DEMO_TOKEN` 与
   `OSS_JWT_SECRET` 安全写入 `deploy/voice-demo/.env`。
5. 运行 `./deploy/voice-demo/deploy.sh`。
6. 将服务器现有 HTTPS 反向代理的独立主机名
   `<公网IP>.sslip.io` 转发到 `http://127.0.0.1:18080`。

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

