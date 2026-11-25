# Tsec Supervisor

供主办方自建的 **TsecBench 本地模式赛事成绩面板**。选手在 Chrome 扩展中同意上报后，主办方可以在浏览器中查看实时排名、得分趋势和账号变更等复核线索。每场比赛使用自己的服务地址、数据库和参赛令牌。

扩展适配 `tsecbench.zc.tencent.com` 的跑分接口，后端使用 FastAPI 和 SQLite。成绩来自选手浏览器的观测，主办方应结合平台记录复核争议成绩。其他平台需要单独适配采集接口。

## 在自己电脑上试一场

需要 Python 3.12+，选手端需要 Chrome 111+。以下服务端命令适用于 macOS 或 Linux。在仓库根目录执行：

```bash
cd server
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
cp .env.example .env
python -c 'import secrets; print("TSEC_ADMIN_TOKEN=" + secrets.token_urlsafe(32)); print("TSEC_IP_HASH_SALT=" + secrets.token_hex(32))' >> .env
uvicorn app.main:app --env-file .env --host 127.0.0.1 --port 8000 --no-proxy-headers
```

1. 打开 <http://127.0.0.1:8000>，进入「管理控制台」，用本机 `server/.env` 中的 `TSEC_ADMIN_TOKEN` 登录。
2. 创建一个测试选手，令牌字段留空即可生成随机令牌。复制新令牌。批量导入时每行填写显示名和唯一编号，例如 `选手甲 team-01`。
3. 在同一台电脑打开 `chrome://extensions`，开启开发者模式，选择「加载已解压的扩展程序」，选中仓库的 `extension/`。
4. 在扩展设置中填入**选手令牌**，勾选同意与启用，点击「保存并测试连接」。核对返回的选手名和编号。
5. 在同一 Chrome 配置中登录 TsecBench，打开本地模式跑分页面。产生首条跑分数据后，在公开面板查看成绩，在管理台核对账号绑定。

## 给自己的比赛使用

1. 准备一个 HTTPS 域名，按[部署手册](docs/OPERATIONS.md)安装服务并设置赛事名称、计分时段和题集。每场独立比赛使用单独的 SQLite 数据库。
2. 在仓库根目录执行以下命令，把示例地址换成实际的面板地址：

   ```bash
   python3 scripts/build_extension.py --server-url https://scores.example.org
   ```

3. 将 `dist/` 生成的扩展 ZIP 发给选手，分别下发参赛令牌。ZIP 内已写入赛事地址和相应的主机权限。选手解压安装后可在设置页核对目的地址。[选手安装说明](extension/README.md)
4. 正式开赛前，用一名测试选手走通「身份验证 → 上报 → 出榜」。如需清空彩排成绩，可为正式比赛指定新的数据库并重新签发选手令牌。

## 赛事配置

| 环境变量 | 用途 |
| --- | --- |
| `TSEC_EVENT_NAME` | 公开面板的赛事标题 |
| `TSEC_SCORING_START`、`TSEC_SCORING_END` | 分别限制跑分开始时间的下界（含）和上界（不含）。支持分别留空。有截止时间时公开成绩按截止点取值。填写带时区的 ISO 8601 时间，例如 `2030-05-18T09:00:00+08:00` |
| `TSEC_SCORING_SET_NAME` | 指定参与排名的题集名称，留空允许所有题集 |
| `TSEC_STORE_RAW_IP` | 默认 `false`。默认保存加盐的 IP 摘要用于比较来源变化 |
| `TSEC_RETENTION_DAYS` | 跑分、事件及设备等明细的保留天数，默认 30 天，范围 1–365 |
| `TSEC_IP2REGION_PATH`、`TSEC_EXPECTED_PROVINCE` | 可选的本地 xdb 文件路径和期望省份。当前地域核验需要开启原始 IP 保存；默认关闭 |
| `TSEC_DB_PATH` | SQLite 文件路径。服务账号需要能写入所在目录 |

公开页展示选手显示名、TsecBench 用户名、Agent、成绩及核验状态。首次观测到的用户名与参赛令牌绑定，后续账号变化进入管理台供人工核对。[数据字段](docs/DATA_CONTRACT.md) · [参赛者隐私说明](docs/PRIVACY.md)

## 开发与打包

在上面创建的虚拟环境中，从仓库根目录运行：

```bash
pip install -e './server[dev]'
(cd server && python -m pytest)
node --test extension/tests/*.mjs
python scripts/build_release.py
```

服务端源码包从当前 Git 提交生成。主办方应使用自己的地址单独生成选手 ZIP。

项目采用 [MIT 许可证](LICENSE)。内置的 ip2region Python 查询代码保留[上游许可证与版权声明](server/app/ip2region/LICENSE)，仓库不附带 IP 地址库。
