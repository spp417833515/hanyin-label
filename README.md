# 汉印标签打印 (HanyinLabel)

给 **汉印 HPRT HM-T260L** 标签打印机用的 Windows 桌面程序：本地 Web 界面设计 / 打印标签，同时提供 HTTP API，局域网里的其他程序也能调用打印。

- 整张标签在电脑上渲染成 203 DPI 位图再发给打印机：**预览 = 实际打印效果**，中文不依赖打印机字库
- 打印协议是从官方 Windows 驱动的输出逆向出来的私有光栅格式（**不是 TSPL**），USB 和蓝牙都能打
- 服务端只用 Python 标准库 (`http.server`)，依赖仅 Pillow / pywin32 / qrcode
- 打包成单个安装包（PyInstaller + Inno Setup），当前用户安装，不需要管理员权限

> 官方软件「汉码」(`HMark.exe`) 只作对照参考；这个项目是独立实现，不依赖它。

---

## 目录

- [功能概览](#功能概览)
- [目录结构](#目录结构)
- [运行](#运行)
- [界面](#界面)
- [模板与元素](#模板与元素)
- [变量](#变量)
- [HTTP API](#http-api)
- [打印协议](#打印协议)
- [打包发布](#打包发布)
- [命令行工具 label.py](#命令行工具-labelpy)
- [开发注意事项](#开发注意事项)
- [已知问题 / 待办](#已知问题--待办)

---

## 功能概览

| 功能 | 说明 |
|---|---|
| 标签设计 | 三栏工作台：模板列表 / 画布 / 属性面板。元素可拖动、8 个手柄改尺寸、Del 删除、Ctrl+D 复制、方向键微调、Ctrl+Z / Ctrl+Y 撤销重做 |
| 元素类型 | 文本、二维码、线条（实心矩形）、方框（空心矩形） |
| 变量 | `{日期}` `{日期+30天}` `{时间}` `{流水号}` 自动填，`{品名}` 等打印时填写；流水号打印成功后自动递增 |
| 批量打印 | API 一次传多行变量，每行指定份数，单次最多 500 张 |
| 连接方式 | 顶部全局切换 USB / 蓝牙；USB 每 2 秒检测在线，蓝牙按需检测 |
| 打印机校准 | 放纸指引、X/Y 偏移校准（所有打印都套用）、走纸、经蓝牙读写打印机参数（纸张类型 / 浓度 / 休眠 / 自动关机） |
| HTTP API | 本机界面免密钥，其他调用方必须带密钥；局域网访问可开关；调用记录可查 |
| 托盘 | 关掉窗口后服务在托盘继续运行；右键菜单：打开 / 设置 / 开机启动 / 退出 |
| 开机自启 | `HKCU\...\Run\HanyinLabelPrint`，以 `--tray` 静默启动 |

---

## 目录结构

```
标签打印/
├── server.py          HTTP 服务: 路由表、鉴权、静态文件、托盘/窗口启动、局域网开关时重绑地址
├── core.py            业务: 设置 / 尺寸预设 / 模板与变量 / 渲染 / 打印 / 校准 / 打印机参数 / 密钥 / 开机启动
├── printer.py         打印协议编码 (LZO1X 光栅) + 三个通道 (驱动 GDI / USB RAW / 蓝牙串口) + V1 参数指令
├── label.py           字体、二维码绘制、presets.json 读写; 同时是早期的命令行打印工具
├── tray.py            托盘图标 (pywin32, 无额外依赖)
├── presets.json       打印机配置: 驱动队列名、USB 设备 ID、蓝牙 COM 口、DPI
├── requirements.txt
├── 启动标签打印.bat   开发版启动脚本 (GBK + CRLF, 不要改编码)
├── 界面方案.html      最初 5 套界面方案的设计稿 (选定方案 A 三栏工作台), 仅作参考
├── web/               前端 (纯原生 JS, 无构建步骤)
│   ├── index.html     单页: 标签设计 / 打印机校准 / 设置 / API 与密钥 四个页签
│   ├── common.js      请求封装、提示、变量规则 (必须和 core.expand 一致)、打印机状态
│   ├── editor.js      标签设计画布
│   ├── printerview.js 打印机校准页
│   ├── apiview.js     API 页 (服务开关 / 密钥 / 调用记录 / 文档)
│   ├── settingsview.js 设置页
│   ├── main.js        页签路由
│   ├── style.css
│   ├── api.html       旧 API 页地址, 跳转到 /#api/service
│   └── favicon.ico / icon.png   由 build/make_icon.py 生成
├── build/
│   ├── build.ps1      一键构建: 图标 → exe → 安装包
│   ├── make_icon.py   生成 web/favicon.ico、web/icon.png
│   ├── HanyinLabel.spec / version_info.txt   PyInstaller 配置
│   ├── installer.iss  Inno Setup 脚本
│   └── ChineseSimplified.isl   Inno Setup 简体中文语言文件 (必须 UTF-8 BOM)
└── data/              开发版数据目录 (不进 git, 见下)
```

不进仓库的内容（见 `.gitignore`）：`data/`、`build/.venv`、`build/dist`、`build/output`、`build/work`、构建日志、`__pycache__`、`preview.png`。

---

## 运行

### 环境

- Windows 10/11，Python 3.12（开发用 3.12.9）
- 依赖：`pip install -r requirements.txt`（Pillow、pywin32、qrcode）
- 打印机驱动：USB 打印需要装官方驱动 *HPRT HM-T260L*（队列名要和 `presets.json` 里 `printer.queue` 一致）
- 蓝牙：先在 Windows 里配对打印机，系统会生成两个蓝牙串口，一般编号小的能用；在「设置」页选择 COM 口
- 字体：微软雅黑 `msyh.ttc` / `msyhbd.ttc`，没有则用黑体 `simhei.ttf`

### 开发版

```bat
启动标签打印.bat                    :: = pythonw server.py --open, 打开程序窗口并驻留托盘
python server.py --no-tray          :: 前台运行, 日志打到控制台, Ctrl+C 停止
python server.py --tray             :: 只在托盘后台运行 (开机自启用的就是这个)
python server.py --port 9000        :: 换端口 (默认 8765, 见 settings.json)
```

- 服务地址 `http://127.0.0.1:8765/`，程序窗口是 Edge/Chrome 的 `--app` 模式窗口（找不到就用默认浏览器）
- 已经在运行时再启动只会打开窗口，不会起第二个服务（通过 `/api/ping` 判断）
- 开发版数据在项目里的 `data/`，安装版数据在 `%LOCALAPPDATA%\HanyinLabel\data`，两者互不影响

### 安装版

运行 `build\output\汉印标签打印-安装-<版本>.exe`：

- 安装到 `%LOCALAPPDATA%\Programs\HanyinLabel`，可选桌面快捷方式和开机自启
- 用户数据在 `%LOCALAPPDATA%\HanyinLabel\data`，**卸载时保留**
- 前端打包在 exe 里，**改了 web/ 也要重新构建并覆盖安装才生效**

### 数据文件

| 文件 | 内容 |
|---|---|
| `settings.json` | 端口、局域网开关 `lan`、连接方式 `channel` (`auto`/`usb`/`driver`/`bt`)、尺寸预设 `sizes`、偏移校准 `offset`、蓝牙口 `bt_port` |
| `templates.json` | 模板列表。首次运行写入 5 个示例模板；旧版"每行文字"模板读取时自动迁移成元素 |
| `keys.json` | API 密钥，**只存 SHA-256**，明文只在生成时显示一次 |
| `server.log` | 服务日志（1 MB 轮转，保留 2 份） |
| `stdout.log` | pythonw 下没有控制台时的 stdout/stderr |

---

## 界面

单页应用，顶部四个页签（URL hash 路由）：

| 页签 | 地址 | 内容 |
|---|---|---|
| 标签设计 | `#design` | 左：模板列表；中：画布（按 mm 比例显示，所见即所得）；右：元素属性 / 变量填写 / 份数 / 打印。打印机未连接时打印按钮禁用 |
| 打印机校准 | `#printer` | 放纸指引；打印校准标签（四边细框 + 中心十字），调 X/Y 偏移直到四边都能看到；走纸；经蓝牙读写打印机参数 |
| 设置 | `#settings` | 开机启动、蓝牙串口选择、打开数据文件夹、退出程序 |
| API 与密钥 | `#api/service` `#api/keys` `#api/calls` `#api/docs` | 局域网开关与地址、生成/撤销密钥、最近 200 条调用记录、接口文档与在线试调 |

画布快捷键：拖中间移动 · 拖四角/四边改大小（文本拖上下边改字号，二维码只有四角且保持正方形）· `Del` 删除 · `Ctrl+D` 复制 · 方向键微调 0.5 mm（`Shift` 2 mm，`Alt` 0.1 mm）· `Ctrl+Z` 撤销 · `Ctrl+Y` / `Ctrl+Shift+Z` 重做 · `Ctrl+S` 保存 · `Ctrl+P` 打印 · `Esc` 取消选中。

窗口变窄时三栏按 1480 / 1340 / 1180 px 分档收起。

---

## 模板与元素

坐标、尺寸单位都是 **mm**，原点在标签左上角。标签宽 10~50 mm（打印头 48 mm = 384 点，超出部分裁掉），高 8~200 mm。

```jsonc
{
  "id": "prod",
  "name": "生产日期标签",           // 名称唯一, API 可以用名称代替 id
  "size": [25, 15],                // [宽, 高] mm; 宽 = 横跨打印头方向, 高 = 出纸方向
  "elements": [ ... ],             // 最多 40 个
  "fields": {"品名": "不锈钢螺丝"}, // 变量默认值 (只保留模板里实际出现的变量)
  "serial": 1,                     // 下一个流水号
  "serial_step": 1
}
```

| 类型 | 字段 | 说明 |
|---|---|---|
| `text` | `x y w text pt bold align shrink` | 字号 `pt` 固定（3~72），框高 = `pt × 0.3528 × 1.25` mm；`align` = `left`/`center`/`right`；`shrink=true` 时内容超过框宽才缩小，**不会自动放大或换行** |
| `qr` | `x y size data` | 正方形，纠错等级 M；`data` 里同样可以写变量 |
| `rect` | `x y w h` | 实心矩形，细的就是线 |
| `box` | `x y w h t` | 空心方框，`t` 为线宽 |

渲染流程：`core.render_label()` 用 Pillow 画灰度图 → 阈值二值化成 `1` 模式 → `printer.encode_job()` 编码。预览接口返回的 PNG 和打印出来的位图是同一张。

---

## 变量

模板文字（以及二维码内容）里用 `{...}` 写变量。前端 `web/common.js` 和后端 `core.expand()` 实现同一套规则，**改一处必须同步改另一处**。

| 写法 | 结果 | 说明 |
|---|---|---|
| `{日期}` | `2026-10-13` | 打印时的日期 |
| `{日期:yyyy/MM/dd}` | `2026/10/13` | 冒号后是格式：`yyyy MM dd HH mm ss` |
| `{日期+30天}` / `{日期-7天}` | 推算日期 | 保质期、到期日 |
| `{时间}` / `{时间:HH:mm:ss}` | `14:05` | 默认 `HH:mm` |
| `{流水号}` / `{流水号:000000}` | `0001` / `000001` | 默认 4 位补零；格式的长度 = 位数。每张单独渲染，打印成功后才写回下一个值 |
| `{品名}` | 填写值 | 其他名字都是"打印时填写"的变量，没填会报缺少变量 |
| `{输入:品名}` | 同 `{品名}` | 兼容写法 |

---

## HTTP API

默认地址 `http://127.0.0.1:8765`，开启局域网访问后监听 `0.0.0.0`。所有响应都是 JSON：成功 `{"ok": true, ...}`，失败 `{"ok": false, "error": "中文说明"}`；预览接口直接返回 `image/png`。

### 鉴权

| 调用方 | 判定 | 权限 |
|---|---|---|
| 本机界面 | 回环地址 **且** `Host` 是 `127.0.0.1`/`localhost` **且** 没有外站 `Origin` | 全部接口，免密钥 |
| 其他（局域网设备、本机脚本、别的网页） | — | 必须带密钥；只能用"公开"接口（查模板、预览、打印、状态） |

```
Authorization: Bearer hm_xxxxxxxx
X-API-Key: hm_xxxxxxxx          (二选一)
```

- 密钥在 API 页「密钥」里生成，格式 `hm_` + 32 位随机串，只显示一次
- 局域网访问关闭时：非本机请求一律 403；切换开关时服务会在 0.4 s 后重新绑定监听地址
- 带外站 `Origin` 的本机请求会被当作外部调用，防止别的网页借浏览器调用
- 状态码：400 参数错 · 401 缺/错密钥 · 403 无权限 · 404 模板不存在 · 503 打印机未连接 · 500 内部错误

### 接口

需要密钥（外部可调用）：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/ping` | 公开、无需密钥。`{"app":"hanyin-label","version":...}` |
| GET | `/api/status` | 打印机是否在线 / 能否打印 / 当前通道 / 服务地址 |
| GET | `/api/presets` | 尺寸预设 |
| GET | `/api/templates` | 模板列表（附带 `variables`：哪些自动填、哪些要填写） |
| GET | `/api/templates/{id或名称}` | 单个模板 |
| POST | `/api/preview` | 预览 PNG：`{template, fields, serial?, scale?}` 或 `{template_obj}` 预览未保存的模板 |
| GET | `/api/preview?template=..&scale=3&品名=..` | 同上，查询参数版，方便直接放进 `<img>` |
| POST | `/api/print` | 按模板打印，见下 |
| POST | `/api/print/text` | 不用模板，直接打几行字：`{lines, size?, qr?, copies?, align?, fields?, channel?}` |

只允许本机界面：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST / DELETE | `/api/presets`、`/api/presets/{w}x{h}` | 增删尺寸预设 |
| POST / PUT / DELETE | `/api/templates`、`/api/templates/{id}` | 新建 / 修改 / 删除模板 |
| GET / POST / DELETE | `/api/keys`、`/api/keys/{id}` | 列出 / 生成 / 撤销密钥 |
| GET / POST | `/api/settings` | 读写设置：`lan` `channel` `offset` `bt_port` `autostart` |
| GET | `/api/log` | 最近 200 条外部调用和所有打印调用 |
| GET / POST | `/api/calibrate/preview`、`/api/calibrate/print` | 校准标签预览 / 打印 |
| POST | `/api/printer/feed` | 走纸到下一张 |
| GET / POST | `/api/printer/params` | 经蓝牙读 / 写打印机参数 |
| POST | `/api/printer/learn` | 发送纸张学习指令 |
| POST | `/api/printer/bt-check` | 检测蓝牙能否连接 |
| GET | `/api/printer/ports` | 列出本机串口（标出蓝牙串口） |
| POST | `/api/admin/open-data` | 在资源管理器打开数据目录 |
| POST | `/api/admin/shutdown` | 停止服务 |

### 打印示例

```bash
# 单条: 模板可用 id 或名称; fields 覆盖模板默认值; copies 1~500
curl -X POST http://192.168.1.10:8765/api/print \
  -H "Authorization: Bearer hm_你的密钥" -H "Content-Type: application/json" \
  -d '{"template": "生产日期标签", "fields": {"品名": "不锈钢螺丝"}, "copies": 2}'

# 批量: 每行一组变量 + 份数, 总数不超过 500
curl -X POST http://192.168.1.10:8765/api/print \
  -H "Authorization: Bearer hm_你的密钥" -H "Content-Type: application/json" \
  -d '{"template": "prod", "rows": [{"fields": {"品名": "螺丝"}, "copies": 3}, {"fields": {"品名": "螺母"}}]}'
```

```json
{"ok": true, "printed": 4, "channel": "bt", "serial_next": 5}
```

`channel` 可选 `auto` / `usb` / `driver` / `bt`，不传则用设置里的连接方式。`auto`/`usb`/`driver` 打印前都会现查 USB，不在线直接返回 503，**不会自动改走蓝牙**。

---

## 打印协议

协议从官方驱动（HPRT HM-T260L, `hprtmobile.inf` 2.7.4.1）的实际输出逆向得到。**这台机器不认 TSPL，不要按 TSPL 调试。**

### 光栅打印

```
00 × 260                         唤醒 (打印机休眠时最前面的字节会丢)
1D 's' 'e' 't' 'p' 01            任务头
每张标签:
    若干块  1D 76 30 30  xL xH  yL yH  len(4 字节小端)  LZO1X 数据
            x = 每行字节数, 按 4 字节对齐; y = 本块行数 (驱动每块 10 行); 位 1 = 黑
    12 0C                        本张结束, 走到下一张标签起点
```

- 203 DPI，打印头 384 点 = 48 mm，标签靠打印头**左对齐**
- LZO1X 只用"单段字面量"编码（不压缩但是合法流），固件能正常解开；`printer.py` 里也带了完整的解压器和 `decode_job()`，用来分析驱动抓到的数据
- 偏移校准在编码时整体平移位图（可为负，移出去的部分裁掉），**页高始终等于标签高度**，不影响走纸定位
- 走纸：唤醒 + 任务头 + `12 0C`（不带光栅数据）

### 三个通道

| 通道 | 实现 | 说明 |
|---|---|---|
| `usb`（`auto` 同） | 自己编码字节流，经驱动队列以 `RAW` 数据类型发出 | 默认 USB 方式 |
| `driver` | Windows GDI 打印，纸张设成标签尺寸 | 和「汉码」走同一条路，只能 USB |
| `bt` | 同样的字节流写到蓝牙虚拟串口 | 默认 COM5，可在设置里改 |

USB 在线检测：WMI 查 `Win32_PnPEntity` 里 `DeviceID LIKE 'USBPRINT%'` 且包含 `HPRTHM-T260L`。

### 打印机参数（V1 指令，经蓝牙双向读写）

```
读:   1B 1C "& V1 getkey\r\n" 01 <编号 2B 小端> <长度 1B>          → <值>
写:   1B 1C "& V1 setkey\r\n" 01 <编号 2B 小端> <长度 1B> <值>     → <编号 2B> 00
保存: 1B 1C "& V1 do \"save_param_zone\"\r\n"                    → 00
```

| 编号 | 长度 | 参数 | 取值 |
|---|---|---|---|
| 4 | 16 | 机型 | ASCII |
| 206 | 1 | 纸张类型 | 2 标签纸（间隙）/ 0 连续纸 / 1 黑标纸 |
| 203 | 1 | 打印浓度 | 35 淡 / 55 偏淡 / 75 正常 / 95 偏浓 |
| 400 | 4 | 休眠等待（秒） | 60~600 |
| 401 | 4 | 自动关机（分钟） | 10~120 |

编号和取值范围来自汉码软件里 HM-T260L 的机型配置（`Config/DeviceKey.json`），均已实测。`do "paper_learn"` 打印机只响一声，是否真的重新学习纸张尚不确定。

---

## 打包发布

```powershell
.\build\build.ps1
```

1. 首次运行会建 `build\.venv` 并装 `pyinstaller pillow pywin32 qrcode`
2. `make_icon.py` 生成图标
3. PyInstaller onedir → `build\dist\HanyinLabel\`（web/ 和 presets.json 打进 `_internal`）
4. Inno Setup 6（用户级安装：`winget install --id JRSoftware.InnoSetup -e --scope user`）→ `build\output\汉印标签打印-安装-<版本>.exe`

注意：

- **改版本号要改三处**：`server.py` 的 `VERSION`、`build/installer.iss` 的 `AppVersion`、`build/version_info.txt`
- 火绒等杀毒软件会锁住刚生成的安装包，所以脚本先输出到 `%TEMP%` 再复制，失败自动重试 3 次
- `ChineseSimplified.isl` 必须是 **UTF-8 带 BOM**，否则安装界面中文乱码
- PyInstaller 把进度写到 stderr，PowerShell 5.1 会误判为错误，所以脚本用 `cmd /c` 包一层并逐步检查退出码
- 安装/卸载前会 `taskkill` 掉正在运行的托盘程序；卸载时删除开机自启项，保留用户数据

---

## 命令行工具 label.py

早期的命令行打印工具，使用 `presets.json` 里的预设（每行文字 + 可选左侧二维码的自动排版），现在主要用于调试协议。服务端也复用了它的字体和二维码绘制函数。

```bash
python label.py status                           # 检测打印机 (USB / 蓝牙)
python label.py preview "第一行" "第二行"         # 只生成 preview.png
python label.py print "第一行" "第二行" -n 3      # 打印 3 张
python label.py print -p 25x15-二维码 --qr https://example.com "扫码查看"
python label.py batch 列表.csv                    # 每行一张; 列 qr/二维码, copies/份数, 其余列为文字行
python label.py test                             # 自检标签 (边框 + 尺寸)
python label.py print ... --dry-run              # 只生成预览和 .prn 字节流, 不发送
```

注意 `label.py` 的 `--via auto` 是"有 USB 走驱动，否则蓝牙"，和服务端的 `auto` 不同。

---

## 开发注意事项

- **时区**：Windows 上残留的 `TZ` 环境变量会让 Python 算错本地时间（`{日期}`/`{时间}` 打错）。`server.py` 检测到 `TZ` 会去掉后重启自己，`启动标签打印.bat` 也会先清掉
- **变量规则两份实现**：`web/common.js` 与 `core.expand()` / `core.template_vars()` 必须一致
- **文本排版**：字号固定，不做自动放大 / 自动布局，只在超宽时缩小（用户明确不喜欢"自动变化"的排版）
- 业务逻辑放 `core.py`，`server.py` 只做路由、参数校验和鉴权
- 打印、蓝牙读写参数共用一把锁 `_print_lock`，避免并发写同一个设备
- 蓝牙不轮询（每次检测要建连接，还会占住打印机蓝牙），只在切换到蓝牙、打印前后检测并缓存结果
- `启动标签打印.bat` 必须保持 **GBK + CRLF**（`.gitattributes` 里设了 `-text`）

---

## 已知问题 / 待办

- 打印机约 20 分钟无操作会自动关机，USB 设备随之消失；已经通过参数指令把休眠改为 600 秒、自动关机改为 120 分钟，彻底的防休眠方案（软件定时保活等）待定
- 打印机开着时经 USB 走服务实打尚待完整验证（目前主要用蓝牙打印）
- `paper_learn` 指令效果未确认
- 纸卷没卡正会导致左右跑偏和歪斜，先按校准页的放纸指引装纸再调偏移
