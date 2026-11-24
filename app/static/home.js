(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];
  const taskViews = window.ShiyingTaskViews || (typeof require !== 'undefined' ? require('./task-views.js') : {
    isActive: job => ['queued', 'discovering', 'capturing', 'downloading', 'postprocessing', 'cancelling'].includes(job.status),
    needsAction: job => ['partial', 'failed', 'needs_auth', 'interrupted', 'cancelled'].includes(job.status)
  });
  const state = { jobs: new Map(), deleted: new Set(), pendingRemoval: [], pendingSubmit: null, config: null, filter: 'all', view: 'home', ready: false, everLoaded: false, versionBlocked: false, refreshSettled: false, busy: false, refreshing: false, detailId: null, eventSource: null, sseUp: false, recovery: null, query: '', queries: {home: '', tasks: '', completed: ''}, mediaFilter: 'all', platformFilter: 'all', sortOrder: 'newest', manageCompleted: false, selectedCompleted: new Set(), visibleCompleted: [], emptyAction: 'add', composerCollapsed: undefined, cacheCheckedAt: 0, searchHits: [], searchBusy: false, searchRequestId: null,
    // 资料库分页状态：GET /api/jobs?view=library 返回 {items,next_cursor,total_estimate,index_status}。
    // order/wechatKeys 存 state.jobs 的键，真正的数据仍在 state.jobs 中，详情/移除逻辑无需改动。
    library: { order: [], wechatKeys: [], nextCursor: null, loading: false, exhausted: false, indexStatus: 'ready', total: 0, requestId: 0 },
    // GET /api/platforms 的三态能力登记（app/capabilities.py:PlatformCapability），
    // 是平台标签与状态标识的权威来源；未加载时保留下面的静态兜底文案。
    platformCapabilities: [] };
  const REQUEST_TIMEOUT_MS = 20000;
  // 资料库改走后端游标分页：limit 上限 100（app/main.py:_library_page），每页取 50。
  const LIBRARY_PAGE_SIZE = 50;
  // 前端排序值 -> 后端 sort（后端只支持 created_desc / created_asc；title 由前端对已载入页排序）。
  const LIBRARY_BACKEND_SORTS = {newest: 'created_desc', oldest: 'created_asc'};
  // 后端 media_type 白名单（app/main.py:_LIBRARY_MEDIA_TYPES）；audio/other 留给前端筛选。
  const LIBRARY_BACKEND_MEDIA = new Set(['video', 'image', 'unknown', 'mixed']);
  // Preview runs a full discovery, and scanning a profile can legitimately take
  // minutes; the QR login flow waits up to five minutes for the user. Those are
  // not the same budget as a normal settings or list request.
  // 与 app/search/service.py:TOTAL_HITS_CAP 保持一致，用于提示多平台合计上限。
  const SEARCH_TOTAL_CAP = 30;
  const PREVIEW_TIMEOUT_MS = 240000;
  // 与后端 300s 等待上限对齐，多留 10s 给响应传输；避免前端空等 6 分钟。
  const LOGIN_TIMEOUT_MS = 310000;
  // AI 搜索要串行跑多个平台（含无头浏览器），比普通请求预算长得多。
  const SEARCH_TIMEOUT_MS = 90000;
  // 后端 SearchRequest.image 的上限是 8MiB 原始字节，按 base64 data URI 字符数
  // 换算为 (8MiB+2)//3*4+64；前端必须采用同一换算（app/search/models.py），
  // 否则 6.3MB 以上的合法图片会在后端接受后被前端误拒。
  const SEARCH_IMAGE_MAX_DATA_URL_CHARS = 11184876;
  let searchImageDataUrl = '';
  let searchStatus = null;
  const platforms = { douyin: '抖音', 'selected-douyin': '抖音精选', kuaishou: '快手', xiaohongshu: '小红书', bilibili: 'B站', wechat: '视频号', youtube: 'YouTube' };
  const canonicalPlatform = value => value === 'xhs' ? 'xiaohongshu' : value === 'bili' ? 'bilibili' : value;
  const labels = { queued: '排队中', discovering: '解析中', capturing: '等待微信播放', downloading: '下载中', postprocessing: '正在合并', completed: '已完成', partial: '部分完成', failed: '下载失败', needs_auth: '需要登录', cancelled: '已取消', cancelling: '正在取消', interrupted: '已中断', paused: '已暂停' };
  const active = job => taskViews.isActive(job);
  const failed = job => taskViews.needsAction(job);
  const recreateRequired = job => job.wechat || job.status === 'interrupted' || job.issue_code === 'cookie_unavailable' || (job.status === 'partial' && !(job.items || []).some(i => ['failed','needs_auth','cancelled','paused'].includes(i.status)));
  const tone = job => job.status === 'completed' ? 'success' : active(job) ? 'active' : job.status === 'needs_auth' || job.status === 'partial' || job.status === 'paused' ? 'warning' : failed(job) ? 'failed' : '';
  const escape = value => String(value ?? '').replace(/[&<>"']/g, ch => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[ch]));
  // 平台返回的标题/作者名可能已带 HTML 实体（如 &amp;）。直接 escape 会把 & 再
  // 转一次成为 &amp;amp;，页面显示成字面实体。先还原实体再统一 escape：XSS 中
  // 的危险字符仍会被转义为文本，防护不受影响。
  const decodeHtmlEntities = value => String(value ?? '')
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&#0*39;|&apos;/g, "'")
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&');
  const escapeDisplay = value => escape(decodeHtmlEntities(value));
  const icon = name => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const platformIcon = value => {
    const key = canonicalPlatform(value);
    if (!Object.hasOwn(platforms, key)) return icon('film');
    const extension = key === 'wechat' ? 'svg' : key === 'bilibili' ? 'jpg' : 'png';
    return `<img class="thumb-platform-icon" src="/static/platform-icons/${key}.${extension}" alt="" width="22" height="22" aria-hidden="true">`;
  };
  const localize = value => window.ShiyingRuntime?.localizeRuntimeMessage(value) || String(value || '');
  const bytes = value => {
    if (!Number.isFinite(value) || value <= 0) return '—';
    const units = ['B','KB','MB','GB']; let n = value, i = 0;
    while (n >= 1024 && i < 3) { n /= 1024; i++; }
    return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
  };
  function toast(message, error = false, action = null) {
    const node = document.createElement('div'); node.className = `toast${error ? ' error' : ''}`; node.textContent = message;
    if (action) {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'toast-action'; button.textContent = action.label;
      button.addEventListener('click', () => { action.run(); node.remove(); }); node.append(button);
    }
    $('#toast-region').append(node); window.setTimeout(() => node.remove(), error || action ? 8500 : 3500);
  }

  const loadedCovers = new Set();
  const failedCovers = new Set();
  // 封面回调只传 this，任务标识经 data-* 属性（dataset 已按 HTML 语义解码）
  // 读取：内联事件属性会先做实体解码再编译为 JS，直接把值拼进 JS 字符串
  // 是脆弱模式，一旦插值来源变化就会变成注入点。
  window._onCoverLoad = (img) => {
    const id = img?.dataset?.coverJob;
    if (!id || img.isConnected === false) return;
    loadedCovers.add(id);
    failedCovers.delete(id);
    img.classList.add('loaded');
  };
  window._onCoverError = (img) => {
    const id = img?.dataset?.coverJob;
    const requestStatus = img?.dataset?.coverStatus;
    if (!id || img.isConnected === false) return;
    const job = state.jobs.get(`j:${id}`) || state.jobs.get(`w:${id}`);
    loadedCovers.delete(id);
    failedCovers.add(id);
    img.remove();
    const list = $('#task-list');
    if (list) list._html = null;
    if (job && requestStatus !== 'completed' && job.status === 'completed') {
      failedCovers.delete(id);
      render();
    }
  };
  // 搜索结果缩略图：只负责显示/回退，不依赖任务状态；失败时露出占位层。
  window._onSearchCoverLoad = (img) => {
    if (img) img.classList.add('loaded');
  };
  window._onSearchCoverError = (img) => {
    if (img) img.remove();
  };

  let formMessageTimer = null;
  let libraryReloadTimer = null;
  let libraryObserver = null;
  function setFormMessage(text, autoDismissMs = 6000) {
    if (formMessageTimer) {
      if (typeof clearTimeout !== 'undefined') clearTimeout(formMessageTimer);
      else if (typeof window !== 'undefined' && window.clearTimeout) window.clearTimeout(formMessageTimer);
      formMessageTimer = null;
    }
    const box = $('#form-message');
    if (!box) return;
    const textEl = $('#form-message-text') || box;
    if (!text) {
      box.hidden = true;
      textEl.textContent = '';
      return;
    }
    textEl.textContent = text;
    box.hidden = false;
    if (autoDismissMs > 0) {
      const scheduleFn = typeof setTimeout !== 'undefined' ? setTimeout : (typeof window !== 'undefined' ? window.setTimeout : null);
      if (scheduleFn) {
        formMessageTimer = scheduleFn(() => {
          setFormMessage('');
        }, autoDismissMs);
      }
    }
  }
  async function api(path, method = 'GET', body, options = {}) {
    const timeoutMs = options.timeoutMs || REQUEST_TIMEOUT_MS;
    let response;
    try {
      response = await fetch(path, { method, headers: body === undefined ? {} : { 'Content-Type':'application/json' }, body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(timeoutMs) });
    } catch (error) {
      if (error && (error.name === 'TimeoutError' || error.name === 'AbortError')) {
        const failure = new Error(`请求超过 ${Math.round(timeoutMs / 1000)} 秒没有响应，后台可能仍在处理。请刷新任务列表确认后再操作。`);
        failure.timeout = true;
        throw failure;
      }
      throw error;
    }
    if (!response.ok) {
      let detail; try { detail = (await response.json()).detail; } catch { detail = null; }
      // 搜索接口统一返回 {"message","issue_code"} 对象，FastAPI 校验错误则是 detail 数组。
      const message = typeof detail === 'string' ? detail
        : typeof detail?.message === 'string' ? detail.message
        : detail?.[0]?.msg;
      const error = new Error(message || `请求失败（${response.status}），请稍后重试。`);
      error.httpStatus = response.status;
      error.code = detail && typeof detail === 'object' ? detail.code : undefined;
      throw error;
    }
    return response.json();
  }
  // 实验通道需用户明确确认：后端返回 409 + code，弹窗同意后带字段重试。
  // 会话内确认一次即可，避免批量下载时反复弹窗。
  let experimentalConfirmed = false;
  async function createJobWithConfirm(body) {
    const payload = experimentalConfirmed ? { ...body, confirmed_experimental: true } : body;
    try {
      return await api('/api/jobs', 'POST', payload);
    } catch (error) {
      if (error?.code !== 'experimental_confirmation_required' || experimentalConfirmed) throw error;
      if (!window.confirm(`${error.message || '该平台为实验通道'}\n\n仍要创建下载任务吗？`)) throw error;
      experimentalConfirmed = true;
      return api('/api/jobs', 'POST', { ...body, confirmed_experimental: true });
    }
  }
  function connection(ready, message) {
    state.ready = ready; $('#connection').className = `connection ${ready ? 'online' : 'offline'}`;
    $('#connection span').textContent = message || (ready ? '本地运行' : '连接中断');
    $('#submit-button').disabled = !ready || state.busy;
    if ($('#preview-button')) $('#preview-button').disabled = !ready || state.busy;
  }
  function put(job, wechat = false) {
    const entry = wechat ? { ...job, wechat: true, platform:'wechat', source_url:job.url, items:[] } : job;
    const key = `${wechat ? 'w' : 'j'}:${job.id}`;
    if (state.deleted.has(key)) return key;
    const old = state.jobs.get(key);
    if (!old || wechat || (entry.revision ?? 0) >= (old.revision ?? 0)) {
      state.jobs.set(key, entry);
      if (old && old.status !== entry.status) {
        failedCovers.delete(entry.id);
      }
      if (state.ready && old && old.status !== 'completed' && entry.status === 'completed') {
        toast(`下载完成：${title(entry)}`, false, {label: '查看', run: () => {
          state.sortOrder = 'newest'; view('completed'); resetListFilters(); $('#completed-sort').value = 'newest';
        }});
      }
    }
    return key;
  }
  function title(job) {
    return job.title || job.items?.find(i => i.title && i.title !== 'Untitled')?.title || (job.status === 'discovering' ? '正在读取作品信息…' : `${platforms[canonicalPlatform(job.platform)] || '视频'}分享作品`);
  }
  function progress(job) {
    const items = job.items || [];
    const item = items.find(i => i.id === job.active_item_id) || items.find(i => ['downloading','postprocessing'].includes(i.status));
    const p = item?.progress || {};
    const downloaded = job.wechat ? job.downloaded_bytes : items.reduce((n, i) => n + (i.progress?.downloaded_bytes || 0), 0);
    const total = job.wechat ? job.total_bytes : items.reduce((n, i) => n + (i.progress?.total_bytes || 0), 0);
    // Unknown totals stay unknown; completed file count is not a byte percentage.
    const percent = job.wechat ? job.percent : p.percent;
    const speed = job.wechat ? job.speed_bytes_per_second : p.speed_bytes_per_second;
    return { downloaded, total, percent: Number.isFinite(percent) ? Math.max(0, Math.min(100, percent)) : null, speed };
  }
  function message(job) {
    if (job.wechat) return job.message || '';
    if (job.error || job.auth_message || job.issue_message || job.items?.some(i => i.error || i.auth_message)) return window.ShiyingRuntime?.localizedPrimaryJobIssueMessage(job) || localize(job.error || job.auth_message);
    return '';
  }
  function formatTaskError(issue) {
    if (!issue) return '';
    const lines = String(issue).split('\n').map(l => l.trim()).filter(Boolean);
    if (!lines.length) return '';
    if (lines.some(l => l.includes('尚未归类的异常') || l.includes('遇到异常'))) {
      const detailLine = lines.find(l => /^(具体情况|技术详情|具体原因)[：:]/.test(l) && !l.includes('尚未归类的异常') && !l.includes('程序遇到了'));
      if (detailLine) {
        return detailLine.replace(/^(具体情况|技术详情|具体原因)[：:]\s*/, '').replace(/^发生了什么[：:]\s*/, '');
      }
      const meaningfulLine = lines.find(l => !l.includes('尚未归类的异常') && !l.includes('程序遇到了') && !l.startsWith('解决办法：') && !l.startsWith('解决办法:') && !l.includes('先从原链接重试一次'));
      if (meaningfulLine) {
        return meaningfulLine.replace(/^(具体情况|技术详情|具体原因)[：:]\s*/, '').replace(/^发生了什么[：:]\s*/, '');
      }
    }
    return lines[0].replace(/^发生了什么[：:]\s*/, '');
  }
  function formatDetailIssue(issue) {
    if (!issue) return '';
    const text = String(issue).trim();
    const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
    let desc = '', sol = '', detail = '';
    for (const line of lines) {
      if (/^发生了什么[：:]/.test(line)) {
        desc = line.replace(/^发生了什么[：:]\s*/, '');
      } else if (/^解决办法[：:]/.test(line)) {
        sol = line.replace(/^解决办法[：:]\s*/, '');
      } else if (/^(具体情况|技术详情|具体原因)[：:]/.test(line)) {
        detail = (detail ? detail + '\n' : '') + line;
      } else if (desc && !sol) {
        desc += '\n' + line;
      } else if (sol && !detail) {
        sol += '\n' + line;
      } else {
        detail = (detail ? detail + '\n' : '') + line;
      }
    }
    if (!desc && !sol) {
      return `<div class="detail-issue" role="note"><div class="detail-issue-text">${escape(text)}</div></div>`;
    }
    if (desc.includes('尚未归类的异常') || desc.includes('遇到异常')) {
      const meaningful = lines.find(l => !l.includes('尚未归类的异常') && !l.includes('程序遇到了') && !l.startsWith('解决办法：') && !l.startsWith('解决办法:') && !l.includes('先从原链接重试一次'));
      if (meaningful) {
        desc = meaningful.replace(/^(具体情况|技术详情|具体原因)[：:]\s*/, '').replace(/^发生了什么[：:]\s*/, '');
      }
    }
    let html = '<div class="detail-issue" role="note">';
    if (desc) {
      html += `<div class="detail-issue-section"><strong class="detail-issue-label">原因</strong><p class="detail-issue-text">${escape(desc)}</p></div>`;
    }
    if (sol) {
      html += `<div class="detail-issue-section"><strong class="detail-issue-label">建议</strong><p class="detail-issue-text">${escape(sol)}</p></div>`;
    }
    if (detail) {
      html += `<details class="detail-issue-tech"><summary>技术细节</summary><pre>${escape(detail)}</pre></details>`;
    }
    html += '</div>';
    return html;
  }
  function renderTaskRow(key, job, library) {
    const p = progress(job), isActive = active(job), completed = job.status === 'completed', issue = message(job), platform = canonicalPlatform(job.platform);
    const output = taskViews.files(job), availability = taskViews.fileAvailability(job);
    const media = {video: '视频', audio: '音频', image: '图文', other: '文件'}[taskViews.mediaType(job)];
    const statusLabel = labels[job.status] || window.ShiyingRuntime?.statusLabel(job) || '状态未知';
    const meta = [platforms[platform] || job.platform || '其他平台'];
    if (job.author) meta.push(job.author);
    if (job.items?.[0]?.metadata?.access_mode === 'public') meta.push('游客解析');
    meta.push(media);
    if (completed && output.length > 1) meta.push(`${output.length} 个文件`);
    else if (!completed && job.items?.length > 1) meta.push(`${job.completed_items || 0} / ${job.total_items || job.items.length} 项已保存`);
    if (job.items?.[0]?.resolution) meta.push(job.items[0].resolution);
    const date = completed ? taskViews.completionTime(job) : job.created_at;
    if (date && Number.isFinite(Date.parse(date))) meta.push(new Date(date).toLocaleString('zh-CN', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'}));
    const kind = job.resolved_source_kind || job.source_kind;
    const isCollection = ['playlist', 'profile'].includes(kind);
    const sourceUrl = detailUrl(job.source_url || job.url);
    const workUrl = !isCollection && job.items?.[0]?.source_url ? detailUrl(job.items[0].source_url) : sourceUrl;
    let actions = ''; const menu = [];
    if (completed) {
      if (availability === 'missing') actions = '<button class="secondary" data-action="retry">重新下载</button>';
      else if (output.length === 1 && output[0].exists === true && output[0].can_open) actions = `<button class="secondary" data-action="open-file">${icon('play')}打开${media === '文件' ? '文件' : media === '图文' ? '图片' : media}</button>`;
      else actions = '<button class="secondary" data-action="detail">查看文件</button>';
      actions += `<button class="icon-button folder-action" data-action="folder" aria-label="打开文件夹" title="打开此任务文件实际所在的文件夹">${icon('folder')}</button>`;
    } else if (isActive) {
      if (!job.wechat && job.status === 'downloading') actions = '<button class="secondary" data-action="pause">暂停</button>';
      menu.push(`<button data-action="cancel" ${job.status === 'cancelling' ? 'disabled' : ''}>取消下载</button>`);
    } else if (job.status === 'paused') {
      actions = '<button class="secondary" data-action="resume">继续</button>';
      menu.push('<button data-action="cancel">取消下载</button>');
    } else if (failed(job) && Object.hasOwn(labels, job.status) && job.retryable !== false) {
      actions = `<button class="secondary" data-action="retry">${recreateRequired(job) ? '重新下载' : job.status === 'partial' ? '重试失败项' : '重试'}</button>`;
    }
    if (failed(job) && workUrl && !isCollection) {
      actions += `<a class="icon-button web-action" href="${escape(workUrl)}" target="_blank" rel="noopener noreferrer" aria-label="在浏览器中打开作品" title="在默认浏览器中打开原网页观看">${icon('link')}</a>`;
    }
    if (job.status === 'partial' && output.length) actions += '<button class="secondary" data-action="detail">查看已保存文件</button>';
    if (sourceUrl) {
      const openLabel = isCollection ? (kind === 'profile' ? '在浏览器中打开主页' : '在浏览器中打开合集') : '在浏览器中打开作品';
      menu.push(`<a class="task-menu-item" href="${escape(workUrl || sourceUrl)}" target="_blank" rel="noopener noreferrer">${icon('link')}<span>${escape(openLabel)}</span></a>`);
    }
    menu.push('<button data-action="detail">查看详情</button>');
    if (job.status === 'needs_auth') menu.push('<button data-action="verify">登录 / 验证</button>');
    if (!isActive) menu.push('<button data-action="remove">移除记录</button>');
    actions += `<details class="task-more"><summary aria-label="${escape(title(job))}的更多操作">更多</summary><div class="task-menu">${menu.join('')}</div></details>`;
    const merging = job.status === 'postprocessing' || job.items?.some(item => item.status === 'postprocessing');
    const info = job.status === 'discovering' ? localize(job.activity_message || '正在解析作品，等待平台响应…') : job.status === 'queued' ? '等待前面的任务完成' : job.status === 'capturing' ? '等待微信播放，请按任务详情中的提示操作' : job.status === 'cancelling' ? '正在停止请求…' : merging ? '正在合并音视频…' : `${p.percent === null ? '传输中' : p.percent.toFixed(0) + '%'}${p.speed > 0 ? ' · ' + bytes(p.speed) + '/s' : ''}`;
    const completeSize = output.length && output.every(file => Number.isFinite(file.size_bytes)) ? output.reduce((n, file) => n + file.size_bytes, 0) : job.wechat ? job.size_bytes : job.items?.length && job.items.every(item => Number.isFinite(item.metadata?.output_size_bytes)) ? job.items.reduce((n, item) => n + item.metadata.output_size_bytes, 0) : null;
    const size = completed ? bytes(completeSize) : p.total > 0 ? `${bytes(p.downloaded)} / ${bytes(p.total)}` : bytes(p.downloaded);
    const showBar = job.status === 'downloading' && !merging && p.percent !== null;
    const firstIssue = formatTaskError(issue);
    const coverUrl = `/api/jobs/${encodeURIComponent(job.id)}/cover`;
    const hasCover = !job.wechat && !failedCovers.has(job.id), isLoaded = loadedCovers.has(job.id);
    const imgTag = hasCover ? `<img class="task-thumb-img${isLoaded ? ' loaded' : ''}" src="${coverUrl}" alt="" loading="lazy" data-cover-job="${escape(job.id)}" data-cover-status="${escape(job.status)}" onload="window._onCoverLoad&&window._onCoverLoad(this)" onerror="window._onCoverError&&window._onCoverError(this)">` : '';
    const missing = availability === 'missing' ? '文件已移动或删除' : availability === 'partial' ? '部分文件已移动或删除' : '';
    // 影像档案身份：无真实封面时按内容形态使用对应的缺省封面，而不是让所有
    // 任务都套同一个 film 占位符。mediaType 来自 task-views.js 的稳定分类，
    // 实况照片另由 metadata 标记识别（静态图与动态轨成对）。
    const mediaKind = taskViews.mediaType(job);
    const isLive = (job.items || []).some(item => item.metadata?.live_photo || item.media_type === 'live');
    const fallbackClass = isLive ? 'media-live' : mediaKind === 'image' ? 'media-image' : 'media-video';
    return `<article class="task-row platform-${escape(platform)}${library ? ' completed-row' : ''}${state.selectedCompleted.has(key) ? ' row-selected' : ''}" data-key="${escape(key)}">
      <div class="content-cell">
        ${library && state.manageCompleted ? `<label class="task-select"><input type="checkbox" data-select-key="${escape(key)}" aria-label="选择：${escape(title(job))}" ${state.selectedCompleted.has(key) ? 'checked' : ''}${state.versionBlocked ? ' disabled' : ''}></label>` : ''}
        <div class="task-thumb-wrap ${fallbackClass}${isLoaded ? ' has-cover' : ''}" aria-hidden="true">${imgTag}<div class="media-placeholder" aria-hidden="true">${platformIcon(platform)}<small>${escape(platforms[platform] || '文件')}</small></div></div>
        <div class="task-copy">
          <button class="task-title" data-action="detail" title="${escape(title(job))}">${escape(title(job))}</button>
          <p class="task-meta">${escape(meta.join(' · '))}</p>
          ${isActive ? `<div class="mini-progress">${showBar ? `<div class="track" role="progressbar" aria-label="当前文件下载进度" aria-valuenow="${p.percent}" aria-valuemin="0" aria-valuemax="100"><span style="width:${p.percent}%"></span></div>` : ''}<span class="transfer">${escape(info)}</span></div>` : ''}
          ${failed(job) && firstIssue ? `<p class="task-error" title="${escape(issue)}">${escape(firstIssue)}</p>` : ''}
          ${completed && missing ? `<p class="task-file-warning">${icon('alert')}${missing}</p>` : ''}
        </div>
      </div>
      ${library ? '' : `<div class="status-cell"><span class="status ${tone(job)}">${escape(statusLabel)}</span></div>`}
      <div class="size-cell">${escape(size)}</div><div class="operations">${actions}</div>
    </article>`;
  }
  function render() {
    const jobs = [...state.jobs.entries()], counts = taskViews.counts(jobs), library = state.view === 'completed';
    // 资料库的行数据来自后端分页结果（order + 微信补充）；其余视图仍用完整任务表。
    const source = library ? libraryEntries() : jobs;
    $('#nav-count').textContent = counts.unfinished;
    $('#nav-count').title = `${counts.unfinished} 个未完成任务，含暂停和需处理任务`;
    $('[data-view="tasks"]').setAttribute('aria-label', `下载任务，${counts.unfinished} 个未完成`);
    for (const name of ['unfinished', 'active', 'paused', 'failed']) $(`#${name}-count`).textContent = counts[name];
    $$('#task-tabs button').forEach(b => { b.classList.toggle('active', b.dataset.filter === state.filter); b.setAttribute('aria-pressed', String(b.dataset.filter === state.filter)); });
    $$('#media-tabs button').forEach(b => { b.classList.toggle('active', b.dataset.mediaFilter === state.mediaFilter); b.setAttribute('aria-pressed', String(b.dataset.mediaFilter === state.mediaFilter)); });
    $('[data-media-filter="other"]').hidden = state.mediaFilter !== 'other' && !jobs.some(([, job]) => job.status === 'completed' && taskViews.mediaType(job) === 'other');
    // 后端已用同一组条件过滤/排序；这里对「已载入的页」再跑一次 select 是幂等的，
    // 同时让微信补充记录与 title 排序、audio/other 等仅前端支持的筛选项保持一致。
    const matched = taskViews.select(source, {view: state.view, filter: state.filter, type: state.mediaFilter, platform: state.platformFilter, sort: state.sortOrder, query: state.query, platformNames: platforms});
    const shown = state.view === 'home' ? matched.slice(0, 6) : matched;
    state.visibleCompleted = library ? matched.map(([key]) => key) : [];
    const visibleKeys = new Set(state.visibleCompleted);
    for (const key of state.selectedCompleted) if (!visibleKeys.has(key)) state.selectedCompleted.delete(key);
    $('#completed-batch').hidden = !library || !state.manageCompleted;
    $('#manage-completed').setAttribute('aria-pressed', String(state.manageCompleted));
    $('#manage-completed').textContent = state.manageCompleted ? '完成管理' : '批量管理';
    $('#manage-completed').disabled = state.versionBlocked || (!counts.completed && !state.manageCompleted);
    $('#completed-selection-count').textContent = `已选 ${state.selectedCompleted.size} 条`;
    $('#remove-selected').disabled = state.versionBlocked || !state.selectedCompleted.size;
    $('#select-visible').disabled = state.versionBlocked || !state.visibleCompleted.length;
    $('#select-visible').checked = state.visibleCompleted.length > 0 && state.selectedCompleted.size === state.visibleCompleted.length;
    $('#select-visible').indeterminate = state.selectedCompleted.size > 0 && state.selectedCompleted.size < state.visibleCompleted.length;
    const html = shown.map(([key, job]) => renderTaskRow(key, job, library)).join('');
    // Avoid replacing focused controls when no underlying data changed; when the
    // data did change, put keyboard focus back on the same task action instead of
    // dropping it to <body> on every progress event.
    const list = $('#task-list');
    if (list._html !== html) {
      const activeNode = document.activeElement;
      const row = activeNode && activeNode.closest ? activeNode.closest('[data-key]') : null;
      const focusKey = row ? row.dataset.key : null;
      const focusAction = activeNode && activeNode.dataset ? activeNode.dataset.action : null;
      const focusSelectKey = activeNode && activeNode.dataset ? activeNode.dataset.selectKey : null;
      const keepFocus = Boolean(focusKey);

      // 提取已有行中已经加载成功的缩略图 DOM 节点，重渲染时直接复用，彻底根治重试与进度刷新时的封面闪烁
      const thumbCache = new Map();
      for (const rowNode of list.querySelectorAll('[data-key]')) {
        const wrap = rowNode.querySelector('.task-thumb-wrap');
        const img = wrap && wrap.querySelector('.task-thumb-img');
        if (wrap && img && img.classList.contains('loaded') && img.naturalWidth > 0) {
          thumbCache.set(rowNode.dataset.key, wrap);
        }
      }

      list.innerHTML = html; list._html = html;

      if (thumbCache.size) {
        for (const rowNode of list.querySelectorAll('[data-key]')) {
          const cachedWrap = thumbCache.get(rowNode.dataset.key);
          if (cachedWrap) {
            const currentWrap = rowNode.querySelector('.task-thumb-wrap');
            if (currentWrap) currentWrap.replaceWith(cachedWrap);
          }
        }
      }

      if (keepFocus) {
        const restoredRow = [...list.querySelectorAll('[data-key]')].find(node => node.dataset.key === focusKey);
        // 批量管理的复选框没有 data-action；重渲染后必须把焦点还给同一行的同一
        // 复选框，否则键盘用户按 Space 勾选后，下一次 Space/Enter 会误触发标题
        // 按钮打开详情，而不是取消勾选。
        let target = null;
        if (restoredRow) {
          if (focusSelectKey) {
            target = [...restoredRow.querySelectorAll('input[data-select-key]')]
              .find(node => node.dataset.selectKey === focusSelectKey);
          }
          if (!target && focusAction) target = restoredRow.querySelector(`[data-action="${focusAction}"]`);
          if (!target) target = restoredRow.querySelector('.task-title');
        }
        if (target) target.focus({ preventScroll: true });
      }
    }
    // 状态矩阵：loading / offline / empty-first / empty-filter / ready 互斥。
    // 历史缺陷是「连接失败」与「空库」同屏出现，首次失败时并不知道用户是否
    // 有历史记录，因此绝不给出空库结论；这里用单一 list-state 驱动全部表达。
    const section = $('#tasks-section');
    // refreshSettled 区分「首屏还没读完」与「首次读取就失败」：后者必须立刻
    // 显示离线面板，不能一直挂在骨架上，也不能降级成空库结论。
    const listState = !state.ready && !state.refreshSettled ? 'loading'
      : !state.ready ? 'offline'
      : shown.length > 0 ? 'ready'
      : (Boolean(state.query.trim()) || (library ? state.mediaFilter !== 'all' || state.platformFilter !== 'all' : state.view === 'tasks' && state.filter !== 'all')) ? 'empty-filter'
      : 'empty-first';
    section.dataset.listState = listState;
    $('#offline-panel').hidden = listState !== 'offline';
    const empty = $('#empty-state');
    empty.hidden = shown.length > 0 || !state.ready || !$('#list-error').hidden;
    const searching = Boolean(state.query.trim());
    const filtered = searching || (library ? state.mediaFilter !== 'all' || state.platformFilter !== 'all' : state.view === 'tasks' && state.filter !== 'all');
    const scopeCount = library ? (state.library.total || matched.length) : state.view === 'tasks' ? counts.unfinished : jobs.length;
    state.emptyAction = filtered ? 'reset' : state.view === 'tasks' && counts.completed ? 'completed' : 'add';
    empty.querySelector('h3').textContent = filtered ? library ? '没有找到匹配的内容' : '没有匹配的未完成任务' : library ? '还没有下载完成的内容' : state.view === 'tasks' ? '暂无未完成任务' : '从一条喜欢的链接开始';
    empty.querySelector('p').textContent = filtered ? '试试其他关键词，或清除筛选查看当前页面的全部结果。' : library ? '成功下载的内容会自动出现在这里；部分完成的任务仍在“下载任务”中。' : state.view === 'tasks' ? counts.completed ? '下载好的内容已经收纳到“已完成”。' : '添加分享链接后，可以在这里查看进度和处理下载问题。' : '粘贴上方分享链接，下载任务会出现在这里。';
    $('#empty-action').textContent = filtered ? '清除筛选' : state.emptyAction === 'completed' ? '查看已完成' : library ? '去添加下载' : '新建下载';
    const illustration = $('#empty-illustration');
    if (illustration) {
      illustration.src = filtered || listState !== 'empty-first' ? '/static/media/empty-result.webp' : '/static/media/empty-first.webp';
      illustration.alt = '';
    }
    $('#task-summary').textContent = !state.ready
      ? (state.everLoaded ? '暂时无法读取任务 · 显示最后一次成功读取的内容' : '暂时无法读取任务')
      : state.view === 'home' ? `最近 ${shown.length} 个任务 · ${counts.unfinished} 个未完成 · ${counts.completed} 个已完成`
      : library ? `已载入 ${matched.length} 条${state.library.total ? ` / 共约 ${state.library.total} 条` : ''}${filtered ? ' · 当前筛选' : ''}`
      : `${scopeCount} 个未完成任务${filtered ? ` · 当前筛选 ${matched.length} 个` : ''}`;
    $('#task-footer-note').textContent = library
      ? `${state.library.indexStatus && state.library.indexStatus !== 'ready' ? '索引重建中 · 显示回退结果 · ' : ''}移除记录不会删除本地文件`
      : '文件保存在本机 · 不上传';
    updateLibraryMore();
    // 移动端底部 Tab 同步未完成计数；不依赖侧栏的 #nav-count（窄屏侧栏隐藏）。
    const mobileCount = document.querySelector('.mobile-tabs .count');
    if (mobileCount) mobileCount.textContent = counts.unfinished;
    updateComposerCompact();
    if ($('#details-dialog').open) renderDetails();
  }
  let detailBusy = false;
  function detailItems(job) {
    if (job.wechat) return [{ id: 'legacy', title: job.title || title(job), status: job.status, resolution: job.resolution, output_paths: job.path ? [job.path] : [], metadata: { output_files: job.output_files || [] }, progress: { percent: job.percent, downloaded_bytes: job.downloaded_bytes, total_bytes: job.total_bytes, speed_bytes_per_second: job.speed_bytes_per_second } }];
    const selected = Array.isArray(job.selected_item_ids) ? new Set(job.selected_item_ids) : null;
    return (job.items || []).filter(item => !selected || selected.has(item.id));
  }
  function detailFiles(item) {
    return (item.output_paths || []).map((path, index) => {
      const facts = item.metadata?.output_files?.find(file => file.index === index && file.path === path);
      return { index, path, name: String(path).split(/[\\/]/).pop(), exists: null, size_bytes: null, can_open: false, ...facts };
    });
  }
  function detailSummary(job) {
    const items = detailItems(job), files = items.flatMap(detailFiles);
    const count = status => items.filter(item => item.status === status).length;
    const total = Math.max(items.length, Number(job.total_items) || 0, job.selected_item_ids?.length || 0);
    const available = files.filter(file => file.exists === true), missing = files.filter(file => file.exists === false).length;
    return { items, files, total, completed: count('completed'), failed: count('failed') + count('needs_auth'), skipped: count('skipped'), cancelled: count('cancelled'), available: available.length, missing, unknown: files.filter(file => file.exists === null).length, size: available.reduce((sum, file) => sum + (file.size_bytes || 0), 0) };
  }
  function detailTitle(job, items) {
    if (job.title) return job.title;
    const kind = job.resolved_source_kind || job.source_kind;
    if (kind === 'playlist') return `${job.author || platforms[canonicalPlatform(job.platform)] || '分享'}的合集`;
    if (kind === 'profile') return `${job.author || platforms[canonicalPlatform(job.platform)] || '作者'}的作品`;
    return items.find(item => item.title && item.title !== 'Untitled')?.title || title(job);
  }
  function detailUrl(value) {
    if (!value || typeof value !== 'string') return '';
    try {
      const url = new URL(value.trim());
      if (!['http:', 'https:'].includes(url.protocol)) return '';
      if (url.username || url.password) { url.username = ''; url.password = ''; }
      return url.href;
    } catch { return ''; }
  }
  function detailSize(value) { return value === 0 ? '0 B' : bytes(value); }
  function detailQuality(item) {
    const match = /^(\d+)\s*[x×]\s*(\d+)$/i.exec(item.resolution || '');
    if (!match) return item.resolution && item.resolution !== 'unknown' ? item.resolution : '';
    const width = Number(match[1]), height = Number(match[2]), edge = Math.min(width, height);
    return [360, 480, 720, 1080, 1440, 2160, 4320].includes(edge) ? `${edge}p${height > width ? ' 竖屏' : ''}` : `${width} × ${height}`;
  }
  function detailParent(path) {
    const value = String(path || ''), end = Math.max(value.lastIndexOf('/'), value.lastIndexOf('\\'));
    return end === 0 || end === 2 && value[1] === ':' ? value.slice(0, end + 1) : end > 0 ? value.slice(0, end) : '';
  }
  function detailDirectory(job, files) {
    const parents = [...new Set(files.map(file => detailParent(file.path)).filter(Boolean))];
    return parents.length === 1 ? parents[0] : job.output_dir || job.output_root || state.config?.download_dir || '';
  }
  function detailButton(action, label, options = {}) {
    const attrs = `${options.itemId ? ` data-item-id="${escape(options.itemId)}"` : ''}${Number.isInteger(options.fileIndex) ? ` data-file-index="${options.fileIndex}"` : ''}${options.jobAction ? ` data-action="${escape(options.jobAction)}"` : ''}`;
    return `<button type="button" class="${options.primary ? 'primary' : options.text ? 'text-button' : 'secondary'}" data-detail-action="${action}"${attrs}${options.disabled || detailBusy && action !== 'close' ? ' disabled' : ''}>${options.icon ? icon(options.icon) : ''}${escape(label)}</button>`;
  }
  function detailProgress(job, items) {
    if (!active(job) && job.status !== 'postprocessing') return '';
    const item = items.find(item => item.id === job.active_item_id) || items.find(item => ['downloading', 'postprocessing'].includes(item.status));
    let phase = '';
    if (job.cancel_requested || job.status === 'cancelling') phase = '正在停止请求，已保存的文件会保留。';
    else if (job.pause_requested) phase = '正在暂停，请等待当前处理安全停止。';
    else if (item?.status === 'postprocessing' || job.status === 'postprocessing') phase = '传输已结束，正在合并音视频；合并完成后才算下载完成。';
    else if (job.status === 'discovering') phase = localize(job.activity_message || '正在读取作品信息，数量会随解析更新。');
    else if (job.status === 'queued') phase = '正在排队，等待前面的任务完成。';
    else if (job.status === 'capturing') phase = localize(job.message || '等待在微信中播放要保存的内容。');
    if (phase) return `<div class="detail-progress"><p>${escape(phase)}</p></div>`;
    const p = job.wechat ? items[0]?.progress || {} : item?.progress || {};
    const percent = Number.isFinite(p.percent) ? Math.max(0, Math.min(100, p.percent)) : null;
    const transfer = [];
    if (percent !== null) transfer.push(`${percent.toFixed(0)}%`);
    if (p.downloaded_bytes > 0) transfer.push(p.total_bytes > 0 ? `${bytes(p.downloaded_bytes)} / ${bytes(p.total_bytes)}` : `已传输 ${bytes(p.downloaded_bytes)}`);
    if (p.speed_bytes_per_second > 0) transfer.push(`${bytes(p.speed_bytes_per_second)}/s`);
    if (Number.isFinite(p.eta_seconds) && p.eta_seconds > 0) transfer.push(`预计剩余 ${p.eta_seconds < 60 ? `${Math.ceil(p.eta_seconds)} 秒` : `${Math.ceil(p.eta_seconds / 60)} 分钟`}`);
    return `<div class="detail-progress">${items.length > 1 && item ? `<p class="detail-current">当前作品：${escape(item.title || '未命名作品')}</p>` : ''}${percent !== null ? `<div class="track" role="progressbar" aria-label="当前作品传输进度" aria-valuenow="${percent}" aria-valuemin="0" aria-valuemax="100"><span style="width:${percent}%"></span></div>` : ''}<p>${escape(transfer.join('　') || '正在传输，暂时无法估算剩余时间。')}</p></div>`;
  }
  function detailFileActions(item, file) {
    const options = { itemId: item.id, fileIndex: file.index };
    const suffix = file.name.split('.').pop().toLowerCase();
    const label = ['mp3', 'm4a', 'aac', 'flac', 'ogg', 'wav', 'opus'].includes(suffix) ? '播放音频' : item.media_type === 'image' ? '打开图片' : '打开文件';
    return `<div class="detail-file-actions">${detailButton('open', label, { ...options, disabled: !file.can_open })}${detailButton('reveal', '定位文件', { ...options, text: true, disabled: file.exists !== true })}${detailButton('copy-file', '复制路径', { ...options, text: true })}</div>`;
  }
  function detailItem(job, item) {
    const files = detailFiles(item), parts = [];
    const quality = detailQuality(item); if (quality) parts.push(quality);
    const extensions = [...new Set(files.map(file => file.name.includes('.') ? file.name.split('.').pop().toUpperCase() : '').filter(Boolean))];
    if (extensions.length) parts.push(extensions.join(' / '));
    if (files.length && files.every(file => Number.isFinite(file.size_bytes))) parts.push(detailSize(files.reduce((sum, file) => sum + file.size_bytes, 0)));
    if (files.length > 1) parts.push(`${files.length} 个文件`);
    const missing = files.filter(file => file.exists === false).length;
    const itemIssue = localize(item.error || item.auth_message);
    const retry = !job.wechat && !active(job) && job.retryable !== false && item.retryable !== false && ['failed', 'needs_auth', 'cancelled', 'paused'].includes(item.status);
    const isCollection = ['playlist', 'profile'].includes(job.resolved_source_kind || job.source_kind);
    const itemUrl = detailUrl(item.source_url || (!isCollection ? job.source_url : ''));
    let output = '';
    if (files.length === 1) output = detailFileActions(item, files[0]);
    else if (files.length > 1) output = `<details class="detail-files" data-detail-section="files-${escape(item.id)}"><summary>查看 ${files.length} 个文件</summary>${files.map(file => `<div class="detail-file"><div><span class="detail-filename">${escape(file.name)}</span><small>${file.exists === false ? '文件已移动或删除' : file.exists === null ? '暂时无法确认文件状态' : Number.isFinite(file.size_bytes) ? detailSize(file.size_bytes) : ''}</small></div>${detailFileActions(item, file)}</div>`).join('')}</details>`;
    const itemActionButtons = [];
    if (['failed', 'needs_auth'].includes(item.status) && itemUrl) {
      itemActionButtons.push(`<a class="secondary detail-link-action" href="${escape(itemUrl)}" target="_blank" rel="noopener noreferrer">${icon('link')}在浏览器中打开此作品</a>`);
    }
    if (retry) itemActionButtons.push(detailButton('retry-item', '重试此项', { itemId: item.id, icon: 'refresh' }));
    const itemActions = itemActionButtons.length ? `<div class="detail-file-actions">${itemActionButtons.join('')}</div>` : '';
    return `<article class="detail-item"><div class="detail-item-heading"><h4>${escape(item.title && item.title !== 'Untitled' ? item.title : '未命名作品')}</h4><span class="status ${tone(item)}">${escape(item.status === 'skipped' ? '已跳过' : labels[item.status] || '状态未知')}</span></div>${parts.length ? `<p class="detail-item-meta">${parts.map(part => `<span>${escape(part)}</span>`).join('')}</p>` : ''}${itemIssue ? `<p class="detail-issue">${escape(itemIssue)}</p>` : ''}${missing ? `<p class="detail-file-warning">${missing === files.length ? '文件已不在原位置' : `${missing} 个文件已不在原位置`}，下载记录仍保留。</p>` : ''}${output}${itemActions}</article>`;
  }
  function detailReplace(target, html) {
    if (target._html === html) return;
    const sameJob = target._detailId === state.detailId;
    const expanded = sameJob ? [...target.querySelectorAll('details[open]')].map(node => node.dataset.detailSection) : [];
    const focused = document.activeElement;
    const token = node => JSON.stringify([node.dataset.detailAction, node.dataset.action, node.dataset.itemId, node.dataset.fileIndex]);
    const focusToken = sameJob && target.contains(focused) && focused.dataset.detailAction ? token(focused) : null;
    const scroll = sameJob ? target.scrollTop : 0;
    target.innerHTML = html; target._html = html; target._detailId = state.detailId;
    target.querySelectorAll('details').forEach(node => { if (expanded.includes(node.dataset.detailSection)) node.open = true; });
    if (focusToken) [...target.querySelectorAll('[data-detail-action]')].find(node => token(node) === focusToken)?.focus({ preventScroll: true });
    target.scrollTop = scroll;
  }
  function renderDetails() {
    const job = state.jobs.get(state.detailId); if (!job) { $('#details-dialog').close(); return; }
    $('#details-dialog').dataset.key = state.detailId;
    const summary = detailSummary(job), { items, files } = summary;
    const issue = message(job);
    const runtimeWarning = !job.wechat ? window.ShiyingRuntime?.warningPresentation(job) : null;
    const warningText = runtimeWarning && !runtimeWarning.isAlert ? runtimeWarning.message : localize(job.warning);
    const warning = warningText && warningText !== issue ? warningText : '';
    const url = detailUrl(job.source_url), directory = detailDirectory(job, files);
    const source = [platforms[canonicalPlatform(job.platform)] || '分享作品', job.author].filter(Boolean);
    const result = [`已完成 ${summary.completed} / ${summary.total} 项`];
    if (summary.failed) result.push(`失败 ${summary.failed} 项`);
    if (summary.skipped) result.push(`跳过 ${summary.skipped} 项`);
    if (summary.cancelled) result.push(`取消 ${summary.cancelled} 项`);
    if (summary.available) result.push(`${summary.available} 个文件可用`, detailSize(summary.size));
    const scope = Array.isArray(job.selected_item_ids) ? `本次仅下载选中的 ${job.selected_item_ids.length} 项，未选内容不计入结果。` : (job.resolved_source_kind || job.source_kind) === 'playlist' || (job.resolved_source_kind || job.source_kind) === 'profile' ? '以下为本次任务的下载结果，不代表来源中的全部内容。' : '';
    const status = job.cancel_requested ? '正在取消' : job.pause_requested ? '正在暂停' : items.some(item => item.status === 'postprocessing') ? '正在合并音视频' : labels[job.status] || '状态未知';
    let html = `<section class="detail-identity"><h3>${escape(detailTitle(job, items))}</h3><div class="detail-source">${source.map(part => `<span>${escape(part)}</span>`).join('')}${url ? `<a href="${escape(url)}" target="_blank" rel="noopener noreferrer">${icon('link')}查看原页面</a>` : ''}</div></section><section class="detail-result" aria-label="本次任务结果"><strong class="detail-result-status ${tone(job)}">${icon(job.status === 'completed' ? 'check' : failed(job) || job.status === 'paused' ? 'alert' : 'download')}${escape(status)}</strong><p class="detail-result-counts">${result.map(part => `<span>${escape(part)}</span>`).join('')}</p>${scope ? `<p class="detail-hint">${escape(scope)}</p>` : ''}${detailProgress(job, items)}</section>`;
    if (issue) html += formatDetailIssue(issue);
    if (warning) html += `<div class="detail-notice">${escape(warning)}</div>`;
    if (job.discovery_complete === false) html += '<div class="detail-notice">来源内容尚未完整解析。即使已识别的作品全部完成，也不能视为整个合集下载完整。</div>';
    if (summary.missing) html += `<div class="detail-notice">${summary.missing} 个文件已移动或删除。可复制原路径检查，或从原页面重新添加下载；任务记录不会因此删除。</div>`;
    if (summary.unknown) html += '<p class="detail-hint">部分文件状态暂时无法确认，请检查磁盘或文件权限后刷新。</p>';
    if (directory) html += `<section class="detail-location" aria-label="保存位置"><div>${icon('folder')}<span>保存位置</span><strong title="${escape(directory)}">${escape(directory)}</strong></div>${detailButton('copy-directory', '复制目录', { text: true })}</section>`;
    html += `<section class="detail-contents" aria-label="下载内容"><h3>下载内容${summary.total ? `<span>${summary.total} 项</span>` : ''}</h3>${items.length ? items.map(item => detailItem(job, item)).join('') : `<p class="detail-empty">${job.status === 'discovering' || job.status === 'queued' ? '作品信息将在解析后显示。' : '此任务尚未生成可用的文件记录。'}</p>`}</section>`;
    const date = value => value && !Number.isNaN(new Date(value).getTime()) ? new Date(value).toLocaleString('zh-CN') : '—';
    html += `<details class="detail-technical" data-detail-section="technical"><summary>技术详情</summary><dl><dt>任务编号</dt><dd>${escape(job.id)}</dd><dt>创建时间</dt><dd>${escape(date(job.created_at))}</dd>${job.finished_at ? `<dt>结束时间</dt><dd>${escape(date(job.finished_at))}</dd>` : ''}<dt>来源链接</dt><dd class="detail-url">${escape(job.source_url || '—')}</dd></dl>${items.map(item => `<div class="detail-technical-item"><strong>${escape(item.title || item.id)}</strong>${item.resolution ? `<p>分辨率：${escape(item.resolution)}</p>` : ''}${item.selected_format ? `<p>提取器格式编号：${escape(item.selected_format)}</p>` : ''}${item.diagnostic_code ? `<p>诊断编号：${escape(item.diagnostic_code)}</p>` : ''}${detailFiles(item).map(file => `<p class="detail-path">${escape(file.path)}</p>`).join('')}</div>`).join('')}</details>`;
    detailReplace($('#details-content'), html);
    const action = (name, label, primary = false, disabled = false) => detailButton('job', label, { jobAction: name, primary, disabled });
    let actions = '';
    if (active(job)) {
      if (!job.wechat && job.status === 'downloading') actions += action('pause', job.pause_requested ? '正在暂停…' : '暂停下载', true, job.pause_requested || job.cancel_requested);
      actions += action('cancel', job.cancel_requested || job.status === 'cancelling' ? '正在取消…' : '取消下载', !actions, job.cancel_requested || job.status === 'cancelling');
    } else if (job.status === 'paused') actions += action('resume', '继续下载', true) + action('cancel', '取消下载');
    else if (failed(job) && job.retryable !== false) {
      if (job.status === 'needs_auth') actions += action('verify', '登录 / 验证', true);
      actions += action('retry', recreateRequired(job) ? '重新下载' : job.status === 'partial' ? '重试未完成项' : '重试下载', !actions);
    }
    const isCollection = ['playlist', 'profile'].includes(job.resolved_source_kind || job.source_kind);
    if (failed(job) && url && !isCollection) {
      actions += `<a class="secondary" href="${escape(url)}" target="_blank" rel="noopener noreferrer">${icon('link')}在浏览器中打开原作品</a>`;
    }
    if (summary.available || summary.unknown) actions += action('folder', '打开下载文件夹', !actions);
    detailReplace($('#details-actions'), `<span class="detail-footer-note">${files.length ? '文件保存在本机' : '关闭详情不会停止任务'}</span>${actions}${detailButton('close', '关闭')}`);
  }
  async function detailCopy(value) {
    if (!value) throw new Error('没有可复制的路径。');
    try { await navigator.clipboard.writeText(value); }
    catch {
      const input = document.createElement('textarea'); input.value = value;
      input.style.position = 'fixed'; input.style.opacity = '0'; $('#details-dialog').append(input); input.select();
      const copied = document.execCommand('copy'); input.remove();
      if (!copied) throw new Error('无法访问剪贴板，请展开技术详情手动复制路径。');
    }
    toast('路径已复制');
  }
  async function detailAct(event) {
    const button = event.target.closest('[data-detail-action]'); if (!button) return;
    const action = button.dataset.detailAction;
    if (action === 'close') { $('#details-dialog').close(); return; }
    if (detailBusy || button.disabled) return;
    const job = state.jobs.get(state.detailId); if (!job) return;
    const item = detailItems(job).find(item => item.id === button.dataset.itemId);
    const file = item && detailFiles(item).find(file => file.index === Number(button.dataset.fileIndex));
    detailBusy = true; button.disabled = true;
    try {
      if (action === 'job') await act(event);
      else if (action === 'copy-directory') await detailCopy(detailDirectory(job, detailSummary(job).files));
      else if (action === 'copy-file' && file) await detailCopy(file.path);
      else if (['open', 'reveal'].includes(action) && file) {
        await api('/api/files/open', 'POST', { job_id: job.id, item_id: job.wechat ? null : item.id, file_index: file.index, wechat: !!job.wechat, reveal: action === 'reveal' });
        toast(action === 'reveal' ? '已在文件管理器中定位文件' : '已使用系统默认应用打开文件');
      } else if (action === 'retry-item' && item) {
        const updated = await api(`/api/jobs/${encodeURIComponent(job.id)}/retry`, 'POST', { item_id: item.id });
        put(updated); render(); toast('此项已重新加入队列，已完成内容不会重复下载');
      }
    } catch (error) { toast(localize(error.message), true); if (error.httpStatus === 404) await refresh(); }
    finally { detailBusy = false; if (button.isConnected) button.disabled = false; renderDetails(); }
  }
  // ---- 影像档案层：输入识别、状态持久化、首屏密度 -------------------------
  // 依据：全项目审查 :253（提交后才知道哪些被接受）、:264（刷新丢视图/草稿）、
  // UI 审计 :168-174（已有任务时添加区仍占满首屏）。三者都在原 DOM 契约内实现，
  // 不引入路由框架，也不改动被测试断言的选择器与接口。
  const UI_STATE_KEY = 'shiying-ui-state-v04';
  const UI_DRAFT_KEY = 'shiying-link-draft-v04';
  // 前端白名单需与后端 app/platforms/_identify.py 的域名集合保持一致：
  // iesdouyin.com / gifshow.com / *.m.chenzhongtech.com / xhslink.cn 都是后端
  // 能成功建任务的合法域名，漏掉会把它们误标为「无法识别」。
  const supportedHost = host =>
    /(^|\.)(douyin|iesdouyin|kuaishou|gifshow|xiaohongshu|xhslink|bilibili|youtube|weixin|wechat)\.(com|cn|qq\.com)$/.test(host)
    || /^[a-z0-9-]+\.m\.chenzhongtech\.com$/.test(host)
    || host === 'b23.tv' || host === 'youtu.be';

  function updateLinkInsights() {
    const box = $('#link-insights'), invalidBox = $('#link-invalid-list');
    if (!box) return;
    const text = $('#share-input')?.value || '';
    // 计数需看到原始出现次数：extractShareLinks 已在源头去重，直接用它会让下面的
    // 重复检测永远命中不了。tokens 保留重复，links 仅用于「识别到 N 条链接」的展示。
    const tokens = extractShareLinkTokens(text);
    const links = [...new Set(tokens)];
    if (!text.trim()) { box.hidden = true; if (invalidBox) invalidBox.hidden = true; return; }
    // 只统计 URL 级问题：不支持的平台与畸形链接。分享文案里的普通中文
    // 片段不是链接，不计入「无法识别」警示，避免贴正常链接时误导用户。
    const uniqueSeen = new Set(), duplicates = [], unsupported = [], malformed = [];
    for (const link of tokens) {
      if (uniqueSeen.has(link)) { duplicates.push(link); continue; }
      uniqueSeen.add(link);
      let host = '';
      try { host = new URL(link).hostname.toLowerCase(); } catch { malformed.push(link); continue; }
      if (!supportedHost(host)) unsupported.push(link);
    }
    const invalidCount = unsupported.length + malformed.length;
    const parts = [
      `<span class="insight-item insight-ok">识别到 <strong>${links.length}</strong> 条链接</span>`,
      `<span class="insight-item ${duplicates.length ? 'insight-warn' : ''}">重复 <strong>${duplicates.length}</strong></span>`,
      `<span class="insight-item ${invalidCount ? 'insight-warn' : ''}">无法识别 <strong>${invalidCount}</strong></span>`
    ];
    if (links.length > 50) parts.push('<span class="insight-item insight-bad">超过 50 条上限</span>');
    parts.push('<span class="insight-help">已接入 7 个平台 · Ctrl ↵ 提交</span>');
    box.innerHTML = parts.join('');
    box.hidden = false;
    if (invalidBox) {
      const samples = [...unsupported, ...malformed].slice(0, 3);
      invalidBox.hidden = !samples.length;
      invalidBox.textContent = samples.length ? `无法识别的链接：${samples.join(' · ')}${unsupported.length + malformed.length > 3 ? ' …' : ''}` : '';
    }
  }

  // 完全退出：两步确认防止误触；桌面壳里走 pywebview 桥（stop.py 身份校验），
  // 浏览器直连模式没有桥，只能指引回安装目录运行停止脚本。
  let exitArmTime = 0;
  async function fullExit() {
    const button = $('#full-exit'), stateEl = $('#exit-state');
    if (!button) return;
    if (Date.now() - exitArmTime > 5000) {
      exitArmTime = Date.now();
      button.textContent = '确认完全退出？';
      if (stateEl) { stateEl.hidden = false; stateEl.textContent = '再次点击确认。正在下载的任务会被中断。'; }
      window.setTimeout(() => { if (button.isConnected && Date.now() - exitArmTime >= 5000) { button.textContent = '完全退出'; exitArmTime = 0; } }, 5000);
      return;
    }
    exitArmTime = 0;
    button.disabled = true;
    const bridge = window.pywebview?.api?.quit_app;
    if (typeof bridge !== 'function') {
      if (stateEl) { stateEl.hidden = false; stateEl.textContent = '当前是在浏览器中打开的页面。请运行安装目录里的「停止拾影.bat」（macOS/Linux 为 scripts/unix/stop.sh）完成完全退出。'; }
      button.disabled = false; button.textContent = '完全退出';
      return;
    }
    if (stateEl) { stateEl.hidden = false; stateEl.textContent = '正在停止本机服务…'; }
    let result = null, bridgeError = '';
    try { result = await bridge(); } catch (error) { bridgeError = String(error?.message || error); }
    if (result && result.ok) {
      if (stateEl) { stateEl.hidden = false; stateEl.textContent = '本机服务已停止，桌面窗口即将关闭。'; }
      toast('已完全退出');
      return;
    }
    if (stateEl) { stateEl.hidden = false; stateEl.textContent = (result && result.message) || `退出未完成：${bridgeError || '桌面桥没有响应'}。可运行「停止拾影.bat」收尾。`; }
    button.disabled = false; button.textContent = '完全退出';
  }

  // ---- AI 搜索 --------------------------------------------------------
  async function loadSearchStatus(force = false) {
    if (searchStatus && !force) return searchStatus;
    try { searchStatus = await api('/api/search/status'); } catch { searchStatus = null; }
    return searchStatus;
  }
  // ---- 平台能力三态：以 GET /api/platforms 为准派生标签与状态标识 ----------
  // 静态 chip 保留为无网络时的兜底；能力数据到达后按 supported/experimental/
  // unavailable 与 evidence_missing 覆盖标签、提示与角标，不再靠前端写死状态。
  function capabilityFor(platform, channel = 'default') {
    const entries = state.platformCapabilities || [];
    return entries.find(entry => entry.platform === platform && entry.channel === channel)
      || (channel === 'default' ? null : entries.find(entry => entry.platform === platform && entry.channel === 'default'))
      || null;
  }
  function capabilityBadge(entry, kind = 'download') {
    if (!entry) return null;
    const value = entry[kind] || 'unavailable';
    const name = ['supported', 'experimental', 'unavailable'].includes(value) ? value : 'unavailable';
    const label = {supported: '支持', experimental: '实验', unavailable: '不可用'}[name];
    const notes = [];
    if (entry.evidence_missing) notes.push('证据缺失');
    if (entry.reason) notes.push(entry.reason);
    return {state: name, label, title: `${label}${notes.length ? '：' + notes.join('；') : ''}`};
  }
  async function loadPlatformCapabilities() {
    try {
      const entries = await api('/api/platforms');
      state.platformCapabilities = Array.isArray(entries) ? entries : [];
    } catch { state.platformCapabilities = []; }
    applyPlatformCapabilities();
  }
  function applyPlatformCapabilities() {
    const entries = state.platformCapabilities || [];
    for (const entry of entries) {
      if (entry.channel === 'default' && entry.label) platforms[entry.platform] = entry.label;
    }
    $$('.platform-row .platform-chip').forEach(chip => {
      const entry = capabilityFor(chip.dataset.platform);
      const badge = capabilityBadge(entry);
      if (!badge) {
        chip.removeAttribute('data-capability'); chip.removeAttribute('data-capability-label');
        chip.classList.remove('capability-supported', 'capability-experimental', 'capability-unavailable');
        if (chip.dataset.baseTitle) chip.title = chip.dataset.baseTitle;
        chip.removeAttribute('aria-label');
        return;
      }
      if (!chip.dataset.baseTitle) chip.dataset.baseTitle = chip.title;
      chip.dataset.capability = badge.state;
      chip.dataset.capabilityLabel = badge.label + (entry.evidence_missing ? ' · 证据缺失' : '');
      chip.classList.remove('capability-supported', 'capability-experimental', 'capability-unavailable');
      chip.classList.add(`capability-${badge.state}`);
      chip.title = `${chip.dataset.baseTitle}（下载${badge.title}）`;
      chip.setAttribute('aria-label', `${platforms[chip.dataset.platform] || chip.textContent.trim()}：下载${badge.title}`);
    });
    const summary = $('.platform-row small');
    if (summary && entries.length) {
      const defaults = entries.filter(entry => entry.channel === 'default');
      const supported = defaults.filter(entry => entry.download === 'supported').length;
      const experimental = defaults.filter(entry => entry.download === 'experimental').length;
      const missing = entries.filter(entry => entry.evidence_missing).length;
      summary.textContent = `已登记 ${defaults.length} 个平台 · 支持 ${supported} · 实验 ${experimental}${missing ? ` · 证据缺失 ${missing}` : ''} · 点击图标打开官网`;
    }
    if (searchStatus) renderSearchPlatforms(searchStatus);
  }
  function renderSearchPlatforms(status) {
    const box = $('#search-platforms'); if (!box) return;
    const available = status?.platforms || [];
    const blocked = status?.unsearchable || {};
    box.innerHTML = [
      ...available.map(p => {
        const badge = capabilityBadge(capabilityFor(p.value), 'search');
        const tip = badge ? `搜索能力：${badge.title}${p.reason ? '；' + p.reason : ''}` : (p.reason || '');
        return `<label class="search-platform"${tip ? ` title="${escape(tip)}"` : ''}><input type="checkbox" value="${escape(p.value)}" checked><span>${escape(p.label)}${badge ? `<small class="capability-tag capability-${badge.state}">${escape(badge.label)}</small>` : ''}</span></label>`;
      }),
      ...Object.entries(blocked).map(([value, info]) => {
        const badge = capabilityBadge(capabilityFor(value), 'search');
        const tip = badge ? `搜索能力：${badge.title}${info.reason ? '；' + info.reason : ''}` : (info.reason || '');
        return `<label class="search-platform disabled" title="${escape(tip)}"><input type="checkbox" value="${escape(value)}" disabled><span>${escape(info.label || value)}${badge ? `<small class="capability-tag capability-${badge.state}">${escape(badge.label)}</small>` : ''}</span></label>`;
      })
    ].join('');
  }
  function selectedSearchPlatforms() {
    return [...document.querySelectorAll('#search-platforms input[type="checkbox"]:checked:not(:disabled)')].map(input => input.value);
  }
  function updateSearchLimitHint() {
    const hint = $('#search-limit-hint'); if (!hint) return;
    const limit = Number($('#search-limit')?.value || 10);
    const count = selectedSearchPlatforms().length;
    const total = Math.min(limit * count, SEARCH_TOTAL_CAP);
    hint.textContent = count
      ? `已选 ${count} 个平台 × 每平台 ${limit} 条，合计最多 ${total} 条（多平台总计上限 ${SEARCH_TOTAL_CAP} 条）。`
      : `多平台合计最多返回 ${SEARCH_TOTAL_CAP} 条。`;
  }
  async function openSearch() {
    const dialog = $('#search-dialog'); if (!dialog) return;
    $('#search-results-section').hidden = true;
    $('#search-results').innerHTML = '';
    $('#search-message').textContent = '';
    $('#search-selected-count').textContent = '0';
    $('#search-download-selected').disabled = true;
    $('#search-image-state').textContent = '';
    // 上传的图片只属于上一次会话，重新打开时必须丢弃，避免隐式压过新填的图片 URL。
    searchImageDataUrl = '';
    const imageFile = $('#search-image-file'); if (imageFile) imageFile.value = '';
    const note = $('#search-plan-note'); note.hidden = true; note.textContent = '';
    dialog.showModal();
    const status = await loadSearchStatus(true);
    renderSearchPlatforms(status);
    updateSearchLimitHint();
    if (status) {
      note.hidden = false;
      // 文案必须和「跳过 AI」的勾选口径一致：只有 AI 已开启且 Key 已配置
      // 才算就绪。仅看 key_configured 会在关闭 AI 时既勾选跳过 AI、又宣称
      // AI 已就绪，让用户误判当前搜索模式。
      const aiReady = Boolean(status.enabled) && Boolean(status.key_configured);
      note.textContent = aiReady
        ? `AI 已就绪（${status.model}${status.agent_reach?.available ? '；Agent-Reach 已安装' : ''}）`
        : status.key_configured
          ? 'AI 已在设置中关闭：当前使用关键词搜索，可在设置中开启 AI 搜索。'
          : '未配置 AI Key：将直接使用关键词搜索，可在设置中配置 Agnes 免费 Key';
      $('#search-no-ai').checked = !aiReady;
    }
  }
  function bindSearchImage() {
    const file = $('#search-image-file'); if (!file) return;
    file.addEventListener('change', () => {
      const chosen = file.files && file.files[0];
      if (!chosen) { searchImageDataUrl = ''; $('#search-image-state').textContent = ''; return; }
      if (chosen.size > 8 * 1024 * 1024) {
        searchImageDataUrl = '';
        file.value = '';
        $('#search-image-state').textContent = '图片超过 8MB，请压缩后再试。';
        return;
      }
      const reader = new FileReader();
      reader.onload = () => {
        const dataUrl = String(reader.result || '');
        if (dataUrl.length > SEARCH_IMAGE_MAX_DATA_URL_CHARS) {
          searchImageDataUrl = '';
          file.value = '';
          $('#search-image-state').textContent = '图片编码后超过上限，请压缩后再试。';
          return;
        }
        searchImageDataUrl = dataUrl;
        $('#search-image-state').textContent = `已选择：${chosen.name}`;
      };
      reader.onerror = () => { searchImageDataUrl = ''; $('#search-image-state').textContent = '读取图片失败。'; };
      reader.readAsDataURL(chosen);
    });
  }
  async function submitSearch() {
    if (state.searchBusy) return;
    const query = ($('#search-query')?.value || '').trim();
    const imageUrl = ($('#search-image-url')?.value || '').trim();
    const image = searchImageDataUrl || imageUrl;
    if (!query && !image) { $('#search-message').textContent = '请输入描述或选择图片。'; return; }
    const platforms = selectedSearchPlatforms();
    if (!platforms.length) { $('#search-message').textContent = '请至少选择一个平台。'; return; }
    const limit = Number($('#search-limit')?.value || 10);
    const button = $('#search-submit');
    state.searchBusy = true; button.disabled = true; button.textContent = '搜索中…';
    $('#search-message').textContent = '';
    // 每次提交都从空白结果开始：请求失败时若保留上一次的候选，用户会把旧
    // 结果误认为当前查询的结果（openSearch 打开弹窗时同样是这份重置）。
    $('#search-results-section').hidden = true;
    $('#search-results').innerHTML = '';
    $('#search-results-meta').textContent = '';
    $('#search-selected-count').textContent = '0';
    $('#search-download-selected').disabled = true;
    state.searchHits = [];
    const requestId = String(Date.now());
    state.searchRequestId = requestId;
    try {
      const data = await api('/api/search', 'POST', {
        query: query || null,
        image: image || null,
        platforms,
        limit,
        ai_mode: !$('#search-no-ai')?.checked,
        request_id: requestId
      }, { timeoutMs: SEARCH_TIMEOUT_MS });
      if (state.searchRequestId !== requestId) return;
      renderSearchResults(data);
    } catch (error) {
      if (state.searchRequestId === requestId) $('#search-message').textContent = localize(error.message);
    } finally {
      state.searchBusy = false; button.disabled = false; button.textContent = '搜索';
    }
  }
  function formatSearchDuration(seconds) {
    const total = Math.max(0, Number(seconds) || 0);
    const minutes = Math.floor(total / 60), rest = total % 60;
    return `${minutes}:${String(rest).padStart(2, '0')}`;
  }
  function renderSearchResults(data) {
    state.searchHits = Array.isArray(data.hits) ? data.hits : [];
    const section = $('#search-results-section'); section.hidden = false;
    const summary = (data.results || []).map(result => {
      const label = platforms[result.platform] || result.platform;
      return result.issue ? `${label}：${localize(result.issue.message)}` : `${label} ${result.hits.length} 条`;
    });
    $('#search-results-meta').textContent = `共 ${state.searchHits.length} 个结果` + (summary.length ? ` · ${summary.join(' · ')}` : '');
    const note = $('#search-plan-note');
    if (data.plan?.fallback_reason) { note.hidden = false; note.textContent = data.plan.fallback_reason; }
    else if (data.plan?.description) { note.hidden = false; note.textContent = `AI 理解：${data.plan.description}`; }
    $('#search-results').innerHTML = state.searchHits.map((hit, index) => {
      const label = platforms[hit.platform] || hit.platform;
      const cover = hit.cover_url
        ? `/api/search/thumbnail?platform=${encodeURIComponent(hit.platform)}&url=${encodeURIComponent(hit.cover_url)}`
        : '';
      const meta = [label, hit.author, hit.duration_seconds ? formatSearchDuration(hit.duration_seconds) : ''].filter(Boolean).map(escapeDisplay).join(' · ');
      return `<label class="preview-item search-item">
        <input type="checkbox" value="${index}" checked>
        <span class="search-thumb"><span class="media-placeholder"><svg><use href="#i-film"/></svg></span>${cover ? `<img src="${escape(cover)}" alt="" loading="lazy" onload="window._onSearchCoverLoad&&window._onSearchCoverLoad(this)" onerror="window._onSearchCoverError&&window._onSearchCoverError(this)">` : ''}</span>
        <span class="search-item-body"><strong>${escapeDisplay(hit.title || '未命名作品')}</strong><small>${meta}</small></span>
      </label>`;
    }).join('');
    $('#search-results').hidden = state.searchHits.length === 0;
    if (!state.searchHits.length) {
      $('#search-message').textContent = '没有找到结果，试试换个描述或减少平台限制。';
    } else if (section.scrollIntoView) {
      // 结果列表在表单下方：搜索完成后直接带到可视区域，避免用户找不到。
      section.scrollIntoView({ block: 'nearest' });
    }
    updateSearchCount();
  }
  function updateSearchCount() {
    const boxes = [...document.querySelectorAll('#search-results input[type="checkbox"]')];
    const checked = boxes.filter(box => box.checked).length;
    $('#search-selected-count').textContent = String(checked);
    $('#search-download-selected').disabled = checked === 0;
    boxes.forEach(box => { const item = box.closest('.search-item'); if (item) item.classList.toggle('selected', box.checked); });
  }
  function setSearchSelection(checked) {
    document.querySelectorAll('#search-results input[type="checkbox"]').forEach(box => { box.checked = checked; });
    updateSearchCount();
  }
  async function downloadSearchSelected() {
    const boxes = [...document.querySelectorAll('#search-results input[type="checkbox"]:checked')];
    if (!boxes.length) return;
    const button = $('#search-download-selected');
    button.disabled = true;
    let created = 0, skipped = 0, failed = 0;
    for (const box of boxes) {
      const hit = state.searchHits[Number(box.value)];
      if (!hit) continue;
      try {
        const existing = await api('/api/jobs/exists?url=' + encodeURIComponent(hit.source_url));
        if (existing && existing.exists) { skipped += 1; continue; }
        await createJobWithConfirm({ url: hit.source_url, auto_start: true });
        created += 1;
      } catch (error) {
        failed += 1;
      }
    }
    const parts = [`已创建 ${created} 个任务`];
    if (skipped) parts.push(`跳过 ${skipped} 个已有任务`);
    if (failed) parts.push(`${failed} 个创建失败`);
    $('#search-message').textContent = parts.join('，') + '。';
    if (created) { toast(`已添加 ${created} 个下载任务`); await refresh(); }
    updateSearchCount();
  }
  async function saveSearchKey() {
    const input = $('#ai-search-key');
    const value = (input?.value || '').trim();
    if (!value) { toast('请输入要保存的 API Key', true); return; }
    try {
      const result = await api('/api/search/key', 'POST', { api_key: value });
      input.value = '';
      $('#ai-search-key-state').textContent = result.configured ? '已配置（DPAPI 加密保存，不回显）' : '未配置';
      searchStatus = null;
      toast('AI Key 已保存');
    } catch (error) { toast(localize(error.message), true); }
  }
  function renderSearchCookieState(cookies, login) {
    const el = $('#search-cookie-state'); if (!el) return;
    const labels = { douyin: '抖音', kuaishou: '快手', xiaohongshu: '小红书' };
    const loginState = login || {};
    const entries = Object.keys(labels).map(platform => {
      const loggedIn = !!loginState[platform];
      const imported = Number(cookies?.[platform]?.count || 0);
      return `${labels[platform]} ${loggedIn ? '已登录' : (imported ? `已导入 ${imported} 条` : '未登录')}`;
    });
    const any = Object.values(loginState).some(Boolean) || Object.values(cookies || {}).some(info => info.count > 0);
    el.textContent = entries.join(' · ') + (any ? '' : '。匿名搜索会被拦截，建议先点对应平台登录。');
  }
  async function loginSearchPlatform(platform) {
    const labels = { douyin: '抖音', kuaishou: '快手', xiaohongshu: '小红书' };
    const stateEl = $('#search-cookie-state');
    // 只禁用被点击的渠道：一个渠道挂起时其他平台仍可登录。
    const button = [...document.querySelectorAll('[data-search-login]')]
      .find(node => node.dataset.searchLogin === platform);
    if (button) button.disabled = true;
    let failed = false;
    if (stateEl) stateEl.textContent = `等待${labels[platform] || platform}登录…（在弹出窗口扫码；关闭窗口可取消，最长 5 分钟）`;
    try {
      await api(`/api/search/login/${encodeURIComponent(platform)}`, 'POST', {}, { timeoutMs: LOGIN_TIMEOUT_MS });
      toast(`${labels[platform] || platform}登录态已保存`);
    } catch (error) {
      failed = true;
      if (stateEl) stateEl.textContent = localize(error.message);
      toast(localize(error.message), true);
    } finally {
      if (button) button.disabled = false;
      searchStatus = null;
      const status = await loadSearchStatus(true);
      if (status && !failed) renderSearchCookieState(status.cookies || {}, status.login || {});
    }
  }
  async function importSearchCookies() {
    const button = $('#import-search-cookies'); if (!button) return;
    const stateEl = $('#search-cookie-state');
    const previous = button.textContent;
    button.disabled = true; button.textContent = '导入中…';
    if (stateEl) stateEl.textContent = '正在读取 Chrome 登录态…（Chrome 必须完全退出）';
    try {
      const result = await api('/api/search/cookies/import', 'POST', {}, { timeoutMs: 60000 });
      const labels = { douyin: '抖音', kuaishou: '快手', xiaohongshu: '小红书' };
      const imported = result.imported || {};
      const parts = Object.entries(imported).map(([platform, count]) => `${labels[platform] || platform} ${count} 条`);
      if (stateEl) stateEl.textContent = `已导入：${parts.join(' · ')}`;
      searchStatus = null;
      toast('搜索登录态已导入');
    } catch (error) {
      if (stateEl) stateEl.textContent = localize(error.message);
      toast(localize(error.message), true);
      // 导入失败也要刷新状态，避免旧文案让用户误以为登录渠道不可用。
      // loadSearchStatus 只更新缓存变量，必须同步重绘 DOM 才能把错误文案
      // 换回各平台真实登录状态；错误本身已由上面的 toast 告知用户。
      searchStatus = null;
      const status = await loadSearchStatus(true);
      if (status) renderSearchCookieState(status.cookies || {}, status.login || {});
    } finally {
      button.disabled = false; button.textContent = previous;
    }
  }

  function persistUiState() {
    try {
      localStorage.setItem(UI_STATE_KEY, JSON.stringify({view: state.view, filter: state.filter, mediaFilter: state.mediaFilter, platformFilter: state.platformFilter, sortOrder: state.sortOrder, query: state.query}));
      localStorage.setItem(UI_DRAFT_KEY, $('#share-input')?.value || '');
    } catch { /* 本机隐私模式下 localStorage 可能不可用，静默降级为内存态 */ }
  }

  function restoreUiState() {
    try {
      const saved = JSON.parse(localStorage.getItem(UI_STATE_KEY) || '{}');
      if (['home','tasks','completed'].includes(saved.view)) state.view = saved.view;
      if (['all','active','paused','failed'].includes(saved.filter)) state.filter = saved.filter;
      if (['all','video','audio','image','other'].includes(saved.mediaFilter)) state.mediaFilter = saved.mediaFilter;
      if (['all','douyin','selected-douyin','kuaishou','wechat','xiaohongshu','bilibili','youtube','other'].includes(saved.platformFilter)) state.platformFilter = saved.platformFilter;
      if (['newest','oldest','title'].includes(saved.sortOrder)) state.sortOrder = saved.sortOrder;
      if (typeof saved.query === 'string') { state.query = saved.query; state.queries[state.view] = saved.query; }
      const draft = localStorage.getItem(UI_DRAFT_KEY) || '';
      if (draft && $('#share-input')) $('#share-input').value = draft;
    } catch { /* 损坏的本地状态不影响首屏，按默认工作台启动 */ }
  }

  function composerIsCollapsed() {
    // undefined 表示用户还没点过按钮：首次使用保留完整引导，已有任务时默认收起。
    // 一旦用户显式选择，就以选择为准——包括空任务态，否则「收起引导」无效。
    return state.composerCollapsed === undefined ? state.jobs.size > 0 : Boolean(state.composerCollapsed);
  }
  function updateComposerCompact() {
    const composer = $('#composer'), toggle = $('#composer-toggle');
    if (!composer || state.view !== 'home') return;
    const collapsed = composerIsCollapsed();
    composer.classList.toggle('compact', collapsed);
    if (toggle) {
      toggle.textContent = collapsed ? '展开引导' : '收起引导';
      toggle.setAttribute('aria-expanded', String(!collapsed));
    }
  }

  function view(name, filter) {
    const previousView = state.view;
    state.queries[state.view] = state.query;
    state.view = ['home','tasks','completed'].includes(name) ? name : 'home';
    state.query = state.queries[state.view]; $('#task-search').value = state.query;
    if (state.view === 'tasks' && ['all','active','paused','failed'].includes(filter)) state.filter = filter;
    state.manageCompleted = false; state.selectedCompleted.clear();
    const home = state.view === 'home', library = state.view === 'completed';
    $('#composer').hidden = !home; $('#tasks-section').dataset.view = state.view;
    $('#page-title').textContent = {home:'收集', tasks:'进度', completed:'资料库'}[state.view];
    $('#page-subtitle').textContent = home ? '粘贴链接，把喜欢的内容留在本机' : library ? '按作者与日期归档的本地内容' : '进行中的下载，以及需要你处理的任务';
    $('#tasks-title').textContent = home ? '最近收集' : library ? '本机资料库' : '进行中的任务';
    $('#task-scope').textContent = home ? '最近添加的下载；完成后的内容会收进“资料库”。' : library ? '仅显示成功完成的下载；部分完成的任务仍在“进度”中。' : '等待、下载、暂停和需处理的任务都在这里。';
    $('#show-all').hidden = !home; $('#show-completed').hidden = !home;
    $('#new-download').hidden = home; $('#manage-completed').hidden = !library;
    $('#task-filters').hidden = home; $('#task-tabs').hidden = state.view !== 'tasks';
    $('#media-tabs').hidden = !library; $('#completed-controls').hidden = !library;
    $('#task-search').setAttribute('aria-label', library ? '搜索已完成内容的标题或作者' : '搜索未完成任务的标题或作者');
    $$('.nav-button[data-view]').forEach(b => { const selected = b.dataset.view === state.view; b.classList.toggle('selected', selected); selected ? b.setAttribute('aria-current','page') : b.removeAttribute('aria-current'); });
    updateComposerCompact();
    persistUiState();
    render();
    // 进入资料库走分页首页；离开时恢复完整任务表，避免其它视图看到半页数据。
    if (state.everLoaded && previousView !== state.view && (previousView === 'completed' || state.view === 'completed')) refresh();
  }
  function manageCompleted(enabled) {
    state.manageCompleted = Boolean(enabled); state.selectedCompleted.clear(); render();
  }
  function resetListFilters() {
    state.query = ''; state.queries[state.view] = ''; $('#task-search').value = '';
    if (state.view === 'tasks') state.filter = 'all';
    if (state.view === 'completed') {
      state.mediaFilter = 'all'; state.platformFilter = 'all';
      $('#completed-platform').value = 'all';
    }
    state.selectedCompleted.clear(); render();
    if (state.view === 'completed') scheduleLibraryReload();
  }
  function addDownload() { view('home'); $('#share-input').focus(); }

  // ---- 资料库：走后端 GET /api/jobs?view=library 的游标分页 ----------------
  // 后端 LibraryItem 只有汇总字段，没有 items/revision/source_url（app/models.py）。
  // 这里补成现有任务行/详情能复用的任务对象；真实文件明细在打开详情时再按需取。
  function libraryJob(item) {
    return {
      id: item.id,
      title: item.title || item.id,
      platform: item.platform,
      author: item.author || undefined,
      status: item.status,
      created_at: item.created_at,
      revision: 0,
      items: [{
        id: `${item.id}-library`,
        title: item.title || item.id,
        media_type: item.media_type || 'unknown',
        status: 'completed',
        output_paths: [],
      }],
      output_dir: item.output_dir,
      total_items: item.total_items,
      completed_items: item.completed_items,
      failed_items: item.failed_items,
      total_bytes: item.total_bytes,
      missing_files: item.missing_files,
      cover_url: item.cover_url,
      libraryEntry: true,
    };
  }
  function libraryQueryParts(cursor) {
    const parts = ['view=library', `limit=${LIBRARY_PAGE_SIZE}`, `q=${encodeURIComponent(state.query)}`];
    if (state.platformFilter !== 'all' && state.platformFilter !== 'other') parts.push(`platform=${encodeURIComponent(state.platformFilter)}`);
    if (LIBRARY_BACKEND_MEDIA.has(state.mediaFilter)) parts.push(`media_type=${encodeURIComponent(state.mediaFilter)}`);
    if (LIBRARY_BACKEND_SORTS[state.sortOrder]) parts.push(`sort=${LIBRARY_BACKEND_SORTS[state.sortOrder]}`);
    if (cursor) parts.push(`cursor=${encodeURIComponent(cursor)}`);
    return parts;
  }
  async function loadLibrary(reset = false) {
    const lib = state.library;
    if (lib.loading) {
      if (!reset) return;
      lib.requestId += 1; // 让在途的续页响应作废，本次重置优先
      lib.loading = false;
    }
    if (!reset && (lib.exhausted || !lib.nextCursor)) return;
    const requestId = ++lib.requestId;
    lib.loading = true;
    updateLibraryMore();
    try {
      const parts = libraryQueryParts(reset ? null : lib.nextCursor);
      const page = await api(`/api/jobs?${parts.join('&')}`);
      if (requestId !== lib.requestId) return; // 过期响应（筛选已变更）直接丢弃
      const items = Array.isArray(page?.items) ? page.items : [];
      if (reset) { lib.order = []; lib.wechatKeys = []; lib.exhausted = false; }
      const seen = new Set(lib.order);
      for (const item of items) {
        const key = put(libraryJob(item));
        if (!seen.has(key)) { lib.order.push(key); seen.add(key); }
      }
      lib.indexStatus = page?.index_status || 'fallback';
      lib.total = Number(page?.total_estimate) || 0;
      lib.nextCursor = page?.next_cursor || null;
      lib.exhausted = !lib.nextCursor;
      if (reset) {
        // 微信记录存放在 data/wechat-jobs.json，不进索引；单独补齐（无分页端点）。
        try {
          const wechat = await api('/api/wechat/jobs');
          lib.wechatKeys = (Array.isArray(wechat) ? wechat : [])
            .filter(job => job.status === 'completed')
            .map(job => put(job, true));
        } catch { lib.wechatKeys = []; }
      }
    } finally {
      if (requestId === lib.requestId) { lib.loading = false; updateLibraryMore(); }
    }
  }
  function scheduleLibraryReload() {
    if (state.view !== 'completed') return;
    const setTimer = typeof setTimeout !== 'undefined' ? setTimeout : window?.setTimeout;
    const clearTimer = typeof clearTimeout !== 'undefined' ? clearTimeout : window?.clearTimeout;
    if (!setTimer) { loadLibrary(true); return; }
    if (libraryReloadTimer && clearTimer) clearTimer(libraryReloadTimer);
    libraryReloadTimer = setTimer(() => { libraryReloadTimer = null; loadLibrary(true); }, 250);
  }
  function libraryEntries() {
    const keys = [...new Set([...state.library.order, ...state.library.wechatKeys])];
    return keys.map(key => [key, state.jobs.get(key)]).filter(([, job]) => job);
  }
  function updateLibraryMore() {
    const wrap = $('#library-more'); if (!wrap) return;
    const lib = state.library;
    const hasMore = Boolean(lib.nextCursor) && !lib.exhausted;
    wrap.hidden = state.view !== 'completed' || (!hasMore && !lib.loading);
    const button = $('#load-more');
    if (button) { button.hidden = !hasMore; button.disabled = lib.loading; }
    const status = $('#library-more-status');
    if (status) status.textContent = lib.loading ? '正在读取…'
      : hasMore ? `已载入 ${lib.order.length + lib.wechatKeys.length} 条，可继续加载`
      : '';
    const sentinel = $('#library-sentinel');
    if (sentinel) sentinel.hidden = !hasMore || state.view !== 'completed';
  }
  function ensureLibraryObserver() {
    if (libraryObserver || typeof IntersectionObserver === 'undefined') return;
    const sentinel = $('#library-sentinel'); if (!sentinel) return;
    libraryObserver = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting) && state.view === 'completed' && !state.versionBlocked) loadLibrary(false);
    }, {rootMargin: '240px'});
    libraryObserver.observe(sentinel);
  }

  async function refresh(options = {}) {
    if (state.refreshing) return;
    state.refreshing = true;
    const previousKeys = [...state.jobs.keys()];
    try {
      const health = await api('/api/health');
      // 版本错位时进入「阻断」态：刷新循环仍会重试以便服务重启后自动恢复，
      // 但在此之前 SSE 事件与批量操作都必须停手，否则会出现「已断连」却仍在
      // 实时更新数据、仍能移除记录的自相矛盾界面。
      state.versionBlocked = health.app_id !== document.querySelector('meta[name=app-id]').content
        || health.build_id !== document.querySelector('meta[name=app-build]').content
        || Boolean(health.restart_required);
      if (state.versionBlocked) throw new Error('程序已更新，请先停止服务，再重新启动拾影。');
      let jobs = [], wechatJobs = [];
      if (state.view === 'completed') {
        // 资料库只按游标取当前页，不再一次性读取全部任务。周期/SSE 刷新带
        // keepLibrary，保留用户已经翻到的页与筛选结果；显式刷新才重置到第一页。
        if (!options.keepLibrary) await loadLibrary(true);
      } else {
        [jobs, wechatJobs] = await Promise.all([api('/api/jobs'), api('/api/wechat/jobs')]);
        const present = new Set([...jobs.map(j => `j:${j.id}`), ...wechatJobs.map(j => `w:${j.id}`)]);
        previousKeys.forEach(key => { if (!present.has(key)) state.jobs.delete(key); });
      }
      jobs.forEach(j => put(j)); wechatJobs.forEach(j => put(j,true));
      if (!state.config) {
        state.config = await api('/api/config');
        $('#save-path').textContent = state.config.download_dir; $('#save-path').title = state.config.download_dir;
      }
      state.recovery = health.recovery || null;
      state.everLoaded = true;
      state.refreshSettled = true;
      state.versionBlocked = false;
      connection(true);
      // 恢复成功时把离线面板文案复位，避免下次断连残留上一次的具体错误。
      const offlineCopy = document.querySelector('#offline-panel p');
      if (offlineCopy) offlineCopy.textContent = '本机服务没有响应。已显示最后一次成功读取的内容，重新连接后会自动更新。';
      if (state.recovery && state.recovery.message) {
        $('#list-error').hidden = false; $('#list-error').textContent = state.recovery.message;
      } else {
        $('#list-error').hidden = true;
      }
      if (Date.now() - state.cacheCheckedAt > 15000) loadCache();
      render();
    } catch (error) {
      // 离线态只由 #offline-panel 表达，不再同时把「连接中断」「空库」「正在读取」
      // 三种互相矛盾的提示堆在页面上（UI 审计 :94-104 的已复现缺陷）。
      // 配置损坏的恢复提示仍走 #list-error，独立可见，不被离线面板吞掉。
      connection(false);
      // 版本阻断在配置读取之前抛错，但 /api/config 通常仍可访问（实测为 200）。
      // 补取一次让「保存至」显示真实目录，而不是长期停在「读取中…」；网络故障
      // 下该请求同样失败，静默保留占位文案即可。
      if (state.versionBlocked && !state.config) {
        try {
          const config = await api('/api/config');
          state.config = config;
          $('#save-path').textContent = config.download_dir;
          $('#save-path').title = config.download_dir;
        } catch {}
      }
      const detail = localize(error.message) || '无法连接本机服务，请重新打开拾影。';
      const offlineCopy = document.querySelector('#offline-panel p');
      if (offlineCopy) offlineCopy.textContent = state.everLoaded
        ? `${detail} 已显示最后一次成功读取的内容。`
        : detail;
      if (!state.recovery?.message) $('#list-error').hidden = true;
      state.refreshSettled = true;
      render();
    } finally { state.refreshing = false; }
  }
  // 提取 URL 并清理尾部标点，保留重复出现；提交等场景仍需去重时用 extractShareLinks。
  function extractShareLinkTokens(text) {
    const matches = String(text || '').match(/https?:\/\/[^\s<>"']+/gi) || [];
    return matches.map(value => value.replace(/[).,;!?\]}。，；！？】）]+$/u, '')).filter(Boolean);
  }
  function extractShareLinks(text) {
    return [...new Set(extractShareLinkTokens(text))];
  }
  // POST /api/jobs 会把 paste_text 里的非链接行计入 ignored_text_count。
  // 一链接一请求时每个请求都带完整原文（仅统计用，创建仍用干净的 url）；只带
  // 链接所在行会漏掉独立成行的推荐文案，导致「已忽略 N 行」少算。后端不会从
  // paste_text 创建任务，所以多链接也不会因此多建任务。
  function pasteStatsText(text) {
    // 与后端 CreateJobRequest.paste_text 上限、输入框 maxlength 对齐。
    return String(text || '').slice(0, 32768);
  }
  function collectPasteStats(response, totals) {
    if (!response || typeof response !== 'object') return;
    // 每个请求都带同一份原文，后端回报的是同一份总数；求和会按链接数重复计，
    // 取最大值才是本次粘贴实际被忽略的行数。
    totals.ignored = Math.max(totals.ignored, Math.max(0, Number(response.ignored_text_count) || 0));
    if (Array.isArray(response.unrecognized_samples)) totals.unrecognized.push(...response.unrecognized_samples.filter(Boolean));
    if (Array.isArray(response.duplicates)) totals.duplicates.push(...response.duplicates.filter(Boolean));
  }
  function renderPasteStats(totals) {
    const box = $('#paste-stats'); if (!box) return;
    const ignored = Math.max(0, Number(totals.ignored) || 0);
    const unrecognized = [...new Set(totals.unrecognized)];
    const duplicates = [...new Set(totals.duplicates)];
    if (!ignored && !unrecognized.length && !duplicates.length) { box.hidden = true; box.innerHTML = ''; return; }
    const parts = [];
    if (ignored) parts.push(`已忽略 <strong>${ignored}</strong> 行非链接文本`);
    if (unrecognized.length) parts.push(`<span class="paste-warn">无法识别：${escape(unrecognized.slice(0, 3).join(' · '))}${unrecognized.length > 3 ? ' …' : ''}</span>`);
    if (duplicates.length) parts.push(`重复：${escape(duplicates.slice(0, 3).join(' · '))}${duplicates.length > 3 ? ' …' : ''}`);
    box.innerHTML = parts.join('');
    box.hidden = false;
  }
  async function submitLinks(links, create, onStats) {
    const accepted = [], rejected = [], unconfirmed = [];
    for (let index = 0; index < links.length; index++) {
      const url = links[index];
      try {
        if (url.length > 4096) {
          const error = new Error('链接超过 4096 个字符。');
          error.httpStatus = 422;
          throw error;
        }
        const response = await create(url);
        if (onStats) onStats(response);
        accepted.push(url);
      } catch (error) {
        if (error && Number.isInteger(error.httpStatus) && error.httpStatus >= 400 && error.httpStatus < 500 && error.httpStatus !== 408) {
          rejected.push({url, message: error.message});
        } else {
          // A timeout or disconnected response may have created a job. Stop
          // instead of blindly repeating it; the user can inspect the list.
          unconfirmed.push(...links.slice(index));
          break;
        }
      }
    }
    return {accepted, rejected, unconfirmed};
  }
  async function createAll(links) {
    state.busy = true; connection(state.ready); $('#submit-button span').textContent = '正在创建…';
    // 批量提交可能耗时数分钟：只有输入框内容仍是提交时的原文时才回填，
    // 避免覆盖用户在等待期间粘贴的下一批链接。
    const submittedText = $('#share-input').value;
    const pasteStats = {ignored: 0, unrecognized: [], duplicates: []};
    renderPasteStats(pasteStats);
    try {
      const result = await submitLinks(links, async url => {
        let host;
        try { host = new URL(url).hostname; }
        catch { const error = new Error('链接格式无效。'); error.httpStatus = 422; throw error; }
        const response = await createJobWithConfirm({url, paste_text: pasteStatsText(submittedText)});
        // 统计字段属于本次粘贴，不是任务本身，别混进 state.jobs 的任务对象。
        const job = { ...(response || {}) };
        delete job.ignored_text_count; delete job.unrecognized_samples; delete job.duplicates;
        put(job);
        return response;
      }, response => collectPasteStats(response, pasteStats));
      const inputEdited = $('#share-input').value !== submittedText;
      const remainder = [...result.rejected.map(item => item.url), ...result.unconfirmed].join('\n');
      if (!inputEdited) {
        $('#share-input').value = remainder;
      }
      state.filter = 'all'; render(); renderPasteStats(pasteStats);
      if (result.accepted.length) toast(`已添加 ${result.accepted.length} 个下载任务`);
      const notices = result.rejected.map(item => `${item.url}：${localize(item.message)}`);
      if (result.unconfirmed.length) notices.push('连接中断，剩余链接未全部确认。请先刷新任务列表，核对后再提交。');
      if (inputEdited && remainder) notices.unshift('提交期间输入框有改动，以下链接未自动回填，请手动处理：\n' + remainder);
      setFormMessage(notices.join('\n'));
    } catch (error) { setFormMessage(localize(error.message)); }
    finally { state.busy = false; $('#submit-button span').textContent = '解析并下载'; connection(state.ready); }
  }

  async function submit(event) {
    event.preventDefault(); if (!state.ready || state.busy) return;
    const text = $('#share-input').value.trim();
    const links = extractShareLinks(text);
    if (!links.length) { setFormMessage('请粘贴包含 http:// 或 https:// 的完整分享链接。'); return; }
    if (links.length > 50) { setFormMessage('一次最多添加 50 条不同链接，请分批提交。'); return; }
    setFormMessage('');
    state.busy = true; connection(state.ready); $('#submit-button span').textContent = '正在检查…';
    const fresh = [], duplicates = [];
    try {
      for (const url of links) {
        try {
          const found = await api(`/api/jobs/exists?url=${encodeURIComponent(url)}`);
          if (found.exists && found.job) duplicates.push({url, job: found.job});
          else fresh.push(url);
        } catch { fresh.push(url); }
      }
    } finally {
      state.busy = false; connection(state.ready); $('#submit-button span').textContent = '解析并下载';
    }
    if (duplicates.length) { showDuplicates(fresh, duplicates); return; }
    await createAll(fresh);
  }

  function showDuplicates(fresh, duplicates) {
    state.pendingSubmit = { fresh, duplicates };
    $('#duplicate-list').innerHTML = duplicates.slice(0, 5).map(({job}) => `
      <div class="duplicate-item"><strong>${escape(job.title || '未命名任务')}</strong>
      <span>${escape(platforms[job.platform] || job.platform)} · ${escape(labels[job.status] || job.status)}</span></div>`).join('');
    $('#duplicate-message').textContent = duplicates.length > 5 ? `另有 ${duplicates.length - 5} 条重复链接未列出。` : '';
    $('#duplicate-dialog').showModal();
  }
  async function folder(job, button) {
    if (button) button.disabled = true;
    toast('正在打开下载目录…');
    try {
      const native = typeof window.pywebview?.api?.open_folder === 'function';
      const result = await api('/api/folder/open','POST',{...(job ? {job_id:job.id,wechat:!!job.wechat} : {}),prepare_only:native});
      const opened = native ? await window.pywebview.api.open_folder(result.path) : result;
      toast(opened.foreground ? `下载目录已显示在前台\n${result.path}` : `下载目录已打开\n${result.path}\n若窗口未切到前面，请点击任务栏的文件资源管理器。`);
    } finally { if (button?.isConnected) button.disabled = false; }
  }
  async function act(event) {
    const link = event.target.closest('a');
    if (link) {
      link.closest('.task-more')?.removeAttribute('open');
      return;
    }
    const button = event.target.closest('[data-action]'); if (!button) return;
    const key = button.closest('[data-key]').dataset.key, job = state.jobs.get(key); if (!job) return;
    const action = button.dataset.action;
    button.closest('.task-more')?.removeAttribute('open');
    if (action === 'detail') {
      // 资料库行只有索引汇总字段；打开详情时按需取完整任务，保留文件明细与原链接。
      if (job.libraryEntry) { try { put(await api(`/api/jobs/${encodeURIComponent(job.id)}`)); } catch {} }
      state.detailId = key; renderDetails(); $('#details-dialog').showModal(); return;
    }
    if (action === 'remove') { requestRemoval([key]); return; }
    if (action === 'open-file') {
      const output = taskViews.files(job);
      const target = output.find(file => file.exists === true && file.can_open) || output[0];
      if (!target) { toast('未找到可打开的文件记录', true); return; }
      button.disabled = true;
      try {
        await api('/api/files/open', 'POST', {
          job_id: job.id,
          item_id: job.wechat ? null : target.item_id,
          file_index: target.index ?? 0,
          wechat: !!job.wechat,
          reveal: false
        });
        toast('已使用系统默认应用打开文件');
      } catch (error) {
        toast(localize(error.message), true);
        if (error.httpStatus === 404) await refresh();
      } finally {
        if (button.isConnected) button.disabled = false;
      }
      return;
    }
    button.disabled = true;
    try {
      if (action === 'folder') await folder(job,button);
      else if (action === 'pause') { const updated = await api(`/api/jobs/${job.id}/pause`,'POST',{}); put(updated,!!job.wechat); toast('正在暂停下载…'); }
      else if (action === 'resume') { const updated = await api(`/api/jobs/${job.id}/resume`,'POST',{}); put(updated,!!job.wechat); toast('已恢复下载'); }
      else if (action === 'verify') {
        if (job.wechat) await settings(); else { await api(`/api/jobs/${encodeURIComponent(job.id)}/verify`,'POST',{}); toast('请在 Chrome 完成登录或验证，再回到这里重试。'); }
      } else if (action === 'cancel') { const updated = await api(job.wechat ? `/api/wechat/jobs/${job.id}/cancel` : `/api/jobs/${job.id}/cancel`,'POST',{}); put(updated,!!job.wechat); }
      else if (action === 'retry') {
        failedCovers.delete(job.id);
        loadedCovers.delete(job.id);
        const recreate = recreateRequired(job);
        if (recreate) {
          // Every retry now goes through the unified queue, including old
          // WeChat records created before the legacy pool was retired.
          const fresh = await createJobWithConfirm({url: job.source_url});
          put(fresh); toast('已按当前设置创建新任务');
        } else {
          const updated = await api(`/api/jobs/${job.id}/retry`, 'POST', {});
          put(updated); toast('已重新加入队列');
        }
      }
      render();
    } catch (error) { toast(localize(error.message),true); } finally { if (button.isConnected) button.disabled = false; }
  }
  function requestRemoval(keys) {
    state.pendingRemoval = keys.filter(key => state.jobs.has(key) && !active(state.jobs.get(key)));
    if (!state.pendingRemoval.length) { toast('没有可移除的任务记录。'); return; }
    $('#remove-description').textContent = `将移除 ${state.pendingRemoval.length} 条任务记录。下载的文件和文件夹会保留；记录移除后无法恢复。`;
    $('#remove-message').textContent = '';
    $('#remove-dialog').showModal();
  }
  async function confirmRemoval() {
    const button = $('#confirm-remove'); button.disabled = true;
    let removed = 0; const errors = [], remaining = [];
    try {
      for (const key of state.pendingRemoval) {
        const job = state.jobs.get(key);
        if (!job) continue;
        try {
          await api(job.wechat ? `/api/wechat/jobs/${encodeURIComponent(job.id)}` : `/api/jobs/${encodeURIComponent(job.id)}`, 'DELETE');
          state.jobs.delete(key); state.deleted.add(key); removed++;
        } catch (error) {
          if (error.httpStatus === 404) { state.jobs.delete(key); state.deleted.add(key); }
          else { remaining.push(key); errors.push(localize(error.message)); }
        }
      }
      state.pendingRemoval = remaining; render();
      if (removed) toast(`已移除 ${removed} 条任务记录，下载文件已保留。`);
      if (errors.length) $('#remove-message').textContent = `部分记录未移除：${errors[0]}`;
      else $('#remove-dialog').close();
    } finally { button.disabled = false; }
  }
  function renderCache(cache) {
    if (!cache) return;
    const label = `${bytes(cache.bytes)}${cache.files ? ` · ${cache.files} 个文件` : ''}`;
    $('#storage-used').textContent = cache.bytes ? label : '缓存为空';
    $('#cache-size').textContent = cache.bytes ? label : '无缓存';
    const canClear = Boolean(cache.can_clear && cache.bytes);
    $('#clear-cache').disabled = !canClear;
    const sidebarClear = $('#sidebar-clear-cache');
    if (sidebarClear) {
      sidebarClear.disabled = !canClear;
      sidebarClear.title = !cache.can_clear
        ? `有 ${cache.busy_jobs} 个任务正在下载，完成或取消后可清理`
        : !cache.bytes
          ? '当前没有可清理的缓存'
          : '清理下载缓存（不影响已下载文件）';
    }
    const note = !cache.can_clear
      ? `有 ${cache.busy_jobs} 个任务正在下载，完成或取消后可清理。`
      : !cache.bytes
        ? '当前没有可清理的缓存。'
        : cache.paused_jobs
          ? `清理后 ${cache.paused_jobs} 个暂停或待验证任务需要重新下载。`
          : '清理缓存不会删除已完成的下载文件。';
    $('#cache-state').textContent = note;
  }
  async function loadCache() {
    try { renderCache(await api('/api/cache')); state.cacheCheckedAt = Date.now(); } catch {}
  }
  async function settings() {
    try {
      const [config, wechat, environment, cache, search] = await Promise.all([
        api('/api/config'), api('/api/wechat/status'), api('/api/environment').catch(() => null), api('/api/cache').catch(() => null), api('/api/search/status').catch(() => null)
      ]);
      state.config = config; $('#directory').value = config.download_dir; $('#use-cookies').checked = config.use_chrome_cookies; $('#profile').value = config.chrome_profile || '';
      $('#wechat-cookie').value = ''; $('#wechat-cookie-state').textContent = wechat.cookie_configured ? '已保存' : (wechat.chrome_fallback ? '未保存（下载时将尝试读取 Chrome 中的元宝登录态，需先完全退出 Chrome）' : '未配置'); $('#settings-message').textContent = '';
      if (search) {
        $('#ai-search-enabled').checked = !!search.enabled;
        $('#ai-search-base-url').value = search.base_url || '';
        $('#ai-search-model').value = search.model || '';
        $('#ai-search-key').value = '';
        $('#ai-search-key-state').textContent = search.key_configured ? '已配置（DPAPI 加密保存，不回显）' : '未配置：AI 搜索将退化为直接关键词';
        const reach = search.agent_reach || {};
        const reachLabels = { bilibili: 'B站', youtube: 'YouTube', xiaohongshu: '小红书' };
        const reachChannels = reach.channels || {};
        const reachReady = (reach.ready_channels || []).map(channel => reachLabels[channel] || channel);
        const reachMissing = Object.keys(reachLabels).filter(
          channel => reachChannels[channel] && !['ready', 'verified', 'unverified'].includes(reachChannels[channel].status)
        );
        $('#agent-reach-state').textContent = !reach.available
          ? `${reach.executable ? 'Agent-Reach 渠道未就绪' : 'Agent-Reach 未安装'}（可选）：${reach.install_hint || ''}`
          : reachMissing.length
            ? `Agent-Reach 已安装：${reachReady.join('/') || '暂无'}可用；${reachMissing.map(channel => reachLabels[channel]).join('/')}渠道未就绪，将回退内置搜索。${reach.install_hint || ''}`
            : `Agent-Reach 已安装：${reachReady.join('/')}优先走它的渠道`;
        renderSearchCookieState(search.cookies || {}, search.login || {});
      }
      const problems = environment?.checks?.filter(check => !check.ok) || [];
      $('#environment-status').textContent = environment ? (problems.length ? problems.map(check => check.message).join('\n') : '基础检查通过：已找到所需组件，保存目录可写。') : '暂时无法检查运行环境，请稍后重试。';
      $('#choose-directory').hidden = !window.pywebview?.api?.choose_directory;
      renderCache(cache);
      $('#settings-dialog').showModal();
    } catch (error) { toast(localize(error.message),true); }
  }
  async function saveSettings(event) {
    event.preventDefault(); const button = $('#save-settings'); button.disabled = true; $('#settings-message').textContent = '';
    try {
      state.config = await api('/api/config','PUT',{
        // 设置弹窗只展示部分字段；PUT 是整体验收，必须把已加载的其余字段
        // （index_enabled / capabilities_path 等）原样回传，否则会被后端
        // 按模型默认值静默重置，用户的索引与能力配置会丢失。
        ...state.config,
        download_dir:$('#directory').value.trim(),
        use_chrome_cookies:$('#use-cookies').checked,
        chrome_profile:$('#profile').value.trim() || null,
        ai_search_enabled:$('#ai-search-enabled').checked,
        ai_search_base_url:$('#ai-search-base-url').value.trim(),
        ai_search_model:$('#ai-search-model').value.trim()
      });
      $('#save-path').textContent = state.config.download_dir; $('#save-path').title = state.config.download_dir;
      searchStatus = null;
      $('#settings-dialog').close(); toast('已保存，新任务将使用新的设置');
    } catch (error) { $('#settings-message').textContent = localize(error.message); } finally { button.disabled = false; }
  }
  async function cookie(clear) {
    const value = $('#wechat-cookie').value.trim(); if (!clear && !value) { toast('请先填写腾讯元宝 Cookie',true); return; }
    try { await api('/api/wechat/cookie','POST',{cookie:clear ? '' : value}); $('#wechat-cookie').value = ''; $('#wechat-cookie-state').textContent = clear ? '未配置' : '已配置'; toast(clear ? '已清除视频号登录态' : '已保存视频号登录态'); }
    catch (error) { toast(localize(error.message),true); }
  }
  async function browserLogin() {
    const button = $('#wechat-login'); if (!button) return; button.disabled = true;
    const previousState = $('#wechat-cookie-state').textContent;
    $('#wechat-cookie-state').textContent = '等待扫码…（在弹出的浏览器窗口中完成登录，最长等待 5 分钟）';
    try {
      await api('/api/wechat/login', 'POST', {}, { timeoutMs: LOGIN_TIMEOUT_MS });
      $('#wechat-cookie-state').textContent = '已保存';
      toast('元宝登录态已保存，无需再手动粘贴 Cookie');
    } catch (error) { $('#wechat-cookie-state').textContent = previousState; toast(localize(error.message), true); }
    finally { button.disabled = false; }
  }
  function bind() {
    $('#link-form').addEventListener('submit',submit); $('#settings-form').addEventListener('submit',saveSettings); $('#task-list').addEventListener('click',act);
    $('#share-input').addEventListener('input',() => { setFormMessage(''); updateLinkInsights(); persistUiState(); });
    $('#form-message-close')?.addEventListener('click', () => setFormMessage(''));
    $('#share-input').addEventListener('keydown',e => { if (e.ctrlKey && e.key === 'Enter') { e.preventDefault(); $('#link-form').requestSubmit(); } });
    $$('.nav-button[data-view]').forEach(b => b.addEventListener('click',() => view(b.dataset.view)));
    $('.brand').addEventListener('click',e => { e.preventDefault(); view('home'); });
    $$('[data-filter]').forEach(b => b.addEventListener('click',() => { if (state.view === 'tasks') { state.filter = b.dataset.filter; render(); } }));
    $$('[data-media-filter]').forEach(b => b.addEventListener('click',() => { if (state.view === 'completed') { state.mediaFilter = b.dataset.mediaFilter; state.selectedCompleted.clear(); render(); scheduleLibraryReload(); } }));
    $$('[data-add-download]').forEach(b => b.addEventListener('click', addDownload));
    $('#completed-platform').addEventListener('change', event => { state.platformFilter = event.target.value; state.selectedCompleted.clear(); render(); scheduleLibraryReload(); });
    $('#completed-sort').addEventListener('change', event => { state.sortOrder = event.target.value; render(); scheduleLibraryReload(); });
    $('#load-more')?.addEventListener('click', () => loadLibrary(false));
    $('#manage-completed').addEventListener('click', () => { if (state.versionBlocked) return; manageCompleted(!state.manageCompleted); });
    $('#exit-management').addEventListener('click', () => manageCompleted(false));
    $('#task-list').addEventListener('change', event => {
      if (state.versionBlocked) return;
      const key = event.target.dataset.selectKey;
      if (!state.manageCompleted || !state.visibleCompleted.includes(key)) return;
      if (event.target.checked) state.selectedCompleted.add(key); else state.selectedCompleted.delete(key);
      render();
    });
    $('#select-visible').addEventListener('change', event => {
      if (state.versionBlocked) return;
      state.selectedCompleted.clear();
      if (event.target.checked) state.visibleCompleted.forEach(key => state.selectedCompleted.add(key));
      render();
    });
    $('#remove-selected').addEventListener('click', () => { if (state.versionBlocked) return; requestRemoval([...state.selectedCompleted].filter(key => state.visibleCompleted.includes(key) && state.jobs.get(key)?.status === 'completed')); });
    $$('[data-open-settings]').forEach(b => b.addEventListener('click',settings));
    $$('[data-open-folder]').forEach(b => b.addEventListener('click',() => folder(null,b).catch(e => toast(localize(e.message),true))));
    $$('[data-close-dialog]').forEach(b => b.addEventListener('click',() => b.closest('dialog').close()));
    $$('dialog').forEach(d => { d.addEventListener('click',e => { if (e.target === d) { const r = d.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) d.close(); } }); });
    $('#details-dialog').addEventListener('click', detailAct);
    $('#settings-dialog').addEventListener('close',() => { $('#wechat-cookie').value = ''; });
    $('#help-button').addEventListener('click',() => $('#help-dialog').showModal());
    $('#refresh-button').addEventListener('click',refresh); $('#show-all').addEventListener('click',() => view('tasks', 'all'));
    $('#show-completed').addEventListener('click', () => view('completed'));
    $('#confirm-remove').addEventListener('click',confirmRemoval);
    $('#task-search').addEventListener('input', event => { state.query = event.target.value; state.queries[state.view] = state.query; state.selectedCompleted.clear(); render(); scheduleLibraryReload(); });
    $('#duplicate-skip').addEventListener('click', () => {
      const pending = state.pendingSubmit; state.pendingSubmit = null;
      $('#duplicate-dialog').close();
      if (pending && pending.fresh.length) createAll(pending.fresh);
      else toast('已跳过重复链接');
    });
    $('#duplicate-force').addEventListener('click', () => {
      const pending = state.pendingSubmit; state.pendingSubmit = null;
      $('#duplicate-dialog').close();
      if (pending) createAll([...pending.fresh, ...pending.duplicates.map(item => item.url)]);
    });
    async function clearCacheAction(button) {
      if (!window.confirm('清理下载缓存？未完成的任务会失去续传能力，已完成的下载文件不受影响。')) return;
      const buttons = [$('#clear-cache'), $('#sidebar-clear-cache')].filter(Boolean);
      buttons.forEach(b => { b.disabled = true; });
      try {
        const result = await api('/api/cache/clear', 'POST', {});
        toast(`已清理 ${bytes(result.freed_bytes)} 缓存`);
        await loadCache();
      } catch (error) {
        toast(localize(error.message), true);
        const stateEl = $('#cache-state');
        if (stateEl) stateEl.textContent = localize(error.message);
      } finally {
        await loadCache();
      }
    }
    $('#clear-cache').addEventListener('click', () => clearCacheAction($('#clear-cache')));
    const sidebarClear = $('#sidebar-clear-cache');
    if (sidebarClear) sidebarClear.addEventListener('click', () => clearCacheAction(sidebarClear));
    if ($('#full-exit')) $('#full-exit').addEventListener('click', fullExit);
    if ($('#preview-button')) $('#preview-button').addEventListener('click', openPreview);
    $('#ai-search-button')?.addEventListener('click', openSearch);
    $('#search-limit')?.addEventListener('change', updateSearchLimitHint);
    $('#search-platforms')?.addEventListener('change', updateSearchLimitHint);
    $('#search-submit')?.addEventListener('click', submitSearch);
    $('#search-select-all')?.addEventListener('click', () => setSearchSelection(true));
    $('#search-unselect-all')?.addEventListener('click', () => setSearchSelection(false));
    $('#search-results')?.addEventListener('change', updateSearchCount);
    $('#search-download-selected')?.addEventListener('click', downloadSearchSelected);
    $('#save-ai-key')?.addEventListener('click', saveSearchKey);
    $('#import-search-cookies')?.addEventListener('click', importSearchCookies);
    document.querySelectorAll('[data-search-login]').forEach(button => {
      button.addEventListener('click', () => loginSearchPlatform(button.dataset.searchLogin));
    });
    bindSearchImage();
    if ($('#confirm-download-selected')) $('#confirm-download-selected').addEventListener('click', startPreviewDownload);
    if ($('#preview-select-all')) $('#preview-select-all').addEventListener('click', () => setPreviewSelection(true));
    if ($('#preview-unselect-all')) $('#preview-unselect-all').addEventListener('click', () => setPreviewSelection(false));
    $('#empty-action').addEventListener('click',() => { if (state.emptyAction === 'reset') resetListFilters(); else if (state.emptyAction === 'completed') view('completed'); else addDownload(); });
    $('#save-cookie').addEventListener('click',() => cookie(false)); $('#clear-cookie').addEventListener('click',() => cookie(true));
    $('#wechat-login').addEventListener('click', browserLogin);
    $('#choose-directory').addEventListener('click',async () => { try { const path = await window.pywebview.api.choose_directory(); if (path) $('#directory').value = path; } catch { toast('无法打开文件夹选择窗口，可手动填写路径。',true); } });
    $('#theme-toggle').addEventListener('click',() => { const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'; document.documentElement.dataset.theme = theme; try { localStorage.setItem('shiying-theme',theme); } catch {} syncThemeIcon(); });
    $('#paste-button').addEventListener('click',async () => { try { const text = await navigator.clipboard.readText(); if (text.length > 32768) { toast('文案超过输入框容量，请分批粘贴。',true); return; } $('#share-input').value = text; $('#share-input').focus(); setFormMessage(''); } catch { $('#share-input').focus(); toast('请在输入框内按 Ctrl+V 粘贴链接。'); } });
    $('#share-input').addEventListener('paste',event => {
      const input = event.currentTarget, pasted = event.clipboardData?.getData('text') || '';
      if (input.value.length - (input.selectionEnd - input.selectionStart) + pasted.length > input.maxLength) {
        event.preventDefault(); setFormMessage('文案超过输入框容量，请分批粘贴。');
      }
    });
    document.addEventListener('visibilitychange',() => { if (!document.hidden) refresh({keepLibrary: true}); });
    // 影像档案层事件：全部使用可选链，缺节点时静默降级，不影响旧界面路径。
    $('#composer-toggle')?.addEventListener('click', () => {
      state.composerCollapsed = !composerIsCollapsed();
      updateComposerCompact();
    });
    $('#offline-retry')?.addEventListener('click', () => refresh());
    $('#paste-button')?.addEventListener('click', () => window.setTimeout(updateLinkInsights, 60));
    $$('[data-filter]').forEach(b => b.addEventListener('click', persistUiState));
    $$('[data-media-filter]').forEach(b => b.addEventListener('click', persistUiState));
    $('#task-search')?.addEventListener('change', persistUiState);
    $('#completed-platform')?.addEventListener('change', persistUiState);
    $('#completed-sort')?.addEventListener('change', persistUiState);
    window.addEventListener('beforeunload', persistUiState);
  }
  // ---- 预览选集 -------------------------------------------------------------
  // 这些函数必须和 `$`、`state`、`api`、`extractShareLinks` 处在同一个闭包里。
  // 之前它们定义在 IIFE 之外，点击按钮会在发出任何请求之前抛 `$ is not defined`。
  let previewData = null;

  async function openPreview() {
    if (!state.ready || state.busy) return;
    const text = $('#share-input').value.trim();
    const links = extractShareLinks(text);
    if (!links.length) { setFormMessage('请先粘贴要预览的单条分享链接。'); return; }
    if (links.length > 1) { setFormMessage(`已识别到 ${links.length} 条链接，当前仅预览第 1 条；下载后其余链接将继续保留在输入框中。`); }
    const url = links[0];
    state.busy = true; connection(state.ready);
    const button = $('#preview-button');
    button.disabled = true;
    const started = Date.now();
    const tick = window.setInterval(() => {
      button.querySelector('span').textContent = `正在解析 ${Math.round((Date.now() - started) / 1000)}s…`;
    }, 1000);
    button.querySelector('span').textContent = '正在解析 0s…';
    setFormMessage('');
    try {
      const preview = await api('/api/preview', 'POST', {url}, { timeoutMs: PREVIEW_TIMEOUT_MS });
      previewData = {url, ...preview};
      $('#preview-meta').textContent = `${platforms[preview.platform] || preview.platform} · ${preview.author || '未知作者'} · 共 ${preview.total_items} 项`;
      const list = $('#preview-items');
      list.innerHTML = (preview.items || []).map((item, idx) => {
        const itemTitle = item.title || ('分P ' + (item.playlist_index || (idx + 1)));
        const fullLabel = `${itemTitle}${item.resolution ? ' (' + item.resolution + ')' : ''}`;
        return `
        <div class="preview-item">
          <input type="checkbox" id="prev-item-${idx}" data-item-id="${escape(item.id)}" checked>
          <label for="prev-item-${idx}" title="${escape(fullLabel)}">${escape(fullLabel)}</label>
        </div>
      `;
      }).join('');
      updatePreviewCount();
      list.onchange = updatePreviewCount;
      $('#preview-message').textContent = preview.warning ? localize(preview.warning) : '';
      $('#preview-dialog').showModal();
    } catch (e) {
      previewData = null;
      setFormMessage('解析失败：' + localize(e.message));
    } finally {
      window.clearInterval(tick);
      state.busy = false; connection(state.ready);
      button.querySelector('span').textContent = '预览选集';
      button.disabled = !state.ready;
    }
  }

  function updatePreviewCount() {
    const checked = $$('#preview-items input[type="checkbox"]:checked');
    $('#selected-count').textContent = checked.length;
    $('#confirm-download-selected').disabled = !checked.length;
  }

  function setPreviewSelection(checked) {
    $$('#preview-items input[type="checkbox"]').forEach(el => { el.checked = checked; });
    updatePreviewCount();
  }

  async function startPreviewDownload() {
    if (!previewData) return;
    const checked = $$('#preview-items input[type="checkbox"]:checked');
    const item_ids = checked.map(el => el.dataset.itemId);
    if (!item_ids.length) { $('#preview-message').textContent = '请至少选择一项再下载。'; return; }
    const btn = $('#confirm-download-selected');
    btn.disabled = true;
    $('#preview-message').textContent = '正在创建任务并按所选范围下载…';
    try {
      const job = await createJobWithConfirm({url: previewData.url, auto_start: false});
      put(job);
      const started = await api(`/api/jobs/${job.id}/start`, 'POST', {item_ids});
      put(started);
      const downloadedUrl = previewData.url;
      $('#preview-dialog').close();
      previewData = null;
      const currentText = $('#share-input').value.trim();
      const allLinks = extractShareLinks(currentText);
      if (allLinks.length > 1) {
        const remaining = allLinks.filter(l => l !== downloadedUrl);
        $('#share-input').value = remaining.join('\n');
        toast(`已启动下载，已选 ${item_ids.length} 项（已在输入框保留其余 ${remaining.length} 条链接）`);
      } else {
        $('#share-input').value = '';
        toast(`已启动下载，已选 ${item_ids.length} 项`);
      }
      state.filter = 'all'; render();
    } catch (e) {
      $('#preview-message').textContent = localize(e.message);
    } finally {
      if (btn.isConnected) btn.disabled = !$$('#preview-items input[type="checkbox"]:checked').length;
    }
  }

  function syncThemeIcon() {
    const dark = document.documentElement.dataset.theme === 'dark';
    const button = $('#theme-toggle');
    if (!button) return;
    button.innerHTML = `<svg><use href="#i-${dark ? 'sun' : 'moon'}"/></svg>`;
    button.setAttribute('aria-label', dark ? '切换到浅色主题' : '切换到深色主题');
    button.title = dark ? '切换到浅色主题' : '切换到深色主题';
  }

  async function initialize() {
    bind(); ensureLibraryObserver(); try { document.documentElement.dataset.theme = localStorage.getItem('shiying-theme') || 'light'; } catch {}
    syncThemeIcon();
    restoreUiState();
    // 恢复筛选控件的值与内部 state 对齐，避免「视图回到资料库但下拉仍是全部」。
    if ($('#task-search')) $('#task-search').value = state.query;
    if ($('#completed-platform')) $('#completed-platform').value = state.platformFilter;
    if ($('#completed-sort')) $('#completed-sort').value = state.sortOrder;
    view(state.view);
    updateLinkInsights();
    loadPlatformCapabilities();
    await refresh();
    loadCache();
    if (state.ready) {
      try {
        const environment = await api('/api/environment');
        const problems = environment.checks.filter(check => !check.ok);
        if (problems.length) toast(`运行环境有 ${problems.length} 项需要检查，请打开设置查看。`, true);
      } catch { toast('暂时无法检查运行环境，请在设置中重试。', true); }
    }
    if (state.recovery && state.recovery.message) toast(state.recovery.message, true);
    function connectEvents() {
      // 重连时携带本地最大 revision：服务端先推 snapshot，再续增量，避免丢事件。
      const since = Number.isFinite(state.maxRevision) ? state.maxRevision : null;
      const url = since == null ? '/api/events' : `/api/events?since=${encodeURIComponent(since)}`;
      if (state.eventSource) state.eventSource.close();
      state.eventSource = new EventSource(url);
      state.eventSource.addEventListener('job', event => {
        if (state.versionBlocked) return;
        try {
          const job = JSON.parse(event.data);
          state.maxRevision = Math.max(state.maxRevision || 0, job.revision || 0);
          put(job); render();
        } catch {}
      });
      state.eventSource.addEventListener('snapshot', event => {
        if (state.versionBlocked) return;
        try {
          const data = JSON.parse(event.data);
          (data.jobs || []).forEach(job => {
            state.maxRevision = Math.max(state.maxRevision || 0, job.revision || 0);
            put(job);
          });
          render();
        } catch {}
      });
      // SSE 健康时实时事件已覆盖更新，轮询仅作低频兜底；连接异常时恢复快速轮询。
      state.eventSource.onopen = () => { state.sseUp = true; if (!state.versionBlocked) refresh({keepLibrary: true}); };
      state.eventSource.onerror = () => {
        state.sseUp = false;
        if (!document.hidden) refresh({keepLibrary: true});
        state.eventSource?.close();
        window.setTimeout(connectEvents, 3000);
      };
    }
    connectEvents();
    let pollTick = 0;
    window.setInterval(() => {
      if (document.hidden) return;
      pollTick += 1;
      if (state.sseUp && pollTick % 8 !== 0) return;
      refresh({keepLibrary: true});
    },4000);
    window.addEventListener('beforeunload',() => state.eventSource?.close());
  }

  initialize();
})();
