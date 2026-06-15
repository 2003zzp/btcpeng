/*
 * 数藏市场自动监控+自动滑块破解脚本
 *
 * 用法：
 * 1. 打开目标页面并登录。
 * 2. 按 F12 打开 DevTools，切到 Console。
 * 3. 粘贴整个文件内容并回车。
 * 4. 当前配置会真实点击“批量购买”；运行前请确认 MAX_PRICE、MAX_BUY_TIMES。
 */
(function () {
  'use strict';

  const CONFIG = {
    // true = 只打印将要点击的按钮，不真正购买；false = 真正点击“批量购买”
    DRY_RUN: false,

    // 求购 -> 在售 -> 求购 的刷新间隔，过快可能触发平台风控
    SWITCH_INTERVAL_MS: 1200,

    // 点击“在售”后等待列表渲染的时间
    AFTER_ONSALE_WAIT_MS: 350,

    // 单价上限。设为 null 表示不限制价格。弹窗里的“最高限价”也会用它校验
    MAX_PRICE: 65.74,

    // 总价上限。设为 null 表示不限制总价
    MAX_TOTAL_PRICE: 3300,

    // 最多点击几次批量购买，防止失控。设为 1 更稳
    MAX_BUY_TIMES: 1,

    // 点击“批量购买”后是否继续点击弹窗里的“确定下单”
    AUTO_CONFIRM_ORDER: true,

    // 等待底部下单弹窗出现的时间
    CONFIRM_ORDER_WAIT_MS: 3000,

    // 开启自动尝试破解图形滑块
    AUTO_SOLVE_CAPTCHA: true,

    // 如果自动破解失败，是否暂停脚本并提醒手动处理
    STOP_ON_CAPTCHA_FAILED: true,
    CAPTCHA_ALERT: true,

    // 完成购买动作后是否自动停止
    STOP_AFTER_BUY: true,

    // 是否在页面左上角显示运行状态
    SHOW_PANEL: true,
  };

  const state = {
    running: false,
    timer: null,
    round: 0,
    buyTimes: 0,
    lastAction: '',
    panel: null,
    captchaRetry: 0, // 单轮滑块尝试次数计数
  };

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function now() {
    return new Date().toLocaleTimeString();
  }

  function log(message, extra) {
    const line = `[市场监控 ${now()}] ${message}`;
    if (extra !== undefined) {
      console.log(line, extra);
    } else {
      console.log(line);
    }
    state.lastAction = message;
    updatePanel();
  }

  function textOf(el) {
    return (el && (el.innerText || el.textContent) || '').replace(/\s+/g, ' ').trim();
  }

  function isVisible(el) {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== 'none'
      && style.visibility !== 'hidden'
      && style.opacity !== '0'
      && rect.width > 0
      && rect.height > 0;
  }

  function clickableOf(el) {
    if (!el) return null;
    return el.closest('button, a, [role="button"], uni-button, .u-button, .button, .btn, [class*="button"], [class*="btn"], view, div') || el;
  }

  function allVisibleElements() {
    return Array.from(document.querySelectorAll('button, a, [role="button"], uni-button, view, div, span, text, img, uni-image'))
      .filter((el) => isVisible(el) && el.id !== 'market-auto-monitor-panel' && !el.closest('#market-auto-monitor-panel'));
  }

  function findByExactText(label) {
    const candidates = allVisibleElements()
      .filter((el) => textOf(el) === label)
      .sort((a, b) => {
        const ar = a.getBoundingClientRect();
        const br = b.getBoundingClientRect();
        return (ar.top - br.top) || (ar.left - br.left);
      });

    return clickableOf(candidates[0]);
  }

  function findByIncludesText(label) {
    const candidates = allVisibleElements()
      .filter((el) => textOf(el).includes(label))
      .sort((a, b) => {
        const ar = a.getBoundingClientRect();
        const br = b.getBoundingClientRect();
        return (ar.top - br.top) || (ar.left - br.left);
      });

    return clickableOf(candidates[0]);
  }

  function clickElement(el, reason) {
    if (!el) return false;
    el.scrollIntoView({ block: 'center', inline: 'center' });
    const rect = el.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    const target = document.elementFromPoint(x, y) || el;
    const targets = Array.from(new Set([target, clickableOf(target), el, clickableOf(el)].filter(Boolean)));

    for (const activeTarget of targets) {
      dispatchTapSequence(activeTarget, x, y);
    }

    log(`点击：${reason} @ ${Math.round(x)},${Math.round(y)} -> ${shortElement(target)}`);
    return true;
  }

  function shortElement(el) {
    if (!el) return 'unknown';
    const cls = typeof el.className === 'string' && el.className ? `.${el.className.trim().split(/\s+/).slice(0, 3).join('.')}` : '';
    return `${el.tagName.toLowerCase()}${el.id ? `#${el.id}` : ''}${cls} "${textOf(el).slice(0, 18)}"`;
  }

  function dispatchTapSequence(el, x, y) {
    const base = {
      bubbles: true,
      cancelable: true,
      composed: true,
      clientX: x,
      clientY: y,
      screenX: x,
      screenY: y,
      pageX: x + window.scrollX,
      pageY: y + window.scrollY,
    };

    if (window.PointerEvent) {
      for (const type of ['pointerover', 'pointerenter', 'pointerdown', 'pointerup', 'pointerout']) {
        el.dispatchEvent(new PointerEvent(type, {
          ...base,
          pointerId: 1,
          pointerType: 'touch',
          isPrimary: true,
          buttons: type === 'pointerdown' ? 1 : 0,
        }));
      }
    }

    dispatchTouch(el, 'touchstart', x, y);
    dispatchTouch(el, 'touchend', x, y);

    for (const type of ['mouseover', 'mouseenter', 'mousedown', 'mouseup', 'click']) {
      el.dispatchEvent(new MouseEvent(type, {
        ...base,
        button: 0,
        buttons: type === 'mousedown' ? 1 : 0,
        view: window,
      }));
    }

    if (typeof el.click === 'function') {
      el.click();
    }
  }

  function dispatchTouch(el, type, x, y) {
    try {
      if (!window.Touch || !window.TouchEvent) return;
      const touch = new Touch({
        identifier: Date.now(),
        target: el,
        clientX: x,
        clientY: y,
        screenX: x,
        screenY: y,
        pageX: x + window.scrollX,
        pageY: y + window.scrollY,
        radiusX: 8,
        radiusY: 8,
      });
      el.dispatchEvent(new TouchEvent(type, {
        bubbles: true,
        cancelable: true,
        composed: true,
        touches: type === 'touchend' ? [] : [touch],
        targetTouches: type === 'touchend' ? [] : [touch],
        changedTouches: [touch],
      }));
    } catch (error) {
      // Ignore
    }
  }

  function findTab(label) {
    return findByExactText(label) || findByIncludesText(label);
  }

  function findBatchBuyButton() {
    const candidates = allVisibleElements()
      .filter((el) => {
        const text = textOf(el);
        return text === '批量购买' || text.includes('批量购买');
      })
      .map((el) => clickableOf(el))
      .filter(Boolean)
      .filter(isVisible);

    return bestBottomButton(candidates, '批量购买');
  }

  function bestBottomButton(candidates, label) {
    const unique = Array.from(new Set(candidates));
    const scored = unique
      .map((el) => {
        const rect = el.getBoundingClientRect();
        const text = textOf(el);
        const exact = text === label ? 100000 : 0;
        const bottomScore = rect.bottom * 100;
        const widthScore = rect.width;
        const areaScore = Math.min(rect.width * rect.height, 50000) / 10;
        return { el, score: exact + bottomScore + widthScore + areaScore, rect, text };
      })
      .sort((a, b) => b.score - a.score);

    const picked = scored[0];
    if (picked) {
      log(`识别到按钮候选：${label}，位置 bottom=${Math.round(picked.rect.bottom)} width=${Math.round(picked.rect.width)} text="${picked.text.slice(0, 30)}"`);
    }

    return picked ? picked.el : null;
  }

  function findConfirmOrderButton() {
    const buttons = allVisibleElements()
      .filter((el) => textOf(el) === '确定下单' || textOf(el).includes('确定下单'))
      .map((el) => clickableOf(el))
      .filter(Boolean)
      .filter(isVisible);

    return bestBottomButton(buttons, '确定下单');
  }

  function parsePrice(text) {
    const match = String(text || '').match(/[¥￥]\s*([0-9]+(?:\.[0-9]+)?)/);
    return match ? Number(match[1]) : null;
  }

  function parseAllPrices(text) {
    return Array.from(String(text || '').matchAll(/[¥￥]\s*([0-9]+(?:\.[0-9]+)?)/g))
      .map((match) => Number(match[1]))
      .filter((price) => Number.isFinite(price));
  }

  function findOrderSheet() {
    const candidates = allVisibleElements()
      .filter((el) => {
        const text = textOf(el);
        return text.includes('批量购买')
          && text.includes('最高限价')
          && text.includes('最低限价')
          && text.includes('合计')
          && text.includes('确定下单');
      })
      .sort((a, b) => {
        const ar = a.getBoundingClientRect();
        const br = b.getBoundingClientRect();
        return (br.width * br.height) - (ar.width * ar.height);
      });

    return candidates[0] || null;
  }

  async function waitForConfirmOrderButton() {
    const deadline = Date.now() + CONFIG.CONFIRM_ORDER_WAIT_MS;

    while (Date.now() < deadline) {
      if (await handleCaptchaIfPresent()) return null;
      const button = findConfirmOrderButton();
      if (button) return button;
      await sleep(120);
    }

    return null;
  }

  function findCaptchaBox() {
    const candidates = allVisibleElements()
      .filter((el) => {
        const text = textOf(el);
        return text.includes('安全验证')
          || text.includes('拖动下方滑块完成拼图')
          || text.includes('滑块')
          || text.includes('拼图')
          || (el.className && typeof el.className === 'string' && (el.className.includes('captcha') || el.className.includes('popup-captcha')));
      })
      .sort((a, b) => {
        const ar = a.getBoundingClientRect();
        const br = b.getBoundingClientRect();
        return (br.width * br.height) - (ar.width * ar.height);
      });

    return candidates[0] || null;
  }

  function beep() {
    if (!CONFIG.CAPTCHA_ALERT) return;

    try {
      const AudioContext = window.AudioContext || window.webkitAudioContext;
      if (!AudioContext) return;
      const ctx = new AudioContext();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.value = 880;
      gain.gain.value = 0.08;
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      setTimeout(() => {
        osc.stop();
        ctx.close();
      }, 350);
    } catch (error) {
      // Ignore
    }
  }

  // ==================== 核心新增：自动识别与破解滑块功能 ====================
  
  // 1. 基于像素分析计算缺口距离
  function getSliderDistance(bgImg, trackWidth) {
    try {
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d');
      canvas.width = bgImg.naturalWidth || bgImg.clientWidth;
      canvas.height = bgImg.naturalHeight || bgImg.clientHeight;
      
      // 允许处理跨域图片
      bgImg.crossOrigin = "Anonymous";
      ctx.drawImage(bgImg, 0, 0, canvas.width, canvas.height);
      
      const imgData = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
      const threshold = 65; // 缺口阴影阈值设定
      
      // 针对部分国内数藏平台（uniapp 视图架构），滑块一般占左侧 15% 宽度，直接从 20% 宽度处往后遍历
      const startX = Math.floor(canvas.width * 0.2); 
      
      for (let x = startX; x < canvas.width; x++) {
        for (let y = Math.floor(canvas.height * 0.2); y < canvas.height * 0.8; y++) {
          const idx = (y * canvas.width + x) * 4;
          const r = imgData[idx];
          const g = imgData[idx + 1];
          const b = imgData[idx + 2];
          
          // 如果某一点呈现明显的深灰色缺口特征
          if (r < threshold && g < threshold && b < threshold) {
            // 根据缩放比例计算出实际页面所需的滑动像素值
            const scale = bgImg.clientWidth / canvas.width;
            return x * scale;
          }
        }
      }
    } catch (e) {
      console.error('[滑块解析失败，可能是跨域阻挡]', e);
    }
    return null;
  }

  // 2. 仿真人类物理轨迹并执行拖拽
  async function simulateSliderDrag(sliderHandle, targetX) {
    const rect = sliderHandle.getBoundingClientRect();
    const startX = rect.left + rect.width / 2;
    const startY = rect.top + rect.height / 2;

    // 按下鼠标/触发触摸
    const downEvents = ['mousedown', 'touchstart', 'pointerdown'];
    downEvents.forEach(type => {
      sliderHandle.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: startX, clientY: startY }));
    });

    const steps = 25;
    let currentX = startX;
    
    // 生成带先加速、后减速和轻微震颤的人类手势轨迹
    for (let i = 1; i <= steps; i++) {
      const progress = i / steps;
      // 经典的缓动公式实现
      const easeX = startX + targetX * (1 - Math.pow(1 - progress, 3)); 
      const jitterY = startY + (Math.random() * 4 - 2); // 允许上下 2 像素的微幅抖动防风控
      
      window.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: easeX, clientY: jitterY }));
      window.dispatchEvent(new MouseEvent('touchmove', { bubbles: true, touches: [{ clientX: easeX, clientY: jitterY }] }));
      
      await sleep(Math.random() * 10 + 8); // 随机滑动步骤延迟
    }

    // 终点稍微做一下左右“微调修正”（模拟手抖）
    window.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: startX + targetX + 2, clientY: startY }));
    await sleep(50);
    window.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: startX + targetX, clientY: startY }));
    await sleep(30);

    // 松开滑块
    const upEvents = ['mouseup', 'touchend', 'pointerup', 'click'];
    upEvents.forEach(type => {
      window.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: startX + targetX, clientY: startY }));
    });
    
    log(`[滑块执行] 仿真轨迹模拟完毕，目标滑动位移: ${Math.round(targetX)} 像素`);
  }

  // 3. 增强处理函数：在点击“确认下单”后的统一拦截入口
  async function handleCaptchaIfPresent() {
    const box = findCaptchaBox();
    if (!box) {
      state.captchaRetry = 0; // 无验证码则清空单轮计数
      return false;
    }

    log('检测到图形滑块/安全验证限制！');
    beep();

    if (!CONFIG.AUTO_SOLVE_CAPTCHA) {
      if (CONFIG.STOP_ON_CAPTCHA_FAILED) stop();
      return true;
    }

    // 尝试寻找页面中的大背景图与拖动手柄
    // 适配常见的各种 class 命名、uni-app 图片节点及普通的标签
    const bgImg = box.querySelector('img[src*="captcha"], img[class*="bg"], .uni-image img, uni-image img') || box.querySelector('img');
    const sliderHandle = box.querySelector('[class*="slider"], [class*="move"], [class*="handler"], uni-button, button');

    if (bgImg && sliderHandle && state.captchaRetry < 3) {
      state.captchaRetry++;
      log(`正在尝试启动自动滑块识别与破解，第 ${state.captchaRetry} 次尝试...`);
      
      await sleep(500); // 等待图片渲染完全
      const distance = getSliderDistance(bgImg);
      
      if (distance && distance > 30) {
        log(`像素分析成功，计算出切片缺口距离: ${Math.round(distance)}px，准备执行仿真滑动。`);
        // 扣除掉滑块本身在左边可能存在的初始边距（平台常见边距一般在 5-10px 左右，这里可根据实际平台微调）
        const finalDistance = distance - 5; 
        await simulateSliderDrag(sliderHandle, finalDistance);
        
        await sleep(1500); // 等待服务端验证返回结果
        
        // 再次检测滑块是否消失
        if (!findCaptchaBox()) {
          log('✨ 恭喜！自动破解滑块成功，弹窗已关闭，继续后续流程。');
          state.captchaRetry = 0;
          return false;
        } else {
          log('滑块依然存在，本次解算可能产生了偏移，等待重试。');
          // 尝试点击验证码刷新按钮（如果有）
          const refreshBtn = box.querySelector('[class*="refresh"], .u-icon__icon');
          if (refreshBtn) clickElement(refreshBtn, '刷新验证码图片');
        }
      } else {
        log('像素深度对比失败（图片可能遭遇跨域保护限制或无明显阴影），无法计算距离。');
      }
    }

    // 如果多次自动尝试失败，执行安全挂起
    if (state.captchaRetry >= 3 || CONFIG.STOP_ON_CAPTCHA_FAILED) {
      log('自动滑块破解失败或达到重试上限，脚本已安全挂起，请立即手动滑过！');
      stop();
    }

    return true;
  }
  // =========================================================================

  function collectOnSaleItems() {
    const elements = allVisibleElements();
    const rows = [];

    for (const el of elements) {
      const text = textOf(el);
      if (!text || text.includes('批量购买')) continue;
      if (!/#\d+/.test(text) || !/[¥￥]\s*\d/.test(text)) continue;
      if (!text.includes('购买')) continue;

      rows.push({
        el,
        text,
        price: parsePrice(text),
      });
    }

    const seen = new Set();
    return rows.filter((row) => {
      const key = row.text;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  function priceAllowed(items) {
    if (CONFIG.MAX_PRICE == null) return true;
    if (!items.length) return false;

    const prices = items.map((item) => item.price).filter((price) => Number.isFinite(price));
    if (!prices.length) return false;

    return Math.min(...prices) <= CONFIG.MAX_PRICE;
  }

  function confirmSheetAllowed() {
    const sheet = findOrderSheet();
    if (!sheet) {
      log('已经出现下单按钮，但没有识别到完整的批量购买弹窗，跳过确定下单');
      return false;
    }

    const text = textOf(sheet);
    const prices = parseAllPrices(text);
    const totalMatch = text.match(/合计\s*[¥￥]?\s*([0-9]+(?:\.[0-9]+)?)/);
    const totalPrice = totalMatch ? Number(totalMatch[1]) : prices[prices.length - 1];
    const unitPrices = prices.filter((price) => price !== totalPrice);
    const highestUnitPrice = unitPrices.length ? Math.max(...unitPrices) : null;

    if (CONFIG.MAX_PRICE != null && highestUnitPrice != null && highestUnitPrice > CONFIG.MAX_PRICE) {
      log(`弹窗最高限价 ${highestUnitPrice} 超过 MAX_PRICE=${CONFIG.MAX_PRICE}，跳过确定下单`);
      return false;
    }

    if (CONFIG.MAX_TOTAL_PRICE != null && Number.isFinite(totalPrice) && totalPrice > CONFIG.MAX_TOTAL_PRICE) {
      log(`弹窗合计 ${totalPrice} 超过 MAX_TOTAL_PRICE=${CONFIG.MAX_TOTAL_PRICE}，跳过确定下单`);
      return false;
    }

    log(`弹窗校验通过：最高限价 ${highestUnitPrice ?? '未知'}，合计 ${Number.isFinite(totalPrice) ? totalPrice : '未知'}`);
    return true;
  }

  async function inspectAndMaybeBuy() {
    const items = collectOnSaleItems();
    const batchButton = findBatchBuyButton();

    if (!items.length || !batchButton) {
      log(`在售暂无可批量购买数据，可见商品 ${items.length} 条`);
      return;
    }

    const prices = items.map((item) => item.price).filter((price) => Number.isFinite(price));
    const minPrice = prices.length ? Math.min(...prices) : null;
    const maxPrice = prices.length ? Math.max(...prices) : null;

    log(`发现可购买数据：${items.length} 条，价格 ${minPrice ?? '未知'} - ${maxPrice ?? '未知'}`);

    if (!priceAllowed(items)) {
      log(`价格超过限制，跳过。MAX_PRICE=${CONFIG.MAX_PRICE}`);
      return;
    }

    if (state.buyTimes >= CONFIG.MAX_BUY_TIMES) {
      log(`已达到最大购买次数 ${CONFIG.MAX_BUY_TIMES}，停止`);
      stop();
      return;
    }

    if (CONFIG.DRY_RUN) {
      log('DRY_RUN 已开启：检测到“批量购买”，但不会真实点击');
      return;
    }

    state.buyTimes += 1;
    clickElement(batchButton, `批量购买，第 ${state.buyTimes} 次`);

    await sleep(350);
    if (await handleCaptchaIfPresent()) return;

    if (CONFIG.AUTO_CONFIRM_ORDER) {
      const confirmButton = await waitForConfirmOrderButton();

      if (!confirmButton) {
        log('没有等到“确定下单”按钮，请手动确认弹窗状态');
      } else if (confirmSheetAllowed()) {
        clickElement(confirmButton, '确定下单');
        
        // 【关键点】：点击确定下单后，给服务器响应和滑块生成腾出时间，并强制进入滑块检查入口
        await sleep(600); 
        if (await handleCaptchaIfPresent()) return;
      }
    }

    if (CONFIG.STOP_AFTER_BUY) {
      log('已完成购买动作，按配置自动停止');
      stop();
    }
  }

  async function tick() {
    if (!state.running) return;

    try {
      state.round += 1;
      updatePanel();

      const buyTab = findTab('求购');
      const saleTab = findTab('在售');

      if (!buyTab || !saleTab) {
        log('没有找到“求购”或“在售”tab，请确认页面是否已加载');
        scheduleNext();
        return;
      }

      clickElement(buyTab, '求购 tab');
      await sleep(CONFIG.SWITCH_INTERVAL_MS);

      clickElement(saleTab, '在售 tab');
      await sleep(CONFIG.AFTER_ONSALE_WAIT_MS);

      await inspectAndMaybeBuy();
    } catch (error) {
      console.error('[市场监控] 执行异常：', error);
      log(`执行异常：${error.message || error}`);
    }

    scheduleNext();
  }

  function scheduleNext() {
    if (!state.running) return;
    clearTimeout(state.timer);
    state.timer = setTimeout(tick, CONFIG.SWITCH_INTERVAL_MS);
  }

  function createPanel() {
    if (!CONFIG.SHOW_PANEL || state.panel) return;

    const panel = document.createElement('div');
    panel.id = 'market-auto-monitor-panel';
    panel.style.cssText = [
      'position:fixed',
      'z-index:2147483647',
      'left:8px',
      'top:8px',
      'max-width:260px',
      'padding:8px 10px',
      'background:rgba(0,0,0,.78)',
      'color:#fff',
      'font-size:12px',
      'line-height:1.45',
      'border:1px solid rgba(255,194,91,.65)',
      'border-radius:6px',
      'box-shadow:0 4px 18px rgba(0,0,0,.28)',
      'pointer-events:none',
      'font-family:Arial,"Microsoft YaHei",sans-serif',
    ].join(';');

    document.body.appendChild(panel);
    state.panel = panel;
    updatePanel();
  }

  function updatePanel() {
    if (!state.panel) return;
    state.panel.innerHTML = [
      '<b>市场自动监控（带滑块防拦截）</b>',
      `状态：${state.running ? '运行中' : '已停止'}`,
      `模式：${CONFIG.DRY_RUN ? '只监控' : '真实点击'}`,
      `轮次：${state.round}`,
      `购买：${state.buyTimes}/${CONFIG.MAX_BUY_TIMES}`,
      `最近：${state.lastAction || '-'}`,
    ].join('<br>');
  }

  function start() {
    if (state.running) {
      log('脚本已经在运行');
      return;
    }

    state.running = true;
    createPanel();
    log('启动监控');
    tick();
  }

  function stop() {
    state.running = false;
    clearTimeout(state.timer);
    state.timer = null;
    log('停止监控');
    updatePanel();
  }

  function debugButtons() {
    const rows = allVisibleElements()
      .filter((el) => {
        const text = textOf(el);
        return text.includes('批量购买') || text.includes('确定下单') || text.includes('购买');
      })
      .map((el) => {
        const rect = el.getBoundingClientRect();
        return {
          tag: el.tagName.toLowerCase(),
          className: typeof el.className === 'string' ? el.className : '',
          text: textOf(el).slice(0, 80),
          left: Math.round(rect.left),
          top: Math.round(rect.top),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
          bottom: Math.round(rect.bottom),
        };
      })
      .sort((a, b) => b.bottom - a.bottom);

    console.table(rows);
    return rows;
  }

  window.marketAutoMonitor = {
    start,
    stop,
    debugButtons,
    config: CONFIG,
    state,
  };

  start();
})();