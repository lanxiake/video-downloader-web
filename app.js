/* 视频去水印 · 交互逻辑 */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };

  // 支持反代到子路径（/videowm/）
  var P = (function () {
    var p = location.pathname;
    if (p.charAt(p.length - 1) !== '/') p = p.replace(/[^/]*$/, '');
    return p || '/';
  })();

  /* ══════════ 平台水印预设 ══════════
     box  : 水印所在区域，相对坐标 0~1
     cut  : 标准裁切时切掉哪条边，尽量少损失画面
     实测依据：
       · 抖音/快手/小红书/TikTok —— 水印贴右下角（头像+昵称+logo）
       · B站 —— bilibili logo 在右上角
     竖屏平台的横屏视频会把区域收窄（见 openEditor） */
  var WM = {
    // p = 竖屏(portrait)  l = 横屏(landscape)
    // 注：抖音的左上角水印是"保存到相册"那种；我们从 CDN 下的源通常不带水印，
    // 这套坐标是给确实带水印的素材兜底用的。
    douyin:   { name: '抖音',
      p: { box: { x: 0.014, y: 0.012, w: 0.400, h: 0.082 }, cut: 'top' },
      l: { box: { x: 0.016, y: 0.018, w: 0.250, h: 0.100 }, cut: 'top' } },
    kuaishou: { name: '快手',
      p: { box: { x: 0.720, y: 0.014, w: 0.265, h: 0.080 }, cut: 'top' },
      l: { box: { x: 0.760, y: 0.020, w: 0.225, h: 0.095 }, cut: 'top' } },
    xhs:      { name: '小红书',
      p: { box: { x: 0.500, y: 0.868, w: 0.485, h: 0.076 }, cut: 'bottom' },
      l: { box: { x: 0.620, y: 0.862, w: 0.365, h: 0.100 }, cut: 'bottom' } },
    bilibili: { name: 'B站',
      p: { box: { x: 0.795, y: 0.014, w: 0.195, h: 0.070 }, cut: 'top' },
      l: { box: { x: 0.845, y: 0.012, w: 0.150, h: 0.085 }, cut: 'top' } },
    weibo:    { name: '微博',
      p: { box: { x: 0.795, y: 0.014, w: 0.195, h: 0.072 }, cut: 'top' },
      l: { box: { x: 0.855, y: 0.015, w: 0.140, h: 0.075 }, cut: 'top' } },
    acfun:    { name: 'AcFun',
      p: { box: { x: 0.795, y: 0.014, w: 0.195, h: 0.072 }, cut: 'top' },
      l: { box: { x: 0.855, y: 0.015, w: 0.140, h: 0.075 }, cut: 'top' } },
    huya:     { name: '虎牙',
      p: { box: { x: 0.550, y: 0.868, w: 0.440, h: 0.078 }, cut: 'bottom' },
      l: { box: { x: 0.620, y: 0.868, w: 0.365, h: 0.090 }, cut: 'bottom' } },
    ixigua:   { name: '西瓜',
      p: { box: { x: 0.560, y: 0.868, w: 0.430, h: 0.078 }, cut: 'bottom' },
      l: { box: { x: 0.620, y: 0.868, w: 0.365, h: 0.090 }, cut: 'bottom' } },
    tiktok:   { name: 'TikTok',
      p: { box: { x: 0.550, y: 0.868, w: 0.440, h: 0.078 }, cut: 'bottom' },
      l: { box: { x: 0.620, y: 0.868, w: 0.365, h: 0.090 }, cut: 'bottom' } },
    other:    { name: '通用',
      p: { box: { x: 0.560, y: 0.868, w: 0.430, h: 0.078 }, cut: 'bottom' },
      l: { box: { x: 0.620, y: 0.868, w: 0.365, h: 0.090 }, cut: 'bottom' } }
  };

  /* 取当前视频方向对应的预设（视频还没加载时按竖屏算） */
  function platBox() {
    var info = WM[S.platform] || WM.other;
    var portrait = !S.vw || !S.vh || S.vh >= S.vw;
    return info[portrait ? 'p' : 'l'] || info.p;
  }
  function platName() {
    return (WM[S.platform] || WM.other).name;
  }

  var S = {
    task: null, file: null,
    vw: 0, vh: 0, duration: 0,
    platform: 'other',
    mode: 'crop',            // crop | blur | custom
    audioOnly: false,
    subMode: 'crop',         // 自定义模式下：crop | blur
    box: null,               // 自定义框（0~1）
    ready: false,
    busy: false,
    dlUrl: '',               // 结果文件的绝对下载地址
    expireAt: 0              // 产物过期时间戳
  };

  /* ══════════ 工具 ══════════ */
  function fmt(t) {
    t = Math.max(0, Math.floor(t || 0));
    var m = Math.floor(t / 60), s = t % 60;
    return (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
  }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  function setState(txt, kind) {
    $('stateTxt').textContent = txt;
    var d = $('dot');
    d.className = 'dot' + (kind === 'busy' ? ' busy' : kind === 'err' ? ' err' : '');
  }
  function showErr(msg) {
    var e = $('err');
    if (!msg) { e.style.display = 'none'; return; }
    e.textContent = msg;
    e.style.display = 'block';
  }

  function detectPlatform(url) {
    var u = String(url || '');
    if (/douyin\.com|iesdouyin/.test(u)) return 'douyin';
    if (/bilibili\.com|b23\.tv/i.test(u)) return 'bilibili';
    if (/xiaohongshu\.com|xhslink\.(com|cn)/.test(u)) return 'xhs';
    if (/weibo\.(com|cn)/.test(u)) return 'weibo';
    if (/acfun\.cn/.test(u)) return 'acfun';
    if (/huya\.com/.test(u)) return 'huya';
    if (/ixigua\.com/.test(u)) return 'ixigua';
    if (/kuaishou\.com|chenzhongtech/.test(u)) return 'kuaishou';
    if (/tiktok\.com/.test(u)) return 'tiktok';
    return 'other';
  }
  function extractUrl(txt) {
    var m = String(txt || '').match(/https?:\/\/[^\s，。、"'<>）)]+/);
    return m ? m[0] : '';
  }

  /* ══════════ 网络 ══════════ */
  function poll(tid, onTick, onDone) {
    var dead = false;
    (function tick() {
      if (dead) return;
      fetch(P + 'api/progress/' + tid).then(function (r) { return r.json(); })
        .then(function (d) {
          if (!d.ok) return onDone({ error: d.error || '任务丢失' });
          if (onTick) onTick(d);
          if (d.status === 'done') return onDone(d);
          if (d.status === 'error') return onDone({ error: d.error || '处理失败' });
          setTimeout(tick, 700);
        }).catch(function (e) { onDone({ error: '网络错误：' + e.message }); });
    })();
    return function () { dead = true; };
  }

  /* ══════════ 解析 ══════════ */
  $('go').addEventListener('click', doParse);
  $('url').addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); doParse(); }
  });

  function doParse() {
    if (S.busy) return;
    var raw = $('url').value.trim();
    var url = extractUrl(raw) || raw;

    // 不是链接就当成已下载过的文件 id：服务重启/页面刷新后还能接着改
    if (!/^https?:\/\//.test(url)) {
      if (/^[A-Za-z0-9]{6,24}$/.test(raw)) return openExisted(raw);
      showErr('没找到有效链接。把整段分享文字粘进来也行。');
      return;
    }

    S.busy = true; S.platform = detectPlatform(url);
    showErr('');
    $('go').disabled = true;
    $('go').innerHTML = '<span class="spin lt"></span>解析中';
    $('prog').style.display = 'block';
    $('bar').classList.add('indet');
    $('barFill').style.width = '30%';
    $('pTitle').textContent = '正在解析链接…';
    $('pPct').textContent = '';
    setState('解析中', 'busy');
    $('url').blur();

    fetch(P + 'api/parse', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: url })
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (!d.ok) return fail(d.error || '解析失败');
      S.task = d.task;
      S.duration = d.duration || 0;
      S.vw = d.width || 0; S.vh = d.height || 0;
      S.platform = detectPlatform(d.webpage || url);
      S.audioOnly = !!d.audio_only;
      $('bar').classList.remove('indet');
      $('barFill').style.width = '0%';
      $('pTitle').textContent = d.title || '';
      // 命中服务端缓存时文件已经在盘上，不用再等下载
      $('pPct').textContent = d.cached ? '已缓存 · 秒开' : '下载中';
      if (d.cached) $('barFill').style.width = '100%';

      poll(d.task, function (p) {
        if (p.status === 'merging') {
          $('pTitle').textContent = '合并音视频…';
          $('barFill').style.width = '100%';
          $('pPct').textContent = '100%';
        } else if (p.status === 'transcoding') {
          $('pTitle').textContent = '转码成手机可播放格式…';
          $('bar').classList.add('indet');
          $('pPct').textContent = '';
        } else {
          var pct = p.percent || 0;
          $('barFill').style.width = pct + '%';
          $('pPct').textContent = pct.toFixed(1) + '%' + (p.speed ? ' · ' + p.speed : '');
        }
      }, function (res) {
        S.busy = false;
        if (res.error || !res.file) return fail(res.error || '下载失败');
        S.file = res.file;
        // 双保险：解析接口的 audio_only 和产物扩展名任一对上就算音频
        if (!S.audioOnly && /\.(m4a|mp3|aac|flac|wav)$/i.test(res.file || '')) {
          S.audioOnly = true;
        }
        S.duration = res.duration || S.duration;
        S.vw = res.width || S.vw;
        S.vh = res.height || S.vh;
        $('prog').style.display = 'none';
        $('go').disabled = false;
        $('go').textContent = '解析视频';
        setState('已就绪');
        openEditor();
      });
    }).catch(function (e) { fail('请求失败：' + e.message); });
  }

  function fail(msg) {
    S.busy = false;
    $('go').disabled = false;
    $('go').textContent = '解析视频';
    $('bar').classList.remove('indet');
    $('prog').style.display = 'none';
    setState('出错', 'err');
    showErr(authHint(msg) || msg);
  }

  // 未授权时给一句能照做的提示
  function authHint(msg) {
    if (/未授权|401/.test(String(msg || ''))) {
      return '登录已过期。用电脑上那个二维码重新扫一次，或者刷新页面重新进。';
    }
    return '';
  }

  // 打开一个已经躺在服务器上的文件（刷新页面后接着改）
  function openExisted(tid) {
    showErr('');
    setState('读取中', 'busy');
    fetch(P + 'api/progress/' + tid).then(function (r) { return r.json(); }).then(function (d) {
      if (!d.ok || d.status !== 'done' || !d.file) {
        setState('就绪');
        showErr('没找到这个文件。请粘完整视频链接。');
        return;
      }
      S.task = tid;
      S.file = d.file;
      S.duration = d.duration || 0;
      S.vw = d.width || 0;
      S.vh = d.height || 0;
      setState('已就绪');
      openEditor();
    }).catch(function () {
      setState('就绪');
      showErr('读取失败，检查服务是否在跑。');
    });
  }

  /* ══════════ 打开编辑器 ══════════ */
  function openEditor() {
    $('inputView').style.display = 'none';
    $('result').style.display = 'none';
    $('editor').style.display = 'block';
    $('dock').classList.add('on');

    S.box = null;                       // 等视频加载出来、知道横竖屏再定

    // 音频平台能下但没有画面，裁切/打码无从谈起——
    // 把选项整块收起来，只留播放器和下载，别让人对着音频文件找水印
    if (S.audioOnly) {
      $('editor').classList.add('audio');
      $('brandSub').textContent = '· 音频';
      var av = $('v');
      av.src = P + 'media/' + encodeURIComponent(S.file);
      av.onloadeddata = null;
      av.onerror = null;
      av.load();
      $('meta').textContent = fmt(S.duration) + '  ·  音频文件';
      $('tag').textContent = '音频';
      var eb = $('exportBtn');
      if (eb) eb.textContent = '保存到手机';
      S.ready = false;
      $('bar').classList.remove('indet');
      return;
    }
    $('editor').classList.remove('audio');
    var eb2 = $('exportBtn');
    if (eb2) eb2.textContent = '开始处理';
    $('brandSub').textContent = '· ' + platName();

    var v = $('v');
    v.src = P + 'media/' + encodeURIComponent(S.file);
    v.onloadeddata = onVideoReady;
    v.onerror = function () { showErr('视频加载失败，试试重新解析'); };
    v.load();

    // 默认选中「标准裁切」
    setMode('crop');
    $('tEnd').value = 1000;
    updateTime();
  }

  function onVideoReady() {
    var v = $('v');
    S.vw = v.videoWidth || S.vw;
    S.vh = v.videoHeight || S.vh;
    S.duration = v.duration || S.duration;
    // 定位到第一帧
    try { v.currentTime = 0.001; } catch (e) {}
    S.ready = true;
    if (!S.box) {
      var pb = platBox().box;
      S.box = { x: pb.x, y: pb.y, w: pb.w, h: pb.h };
    }
    render();
    $('meta').textContent = S.vw + ' × ' + S.vh + '  ·  ' + fmt(S.duration)
      + '  ·  ' + (S.vh >= S.vw ? '竖屏' : '横屏');
    // 手机上把画面顶到屏幕上方，留给手指的空间更大
    try {
      $('frame').scrollIntoView({ block: 'start', behavior: 'smooth' });
    } catch (e) {}
  }

  /* ══════════ 计算输出区域 ══════════ */
  function cutRect(cut, b) {
    // 返回保留的矩形（相对坐标）
    switch (cut) {
      case 'top':   return { x: 0, y: b.y + b.h, w: 1, h: 1 - (b.y + b.h) };
      case 'bottom':return { x: 0, y: 0, w: 1, h: b.y };
      case 'left':  return { x: b.x + b.w, y: 0, w: 1 - (b.x + b.w), h: 1 };
      case 'right': return { x: 0, y: 0, w: b.x, h: 1 };
    }
    return { x: 0, y: 0, w: 1, h: 1 };
  }

  function bestCut(b) {
    // 自定义模式：框在中间时，取四边里面积最大的保留区
    var cands = [
      { k: 'bottom', r: { x: 0, y: 0, w: 1, h: b.y } },
      { k: 'top',    r: { x: 0, y: b.y + b.h, w: 1, h: 1 - (b.y + b.h) } },
      { k: 'right',  r: { x: 0, y: 0, w: b.x, h: 1 } },
      { k: 'left',   r: { x: b.x + b.w, y: 0, w: 1 - (b.x + b.w), h: 1 } }
    ];
    var best = cands[0], area = -1;
    cands.forEach(function (c) {
      var a = c.r.w * c.r.h;
      if (a > area) { area = a; best = c; }
    });
    return best.k;
  }

  function computeOutput() {
    var b = S.box || { x: 0, y: 0, w: 1, h: 1 };
    var mode = S.mode === 'custom' ? S.subMode : S.mode;
    var px = {
      x: b.x * S.vw, y: b.y * S.vh,
      w: b.w * S.vw, h: b.h * S.vh
    };
    if (mode === 'blur') {
      return { mode: mode, box: px, rect: { x: 0, y: 0, w: S.vw, h: S.vh } };
    }
    var cut;
    if (S.mode === 'custom') {
      cut = bestCut(b);
    } else {
      var plat = platBox();
      cut = plat.cut;
      // 竖屏视频却按横屏算法（或反之）时，B站那类顶部水印会失效，就地兜底
      if (plat.cut === 'top' && b.y > 0.3) cut = 'bottom';
    }
    var r = cutRect(cut, b);
    var rect = {
      x: Math.max(0, Math.min(r.x, 1) * S.vw),
      y: Math.max(0, Math.min(r.y, 1) * S.vh),
      w: r.w * S.vw,
      h: r.h * S.vh
    };
    rect.w = Math.min(rect.w, S.vw - rect.x);
    rect.h = Math.min(rect.h, S.vh - rect.y);
    return { mode: mode, box: px, rect: rect };
  }

  /* ══════════ 渲染 ══════════ */
  function paint(canvas, maxW, maxH) {
    var v = $('v');
    if (!S.ready || !v.videoWidth) return;
    var out = computeOutput();

    // 预览永远画整幅原画面：裁切框是浮在上面的一层，框怎么拖画面都不动。
    // 只按体量缩放（1800px 的图留 720 内部宽度），不是按裁切区域缩放。
    var innerW = Math.min(Math.round(S.vw), 720);
    var innerH = Math.max(1, Math.round(innerW * S.vh / S.vw));
    if (canvas.width !== innerW || canvas.height !== innerH) {
      canvas.width = innerW;
      canvas.height = innerH;
    }
    var k = innerW / S.vw;                // 源像素 → 画布像素
    var ctx = canvas.getContext('2d');
    ctx.imageSmoothingQuality = 'high';

    // 底图：整幅，不裁
    ctx.drawImage(v, 0, 0, S.vw, S.vh, 0, 0, innerW, innerH);

    if (out.mode === 'blur') {
      var bx = out.box.x * k;
      var by = out.box.y * k;
      var bw = out.box.w * k;
      var bh = out.box.h * k;
      // 与后端 ffmpeg 的 boxblur 力度对齐：约短边的 1/4
      var blurPx = Math.max(3, Math.min(bw, bh) * 0.25);
      var pad = blurPx * 3;

      ctx.save();
      ctx.beginPath();
      ctx.rect(bx, by, bw, bh);
      ctx.clip();
      var supported = (function () {
        try { ctx.filter = 'blur(2px)'; var ok = ctx.filter !== 'none'; ctx.filter = 'none'; return ok; }
        catch (e) { return false; }
      })();
      if (supported) {
        ctx.filter = 'blur(' + blurPx.toFixed(1) + 'px)';
        ctx.drawImage(v,
          out.box.x - pad / k, out.box.y - pad / k,
          out.box.w + (pad * 2) / k, out.box.h + (pad * 2) / k,
          bx - pad, by - pad, bw + pad * 2, bh + pad * 2);
        ctx.filter = 'none';
      } else {
        // 不支持 filter 就退回马赛克
        var blk = Math.max(3, Math.round(Math.min(bw, bh) / 6));
        var tmp = document.createElement('canvas');
        tmp.width = Math.max(1, Math.round(bw / blk));
        tmp.height = Math.max(1, Math.round(bh / blk));
        tmp.getContext('2d').drawImage(canvas, bx, by, bw, bh, 0, 0, tmp.width, tmp.height);
        ctx.imageSmoothingEnabled = false;
        ctx.drawImage(tmp, 0, 0, tmp.width, tmp.height, bx, by, bw, bh);
        ctx.imageSmoothingEnabled = true;
      }
      ctx.restore();
    }
    return { out: out, cw: innerW, ch: innerH, scale: k };
  }

  function render() {
    var frame = $('frame');
    var avail = (frame.parentElement && frame.parentElement.clientWidth) || 320;
    // 按可用宽度渲染，画布内部铺满 frame——拖拽坐标才能和画面像素 1:1 对上
    var info = paint($('cv'), avail, avail * 4);
    if (!info) return;

    // 竖屏视频（1080x1920）按宽度铺满会撑到一屏外，这里限高，
    // 让 frame 宽度跟着收窄——sel-layer 是相对 frame 的，坐标依然正确
    var maxH = window.innerHeight * 0.6;
    var dispW = avail;
    var dispH = avail * info.ch / info.cw;
    if (dispH > maxH) {
      dispH = maxH;
      dispW = Math.round(maxH * info.cw / info.ch);
    }
    frame.style.width = dispW + 'px';
    updateTag(info);
  }

  function updateTag(info) {
    var out = info.out;
    var tag = $('tag');
    if (out.mode === 'blur') {
      tag.textContent = '打码 ' + Math.round(out.box.w) + '×' + Math.round(out.box.h);
    } else {
      tag.textContent = '裁至 ' + Math.round(out.rect.w) + '×' + Math.round(out.rect.h);
    }
  }

  /* ══════════ 模式切换 ══════════ */
  $('modes').addEventListener('click', function (e) {
    var el = e.target.closest('.mode');
    if (el) setMode(el.dataset.m);
  });
  $('subModes').addEventListener('click', function (e) {
    var el = e.target.closest('.mode');
    if (!el) return;
    S.subMode = el.dataset.s;
    syncModeUI();
    render();
  });

  function setMode(m) {
    if (S.mode === m) return;
    S.mode = m;
    // 换模式 = 换一种"要去掉什么"的思路，上一模式的框必须清掉，
    // 否则会拿着打码的框去裁切，出来的东西完全不对
    var pb = platBox().box;
    S.box = { x: pb.x, y: pb.y, w: pb.w, h: pb.h };
    if (S._redrawBox) S._redrawBox();
    syncModeUI();
    render();
  }

  function syncModeUI() {
    [].forEach.call($('modes').children, function (el) {
      el.classList.toggle('on', el.dataset.m === S.mode);
    });
    [].forEach.call($('subModes').children, function (el) {
      el.classList.toggle('on', el.dataset.s === S.subMode);
    });
    var isCustom = S.mode === 'custom';
    var isBlurNow = isCustom ? S.subMode === 'blur' : S.mode === 'blur';
    $('frame').classList.toggle('custom', isCustom);
    $('frame').classList.toggle('showsel', true);     // 四种模式都显示框
    $('frame').classList.toggle('cutmode', !isBlurNow); // 裁切类压暗框外
    $('frame').classList.toggle('grid', isBlurNow);

    // 说明文案随子模式变：裁切是"框外切掉"，打码是"只糊框内"
    if (isCustom) {
      $('helpTxt').textContent = isBlurNow
        ? '拖动四角框住水印（有放大镜辅助），只把框内糊掉'
        : '拖动四角框住水印（有放大镜辅助），框外会被切掉';
    }
    if (isCustom && S._drawSel) S._drawSel();
  }

  /* ══════════ 自定义框选（手机手感） ══════════ */
  (function initDrag() {
    var frame = $('frame');
    var layer = document.createElement('div');
    layer.className = 'sel-layer';
    layer.innerHTML =
      '<div class="mk" data-mk="t"></div><div class="mk" data-mk="b"></div>' +
      '<div class="mk" data-mk="l"></div><div class="mk" data-mk="r"></div>';
    var box = document.createElement('div');
    box.className = 'sel';
    box.innerHTML = '<div class="fill"></div>' +
      '<div class="h tl" data-d="tl"></div><div class="h tr" data-d="tr"></div>' +
      '<div class="h bl" data-d="bl"></div><div class="h br" data-d="br"></div>' +
      '<div class="h t" data-d="t"></div><div class="h b" data-d="b"></div>' +
      '<div class="h l" data-d="l"></div><div class="h r" data-d="r"></div>';
    layer.appendChild(box);
    frame.appendChild(layer);

    // 放大镜：手指下方那个点到底框住哪了，靠它看
    var loupe = document.createElement('div');
    loupe.className = 'loupe';
    loupe.innerHTML = '<canvas width="140" height="140"></canvas>';
    document.body.appendChild(loupe);
    var loupeCv = loupe.querySelector('canvas');
    var LO = 140, ZOOM = 2.4;

    var mkT = layer.querySelector('[data-mk="t"]');
    var mkB = layer.querySelector('[data-mk="b"]');
    var mkL = layer.querySelector('[data-mk="l"]');
    var mkR = layer.querySelector('[data-mk="r"]');

    function pt(e) {
      var r = frame.getBoundingClientRect();
      return { x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height };
    }
    function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }

    function draw() {
      var b = S.box;
      if (!b) return;
      box.style.left = (b.x * 100) + '%';
      box.style.top = (b.y * 100) + '%';
      box.style.width = (b.w * 100) + '%';
      box.style.height = (b.h * 100) + '%';

      mkT.style.cssText = 'left:0;top:0;width:100%;height:' + (b.y * 100) + '%';
      mkB.style.cssText = 'left:0;bottom:0;width:100%;height:' + ((1 - b.y - b.h) * 100) + '%';
      mkL.style.cssText = 'left:0;top:' + (b.y * 100) + '%;width:' + (b.x * 100) + '%;height:' + (b.h * 100) + '%';
      mkR.style.cssText = 'right:0;top:' + (b.y * 100) + '%;width:' + ((1 - b.x - b.w) * 100) + '%;height:' + (b.h * 100) + '%';
    }
    S._drawSel = draw;

    /* 放大镜：从已解码的第一帧里取一小块放大 */
    function drawLoupe(nx, ny, cx, cy) {
      var v = $('v');
      if (!v || !v.videoWidth || !S.vw || !S.vh) return;
      var ctx = loupeCv.getContext('2d');
      var sw = S.vw / ZOOM, sh = S.vh / ZOOM;
      var sx = clamp(nx * S.vw - sw / 2, 0, Math.max(0, S.vw - sw));
      var sy = clamp(ny * S.vh - sh / 2, 0, Math.max(0, S.vh - sh));
      ctx.fillStyle = '#000';
      ctx.fillRect(0, 0, LO, LO);
      try { ctx.drawImage(v, sx, sy, sw, sh, 0, 0, LO, LO); } catch (e) { return; }
      // 十字准星
      ctx.strokeStyle = 'rgba(0,224,138,.9)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(LO / 2, 0); ctx.lineTo(LO / 2, LO);
      ctx.moveTo(0, LO / 2); ctx.lineTo(LO, LO / 2);
      ctx.stroke();
      // 摆到手指上方，别被手指挡住
      var fr = frame.getBoundingClientRect();
      var lx = cx - LO / 2;
      var ly = cy - LO - 46;
      if (ly < 8) ly = cy + 46;
      lx = clamp(lx, 8, window.innerWidth - LO - 8);
      ly = clamp(ly, 8, window.innerHeight - LO - 8);
      loupe.style.transform = 'translate(' + lx + 'px,' + ly + 'px)';
      loupe.classList.add('on');
    }
    function hideLoupe() { loupe.classList.remove('on'); }

    /* 吸附：贴边、中心、三等分线 */
    var SNAP = [0, 1 / 3, 0.5, 2 / 3, 1];
    function snap(v, tol) {
      var best = v, d = tol;
      for (var i = 0; i < SNAP.length; i++) {
        var dd = Math.abs(v - SNAP[i]);
        if (dd < d) { d = dd; best = SNAP[i]; }
      }
      return best;
    }
    var SNAP_TOL = 0.018;

    var drag = null;
    var D = 1.0;

    frame.addEventListener('pointerdown', function (e) {
      if (S.mode !== 'custom' || !S.box) return;
      var h = e.target.closest('.h');
      var onBox = e.target.closest('.sel');
      var p = pt(e);

      if (!h && !onBox) {
        // 框外按下 → 从这点开始画一个新框
        S.box = { x: p.x, y: p.y, w: 0.001, h: 0.001 };
        drag = { d: 'new', s: p, id: e.pointerId };
        layer.classList.add('dragging');
        draw(); render();
        try { frame.setPointerCapture(e.pointerId); } catch (err) {}
        return;
      }
      e.preventDefault();
      try { frame.setPointerCapture(e.pointerId); } catch (err) {}
      layer.classList.add('dragging');
      drag = {
        d: h ? h.dataset.d : 'move',
        s: p,
        o: { x: S.box.x, y: S.box.y, w: S.box.w, h: S.box.h },
        id: e.pointerId
      };
      // 把手指真实位置留给放大镜
      drag.px = e.clientX; drag.py = e.clientY;
      drawLoupe(p.x, p.y, e.clientX, e.clientY);
    });

    frame.addEventListener('pointermove', function (e) {
      if (!drag || e.pointerId !== drag.id) return;
      e.preventDefault();
      e.stopPropagation();
      var p = pt(e);
      var dx = p.x - drag.s.x, dy = p.y - drag.s.y;
      var MIN = 0.03;

      if (drag.d === 'new') {
        var x0 = Math.min(drag.s.x, p.x), y0 = Math.min(drag.s.y, p.y);
        var x1 = Math.max(drag.s.x, p.x), y1 = Math.max(drag.s.y, p.y);
        S.box = {
          x: clamp(x0, 0, 1), y: clamp(y0, 0, 1),
          w: clamp(x1 - x0, MIN, 1), h: clamp(y1 - y0, MIN, 1)
        };
        draw(); afterBoxChange();
        drawLoupe(clamp(p.x, 0, 1), clamp(p.y, 0, 1), e.clientX, e.clientY);
        return;
      }

      var o = drag.o, b = { x: o.x, y: o.y, w: o.w, h: o.h };
      if (drag.d === 'move') {
        b.x = clamp(snap(o.x + dx, SNAP_TOL), 0, 1 - o.w);
        b.y = clamp(snap(o.y + dy, SNAP_TOL), 0, 1 - o.h);
      } else {
        var l = o.x, t = o.y, r = o.x + o.w, bo = o.y + o.h;
        if (drag.d.indexOf('l') >= 0) l = clamp(snap(o.x + dx, SNAP_TOL), 0, r - MIN);
        if (drag.d.indexOf('r') >= 0) r = clamp(snap(r + dx, SNAP_TOL), l + MIN, 1);
        if (drag.d.indexOf('t') >= 0) t = clamp(snap(o.y + dy, SNAP_TOL), 0, bo - MIN);
        if (drag.d.indexOf('b') >= 0) bo = clamp(snap(bo + dy, SNAP_TOL), t + MIN, 1);
        b = { x: l, y: t, w: r - l, h: bo - t };
      }
      S.box = b; draw();
      afterBoxChange();
      drawLoupe(clamp(p.x, 0, 1), clamp(p.y, 0, 1), e.clientX, e.clientY);
    });

    function end(e) {
      if (!drag || e.pointerId !== drag.id) return;
      // 新建的框太小就当误触，恢复成整画面
      if (drag.d === 'new' && S.box && (S.box.w < 0.04 || S.box.h < 0.04)) {
        var pb = platBox().box;
        S.box = { x: pb.x, y: pb.y, w: pb.w, h: pb.h };
      }
      drag = null;
      layer.classList.remove('dragging');
      hideLoupe();
      draw(); render();
    }
    frame.addEventListener('pointerup', end);
    frame.addEventListener('pointercancel', end);
    frame.addEventListener('pointerleave', function () { hideLoupe(); });

    // 快捷套框：手抖也能一次到位
    var PRESET = {
      all: { x: 0, y: 0, w: 1, h: 1 },
      tl:  { x: 0, y: 0, w: 0.45, h: 0.22 },
      tr:  { x: 0.55, y: 0, w: 0.45, h: 0.22 },
      bl:  { x: 0, y: 0.78, w: 0.45, h: 0.22 },
      br:  { x: 0.55, y: 0.78, w: 0.45, h: 0.22 }
    };
    $('quickBox').addEventListener('click', function (e) {
      var b = e.target.closest('button');
      if (!b) return;
      if (b.dataset.q === 'reset') {
        var pb = platBox().box;
        S.box = { x: pb.x, y: pb.y, w: pb.w, h: pb.h };
      } else {
        var p = PRESET[b.dataset.q];
        if (!p) return;
        S.box = { x: p.x, y: p.y, w: p.w, h: p.h };
      }
      draw(); render();
    });

    S._redrawBox = draw;

    /* 框变了之后：裁切模式下画面不动，只需更新尺寸标签；打码才需要重画 */
    function afterBoxChange() {
      var isBlurNow = (S.mode === 'custom' ? S.subMode : S.mode) === 'blur';
      if (isBlurNow) { render(); return; }
      updateTag({ out: computeOutput() });
    }
    S._afterBoxChange = afterBoxChange;
  })();

  /* ══════════ 时间裁剪 ══════════ */
  function updateTime() {
    var d = S.duration || 0;
    var s = ($('tStart').value / 1000) * d;
    var e = ($('tEnd').value / 1000) * d;
    if (e <= s + 0.3) {
      e = Math.min(d, s + 0.3);
      if (d) $('tEnd').value = Math.round((e / d) * 1000);
    }
    $('vStart').textContent = fmt(s);
    $('vEnd').textContent = fmt(e);
    return [s, e];
  }
  $('tStart').addEventListener('input', updateTime);
  $('tEnd').addEventListener('input', updateTime);

  /* ══════════ 全屏预览 ══════════ */
  $('previewBtn').addEventListener('click', function () {
    var sheet = $('sheet');
    sheet.classList.add('on');
    var cv = $('sheetCv');
    requestAnimationFrame(function () {
      var W = window.innerWidth - 24;
      var H = window.innerHeight - 110;
      var info = paint(cv, W, H);
      if (info) {
        $('sheetNote').textContent =
          (S.mode === 'custom' ? '自定义 · ' : (WM[S.platform] || WM.other).name + ' · ') +
          (info.out.mode === 'blur' ? '打码预览' : '裁切预览');
      }
    });
  });
  $('sheetClose').addEventListener('click', function () {
    $('sheet').classList.remove('on');
  });
  $('sheet').addEventListener('click', function (e) {
    if (e.target === $('sheet') || e.target.classList.contains('body')) {
      $('sheet').classList.remove('on');
    }
  });

  /* ══════════ 导出 ══════════ */
  $('exportBtn').addEventListener('click', doExport);

  function doExport() {
    if (S.busy || !S.file) return;

    // 音频没有画面可处理，直接给原文件，别再走一遍 ffmpeg
    if (S.audioOnly) {
      var a = document.createElement('a');
      a.href = P + 'download/' + encodeURIComponent(S.file);
      a.download = S.file;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setState('已保存', 'ok');
      return;
    }

    var out = computeOutput();
    var ts = updateTime();

    var payload = {
      task: S.task,
      mode: out.mode,
      x: Math.round(out.mode === 'blur' ? out.box.x : out.rect.x),
      y: Math.round(out.mode === 'blur' ? out.box.y : out.rect.y),
      w: Math.round(out.mode === 'blur' ? out.box.w : out.rect.w),
      h: Math.round(out.mode === 'blur' ? out.box.h : out.rect.h),
      start: ts[0],
      end: ts[1]
    };
    if (payload.w < 8 || payload.h < 8) {
      showErr('处理区域太小了，换个方式试试');
      return;
    }

    S.busy = true;
    var btn = $('exportBtn');
    var old = btn.textContent;
    btn.disabled = true;
    btn.innerHTML = '<span class="spin"></span>处理中';
    setState('导出中', 'busy');

    fetch(P + 'api/crop', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (!d.ok) throw new Error(d.error || '提交失败');
      poll(d.task, null, function (res) {
        S.busy = false;
        btn.disabled = false;
        btn.textContent = old;
        setState('完成');
        if (res.error || !res.file) {
          setState('失败', 'err');
          showErr('处理失败：' + (res.error || '').slice(0, 200));
          window.scrollTo({ top: 0, behavior: 'smooth' });
          return;
        }
        showResult(res);
      });
    }).catch(function (e) {
      S.busy = false;
      btn.disabled = false;
      btn.textContent = old;
      setState('失败', 'err');
      showErr('处理失败：' + e.message);
    });
  }

  function showResult(res) {
    $('editor').style.display = 'none';
    $('result').style.display = 'block';
    $('dock').classList.remove('on');
    $('sheet').classList.remove('on');

    var url = P + 'media/' + encodeURIComponent(res.file);
    $('rv').src = url;

    var modeTxt = S.mode === 'custom'
      ? '自定义 · ' + (S.subMode === 'blur' ? '打码' : '裁切')
      : (WM[S.platform] || WM.other).name + ' · ' + (S.mode === 'blur' ? '打码' : '裁切');

    $('specs').innerHTML =
      '<div class="spec"><div class="k">分辨率</div><div class="v">' + res.width + '×' + res.height + '</div></div>' +
      '<div class="spec"><div class="k">时长</div><div class="v">' + fmt(res.duration) + '</div></div>' +
      '<div class="spec"><div class="k">体积</div><div class="v">' + (res.size / 1048576).toFixed(1) + 'MB</div></div>';
    $('modeLine').innerHTML = '处理方式 <b>' + esc(modeTxt) + '</b>';

    // 绝对地址：后端给的是带签名的 /download/...，拼上当前部署前缀
    var dlPath = res.dl
      ? P.replace(/\/$/, '') + res.dl
      : P + 'download/' + encodeURIComponent(res.file);
    var absUrl = location.origin + dlPath;
    S.dlUrl = absUrl;
    S.dlPath = dlPath;

    var save = $('saveBtn');
    if (isWeChat()) {
      // 微信内置浏览器不给存视频，主按钮换成复制链接
      save.textContent = '复制下载链接';
      save.onclick = function () { copyLink(absUrl, save); };
      $('copyBtn').style.display = 'none';
      $('ttlTip').innerHTML = '微信里存不了视频。<br>复制链接后在浏览器打开，或在浏览器里打开本页。';
    } else {
      save.textContent = '保存到手机';
      save.onclick = function () {
        location.href = dlPath;
      };
      $('copyBtn').style.display = '';
    }

    // 保留倒计时
    fetch(P + 'api/fileinfo/' + encodeURIComponent(res.file))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) return;
        S.expireAt = Date.now() + d.expiresIn * 1000;
        tickTtl();
      }).catch(function () {});

    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  /* 文件过期倒计时 */
  var ttlTimer = null;
  function tickTtl() {
    if (!S.expireAt) return;
    clearTimeout(ttlTimer);
    var left = Math.max(0, Math.floor((S.expireAt - Date.now()) / 1000));
    var tip = $('ttlTip');
    if (isWeChat() && left > 0) {
      // 微信里上面已经写了专门提示，倒计时接在后面
      tip.textContent = '文件保留 ' + Math.ceil(left / 60) + ' 分钟后自动删除';
    } else if (left <= 0) {
      tip.innerHTML = '文件已过期删除，重新处理一次即可';
      return;
    } else {
      tip.innerHTML = '文件保留 <b>' + Math.ceil(left / 60) + ' 分钟</b>后自动删除，先存下来';
    }
    ttlTimer = setTimeout(tickTtl, 20000);
  }

  function isWeChat() {
    return /MicroMessenger/i.test(navigator.userAgent);
  }

  function copyLink(text, btn) {
    var done = function () {
      var old = btn.textContent;
      btn.textContent = '✓ 已复制';
      btn.disabled = true;
      setTimeout(function () { btn.textContent = old; btn.disabled = false; }, 1600);
    };
    var fail = function () {
      btn.textContent = '复制失败，请长按视频';
      setTimeout(function () { btn.textContent = '复制下载链接'; }, 2000);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done).catch(fallback);
    } else {
      fallback();
    }
    function fallback() {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.cssText = 'position:fixed;left:-9999px;top:0;opacity:0';
      document.body.appendChild(ta);
      ta.select();
      ta.setSelectionRange(0, ta.value.length);
      var ok = false;
      try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
      document.body.removeChild(ta);
      ok ? done() : fail();
    }
  }

  $('copyBtn').addEventListener('click', function () {
    copyLink(S.dlUrl || location.href, this);
  });

  $('againBtn').addEventListener('click', function () {
    clearTimeout(ttlTimer);
    S.expireAt = 0;
    $('result').style.display = 'none';
    $('editor').style.display = 'block';
    $('dock').classList.add('on');
    render();
  });

  /* ══════════ 响应式 ══════════ */
  var rt = null;
  window.addEventListener('resize', function () {
    clearTimeout(rt);
    rt = setTimeout(function () { if (S.ready) render(); }, 180);
  });
  window.addEventListener('orientationchange', function () {
    setTimeout(function () { if (S.ready) render(); }, 400);
  });

  /* ══════════ 启动自检 ══════════ */
  fetch(P + 'api/health').then(function (r) { return r.json(); })
    .then(function (d) { if (d.ok) setState('就绪'); })
    .catch(function () { setState('未连接', 'err'); });
})();
