"""系统监控插件 — 查询bot所在系统状态（CPU/内存/磁盘），支持图片/文本两种模式"""
import os
import sys
import asyncio
import importlib
import subprocess
import platform
import psutil
from datetime import datetime
from aiohttp import web
from PIL import Image, ImageDraw, ImageFont
from core.plugin.decorators import handler, on_load, on_unload
from core.plugin.web_pages import register_route
from core.base.logger import get_logger, PLUGIN
import core.plugin.context as _ctx_mod

__plugin_meta__ = {
    'name': '系统监控',
    'author': '酸甜牛奶',
    'description': '查询bot所在的系统状态，可查询系统信息、CPU、内存、磁盘。',
    'version': '1.2.0',
    'github': 'https://github.com/MilkNiuNai/Elainabot_System-Status',
    'license': 'MIT',
}

log = get_logger(PLUGIN, '系统监控')
ctx = _ctx_mod.ctx

PLUGIN_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(PLUGIN_DIR, 'data')
IMAGE_PATH = os.path.join(DATA_DIR, 'status.png')
BG_IMAGE_PATH = os.path.join(DATA_DIR, 'background.png')

DEFAULT_CONFIG = {
    'base_url': 'http://127.0.0.1:5200',
    'use_image': True,
    'use_background': False,
    'use_module_hosting': True,
}

# 模块级配置缓存 — @on_load 时由 ctx.ensure_config() 填充
_config = dict(DEFAULT_CONFIG)

_DEPS_CHECKED = False


# ═══════════════════════════════════════════════════════════════
#  依赖 & 字体
# ═══════════════════════════════════════════════════════════════

def ensure_deps():
    """检查并安装缺失的第三方依赖（仅首次调用生效）"""
    global _DEPS_CHECKED
    if _DEPS_CHECKED:
        return
    _DEPS_CHECKED = True
    deps = {'psutil': 'psutil', 'PIL': 'Pillow', 'yaml': 'PyYAML'}
    missing = [pkg for mod, pkg in deps.items() if not importlib.util.find_spec(mod)]
    if missing:
        log.warning(f"缺少依赖: {missing}，尝试安装...")
        try:
            subprocess.check_call(
                [sys.executable, '-m', 'pip', 'install'] + missing, timeout=60
            )
        except Exception as e:
            log.error(f"自动安装失败: {e}")


def find_font():
    """查找可用中文字体：插件目录 > 系统路径

    跨平台 — 使用 sys.platform 做精确匹配:
      win32 → C:/Windows/Fonts/
      linux → /usr/share/fonts/
      darwin → /System/Library/Fonts/
    """
    # 1) 插件自带字体（优先）
    for f in os.listdir(PLUGIN_DIR):
        if f.lower().endswith(('.ttf', '.ttc')):
            return os.path.join(PLUGIN_DIR, f)

    # 2) 系统回退路径
    system_fonts = {
        'win32':  ['C:/Windows/Fonts/msyh.ttc'],
        'linux':  ['/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf'],
        'darwin': ['/System/Library/Fonts/PingFang.ttc'],
    }
    for p in system_fonts.get(sys.platform, []):
        if os.path.exists(p):
            return p
    return None


FONT_PATH = None


def _get_hosting():
    """获取图床模块实例"""
    if not _config['use_module_hosting']:
        return None
    try:
        from core.bot.manager import _bot_manager_ref
        if _bot_manager_ref and _bot_manager_ref.module_manager:
            return _bot_manager_ref.module_manager.get('image_hosting')
    except Exception as e:
        log.warning(f"获取图床模块失败: {e}")
    return None


# ═══════════════════════════════════════════════════════════════
#  图片生成
# ═══════════════════════════════════════════════════════════════

WIDTH, MIN_HEIGHT, MAX_HEIGHT = 600, 520, 1200
PAD_TOP, PAD_BOTTOM = 20, 20

BG_COLOR     = (30, 30, 30)
TEXT_COLOR   = (250, 250, 250)
ACCENT_COLOR = (0, 200, 255)
BAR_BG       = (60, 60, 60)
DISK_COLOR   = (100, 200, 100)

# 各元素垂直间距（按绘制顺序）
SP_TITLE       = 50   # 标题 → 系统信息
SP_OS          = 12   # 系统信息 → 时间
SP_TIME        = 36   # 时间 → CPU 文本
SP_CPU_TEXT    = 16   # CPU 文本 → CPU 进度条
SP_CPU_BAR     = 44   # CPU 进度条 → 内存文本
SP_MEM_TEXT    = 16   # 内存文本 → 内存进度条
SP_MEM_BAR     = 60   # 内存进度条 → 磁盘标题
SP_DISK_TITLE  = 40   # 磁盘标题 → 第一块磁盘
SP_DISK_LABEL  = 12   # 磁盘标签 → 磁盘进度条
SP_DISK_BAR    = 12   # 磁盘进度条 → 磁盘详情
SP_DISK_DETAIL = 40   # 磁盘详情 → 下一块磁盘


@register_route('GET', '/api/ext/status/status.png', auth=False)
async def serve_status(request):
    if os.path.exists(IMAGE_PATH):
        return web.FileResponse(IMAGE_PATH, headers={'Cache-Control': 'no-cache'})
    return web.Response(status=404, text='Image not found')


def _draw_bar(draw, x, y, w, h, pct, color=ACCENT_COLOR):
    """绘制进度条"""
    draw.rectangle([x, y, x + w, y + h], fill=BAR_BG)
    if pct > 0:
        draw.rectangle([x, y, x + int(w * pct / 100), y + h], fill=color)


def _text_h(font, text):
    """文本渲染高度"""
    bbox = font.getbbox(text)
    return bbox[3] - bbox[1]


def _gen_img(data):
    """生成系统状态图片（同步函数，应在 executor 中调用）"""
    global FONT_PATH
    if not FONT_PATH:
        FONT_PATH = find_font()
    try:
        tf = ImageFont.truetype(FONT_PATH, 36)
        lf = ImageFont.truetype(FONT_PATH, 28)
        df = ImageFont.truetype(FONT_PATH, 24)
    except Exception:
        tf = lf = df = ImageFont.load_default()

    SP = (
        SP_TITLE, SP_OS, SP_TIME, SP_CPU_TEXT, SP_CPU_BAR,
        SP_MEM_TEXT, SP_MEM_BAR, SP_DISK_TITLE, SP_DISK_LABEL,
        SP_DISK_BAR, SP_DISK_DETAIL,
    )
    lm, pw, ph = 30, 480, 24

    # ── 第一遍：高度预计算 ──
    y = PAD_TOP
    y += _text_h(tf, "系统状态") + SP[0]
    y += _text_h(lf, f"系统：{data['os_name']}") + SP[1]
    y += _text_h(lf, f"时间：{data['time']}") + SP[2]
    y += _text_h(lf, f"CPU：{data['cpu_percent']:.1f}%") + SP[3]
    y += ph + SP[4]
    mem = f"内存：{data['mem_percent']:.1f}% ({data['mem_used']:.1f}/{data['mem_total']:.1f}GB)"
    y += _text_h(lf, mem) + SP[5]
    y += ph + SP[6]
    y += _text_h(lf, "磁盘") + SP[7]
    dph = ph - 4
    dcount = 0
    for dev, mnt, pct, used, total in data['disk_list']:
        y += _text_h(df, f"{mnt} ({dev}) {pct:.1f}%") + SP[8]
        y += dph + SP[9]
        y += _text_h(df, f"{used:.1f}GB/{total:.1f}GB") + SP[10]
        dcount += 1
        if y > MAX_HEIGHT - PAD_BOTTOM - 40:
            break

    th = max(y + PAD_BOTTOM, MIN_HEIGHT)

    # ── 新建画布 ──
    img = Image.new('RGB', (WIDTH, th), BG_COLOR)
    if _config['use_background'] and os.path.exists(BG_IMAGE_PATH):
        try:
            bg = Image.open(BG_IMAGE_PATH).convert('RGB')
            if bg.size[0] < WIDTH or bg.size[1] < th:
                bg = bg.resize((WIDTH, th), Image.LANCZOS)
            else:
                left = (bg.size[0] - WIDTH) // 2
                top = (bg.size[1] - th) // 2
                bg = bg.crop((left, top, left + WIDTH, top + th))
            img.paste(bg, (0, 0))
        except Exception:
            pass

    # ── 第二遍：实际绘制 ──
    draw = ImageDraw.Draw(img)
    y = PAD_TOP

    def dt(text, font, sp):
        nonlocal y
        draw.text((lm, y), text, font=font, fill=TEXT_COLOR, anchor='la')
        y += _text_h(font, text) + sp

    dt("系统状态", tf, SP[0])
    dt(f"系统：{data['os_name']}", lf, SP[1])
    dt(f"时间：{data['time']}", lf, SP[2])
    dt(f"CPU：{data['cpu_percent']:.1f}%", lf, SP[3])
    _draw_bar(draw, lm, y, pw, ph, data['cpu_percent'])
    y += ph + SP[4]
    dt(mem, lf, SP[5])
    _draw_bar(draw, lm, y, pw, ph, data['mem_percent'])
    y += ph + SP[6]
    dt("磁盘", lf, SP[7])
    for dev, mnt, pct, used, total in data['disk_list'][:dcount]:
        dt(f"{mnt} ({dev}) {pct:.1f}%", df, SP[8])
        _draw_bar(draw, lm, y, pw - 20, dph, pct, DISK_COLOR)
        y += dph + SP[9]
        dt(f"{used:.1f}GB/{total:.1f}GB", df, SP[10])
    hidden = len(data['disk_list']) - dcount
    if hidden > 0:
        draw.text((lm, y + 2), f"...还有 {hidden} 个磁盘未显示",
                  font=df, fill=(150, 150, 150), anchor='la')

    return img


def _gen_text(data):
    """生成文本版状态消息（use_image=False 时使用）"""
    disks = '\n'.join(
        f'> {d[0]} {d[1]}: {d[2]:.1f}% ({d[3]:.1f}/{d[4]:.1f}GB)'
        for d in data['disk_list']
    )
    return (
        f"# 📊 系统状态\n"
        f"> 系统：{data['os_name']}\n"
        f"> 时间：{data['time']}\n"
        f"> CPU：{data['cpu_percent']:.1f}%\n"
        f"> 内存：{data['mem_percent']:.1f}% "
        f"({data['mem_used']:.1f}/{data['mem_total']:.1f}GB)\n"
        f"> 磁盘：\n{disks}"
    )


# ── executor 辅助：将同步 I/O 包装为可传给 run_in_executor 的函数 ──

def _save_png(img, path):
    img.save(path, format='PNG', optimize=True)


def _read_bytes(path):
    with open(path, 'rb') as f:
        return f.read()


# ═══════════════════════════════════════════════════════════════
#  生命周期
# ═══════════════════════════════════════════════════════════════

@on_load
async def init():
    os.makedirs(DATA_DIR, exist_ok=True)

    # 使用框架 ctx 管理 YAML 配置（缺项自动补齐 + 异常保护）
    config = ctx.ensure_config(DEFAULT_CONFIG, filename='config.yaml')
    _config.update(config)

    ensure_deps()
    try:
        psutil.cpu_percent(interval=0.1)
    except Exception:
        pass

    global FONT_PATH
    FONT_PATH = find_font()
    log.info(f"系统监控插件 v{__plugin_meta__['version']} 已加载")


@on_unload
def cleanup():
    log.info("系统监控插件已卸载")


# ═══════════════════════════════════════════════════════════════
#  命令处理器
# ═══════════════════════════════════════════════════════════════

@handler(
    r'^/?系统状态$', name='系统状态', desc='查看系统资源占用',
    priority=5, block=True, owner_only=True,
)
async def system_status(event, match):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    os_name = platform.system()
    cpu = psutil.cpu_percent(interval=0.5) or 0.0
    mem = psutil.virtual_memory()
    mem_u = mem.used / 1024 ** 3
    mem_t = mem.total / 1024 ** 3

    disks = []
    for p in psutil.disk_partitions():
        if 'loop' in p.device or 'snap' in p.device:
            continue
        if p.fstype:
            try:
                u = psutil.disk_usage(p.mountpoint)
                disks.append((
                    p.device, p.mountpoint, u.percent,
                    u.used / 1024 ** 3, u.total / 1024 ** 3,
                ))
            except Exception:
                pass

    data = {
        'os_name': os_name,
        'time': now,
        'cpu_percent': cpu,
        'mem_percent': mem.percent,
        'mem_used': mem_u,
        'mem_total': mem_t,
        'disk_list': disks,
    }

    if not _config['use_image']:
        return await event.reply(
            _gen_text(data),
            buttons=[[{'text': '🔄 刷新', 'data': '系统状态', 'enter': True}]],
        )

    loop = asyncio.get_event_loop()

    # 图片生成 → 线程池，避免阻塞事件循环
    try:
        img = await loop.run_in_executor(None, _gen_img, data)
        await loop.run_in_executor(None, _save_png, img, IMAGE_PATH)
    except Exception as e:
        log.error(f"图片生成失败: {e}")
        return await event.reply(f"生成失败: {e}")

    img_w, img_h = img.size
    timestamp = int(datetime.now().timestamp())
    base_url = _config['base_url']
    image_url = f"{base_url}/api/ext/status/status.png?t={timestamp}"

    # ==================== 修改开始 ====================
    # 使用图床模块统一上传方法 upload_any
    hosting = _get_hosting()
    if hosting:
        img_bytes = await loop.run_in_executor(None, _read_bytes, IMAGE_PATH)
        log.info("📤 使用图床模块上传...")
        try:
            # 调用 upload_any，让图床模块自动选择可用图床
            res = await hosting.upload_any(img_bytes)
            # 处理返回值：可能是字符串 URL，或元组 (bool, url/msg)，或字典
            if isinstance(res, str) and res.startswith('http'):
                image_url = res
                log.info(f"✅ 图床上传成功: {image_url}")
            elif isinstance(res, tuple) and len(res) == 2:
                if res[0] is True and isinstance(res[1], str) and res[1].startswith('http'):
                    image_url = res[1]
                    log.info(f"✅ 图床上传成功: {image_url}")
                else:
                    log.warning(f"图床上传返回失败: {res[1]}")
            elif isinstance(res, dict) and res.get('file_url'):
                image_url = res['file_url']
                log.info(f"✅ 图床上传成功: {image_url}")
            else:
                log.warning(f"图床上传返回未知格式: {res}")
        except Exception as e:
            log.error(f"图床上传异常: {e}")
    else:
        log.warning("图床模块不可用，使用本地图片链接")
    # ==================== 修改结束 ====================

    md = f"![img #{img_w}px #{img_h}px]({image_url})"
    await event.reply(md, buttons=[[{'text': '🔄 刷新状态', 'data': '系统状态', 'enter': True}]])