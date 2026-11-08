# 视频去水印 · 手机浏览器版

在手机上打开网页 → 粘贴视频链接 → 框住水印 → 导出 MP4 存到手机。

解析和转码在**你这台电脑**上跑（yt-dlp + ffmpeg），手机只负责显示和触控框选。
视频不经过任何第三方服务器。

> 不想开电脑？Android 手机可以自己跑，见「九、Android 手机上独立运行」。

---

## 一、启动（电脑上操作）

```bash
cd E:\testsoft\Lumii-data\outputs\video-downloader-web
python server.py
```

启动后会打印两个地址和一个二维码：

```
手机:   http://192.168.0.9:8848   <-- 同一 WiFi 下用这个
```

## 二、手机访问

1. 手机连**和电脑同一个 WiFi**
2. 浏览器打开上面那个地址（扫终端里的二维码也行）
3. 建议「添加到主屏幕」，下次一点就开

> 连不上九成是 Windows 防火墙挡了。管理员权限打开命令提示符，执行一次即可：
> ```cmd
> netsh advfirewall firewall add rule name="videowm-8848" dir=in action=allow protocol=TCP localport=8848
> ```

## 三、怎么用

1. **粘链接** —— 抖音/B站/小红的分享文本整段粘进去就行，会自动抠出链接
2. **等下载** —— 电脑端在下，手机上看进度条；B站 4 分钟 1080P 约 25 秒
3. **框水印** —— 拖绿框四个角，或者点预设（右下角 / 右上角 / 右侧 / 底部）
4. **选模式** —— 默认「模糊遮盖」，也可以切「裁剪画面」
5. **导出** —— 电脑端几秒到几十秒处理完，手机直接播放预览 + 保存

---

## 四、三种去水印方式

解析完会自动识别平台，然后三选一，点一下就能在第一帧上看到效果。

| 方式 | 做什么 | 什么时候用 |
|---|---|---|
| **标准裁切** | 按该平台的水印位置，把含水印那条边切掉 | 想彻底不见水印，不介意画面小一圈 |
| **标准打码** | 只把水印那一小块糊掉，画幅不动 | 要保原构图，尤其竖屏不能裁 |
| **自定义框选** | 自己拖框，框内可选裁切或打码 | 预设没盖住、水印在奇怪位置 |

### 预设在哪

全部集中在 `app.js` 顶部的 `WM` 表里，改一处全站生效：

```js
var WM = {
  douyin:   { box: {x:0.56, y:0.845, w:0.44, h:0.155}, cut:'bottom', portrait:true },
  kuaishou: { box: {x:0.58, y:0.845, w:0.42, h:0.155}, cut:'bottom', portrait:true },
  xhs:      { box: {x:0.50, y:0.870, w:0.50, h:0.130}, cut:'bottom', portrait:true },
  bilibili: { box: {x:0.845,y:0.012, w:0.150,h:0.085}, cut:'top',    portrait:false },
  tiktok:   { box: {x:0.56, y:0.845, w:0.44, h:0.155}, cut:'bottom', portrait:true },
  other:    { box: {x:0.56, y:0.845, w:0.44, h:0.155}, cut:'bottom', portrait:false }
};
```

- `box` 是水印所在矩形（相对画面的 0~1），打码就糊这块
- `cut` 是裁切时切哪条边：`top`/`bottom`/`left`/`right`
- 抖音/快手/小红书/TikTok 水印贴右下角，B站的 logo 在右上角
- 竖屏平台遇到横屏视频会自动收窄水印区

### 裁切的先天限制

矩形裁切切不出"L 形"。右下角有水印，你只能从右边切一条或者从下边切一条——构图必然变。
B站那种右上角的小 logo，切掉顶部 10% 左右就行，16:9 会变成 1.79:1，接近电影宽银幕，看不出来。

真要一个字都不损失，用**标准打码**。

---

## 五、支持的平台（2026-11-02 真实链接实测）

| 平台 | 电脑 | 手机 | 说明 |
|---|---|---|---|
| B站 | ✅ | ✅ | 普通投稿 1080P；番剧/大会员要在电脑上登录过 |
| 小红书 | ✅ | ✅ | 短链自动展开；必须走直连，否则跳登录页 |
| 抖音 | ✅ | ❌ | 需要无头浏览器，手机上用不了（见下） |
| 快手 / TikTok | 未实测 | 未实测 | 提取器在，TikTok 需要代理能通 |

### 一个重要的实测结论

**从这两个平台下到的原片本身就没有水印。**

实测方法：抽 6 帧做逐像素跨帧对比，找"每帧都不变"的固定元素。

| 平台 | 固定元素像素数 | 结论 |
|---|---|---|
| 小红书（720×1280） | 0（四角全干净） | 无水印 |
| 抖音（1080×1920） | 2（可忽略） | 无水印 |

原因：yt-dlp 拿的是 `sns-bak-v6.xhscdn.com` / `douyinvod.com` 这类 CDN 直链，那是**无水印版本**；带水印的是 App 内播放的地址。

也就是说，**"去水印"这件事在下载这一步就完成了**，裁剪和打码只是兜底——当你遇到确实带水印的源（比如别人转发过的、或者 App 内录屏）时才有用。

### 抖音为什么要单独走浏览器

`yt-dlp` 的抖音实现目前是**残缺**的，源码里自己写着：

```python
# TODO: Run verification challenge code to generate signature cookies
raise ExtractorError('Fresh cookies (not necessarily logged in) are needed')
```

它请求的 `aweme/v1/web/aweme/detail/` 需要 `a_bogus` 签名，而它没生成。试过但无效的办法：

- 补 `ttwid`（调 bytedance 注册接口拿到真的，仍然 401）
- 补浏览器能读到的 13 个 cookie（含 `UIFID`）
- 升级 yt-dlp 到 2026.10.26
- 直接请求视频页解析 `RENDER_DATA`（810K HTML 里只有 DNS 预取，没有真实地址）

最后改用 **Playwright 无头浏览器**打开页面，从网络请求里捞 CDN 地址，再带 `Referer` 下载。代价：

- 每次解析约 **18 秒**（浏览器启动 + 页面加载）
- 只能在装了 Playwright 的机器上跑，**手机 Termux 不支持**（会返回明确提示）
- 抓到的直链有时效，解析后要尽快下载

## 六、已知限制

- **必须先有电脑**：手机浏览器不能直接解析这些平台的链接（CORS + 防盗链 + 签名参数），这是浏览器的硬限制，不是偷懒
- **手机和电脑得同一个 WiFi**：走的是局域网，不是公网
- **无鉴权**：同一 WiFi 下别人知道地址也能打开。家里用没问题，公司/公共网络别长期开着
- **导出是重编码**：会损失一点点画质（CRF 20，肉眼基本看不出），但换来所有手机都能播
- **超长视频慢**：导出速度取决于电脑 CPU，4 分钟 1080P 约 30-60 秒
- **源文件攒在硬盘**：`outputs\video-downloads\web\`，定期手动清

## 七、故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 手机打不开页面 | 防火墙 / 不同 WiFi | 先加防火墙规则；确认手机 WiFi 名和电脑一致 |
| 解析转圈后报错 | 提取器失效或要登录 | `pip install -U yt-dlp`；B站高清先在电脑浏览器登录 |
| 下载完放不出画面 | 编码是 AV1/HEVC | 代码会自动转 h264，看 `server.log` 有没有 `transcode fail` |
| 导出报 ffmpeg 失败 | 选区太小或源文件异常 | 选区别小于画面的 5% |
| 端口 8848 被占用 | 上次的进程没退干净 | `netstat -ano \| findstr :8848` 然后 `taskkill /F /PID <pid>` |

日志在同目录 `server.log`。

## 八、技术实现

| 环节 | 做法 |
|---|---|
| 解析 | `yt-dlp -J` 拿元信息（标题/时长/分辨率） |
| 下载 | `yt-dlp` 优先选 `avc1`(h264) 分轨，避免手机不支持的 AV1 |
| 转码 | 下载后探测编码，非 h264 自动转（输入输出分离，不会覆盖源文件） |
| 播放 | 自建 HTTP 服务支持 `Range` + `HEAD`，iOS Safari 能拖进度条 |
| 模糊 | `split → crop → boxblur → overlay` 只糊指定区域 |
| 裁剪 | `crop=w:h:x:y` |
| 导出 | `libx264 + aac + faststart`，全平台通吃 |

接口：

```
POST /api/parse          {url}                → {task, title, duration, ...}
GET  /api/progress/<id>                       → {status, percent, file, ...}
POST /api/crop           {task,mode,x,y,w,h,start,end} → {task}
GET  /media/<file>                            播放（支持 Range）
GET  /download/<file>                         下载（attachment）
```

---

## 九、Android 手机上独立运行（不需要电脑）

手机装了 ffmpeg 和 yt-dlp，就能自己跑这套服务，浏览器访问 `127.0.0.1:8848`。
不用连 WiFi、不用开电脑、没有防火墙问题。

**前提：必须是 Android。iPhone 做不到**——iOS 不给后台常驻进程，也没有能装 ffmpeg 的环境，这是系统层面的限制。

### 步骤

1. 装 [Termux](https://f-droid.org/packages/com.termux/)（**从 F-Droid 装，别用 Google Play 那个，版本已废弃**）

2. 把 `videowm-termux.zip` 传到手机（微信发给自己 / 数据线 / 网盘都行），解压后放进 Termux：

   ```bash
   # 在 Termux 里，从电脑拉（电脑要开着服务、同一个 WiFi）
   cd ~
   bash <(curl -s http://192.168.0.9:8848/install-termux.sh) http://192.168.0.9:8848

   # 或者：自己把 4 个文件放到 ~/videowm，然后
   cd ~/videowm && bash install-termux.sh
   ```

3. 启动：

   ```bash
   ~/start-videowm.sh
   ```

4. 手机浏览器打开 `http://127.0.0.1:8848`

### 和电脑版的三处不同

| | 电脑版 | 手机版 |
|---|---|---|
| 访问地址 | 局域网 IP（手机要连同一 WiFi） | `127.0.0.1`（就本机） |
| 产物位置 | `outputs\video-downloads\web\` | `手机存储/VideoWM`（文件管理器可见） |
| 转码速度 | RTX 4060 Ti 机器，4 分钟 1080P 约 65 秒 | 慢 3-5 倍，同片约 3-5 分钟 |

### 保活

手机系统会杀后台。启动脚本已经带了 `termux-wake-lock`，另外建议：

- Termux 设置里关掉电池优化
- 小米/华为/OPPO 要在「自启动管理」里给 Termux 放行
- 真被杀了就重跑一次 `~/start-videowm.sh`

### 更新提取器

抖音/小红书规则几个月变一次，失效了就更新：

```bash
pip install --break-system-packages -U yt-dlp
```

这也是为什么**不建议打包成 APK**：APK 里的 yt-dlp 是死的，平台一改规则整个包就废，用户只能等重新发版。Termux 里一条命令就更新了。

---

## 十、外网访问（已部署：手机 + cpolar 反代）

手机已经跑起来了，并且通过 cpolar 的 http 隧道挂了公网地址。

### 现在的地址

| 用途 | 地址 |
|---|---|
| **公网（推荐）** | `https://k30pro.cpolar.top/videowm/` |
| 手机本地 | `http://127.0.0.1:8848` |
| 手机局域网 | `http://192.168.0.12:8848` |

链路：`公网 → cpolar(https) → nginx:8080 → /videowm/ 反代 → server.py:8848 → yt-dlp + ffmpeg`

### 手机上装了什么

| 组件 | 位置 | 说明 |
|---|---|---|
| Python 3.14.8 | Termux 自带 | — |
| ffmpeg / ffprobe 9.0.2 | `$PREFIX/bin/` | 带 libx264、mediacodec |
| yt-dlp 2026.10.26 | `$PREFIX/bin/yt-dlp` | — |
| tmux 3.8 | — | 用来托管服务进程 |
| 程序目录 | `~/videowm/` | server.py / index.html / app.js / media/ |

### nginx 反代

配置文件 `$PREFIX/etc/nginx/nginx.conf`，在原 `location /` 之后加了一段：

```nginx
location /videowm/ {
    proxy_pass http://127.0.0.1:8848/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_buffering off;
    proxy_request_buffering off;
    client_max_body_size 0;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
}
```

原欢迎页还在（根路径 `/` 不受影响）。改之前的配置备份在 `nginx.conf.bak-videowm`。

前端已经支持子路径——所有接口按当前目录解析，所以挂在 `/videowm/` 下和直接访问 8848 都能用。

### 启动 / 重启

```bash
# 启动（后台常驻）
export TERM=xterm-256color
tmux new -d -s wm "bash ~/videowm/start.sh > ~/videowm/server.log 2>&1"

# 看日志
tail -f ~/videowm/server.log

# 停止
pkill -f "python3 -u server.py"
```

> `export TERM=xterm-256color` 不能省——SSH 进去时 TERM 没设置，tmux 会直接退出。

### 实测数据（手机端）

| 项目 | 结果 |
|---|---|
| 解析 | B站 3.4 秒 |
| 下载 | 75MB / 36 秒（1.7MB/s） |
| 模糊导出 | 10 秒 1080P 片 → 约 10 秒，1.6x 实时 |
| 外网页面 | 0.2 秒响应 |
| 外网下载 | 283 KB/s（cpolar 免费版限速） |
| Range 播放 | 206 透传正常，可拖进度条 |

### 两个要注意的地方

**1. 服务进程会被系统杀**
Android 内存回收比较激进，进程被杀后需要手动重启（上面的启动命令）。彻底解决要：
- Termux 里 `termux-wake-lock`
- 系统设置里给 Termux 关掉电池优化、允许后台运行

**2. 公网无鉴权**
现在任何人拿到 `k30pro.cpolar.top/videowm/` 这个地址就能用你的手机下载和转码，消耗的是你的流量和 CPU。自己用问题不大，但别把地址发到公开群。

想加访问口令的话说一声，在 server.py 里加个 token 校验就行。



---

## 十一、实测数据（2026-11-02）

用两个真实分享链接跑通全流程，产物都在 `outputs/video-downloads/web/`。

### 小红书

链接：`https://xhslink.cn/o/1k3U19PRN9N`

| 环节 | 结果 | 耗时 |
|---|---|---|
| 短链展开 | → `xiaohongshu.com/explore/6ae3020…` | <1s |
| 解析 | 720×1280 竖屏，21.3 秒 | 3.4s |
| 下载 | 1.4MB | 3s |
| 标准打码 | 720×1280（画幅不变）1.78MB | 12s |
| 标准裁切 | 720×1114（切掉底部 13%）1.67MB | 14s |

### 抖音

链接：`https://v.douyin.com/jpswfEgeEc4/`

| 环节 | 结果 | 耗时 |
|---|---|---|
| 短链展开 | → `douyin.com/video/7702756796519360182` | <1s |
| 解析（浏览器） | 1080×1920 竖屏，21.8 秒 | **18.7s** |
| 下载（直链） | 4.2MB | 2s |
| 标准打码 | 1080×1920，9.5MB | 20s |
| 标准裁切 | 1080×1670（切掉底部 13%）9.2MB | 21s |

> 输出体积比源大（4.2MB → 9.5MB）是重编码导致的：源是平台压过一遍的，我们按 CRF 20 重新编码。要更小可以调 `crop_worker` 里的 `-crf`。

---

## 十二、解析缓存（2026-11-02）

同一条视频解析过一次就不再重复下载——抖音那种要开浏览器的，重复跑太浪费。

### 怎么做的

- 缓存文件：`<media>/.parse-cache.json`
- Key 用**视频 ID 而不是 URL**：`douyin.com:7702756796519360182`
  这样同一条视频换短链、换分享文案都能命中
- Value 存 task / 文件名 / 标题 / 尺寸 / 时长 / 时间戳
- 有效期 24 小时；文件被清理掉就自动作废
- 命中时服务端把任务状态恢复成 `done`，前端直接进编辑态

### 实测

| 环境 | 第 1 次 | 第 2 次 |
|---|---|---|
| 电脑 | 15s | **1s** |
| 手机 | 11s | **1s** |

---

## 十三、编辑交互（2026-11-02 重做）

### 裁切框浮动在原画面上

以前预览显示的是「裁切后」的画面，框一动画面就跟着变，根本判断不了框到哪了。
现在**预览永远画整幅原画面**，裁切框浮在上面，框外压暗——拖框时画面纹丝不动。

验证方法：拖动前后采样 canvas 像素，除解码的 ±1 抖动外完全一致。

### 四种模式的界面差异

| 模式 | 框 | 把手（可拖） | 框外遮罩 |
|---|---|---|---|
| 标准裁切 | 显示 | 隐藏 | 压暗（表示会被切掉） |
| 标准打码 | 显示 | 隐藏 | 无（框内已模糊） |
| 自定义裁切 | 显示 | 显示 | 压暗 |
| 自定义打码 | 显示 | 显示 | 无 |

切换模式时框会**重置回该平台的预设位置**，避免拿打码的框去裁切。

### 手机操作细节

- **放大镜**：拖动时手指上方弹出 140px 圆形放大窗（2.4 倍 + 十字准星）
- **框外拖拽**：画面空白处按下拉动就是一个新框
- **吸附**：边缘 / 中心 / 三等分线，1.8% 内自动对齐
- **↺ 恢复自动**：拖乱了点一下回到平台预设
- **画面限高 64vh**：竖屏不再顶穿一屏
- **进编辑自动滚到顶部**：给手指留空间
