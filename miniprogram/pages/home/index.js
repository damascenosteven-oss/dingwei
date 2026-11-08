const storage = 'family-location.session';

function request(path, session, method = 'GET', data) {
  if (wx.getDeviceInfo().platform !== 'devtools') {
    return Promise.reject(new Error('本次仅开放本机模拟验收。'));
  }
  return new Promise((resolve, reject) =>
    wx.request({
      url: 'http://127.0.0.1:18763/api/v1' + path,
      method,
      data,
      timeout: 10000,
      header: session
        ? { Cookie: session.cookie, 'X-CSRF-Token': session.csrf }
        : {},
      success(response) {
        if (response.statusCode >= 200 && response.statusCode < 300)
          return resolve(response);
        const detail = response.data?.detail;
        const error = new Error(
          typeof detail === 'string' ? detail : '查询失败，请重试。'
        );
        error.status = response.statusCode;
        reject(error);
      },
      fail() {
        reject(new Error('无法连接测试服务，请检查网络后刷新。'));
      }
    })
  );
}

function emptyLocation() {
  return {
    capturedAt: '',
    capturedText: '',
    receivedText: '',
    stale: false,
    markers: [],
    point: null
  };
}

function localTime(iso) {
  const d = new Date(iso);
  const pad = (n) => String(n).padStart(2, '0');
  return (
    [d.getFullYear(), pad(d.getMonth() + 1), pad(d.getDate())].join('-') +
    ' ' +
    [pad(d.getHours()), pad(d.getMinutes()), pad(d.getSeconds())].join(':')
  );
}

Page({
  data: {
    fontSize: 16,
    ...emptyLocation(),
    signedIn: false,
    state: 'unbound',
    signingIn: false,
    email: '',
    password: '',
    error: '',
    command: '',
    working: false,
    bindingCode: ''
  },

  async onShow() {
    this.visible = true;
    this.generation = (this.generation || 0) + 1;
    const size = wx.getAppBaseInfo?.().fontSizeSetting;
    if (size > 0) this.setData({ fontSize: size });
    const previous = this.session;
    this.session = wx.getStorageSync(storage) || null;
    if (
      !this.session ||
      (previous &&
        (this.session.family !== previous.family ||
          this.session.elder !== previous.elder))
    ) {
      this.setData({ ...emptyLocation(), command: '', state: 'unbound' });
    } else if (this.data.capturedAt) {
      this.setData({
        stale: Date.now() - Date.parse(this.data.capturedAt) > 600000
      });
    }
    if (this.session && !previous && !this.data.capturedAt) this.setData({ state: 'loading' });
    this.setData({ signedIn: !!this.session });
    if (!this.session) return;
    if (this.timer) clearInterval(this.timer);
    this.timer = setInterval(() => this.refresh(), 30000);
    await this.refresh();
  },
  onHide() {
    this.visible = false;
    this.generation++;
    this.inFlight = null;
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  },
  onUnload() {
    this.onHide();
  },
  toggleLogin() {
    this.setData({ signingIn: !this.data.signingIn, password: '', error: '' });
  },
  emailInput(e) {
    this.setData({ email: e.detail.value });
  },
  passwordInput(e) {
    this.setData({ password: e.detail.value });
  },

  async login() {
    if (this.data.working) return;
    this.setData({ working: true, error: '' });
    try {
      const result = await request('/auth/login', null, 'POST', {
        email: this.data.email.trim(),
        password: this.data.password
      });
      const cookie = (result.cookies || [])
        .concat(result.header?.['Set-Cookie'] || [])
        .map((value) => value.split(';')[0])
        .find((value) => value.startsWith('session_id='));
      if (!cookie || !result.data.families.length)
        throw new Error('登录未取得家庭会话。');
      const session = {
        cookie,
        csrf: result.data.csrfToken,
        family: result.data.families[0].id
      };
      const elders = (
        await request('/families/' + session.family + '/elders', session)
      ).data;
      if (!elders.length) throw new Error('此测试家庭尚未建立老人资料。');
      session.elder = elders[0].id;
      wx.setStorageSync(storage, session);
      this.setData({ password: '', signingIn: false });
      if (this.visible) await this.onShow();
    } catch (err) {
      this.setData({ error: err.message });
    } finally {
      this.setData({ working: false });
    }
  },
  base() {
    return '/families/' + this.session.family + '/elders/' + this.session.elder;
  },
  async refresh() {
    if (!this.visible || !this.session || this.inFlight) return;
    const generation = this.generation;
    this.inFlight = generation;
    try {
      const base = this.base();
      const [status, latest] = await Promise.all([
        request(base + '/device-status', this.session),
        request(base + '/latest', this.session)
      ]);
      if (!this.visible || generation !== this.generation) return;
      const point = latest.data;
      const state = status.data.state;
      const valid =
        point &&
        Number.isFinite(Date.parse(point.capturedAt)) &&
        state !== 'stopped' &&
        state !== 'unbound';
      const position = valid
        ? {
            point,
            capturedAt: point.capturedAt,
            capturedText: localTime(point.capturedAt),
            receivedText: point.receivedAt ? localTime(point.receivedAt) : '',
            stale:
              !!point.stale ||
              Date.now() - Date.parse(point.capturedAt) > 600000,
            markers: [
              {
                id: 1,
                latitude: point.latitude,
                longitude: point.longitude,
                iconPath: '/assets/location-pin.png',
                width: 28,
                height: 35
              }
            ]
          }
        : emptyLocation();
      this.setData({
        ...position,
        state,
        error: '',
        bindingCode:
          state === 'unbound' || state === 'stopped'
            ? this.data.bindingCode
            : ''
      });
      if (state === 'unbound' || state === 'stopped') {
        this.setData({ command: '' });
        if (this.session.requestId) {
          delete this.session.requestId;
          wx.setStorageSync(storage, this.session);
        }
        return;
      }
      if (this.session.requestId) {
        const result = (
          await request(
            base + '/locate-requests/' + this.session.requestId,
            this.session
          )
        ).data;
        if (!this.visible || generation !== this.generation) return;
        const labels = {
          pending: '正在获取',
          acknowledged: '正在获取',
          completed: '已收到本次位置',
          expired: '请求超时',
          failed: '本次定位失败'
        };
        this.setData({
          command:
            result.failureCode === 'sharing_stopped'
              ? '已停止共享'
              : labels[result.state]
        });
        if (['expired', 'completed', 'failed'].includes(result.state)) {
          delete this.session.requestId;
          wx.setStorageSync(storage, this.session);
        }
      }
    } catch (err) {
      if (this.visible && generation === this.generation) {
        if ([401, 403, 404].includes(err.status)) this.clearSession();
        this.setData({
          error: err.message,
          stale: this.data.capturedAt
            ? Date.now() - Date.parse(this.data.capturedAt) > 600000
            : false
        });
      }
    } finally {
      if (this.inFlight === generation) this.inFlight = null;
    }
  },
  async issueCode() {
    if (this.data.working) return;
    this.setData({ working: true, error: '' });
    try {
      const result = await request(
        this.base() + '/device-enrollments',
        this.session,
        'POST'
      );
      this.setData({ bindingCode: result.data.code });
    } catch (err) {
      this.setData({ error: err.message });
    } finally {
      this.setData({ working: false });
    }
  },
  async locate() {
    if (!this.session || this.session.requestId || this.data.working) return;
    this.setData({ working: true, error: '' });
    try {
      const result = await request(
        this.base() + '/locate-requests',
        this.session,
        'POST'
      );
      this.session.requestId = result.data.id;
      wx.setStorageSync(storage, this.session);
      this.setData({ command: '正在获取' });
    } catch (err) {
      this.setData({ error: err.message });
    } finally {
      this.setData({ working: false });
    }
  },
  clearSession() {
    this.onHide();
    this.visible = true;
    this.session = null;
    wx.removeStorageSync(storage);
    this.setData({
      ...emptyLocation(),
      signedIn: false,
      state: 'unbound',
      command: '',
      bindingCode: '',
      password: ''
    });
  },
  async logout() {
    try {
      await request('/auth/logout', this.session, 'POST');
    } catch (err) {
      this.setData({ error: err.message });
    } finally {
      this.clearSession();
    }
  }
});
