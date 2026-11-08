# 家人位置

老人本人在安卓端同意后，手机通过带常驻通知的定位服务采集位置。每个点先进入手机上的待传队列，再由设备向服务端上报。家人打开微信小程序时读取最近的有效位置、精度和采集时间。超过 10 分钟的点会显示“位置已过期”。

## 本地模拟环境

本轮只使用 Android 模拟器、两个测试家庭和本机服务。家人小程序的网络入口限定在微信开发者工具。安卓调试包通过模拟器的宿主机地址访问同一服务。真实手机、微信发布及 OriginOS 的省电和耗电验收留在后续阶段。

1. 使用 Docker 在宿主机的 `127.0.0.1:55433` 建立 PostgreSQL 17 专用库 `dingwei_qa`。用随机生成的数据库密码；数据库、账号文件、SDK Key 和 APK 均留在仓库外。
2. 使用 Python 3.12 安装 `./backend[dev]`。将 `DATABASE_URL` 指向上述专用库（SQLAlchemy 驱动名为 `postgresql+psycopg`）。在 `backend` 目录运行 `python -m alembic upgrade head`。从仓库根目录运行 `python checks/qa_families.py <仓库外的账号文件>` 初始化两个模拟家庭，只生成账号和老人资料。
3. 在 `backend` 目录运行 `python -m uvicorn app.main:app --host 127.0.0.1 --port 18763`。家人通过微信开发者工具导入仓库根目录，使用测试家庭账号登录并获取绑定码。本机调试需要在该项目的本地设置允许访问回环 HTTP 服务。
4. 安卓端使用 JDK 17 和 Android SDK 36。从本机环境提供与测试包和签名匹配的 `AMAP_ANDROID_KEY`，在 `android/elder-app` 运行 `gradlew assembleDebug`。只向已核对名称、系统版本和控制端口的 `Dingwei_Issue3` 模拟器安装。授予精确位置、后台位置和通知权限，在应用内输入绑定码并同意共享。
5. 关闭家人小程序。用模拟器的系统 GPS 控制台播放合成路线，在服务端核对采集时间与收到时间。回到家人端时会立即查询，留在前台时每 30 秒查询一次。点“立即定位”后，应用单独显示本次请求的结果。老人可以在运行状态页停止共享；断网撤销会先停止本地采集，联网后通知服务端。

已实测完成隔离模拟器的 24 小时长测，64 个窗口全部通过。移动最长收点空窗 33.73 秒，断网恢复最慢 0.96 秒开始补传。完整数据和时钟差处理见下方验收记录。重启后首次解锁前的观察期内没有收点，vivo / iQOO 的真实省电策略仍需要该手机验收。

## 检查与证据

| 环节 | 检查命令 |
| --- | --- |
| 家人首页、登录后的查询与时效 | 仓库根目录运行 `node --test checks/*.test.mjs` |
| 服务端入库、去重、鉴权、拒收与撤销 | 在 `backend` 运行 `python -m ruff check app tests`、`python -m pytest -q` |
| 安卓位置服务和队列 | 在 `android/elder-app` 运行 `gradlew lintDebug testDebugUnitTest assembleDebug` |
| 真实模拟器收点 | `checks/locked_route.py` 从系统控制台注入路线、检查锁定状态，并以只读连接观察 PostgreSQL。按场景保存时间窗口、队列和接收记录。 |
| 生命周期与长测 | `checks/process_recovery.py` 分开测进程退出、重启和强停；`checks/soak.py` 按实际时钟执行移动、静置和断网窗口。 |

[逐场景模拟器实测及截图](docs/evidence/issue-3/README.md) · [本轮验收计划](docs/plans/issue-3-location-acceptance.md) · [架构决策](docs/adr/0002-location-delivery.md) · [问答记录](docs/问答记录.md) · [分支自动检查](https://github.com/damascenosteven-oss/dingwei/actions/workflows/check.yml)
