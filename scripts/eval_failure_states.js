const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const viewsCode = fs.readFileSync(path.join(ROOT, 'app/static/task-views.js'), 'utf-8');
const runtimeCode = fs.readFileSync(path.join(ROOT, 'app/static/app.js'), 'utf-8');
let homeCode = fs.readFileSync(path.join(ROOT, 'app/static/home.js'), 'utf-8');
homeCode = homeCode.replace('  initialize();', `
  window.testHarness = {
    formatTaskError, formatDetailIssue, renderTaskRow, renderDetails, message, state
  };
`);

const window = {
  document: {
    documentElement: { dataset: { ui: 'shiying' } }
  }
};
const document = {
  documentElement: { dataset: { ui: 'shiying' } },
  querySelector: () => ({ className: '', append() {}, textContent: '', dataset: {}, close() {} }),
  querySelectorAll: () => [],
  activeElement: {}
};
const context = vm.createContext({
  window, document, URL, console, setTimeout: () => {}, setInterval: () => {}, clearTimeout: () => {}, clearInterval: () => {}
});

vm.runInContext(runtimeCode, context);
vm.runInContext(viewsCode, context);
vm.runInContext(homeCode, context);

const { formatTaskError, formatDetailIssue, renderTaskRow, message } = window.testHarness;

const testCases = [
  {
    name: '1. 微信元宝Cookie失效（用户截图场景）',
    job: {
      id: 'wechat-cookie-1',
      platform: 'wechat',
      status: 'failed',
      source_url: 'https://channels.weixin.qq.com/sph/12345',
      issue_message: '腾讯未返回播放信息。图集、直播回放等非视频作品暂不支持，普通视频请更新腾讯元宝 Cookie 后重试。',
      items: []
    }
  },
  {
    name: '2. 微信元宝凭证不完整',
    job: {
      id: 'wechat-token-2',
      platform: 'wechat',
      status: 'failed',
      source_url: 'https://channels.weixin.qq.com/sph/67890',
      issue_message: '播放凭证不完整，请更新腾讯元宝登录态后重试。',
      items: []
    }
  },
  {
    name: '3. 微信风控未允许播放',
    job: {
      id: 'wechat-risk-3',
      platform: 'wechat',
      status: 'failed',
      source_url: 'https://channels.weixin.qq.com/sph/abcde',
      issue_message: '微信未允许播放该作品，请检查链接和账号观看权限。',
      items: []
    }
  },
  {
    name: '4. 平台限流 HTTP 429',
    job: {
      id: 'rate-limit-4',
      platform: 'douyin',
      status: 'failed',
      source_url: 'https://www.douyin.com/video/123',
      issue_code: 'rate_limited',
      issue_message: 'HTTP Error 429: Too Many Requests',
      items: []
    }
  },
  {
    name: '5. 需Chrome登录态',
    job: {
      id: 'login-req-5',
      platform: 'xiaohongshu',
      status: 'needs_auth',
      source_url: 'https://www.xiaohongshu.com/explore/123',
      issue_code: 'login_required',
      issue_message: 'The site requires a current Chrome login session',
      items: []
    }
  },
  {
    name: '6. 滑块验证码挑战',
    job: {
      id: 'captcha-6',
      platform: 'douyin',
      status: 'needs_auth',
      source_url: 'https://www.douyin.com/video/456',
      issue_code: 'verification_required',
      issue_message: 'This request requires a CAPTCHA. Please solve the captcha.',
      items: []
    }
  },
  {
    name: '7. Chrome Cookie读取受阻',
    job: {
      id: 'cookie-unavail-7',
      platform: 'bilibili',
      status: 'failed',
      source_url: 'https://www.bilibili.com/video/BV123',
      issue_code: 'cookie_unavailable',
      issue_message: 'Chrome cookies could not be read from the cookie database',
      items: []
    }
  },
  {
    name: '8. 内容被删除或设为私密 (404)',
    job: {
      id: 'content-unavail-8',
      platform: 'kuaishou',
      status: 'failed',
      source_url: 'https://www.kuaishou.com/short-video/123',
      issue_code: 'content_unavailable',
      issue_message: 'HTTP Error 404: This video is private or deleted',
      items: []
    }
  },
  {
    name: '9. 网络中断',
    job: {
      id: 'net-err-9',
      platform: 'youtube',
      status: 'failed',
      source_url: 'https://www.youtube.com/watch?v=123',
      issue_code: 'network_error',
      issue_message: 'Network request failed: connection reset by peer',
      items: []
    }
  },
  {
    name: '10. 合集部分子项失败 (partial)',
    job: {
      id: 'partial-10',
      platform: 'bilibili',
      status: 'partial',
      source_kind: 'playlist',
      source_url: 'https://www.bilibili.com/medialist/play/123',
      items: [
        { id: 'p1', title: '第1集', status: 'completed' },
        { id: 'p2', title: '第2集', status: 'failed', error: '网络超时断开' }
      ]
    }
  },
  {
    name: '11. 媒体地址过期 410',
    job: {
      id: 'expired-11',
      platform: 'douyin',
      status: 'failed',
      source_url: 'https://www.douyin.com/video/789',
      issue_code: 'media_link_expired',
      issue_message: 'Media endpoint returned HTTP 410',
      items: []
    }
  },
  {
    name: '12. 本机未安装 FFmpeg/FFprobe',
    job: {
      id: 'ffmpeg-12',
      platform: 'bilibili',
      status: 'failed',
      source_url: 'https://www.bilibili.com/video/BV456',
      issue_code: 'local_configuration',
      issue_message: 'ffmpeg was not found',
      items: []
    }
  },
  {
    name: '13. 未归类但带具体技术详情的异常',
    job: {
      id: 'unknown-tech-13',
      platform: 'bilibili',
      status: 'failed',
      source_url: 'https://www.bilibili.com/video/BV999',
      issue_message: '发生了什么：程序遇到了尚未归类的异常，暂时无法仅凭现有响应确定网站做了什么。\n解决办法：先从原链接重试一次...\n技术详情：服务器在第42帧解析时遇到了未知视频流格式',
      items: []
    }
  }
];

const report = testCases.map(tc => {
  const rawMsg = message(tc.job);
  const taskRowError = formatTaskError(rawMsg);
  const detailCard = formatDetailIssue(rawMsg);
  const rowHtml = renderTaskRow('j:' + tc.job.id, tc.job, false);
  return {
    name: tc.name,
    taskRowError,
    detailCardSummary: detailCard.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim(),
    hasWebActionLink: rowHtml.includes('class="icon-button web-action"'),
    hasTaskErrorTag: rowHtml.includes('class="task-error"')
  };
});

console.log(JSON.stringify(report, null, 2));
