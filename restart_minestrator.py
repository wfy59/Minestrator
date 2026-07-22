import os
import time
import json
import urllib.request
import urllib.parse
import re
from seleniumbase import SB

_account = os.environ["MINESTRATOR_ACCOUNT"].split(",")
EMAIL      = _account[0].strip()
PASSWORD   = _account[1].strip()
SERVER_ID  = os.environ.get("MINESTRATOR_SERVER_ID", "").strip()
AUTH_TOKEN = os.environ.get("MINESTRATOR_AUTH", "").strip()

_proxy = os.environ.get("GOST_PROXY", "").strip()
LOCAL_PROXY = "http://127.0.0.1:8080" if _proxy else None

_tg = os.environ.get("TG_BOT", "").strip()
TG_CHAT_ID = _tg.split(",")[0].strip() if _tg else ""
TG_TOKEN   = _tg.split(",")[1].strip() if _tg and "," in _tg else ""

LOGIN_URL  = "https://minestrator.com/connexion"
SERVER_URL = f"https://minestrator.com/my/server/{SERVER_ID}"
API_URL    = f"https://mine.sttr.io/server/{SERVER_ID}/poweraction"

# ============================================================
# TG 推送（可选）
# ============================================================

def now_str():
    import datetime
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def send_tg(result, detail=''):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("ℹ️ 未配置 TG_BOT，跳过推送")
        return
    msg = (
        f"🎮 Minestrator 重启通知\n"
        f"🕐 运行时间: {now_str()}\n"
        f"🖥 服务器: 🇫🇷 Minestrator-FR\n"
        f"📊 结果: {result}\n"
        f"{detail}"
    )
    url  = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": TG_CHAT_ID, "text": msg}).encode()
    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=15):
            print("📨 TG推送成功")
    except Exception as e:
        print(f"⚠️ TG推送失败：{e}")


# ============================================================
# Invisible Turnstile：注入监听器，轮询等待 token
# ============================================================

INJECT_TOKEN_LISTENER_JS = """
(function() {
    if (window.__cf_token_listener_injected__) return;
    window.__cf_token_listener_injected__ = true;
    window.__cf_turnstile_token__ = '';

    window.addEventListener('message', function(e) {
        if (!e.origin || e.origin.indexOf('cloudflare.com') === -1) return;
        var d = e.data;
        if (!d || d.event !== 'complete' || !d.token) return;

        console.log('[TokenCapture] complete, token length:', d.token.length);
        window.__cf_turnstile_token__ = d.token;

        var inputs = document.querySelectorAll(
            'input[name="cf-turnstile-response"], input[name="cf_turnstile_response"]'
        );
        for (var i = 0; i < inputs.length; i++) {
            try {
                var nativeSet = Object.getOwnPropertyDescriptor(
                    HTMLInputElement.prototype, 'value'
                ).set;
                nativeSet.call(inputs[i], d.token);
                inputs[i].dispatchEvent(new Event('input',  {bubbles: true}));
                inputs[i].dispatchEvent(new Event('change', {bubbles: true}));
            } catch(err) {
                inputs[i].value = d.token;
            }
        }
    });
    console.log('[TokenCapture] listener injected');
})();
"""

READ_TOKEN_JS = "(function(){ return window.__cf_turnstile_token__ || ''; })()"


def inject_listener(sb):
    try:
        sb.execute_script(INJECT_TOKEN_LISTENER_JS)
        print("📡 Turnstile 监听器已注入")
    except Exception as e:
        print(f"⚠️ 监听器注入失败：{e}")


def wait_for_token(sb, timeout=30) -> str:
    print(f"⏳ 等待 Turnstile Token 自动生成（最多 {timeout} 秒）...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            token = sb.execute_script(READ_TOKEN_JS)
            if token and len(token) > 50:
                print(f"✅ Token 已捕获（长度 {len(token)}）")
                return token
        except Exception:
            pass
        try:
            token = sb.execute_script("""
                (function(){
                    var inp = document.querySelector('input[name="cf-turnstile-response"]');
                    return (inp && inp.value && inp.value.length > 50) ? inp.value : '';
                })()
            """)
            if token:
                print(f"✅ Token 从 input 读取（长度 {len(token)}）")
                return token
        except Exception:
            pass
        time.sleep(1)

    print("ℹ️ 未能捕获到 Token（可能新版页面无验证码），将尝试直接推进。")
    return ''


# ============================================================
# API：通过浏览器 fetch 发送重启指令（携带登录 Cookie）
# ============================================================

def send_restart(sb, token: str) -> bool:
    token_js = json.dumps(token)
    script = (
        "var done = arguments[0];"
        'fetch("' + API_URL + '", {'
        '  method: "PUT",'
        '  headers: {'
        '    "Authorization": "' + AUTH_TOKEN + '",'
        '    "Content-Type": "application/json",'
        '    "Accept": "application/json",'
        '    "X-Requested-With": "XMLHttpRequest"'
        '  },'
        '  body: JSON.stringify({poweraction: "restart", turnstile_token: ' + token_js + '})'
        '})'
        '.then(function(r){ return r.json(); })'
        '.then(function(data){ done({ok: true, data: data}); })'
        '.catch(function(err){ done({ok: false, error: err.toString()}); });'
    )
    try:
        result = sb.execute_async_script(script)
        print(f"📡 API响应：{result}")
        if result.get("ok") and result.get("data", {}).get("api", {}).get("code") == 200:
            print("✅ API 重启指令已成功送达！")
            return True
        print(f"❌ API返回异常：{result}")
        return False
    except Exception as e:
        print(f"⚠️ API请求异常：{e}")
        return False


# ============================================================
# 主流程
# ============================================================

def run_script():
    print("🔧 启动浏览器...")

    sb_kwargs = dict(uc=True, test=True)
    if LOCAL_PROXY:
        sb_kwargs["proxy"] = LOCAL_PROXY
        print(f"🌐 使用代理：{LOCAL_PROXY}")
    else:
        print("ℹ️ 未配置代理，直连运行")

    with SB(**sb_kwargs) as sb:
        print("🚀 浏览器就绪！")

        # ── IP 验证 ──────────────────────────────────────────
        print("🌐 验证出口IP...")
        try:
            sb.open("https://api.ipify.org/?format=json")
            ip_text = re.sub(r'(\d+\.\d+\.\d+\.)\d+', r'\1xx', sb.get_text('body'))
            print(f"✅ 出口IP确认：{ip_text}")
        except Exception:
            print("⚠️ IP验证超时，跳过")

        # ── 登录 ─────────────────────────────────────────────
        print("🔑 打开登录页面...")
        sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=4)
        time.sleep(3)

        # 登录前先把 Turnstile 监听器注入，等 token 出现再点登录
        inject_listener(sb)

        print("✏️ 填写账号密码...")
        try:
            username_selector = "input[type='text'], input[placeholder*='utilisateur'], input[name='pseudo']"
            password_selector = "input[type='password'], input[name='password']"

            sb.wait_for_element_visible(username_selector, timeout=20)
            sb.type(username_selector, EMAIL)
            sb.type(password_selector, PASSWORD)
            try:
                sb.execute_script(
                    "var r=document.querySelector('input[type=\"checkbox\"], #remember'); if(r) r.checked=true;"
                )
            except Exception:
                pass
        except Exception as e:
            print(f"❌ 登录框加载失败，报错详情: {e}")
            sb.save_screenshot("login_fail.png")
            return

        print("📤 提交登录请求...")
        try:
            if sb.is_element_visible('button:contains("Se connecter")'):
                sb.click('button:contains("Se connecter")')
            elif sb.is_element_visible("button[type='submit']"):
                sb.click("button[type='submit']")
            else:
                sb.click(".btn-text")
        except Exception as e:
            print(f"❌ 登录按钮不可用，报错详情: {e}")
            sb.save_screenshot("login_submit_fail.png")
            return

        # 点完登录按钮后立刻再注一次监听器（有些页面会在提交后才渲染 Turnstile）
        inject_listener(sb)

        print("⏳ 等待登录跳转...")
        # 登录等待时间：Minestrator 登录需走 Turnstile + 二次跳转，
        # 之前默认 20s 太短容易超时。这里放宽到 90s（180 * 0.5s）。
        for i in range(180):
            try:
                # 顺手读取 Turnstile token，便于排查
                _ = sb.execute_script(READ_TOKEN_JS)
                if "/connexion" not in sb.get_current_url():
                    print(f"✅ 登录成功！当前页：{sb.get_current_url()}")
                    break
            except Exception:
                pass
            time.sleep(0.5)
        else:
            print("❌ 登录等待超时（已等待 90 秒）")
            try:
                cur = sb.get_current_url()
                print(f"   当前 URL：{cur}")
                body_snip = sb.execute_script("document.body.innerText.slice(0, 500)")
                print(f"   页面文本前 500 字符：{body_snip}")
            except Exception:
                pass
            sb.save_screenshot("login_timeout.png")
            return

        # ── 跳转服务器 management 页 ──────────────────────────────────
        print(f"🔃 跳转至服务器管理页：{SERVER_URL}")
        sb.open(SERVER_URL)
        time.sleep(5)
        print(f"📄 当前页面：{sb.get_current_url()}")
        sb.save_screenshot("server_page.png")

        # ── 试图捕获 Token ────────────────────────────────────
        inject_listener(sb)
        token = wait_for_token(sb, timeout=30)  # 留足时间等 Token

        # ── 发送重启指令（第一保险：API 路径） ─────────────────
        print("🚀 尝试通过后台 API 发送重启请求...")
        api_success = send_restart(sb, token)

        # ── （第二保险：前端 UI 模拟点击兜底） ─────────────────
        if not api_success:
            print("🔄 后台 API 未响应成功，启动第二预案：模拟真人点击前端网页按钮...")
            try:
                # 兼容新老版法文面板的"重启"按钮特征词 (Redémarrer)
                ui_selectors = [
                    'button:contains("Redémarrer")',
                    'a:contains("Redémarrer")',
                    'button[data-action="restart"]',
                    '.btn-restart',
                    '[id*="restart"]'
                ]
                clicked = False
                for selector in ui_selectors:
                    if sb.is_element_visible(selector):
                        sb.click(selector)
                        print(f"✅ 成功点击前端网页重启按钮: {selector}")
                        clicked = True
                        time.sleep(3)
                        break

                if not clicked:
                    raise Exception("在前台页面未匹配到任何叫做 'Redémarrer' 或包含 restart 的按钮")
            except Exception as e:
                print(f"❌ 双重重启方案均告失败。")
                sb.save_screenshot("all_methods_failed.png")
                send_tg("❌ 重启请求失败", f"API与UI点击均失效。错误原因: {e}")
                return

        # ── 刷新页面，验证利用期限 ──────────────────────
        print("🔄 刷新页面等待状态更新...")
        time.sleep(5)
        sb.open(SERVER_URL)
        time.sleep(5)
        try:
            remaining = sb.execute_script(r"""
                (function(){
                    var spans = document.querySelectorAll('[data-slot="base"] span, .time-remaining, span:contains("h")');
                    var parts = [];
                    for (var i = 0; i < spans.length; i++) {
                        var t = spans[i].textContent.trim();
                        if (/^\d+[hms]$/.test(t) || /\d+\s*heur/.test(t)) parts.push(t);
                    }
                    return parts.length ? parts.join(' ') : '';
                })()
            """)
            detail = f"⏰ 利用期限：{remaining}" if remaining else "⏰ 利用期限：已发出重启指令（未能精准截取剩余时间文本）"
        except Exception:
            detail = "利用期限：无法获取具体文本"

        print(f"⏱️ {detail}")
        send_tg("✅ 重启成功！", detail)


if __name__ == "__main__":
    run_script()
