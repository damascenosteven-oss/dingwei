import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';

const code = readFileSync(
  new URL('../miniprogram/pages/home/index.js', import.meta.url),
  'utf8'
);

test('重新打开从家庭接口取最近点，离开时停止查询', async () => {
  let page,
    tick,
    stopped = false;
  const paths = [];
  let state = 'online';
  const capturedAt = '2025-01-01T08:00:00Z';
  const wx = {
    getDeviceInfo: () => ({ platform: 'devtools' }),
    getAppBaseInfo: () => ({}),
    getStorageSync: () => ({
      cookie: 'session_id=fixture',
      csrf: 'test',
      family: 'f',
      elder: 'e'
    }),
    request(options) {
      paths.push(options.url);
      const data = options.url.endsWith('/latest')
        ? {
            capturedAt,
            latitude: 31,
            longitude: 121,
            accuracyM: 8,
            coordinateSystem: 'GCJ02'
          }
        : { state };
      options.success({ statusCode: 200, data });
    }
  };
  runInNewContext(code, {
    wx,
    Page(p) {
      page = p;
    },
    Date,
    setInterval(fn, ms) {
      assert.equal(ms, 30000);
      tick = fn;
      return 1;
    },
    clearInterval() {
      stopped = true;
    }
  });
  page.setData = function (update) {
    Object.assign(this.data, update);
  };
  await page.onShow();
  assert.equal(page.data.capturedAt, capturedAt);
  assert.equal(page.data.stale, true);
  assert.equal(page.data.markers.length, 1);
  assert.match(page.data.markers[0].iconPath, /^\/assets\//);
  const icon = readFileSync(
    new URL('../miniprogram' + page.data.markers[0].iconPath, import.meta.url)
  );
  assert.equal(icon.toString('hex', 0, 8), '89504e470d0a1a0a');
  assert.equal(paths.length, 2);
  state = 'stopped';
  page.data.command = '已收到本次位置';
  await tick();
  assert.equal(page.data.command, '');
  assert.equal(page.data.capturedAt, '');
  assert.equal(paths.length, 4);
  page.onHide();
  assert.equal(stopped, true);
});

test('查询完成前已关闭页面时忽略晚到的返回', async () => {
  let page;
  const pending = [];
  const wx = {
    getDeviceInfo: () => ({ platform: 'devtools' }),
    getAppBaseInfo: () => ({}),
    getStorageSync: () => ({ family: 'f', elder: 'e' }),
    request(options) {
      pending.push(options);
    }
  };
  runInNewContext(code, {
    wx,
    Page(value) {
      page = value;
    },
    Date,
    setInterval() {
      return 1;
    },
    clearInterval() {}
  });
  page.setData = (update) => Object.assign(page.data, update);
  const opening = page.onShow();
  assert.equal(pending.length, 2);
  assert.equal(page.data.state, 'loading');
  page.onHide();
  for (const item of pending)
    item.success({
      statusCode: 200,
      data: { state: 'online', capturedAt: new Date().toISOString() }
    });
  await opening;
  assert.equal(page.data.capturedAt, '');
});

test('回到前台后立即标记已过期的旧点，即使查询尚未返回', () => {
  let page;
  const wx = {
    getDeviceInfo: () => ({ platform: 'devtools' }),
    getAppBaseInfo: () => ({}),
    getStorageSync: () => ({ family: 'f', elder: 'e' }),
    request() {}
  };
  runInNewContext(code, {
    wx,
    Page(value) {
      page = value;
    },
    Date,
    setInterval() {
      return 1;
    },
    clearInterval() {}
  });
  page.setData = (update) => Object.assign(page.data, update);
  page.data.capturedAt = '2025-01-01T00:00:00Z';
  page.onShow();
  assert.equal(page.data.stale, true);
});
