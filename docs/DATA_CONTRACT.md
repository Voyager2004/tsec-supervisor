# 数据合同与禁止字段

## 允许上传

- 参赛绑定：安装实例 UUID、扩展版本。
- 账号标识：TsecBench 用户名、组织、Agent 名称。首次观测的用户名绑定到选手 Token，后续变更会告警而不覆盖。
- 任务标识：run ID、题集 ID/名称、任务状态、运行模式。
- 成绩：当前积分、满分、最终分、完成率、Flag/题目完成数、能力维度分和服务端接收时间点。
- 事件：平台事件 ID、题目代号/名称、分值变化、累计分和时间。
- 监督元数据：页面可见性、心跳时间、粗粒度设备信息。
- 网络来源：服务端从请求连接获取的公网 IP。

扩展使用主办方单独签发的选手令牌作为汇总请求的 Authorization 请求头。

## 永不上传

- TsecBench 的 Authorization、Bearer Token、Cookie、密码和 OAuth 码。
- BENCHMARK_TOKEN、BENCHMARK_BASE_URL、VPN 配置。
- Flag 原文、模型 API Key、环境变量、上传包地址。
- container_addr、靶场内网地址、沙箱日志、LLM 对话与 Trace 原文。
- Local Storage、Session Storage、浏览历史或其他页面内容。

## 公开与管理员视图

- 公开：排名、选手显示名、TsecBench 用户名、Agent、成绩、状态、更新时间、核验标签。
- 管理员：以上字段，加来源 IP、安装实例、粗粒度设备信息和风险标记。
