# Coding Plan 激活码租用系统

腾讯云 Coding Plan 的 API 激活码租用平台。管理员生成激活码 → 顾客自助激活 → API 代理转发 → 过期作废。

## 功能

- **激活码系统**：一人一码，独立 API Key，用后即废
- **多供应商**：支持腾讯云 / OpenAI / DeepSeek / 通义千问等，根据模型名自动路由
- **计时计费**：按时长 + 按调用次数双重计费，0.6元/小时
- **防检测**：单用户轮换、冷却期、UA 轮换、请求间隔模拟
- **管理后台**：控制台概览、生成激活码、租用记录、用户管理、供应商管理
- **顾客自助页**：输入激活码 → 获取 API 凭证 → 实时倒计时 → 过期失效

## 快速开始

```bash
cd backend
pip install -r requirements.txt
python server.py
# 访问 http://localhost:8899
```

## 部署

参见 `start.sh` — systemd + Nginx 反向代理部署脚本。

## 管理员登录

账号 `chengjian`，默认密码部署后修改。

## 技术栈

- 后端：Python FastAPI + SQLite
- 前端：纯 HTML/CSS/JS（零依赖）
- 代理：OpenAI 兼容接口转发
