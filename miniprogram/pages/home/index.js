Page({
  data: { fontSize: 16 },

  onShow() {
    const fontSizeSetting = wx.getAppBaseInfo?.().fontSizeSetting;
    if (fontSizeSetting > 0) {
      this.setData({ fontSize: fontSizeSetting });
    }
  }
});
