"""
闲鱼 IM 自动监控机器人
检测买家付款 → 自动生成激活码 → 自动回复

使用方式:
  python bot_monitor.py
  首次启动会打开浏览器，请在 60 秒内扫码登录闲鱼。
  之后 cookie 持久化，重启无需再次扫码。
"""

import asyncio, json, os, time, re, logging, sys
from datetime import datetime
from pathlib import Path
import httpx

# --- Playwright 可选导入 ---
try:
    from playwright.async_api import async_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False
    print("⚠ playwright 未安装，运行: pip install playwright && python -m playwright install chromium")

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "bot_state.json"
STORAGE_DIR = BASE_DIR / "bot_storage"
API_BASE = "http://127.0.0.1:8899"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("xianyu_bot")

# ==============================
# 配置
# ==============================

# 闲鱼网页版 IM 选择器（可在此修改适配页面变化）
SELECTORS = {
    "login_indicator": 'a[href*="login"], .login-btn, [class*="Login"]',   # 检测是否已登录
    "im_entry": 'a[href*="/im"], [class*="message"], [class*="chat"]',    # IM 入口
    "conversation_list": '[class*="conversation"], [class*="chat-item"], [class*="session"]',  # 会话列表
    "conversation_item": 'li, [class*="item"], [role="listitem"]',         # 单个会话
    "unread_badge": '[class*="unread"], [class*="badge"], .count',         # 未读标记
    "message_list": '[class*="message-list"], [class*="chat-content"]',   # 消息列表
    "message_item": '[class*="message-item"], [class*="msg"]',            # 单条消息
    "system_message": '[class*="system"], [class*="notice"], [class*="tip"]', # 系统消息
    "input_box": 'textarea, [contenteditable="true"], [class*="input"]',  # 输入框
    "send_btn": 'button[type="submit"], [class*="send"]',                  # 发送按钮
}

# 付款检测关键词（在消息文本中匹配）
PAYMENT_KEYWORDS = [
    "买家已付款", "已付款", "已拍下", "请尽快发货",
    "付款成功", "买家已拍下", "订单已支付",
]

# 忽略的消息发送者（不回复）
IGNORE_SENDERS = ["系统消息", "闲鱼小蜜", "淘宝"]

# ==============================
# 状态管理
# ==============================

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"processed_messages": [], "stats": {"generated": 0, "replied": 0, "errors": 0, "last_run": None}}

def save_state(state):
    state["stats"]["last_run"] = datetime.now().isoformat()
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

# ==============================
# API 调用
# ==============================

async def generate_code(duration_minutes=300):
    """调用本地 API 生成激活码"""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{API_BASE}/api/bot/create-code",
            json={"duration_minutes": duration_minutes}
        )
        if resp.status_code == 200:
            return resp.json()
        raise Exception(f"API error {resp.status_code}: {resp.text}")

# ==============================
# 核心机器人
# ==============================

class XianyuBot:
    def __init__(self):
        self.state = load_state()
        self.page = None
        self.browser = None
        self.running = True

    async def start(self):
        if not HAS_PLAYWRIGHT:
            log.error("Playwright 未安装，退出")
            return

        # 是否已有登录态
        has_session = STORAGE_DIR.exists() and list(STORAGE_DIR.glob("*.json"))
        
        # 命令行 --headed 强制显示浏览器（用于首次扫码）
        import sys
        force_headed = "--headed" in sys.argv
        
        # 确定是否使用 headless
        headless = not force_headed and has_session
        if not headless:
            log.info("模式: 有界面 (首次扫码登录或手动 --headed)")

        log.info("启动闲鱼 IM 监控机器人...")
        self.playwright = await async_playwright().start()

        browser_args = [
            "--no-sandbox", "--disable-setuid-sandbox",
            "--disable-dev-shm-usage", "--disable-gpu",
        ]

        self.browser = await self.playwright.chromium.launch_persistent_context(
            str(STORAGE_DIR),
            headless=headless,
            args=browser_args,
            viewport={"width": 1280, "height": 900}
        )
        self.page = self.browser.pages[0] if self.browser.pages else await self.browser.new_page()

        if not has_session:
            log.info("首次启动，需要手动扫码登录...")
            await self.page.goto("https://www.goofish.com/im", wait_until="domcontentloaded", timeout=30000)
            if force_headed:
                log.info("请在浏览器窗口中扫码登录闲鱼（60秒内）...")
            else:
                log.info("请用 --headed 参数重启: python bot_monitor.py --headed")

            # 等待登录
            for i in range(24):
                await asyncio.sleep(5)
                try:
                    url = self.page.url
                    if "login" not in url.lower() and "/im" in url.lower():
                        log.info("登录成功！Cookies 已保存。")
                        log.info("现在可以用 headless 模式运行: python bot_monitor.py")
                        break
                except:
                    pass
                if i % 4 == 0:
                    log.info(f"等待登录中... ({i*5}s)")
            else:
                log.warning("登录超时")
                if not force_headed:
                    log.error("服务器环境无法扫码，请在本机执行: python bot_monitor.py --headed")
                    log.error("扫码登录后把 bot_storage/ 目录上传到服务器的 /opt/coding-plan-rental/backend/")
                    await self.stop()
                    return

        # 确保在 IM 页面
        try:
            if "/im" not in self.page.url:
                await self.page.goto("https://www.goofish.com/im", wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            log.warning(f"导航到 IM 页面失败: {e}")

        log.info("机器人就绪，开始监控消息...")
        await self.monitor_loop()

    async def monitor_loop(self):
        """主循环：轮询检测新消息"""
        while self.running:
            try:
                await self.check_messages()
            except Exception as e:
                log.error(f"消息检测异常: {e}")
                self.state["stats"]["errors"] += 1
                save_state(self.state)

            await asyncio.sleep(15)  # 每 15 秒检测一次

    async def check_messages(self):
        """检测是否有新的付款消息"""
        try:
            # 确保页面还在
            try:
                await self.page.title()
            except:
                log.warning("页面已断开，尝试恢复...")
                await self.page.goto("https://www.goofish.com/im", wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(3)
                return

            # 查找会话列表
            conversations = await self._find_conversations_with_unread()
            if not conversations:
                return

            for conv in conversations:
                try:
                    # 点击打开会话
                    await conv.click()
                    await asyncio.sleep(2)

                    # 获取最新消息
                    latest_msgs = await self._get_latest_messages()
                    if not latest_msgs:
                        continue

                    # 检查是否有付款消息
                    payment_detected = False
                    for msg in latest_msgs[-5:]:  # 只看最近 5 条
                        text = (msg.get("text") or "").strip()
                        sender = (msg.get("sender") or "").strip()

                        # 跳过系统账号自己的消息
                        if any(s in sender for s in IGNORE_SENDERS):
                            continue

                        # 检测付款关键词
                        if self._is_payment_message(text):
                            msg_id = f"{sender}_{text[:50]}_{int(time.time())}"
                            if msg_id in self.state["processed_messages"]:
                                log.info(f"消息已处理过: {msg_id[:60]}")
                                continue

                            payment_detected = True
                            log.info(f"检测到付款消息: {text[:80]}")

                            # 生成激活码
                            code_data = await generate_code()
                            reply_text = code_data["reply_text"]

                            # 发送回复
                            await self._send_reply(reply_text)

                            # 记录状态
                            self.state["processed_messages"].append(msg_id)
                            self.state["stats"]["generated"] += 1
                            self.state["stats"]["replied"] += 1
                            save_state(self.state)

                            log.info(f"已自动回复: {code_data['activation_code']}")
                            break  # 一个会话只处理一次

                    if not payment_detected:
                        # 退回会话列表
                        pass

                except Exception as e:
                    log.error(f"处理会话异常: {e}")
                    continue

        except Exception as e:
            log.error(f"check_messages 异常: {e}")
            raise

    def _is_payment_message(self, text):
        text_lower = text.lower()
        for kw in PAYMENT_KEYWORDS:
            if kw.lower() in text_lower:
                return True
        return False

    async def _find_conversations_with_unread(self):
        """在 IM 页面找到有未读消息的会话"""
        results = []
        # 尝试多种选择器组合
        selectors_to_try = [
            "[class*='unread']",
            ".badge",
            "[class*='count']:not(:empty)",
            "span:has-text('1')",
            "span:has-text('2')",
            "span:has-text('3')",
        ]
        for sel in selectors_to_try:
            try:
                badges = self.page.locator(sel)
                count = await badges.count()
                for i in range(count):
                    badge = badges.nth(i)
                    text = (await badge.text_content()).strip()
                    if text and text.isdigit() and int(text) > 0:
                        # 找到未读标记的父级会话元素
                        parent = badge.locator("xpath=ancestor::li | ancestor::div[contains(@class,'item')] | ancestor::div[contains(@class,'conversation')]")
                        try:
                            await parent.first.click(timeout=1000)
                            await asyncio.sleep(1)
                            results.append(parent.first)
                        except:
                            continue
                if results:
                    break
            except:
                continue
        return results

    async def _get_latest_messages(self):
        """获取当前对话的最新消息"""
        messages = []
        for sel in SELECTORS["message_item"].split(", "):
            try:
                items = self.page.locator(sel.strip())
                count = await items.count()
                for i in range(max(0, count - 10), count):
                    item = items.nth(i)
                    text = (await item.text_content()).strip()
                    if text:
                        # 尝试区分发送者
                        sender = ""
                        sender_el = item.locator("[class*='sender'], [class*='name'], [class*='nick']").first
                        try:
                            sender = (await sender_el.text_content()).strip()
                        except:
                            pass
                        messages.append({"text": text, "sender": sender})
                if messages:
                    break
            except:
                continue

        # Fallback: 获取整个消息区域文本
        if not messages:
            try:
                msg_area = self.page.locator(SELECTORS["message_list"].split(", ")[0].strip())
                if await msg_area.count() > 0:
                    text = (await msg_area.first.text_content()).strip()
                    messages.append({"text": text, "sender": ""})
            except:
                pass

        return messages

    async def _send_reply(self, text):
        """在 IM 输入框中输入并发送回复"""
        try:
            # 找到输入框
            input_el = None
            for sel in SELECTORS["input_box"].split(", "):
                try:
                    el = self.page.locator(sel.strip()).first
                    if await el.count() > 0:
                        input_el = el
                        break
                except:
                    continue

            if not input_el:
                log.error("找不到输入框")
                return

            # 输入文本
            await input_el.click()
            await asyncio.sleep(0.3)
            # 使用键盘输入（更可靠）
            await self.page.keyboard.type(text, delay=20)

            # 找到发送按钮
            for sel in SELECTORS["send_btn"].split(", "):
                try:
                    btn = self.page.locator(sel.strip()).first
                    if await btn.count() > 0:
                        await btn.click()
                        log.info("消息发送成功")
                        return
                except:
                    continue

            # Fallback：按 Enter 发送
            await self.page.keyboard.press("Enter")
            log.info("消息发送成功 (Enter)")

        except Exception as e:
            log.error(f"发送回复失败: {e}")

    async def stop(self):
        self.running = False
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        log.info("机器人已停止")


# ==============================
# 入口
# ==============================

async def main():
    bot = XianyuBot()
    try:
        await bot.start()
    except KeyboardInterrupt:
        log.info("收到中断信号")
    except Exception as e:
        log.error(f"致命错误: {e}")
    finally:
        await bot.stop()

if __name__ == "__main__":
    asyncio.run(main())
