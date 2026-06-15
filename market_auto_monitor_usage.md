# 数藏市场自动监控脚本使用说明

脚本文件：`market_auto_monitor.js`

## 使用步骤

1. 在 Chrome 打开数藏市场页面，并确认已经登录。
2. 按 `F12` 打开开发者工具，进入 `Console` 控制台。
3. 打开 `market_auto_monitor.js`，复制全部内容到控制台并回车。
4. 当前脚本默认会真实点击“批量购买”，请先确认价格上限和购买次数。

## 真实点击购买

脚本顶部当前配置为：

```js
DRY_RUN: false
```

默认价格上限和最大购买次数为：

```js
MAX_PRICE: 64.36,
MAX_BUY_TIMES: 1
```

如需先测试不真实购买，把 `DRY_RUN` 改回 `true`。

## 控制命令

停止脚本：

```js
marketAutoMonitor.stop()
```

重新启动：

```js
marketAutoMonitor.start()
```

临时修改刷新间隔：

```js
marketAutoMonitor.config.SWITCH_INTERVAL_MS = 1500
```

## 工作逻辑

脚本会循环执行：

1. 点击“求购”tab。
2. 等待一小段时间。
3. 点击“在售”tab。
4. 检查是否出现带编号、价格和购买按钮的在售数据。
5. 如果找到“批量购买”按钮，并满足价格、次数限制，就点击它。

脚本不会处理验证码、短信、人脸、支付密码或其他平台风控校验。
如果出现图形滑块/安全验证，脚本会暂停并提示你手动完成。
