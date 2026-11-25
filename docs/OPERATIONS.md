# 主办方部署手册

以下以带 systemd 的 Linux 主机为例。准备 Python 3.12+（含 venv）、Git、Caddy 和一个指向该主机的域名。确保外部能通过 80/443 端口访问 Caddy。示例使用 `scores.example.org`，请换成自己的域名。

## 安装服务

首次安装时执行：

```bash
sudo useradd --system --user-group --home-dir /var/lib/tsec-supervisor --shell /usr/sbin/nologin tsec-supervisor
sudo install -d -o tsec-supervisor -g tsec-supervisor -m 0750 /var/lib/tsec-supervisor
sudo git clone https://github.com/Voyager2004/tsec-supervisor.git /opt/tsec-supervisor
cd /opt/tsec-supervisor
sudo python3 -m venv venv
sudo venv/bin/pip install ./server
sudo install -m 0600 deploy/tsec-supervisor.env.example /etc/tsec-supervisor.env
sudo install -m 0644 deploy/tsec-supervisor.service /etc/systemd/system/tsec-supervisor.service
```

用 `sudoedit /etc/tsec-supervisor.env` 设置赛事名称和计分规则。分别运行两次 `python3 -c 'import secrets; print(secrets.token_hex(32))'`，将两个不同结果分别填入 `TSEC_ADMIN_TOKEN` 和 `TSEC_IP_HASH_SALT`。前者供管理员登录，后者应在整场比赛保持稳定。

配置样例（请替换为本场的实际值）：

```ini
TSEC_EVENT_NAME="春季 Agent 挑战赛"
TSEC_SCORING_START=2030-05-18T09:00:00+08:00
TSEC_SCORING_END=2030-05-18T12:00:00+08:00
TSEC_SCORING_SET_NAME="示例题集"
```

## 启用 HTTPS

将仓库 `deploy/Caddyfile` 中的站点块加入主机的 `/etc/caddy/Caddyfile`，替换示例域名。模板将请求转发给本机 8000 端口。服务的默认可信代理为本机，其他网络拓扑要相应设置 `TSEC_TRUSTED_PROXIES`。

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now tsec-supervisor
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl enable --now caddy
sudo systemctl reload caddy
curl --fail https://scores.example.org/healthz
```

响应中的 `status` 应为 `ok`。打开域名根路径，核对标题、题集和计分时段。修改环境文件后运行 `sudo systemctl restart tsec-supervisor`。

## 分发与赛前核对

在你可写的仓库副本中运行 `python3 scripts/build_extension.py --server-url https://scores.example.org`。把生成的 ZIP 与选手安装说明一同分发。大屏域名更换后重新打包，提醒选手重新核对连接与授权。

- 在管理控制台创建选手。批量导入格式为「显示名 空格 编号」。编号支持 2–40 位英文字母、数字、下划线或连字符，重复编号会跳过。生成的令牌只显示一次，分别下发给对应选手。
- 用一个测试身份登录扩展，核对测试连接返回的姓名、编号以及管理台显示的 TsecBench 绑定账号。
- 打开 TsecBench 本地模式跑分页面并产生一次有效观测，确认成绩出现在公开榜。若管理台有记录但没出榜，请核对题集、跑分开始时间和选手启用状态。
- 管理台的设备、账号与来源变化标记供复核。令牌遗失时使用「换 Token」并单独下发，新令牌签发后旧令牌失效。

## 运维

查看服务日志可使用 `journalctl -u tsec-supervisor -n 100 --no-pager`。默认 SQLite 路径为 `/var/lib/tsec-supervisor/supervisor.sqlite3`，备份运行中的数据库请使用 SQLite 的在线备份接口或 `sqlite3` 的 `.backup` 命令。备份、令牌文件、IP 地址库和赛事原始数据均应存放在私有位置。

服务启动后及运行期间每 6 小时执行一次保留期清理。参赛者注册记录独立保留，管理员可通过控制台删除选手及关联记录。
