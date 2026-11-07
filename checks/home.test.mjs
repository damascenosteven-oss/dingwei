import assert from 'node:assert/strict';
import { readFileSync, statSync } from 'node:fs';
import { test } from 'node:test';
import { runInNewContext } from 'node:vm';

const project = new URL('../', import.meta.url);
const home = 'pages/home/index';

function read(root, path) {
  return readFileSync(new URL(path, root), 'utf8');
}

function config(root, path) {
  return JSON.parse(read(root, path));
}

function miniProgramRoot() {
  return new URL(config(project, 'project.config.json').miniprogramRoot, project);
}

test('开发者工具可从项目配置定位小程序入口', () => {
  const settings = config(project, 'project.config.json');
  assert.equal(settings.compileType, 'miniprogram');
  assert.equal(settings.miniprogramRoot, 'miniprogram/');
  const root = miniProgramRoot();
  assert.doesNotThrow(() => config(root, 'app.json'));
  let registered = false;
  runInNewContext(read(root, 'app.js'), { App() { registered = true; } });
  assert.ok(registered, '缺少 App 注册');
});

test('默认首页已注册且可加载', () => {
  const root = miniProgramRoot();
  assert.equal(config(root, 'app.json').pages?.[0], home, '首个注册页面必须是家人首页');
  for (const ext of ['js', 'json', 'wxml', 'wxss']) {
    assert.ok(statSync(new URL(home + '.' + ext, root)).isFile());
  }
  config(root, home + '.json');
  let registered = false;
  runInNewContext(read(root, home + '.js'), { Page() { registered = true; } });
  assert.ok(registered, '缺少 Page 注册');
});

test('首页初始显示未绑定提示', () => {
  const markup = read(miniProgramRoot(), home + '.wxml');
  assert.match(markup.replace(/<!--[\s\S]*?-->/g, ''), /<text\b[^>]*>\s*尚未绑定老人设备\s*<\/text>/);
});
