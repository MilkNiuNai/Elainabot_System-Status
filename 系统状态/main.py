# -*- coding: utf-8 -*-
import os
import psutil
import platform
import yaml
from datetime import datetime
from aiohttp import web
from PIL import Image, ImageDraw, ImageFont
from core.plugin.decorators import handler, on_load, on_unload
from core.plugin.web_pages import register_route
from core.base.logger import get_logger, PLUGIN

__plugin_meta__ = {
    'name': '系统监控',
    'author': '酸甜牛奶',
    'description': '查看系统磁盘、cpu和内存情况',
    'version': '1.0.1',  # 版本号升级
    'license': 'MIT',
}

log = get_logger(PLUGIN, '系统监控')

# ---------- 插件根目录 ----------
PLUGIN_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(PLUGIN_DIR, 'data')
CONFIG_PATH = os.path.join(DATA_DIR, 'config.yaml')
IMAGE_PATH = os.path.join(DATA_DIR, 'status.png')
BG_IMAGE_PATH = os.path.join(DATA_DIR, 'background.png')

# ---------- 默认配置内容 ----------
DEFAULT_CONFIG_CONTENT = """# 系统状态插件配置文件
# 1. base_url: 你的 ElainaBot 公网访问地址（含端口）
base_url: "http://127.0.0.1:5200"   # 默认本地地址，请务必修改公网访问地址（含端口）

# 2. use_image: 是否使用图片输出
use_image: true   # true为图片模式|false为文本模式

# 3. use_background: 是否使用自定义背景图
#    - true  : 使用 data/background.png 作为背景（若文件存在）
#    - false : 不使用背景图，纯色背景
use_background: false 
"""

# ---------- 加载配置 ----------
def load_config():
    """从 data/config.yaml 读取配置，若文件不存在则创建默认配置"""
    os.makedirs(DATA_DIR, exist_ok=True)

    if not os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                f.write(DEFAULT_CONFIG_CONTENT)
            log.info(f"已创建默认配置文件: {CONFIG_PATH}，请根据需要修改")
        except Exception as e:
            log.error(f"创建配置文件失败: {e}")

    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
            if not config:
                config = {}
            base_url = config.get('base_url', 'http://127.0.0.1:5200')
            use_image = config.get('use_image', True)
            use_background = config.get('use_background', True)
            return base_url, use_image, use_background
    except Exception as e:
        log.error(f"读取 config.yaml 失败: {e}，使用默认值")
        return 'http://127.0.0.1:5200', True, True

BASE_URL, USE_IMAGE, USE_BACKGROUND = load_config()
IMAGE_URL = f'{BASE_URL}/api/ext/status/status.png'

# ---------- 字体智能加载 ----------
def find_font():
    local_font = os.path.join(PLUGIN_DIR, 'font.ttf')
    if os.path.exists(local_font):
        try:
            ImageFont.truetype(local_font, 10)
            log.info(f"✅ 成功加载本地字体: font.ttf")
            return local_font
        except Exception as e:
            log.warning(f"⚠️ font.ttf 加载失败: {e}")
    for f in os.listdir(PLUGIN_DIR):
        if f.lower().endswith(('.ttf', '.ttc')):
            path = os.path.join(PLUGIN_DIR, f)
            try:
                ImageFont.truetype(path, 10)
                log.info(f"✅ 自动发现并加载字体: {f}")
                return path
            except Exception:
                continue
    system_fonts = [
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    ]
    for sf in system_fonts:
        if os.path.exists(sf):
            return sf
    return None

FONT_PATH = find_font()
if not FONT_PATH:
    log.error("❌ 未找到任何可用的中文字体！请检查插件目录下是否有 .ttf 文件")

# ============ 尺寸与外观 ============
WIDTH = 600
MIN_HEIGHT = 520
MAX_HEIGHT = 1200
PADDING_TOP = 20
PADDING_BOTTOM = 20
BG_COLOR = (30, 30, 30)
TEXT_COLOR = (250, 250, 250)
ACCENT_COLOR = (0, 200, 255)
BAR_BG = (60, 60, 60)

# ============ 间距 ============
SPACING_TITLE_TO_SYS = 50
SPACING_SYS_TO_TIME = 12
SPACING_TIME_TO_CPU_TEXT = 36
SPACING_CPU_TEXT_TO_BAR = 16
SPACING_CPU_BAR_TO_MEM_TEXT = 44
SPACING_MEM_TEXT_TO_BAR = 16
SPACING_MEM_BAR_TO_DISK_TITLE = 60
SPACING_DISK_TITLE_TO_FIRST = 40
SPACING_DISK_DEVICE_TO_BAR = 12
SPACING_DISK_BAR_TO_CAP = 12
SPACING_DISK_CAP_TO_NEXT = 40

# ============ 路由 ============
@register_route('GET', '/api/ext/status/status.png', auth=False)
async def serve_status(request):
    if os.path.exists(IMAGE_PATH):
        response = web.FileResponse(IMAGE_PATH)
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return response
    else:
        return web.Response(status=404, text='Image not found')

# ---------- 绘制工具函数 ----------
def _draw_progress_bar(draw, x, y, width, height, percent, color=ACCENT_COLOR):
    fill_width = int(width * percent / 100)
    draw.rectangle([x, y, x + width, y + height], fill=BAR_BG)
    if fill_width > 0:
        draw.rectangle([x, y, x + fill_width, y + height], fill=color)

def _get_text_height(font, text):
    bbox = font.getbbox(text)
    return bbox[3] - bbox[1]

# ---------- 图片生成 ----------
def _generate_status_image(data):
    try:
        if FONT_PATH:
            title_font = ImageFont.truetype(FONT_PATH, 36)
            label_font = ImageFont.truetype(FONT_PATH, 28)
            detail_font = ImageFont.truetype(FONT_PATH, 24)
        else:
            raise Exception("No font")
    except Exception:
        title_font = ImageFont.load_default()
        label_font = ImageFont.load_default()
        detail_font = ImageFont.load_default()
        log.warning("字体加载失败，使用默认字体")

    left_margin = 30
    max_progress_width = 480
    progress_height = 24

    y = PADDING_TOP
    y += _get_text_height(title_font, "系统状态") + SPACING_TITLE_TO_SYS
    y += _get_text_height(label_font, f"系统：{data['os_name']}") + SPACING_SYS_TO_TIME
    y += _get_text_height(label_font, f"时间：{data['time']}") + SPACING_TIME_TO_CPU_TEXT
    y += _get_text_height(label_font, f"CPU：{data['cpu_percent']:.1f}%") + SPACING_CPU_TEXT_TO_BAR
    y += progress_height + SPACING_CPU_BAR_TO_MEM_TEXT
    mem_text = f"内存：{data['mem_percent']:.1f}% ({data['mem_used']:.1f}/{data['mem_total']:.1f}GB)"
    y += _get_text_height(label_font, mem_text) + SPACING_MEM_TEXT_TO_BAR
    y += progress_height + SPACING_MEM_BAR_TO_DISK_TITLE
    y += _get_text_height(label_font, "磁盘") + SPACING_DISK_TITLE_TO_FIRST

    disk_positions = []
    disk_bar_height = progress_height - 4
    for idx, disk in enumerate(data['disk_list']):
        device, mount, percent, used, total = disk
        disk_start_y = y
        display_mount = mount if len(mount) < 15 else mount[:12] + '...'
        line1 = f"{mount} ({device}) {percent:.1f}%"
        y += _get_text_height(detail_font, line1) + SPACING_DISK_DEVICE_TO_BAR
        y += disk_bar_height + SPACING_DISK_BAR_TO_CAP
        cap_text = f"{used:.1f}GB/{total:.1f}GB"
        y += _get_text_height(detail_font, cap_text) + SPACING_DISK_CAP_TO_NEXT
        disk_positions.append({
            'start_y': disk_start_y,
            'end_y': y,
            'device': device,
            'mount': mount,
            'percent': percent,
            'used': used,
            'total': total,
        })
        if y > MAX_HEIGHT - PADDING_BOTTOM - 40:
            break

    total_height = max(y + PADDING_BOTTOM, MIN_HEIGHT)

    img = Image.new('RGB', (WIDTH, total_height), BG_COLOR)

    if USE_BACKGROUND and os.path.exists(BG_IMAGE_PATH):
        try:
            bg_img = Image.open(BG_IMAGE_PATH).convert('RGB')
            if bg_img.size[0] < WIDTH or bg_img.size[1] < total_height:
                bg_img = bg_img.resize((WIDTH, total_height), Image.LANCZOS)
            else:
                bg_w, bg_h = bg_img.size
                left = (bg_w - WIDTH) // 2
                top = (bg_h - total_height) // 2
                bg_img = bg_img.crop((left, top, left + WIDTH, top + total_height))
            img.paste(bg_img, (0, 0))
        except Exception as e:
            log.error(f"背景图加载失败: {e}")

    draw = ImageDraw.Draw(img)
    y = PADDING_TOP

    def draw_text(text, font, spacing_next):
        nonlocal y
        draw.text((left_margin, y), text, font=font, fill=TEXT_COLOR, anchor='la')
        text_h = _get_text_height(font, text)
        y += text_h + spacing_next

    draw_text("系统状态", title_font, SPACING_TITLE_TO_SYS)
    draw_text(f"系统：{data['os_name']}", label_font, SPACING_SYS_TO_TIME)
    draw_text(f"时间：{data['time']}", label_font, SPACING_TIME_TO_CPU_TEXT)
    draw_text(f"CPU：{data['cpu_percent']:.1f}%", label_font, SPACING_CPU_TEXT_TO_BAR)
    _draw_progress_bar(draw, left_margin, y, max_progress_width, progress_height, data['cpu_percent'])
    y += progress_height + SPACING_CPU_BAR_TO_MEM_TEXT
    draw_text(mem_text, label_font, SPACING_MEM_TEXT_TO_BAR)
    _draw_progress_bar(draw, left_margin, y, max_progress_width, progress_height, data['mem_percent'])
    y += progress_height + SPACING_MEM_BAR_TO_DISK_TITLE
    draw_text("磁盘", label_font, SPACING_DISK_TITLE_TO_FIRST)

    rendered_count = 0
    for dp in disk_positions:
        display_mount = dp['mount'] if len(dp['mount']) < 15 else dp['mount'][:12] + '...'
        line1 = f"{dp['mount']} ({dp['device']}) {dp['percent']:.1f}%"
        draw_text(line1, detail_font, SPACING_DISK_DEVICE_TO_BAR)
        _draw_progress_bar(draw, left_margin, y, max_progress_width - 20, disk_bar_height, dp['percent'], color=(100, 200, 100))
        y += disk_bar_height + SPACING_DISK_BAR_TO_CAP
        cap_text = f"{dp['used']:.1f}GB/{dp['total']:.1f}GB"
        draw_text(cap_text, detail_font, SPACING_DISK_CAP_TO_NEXT)
        rendered_count += 1

    hidden_count = len(data['disk_list']) - rendered_count
    if hidden_count > 0:
        draw.text((left_margin, y + 2), f"...还有 {hidden_count} 个磁盘未显示", font=detail_font, fill=(150, 150, 150), anchor='la')

    return img

# ---------- 纯文本输出函数 ----------
def _progress_bar_text(percent, length=10):
    filled = int(round(percent / 100 * length))
    return '█' * filled + '░' * (length - filled)

def _generate_text_status(data):
    """生成纯文本 Markdown 格式的系统状态（带引用块）"""
    now = data['time']
    os_name = data['os_name']
    cpu_percent = data['cpu_percent']
    mem_percent = data['mem_percent']
    mem_used_gb = data['mem_used']
    mem_total_gb = data['mem_total']
    disk_list = data['disk_list']

    cpu_block = (
        f"> **CPU 占用**：{cpu_percent:.1f}%\n"
        f"> {_progress_bar_text(cpu_percent)}"
    )

    mem_block = (
        f"> **内存占用**：{mem_percent:.1f}%\n"
        f"> {_progress_bar_text(mem_percent)}\n"
        f"> （{mem_used_gb:.1f}GB / {mem_total_gb:.1f}GB）"
    )

    disk_blocks = []
    for device, mount, percent, used, total in disk_list:
        bar = _progress_bar_text(percent)
        block = (
            f"> **{device}** ({mount})：{percent:.1f}%\n"
            f"> {bar}\n"
            f"> （{used:.1f}GB / {total:.1f}GB）"
        )
        disk_blocks.append(block)

    md = f"""# 📊 系统状态

> **操作系统**：{os_name}
> **查询时间**：{now}

{cpu_block}

{mem_block}

## 💾 磁盘使用情况
{chr(10).join(disk_blocks) if disk_blocks else '> （无可用磁盘信息）'}
"""
    return md

# ---------- 生命周期 ----------
@on_load
async def init():
    os.makedirs(DATA_DIR, exist_ok=True)
    log.info(f"系统监控插件已加载 (v1.0.1) | BASE_URL: {BASE_URL} | USE_IMAGE: {USE_IMAGE} | USE_BACKGROUND: {USE_BACKGROUND}")
    if USE_BACKGROUND and os.path.exists(BG_IMAGE_PATH):
        log.info(f"检测到自定义背景图: {BG_IMAGE_PATH}，已启用")
    elif USE_BACKGROUND:
        log.info("use_background 为 true，但未找到 background.png，将使用纯色背景")
    else:
        log.info("use_background 为 false，将使用纯色背景")

@on_unload
def cleanup():
    log.info("系统监控插件已卸载")

# ---------- 命令处理器（仅主人可用） ----------
@handler(r'^/?系统状态$', name='系统状态', desc='查看系统资源占用', priority=5, block=True, owner_only=True)
async def system_status(event, match):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    os_name = platform.system()
    try:
        cpu_percent = psutil.cpu_percent(interval=0.5)
        if cpu_percent is None:
            cpu_percent = 0.0
    except Exception:
        cpu_percent = 0.0

    mem = psutil.virtual_memory()
    mem_percent = mem.percent
    mem_used = mem.used / (1024**3)
    mem_total = mem.total / (1024**3)

    disk_list = []
    for part in psutil.disk_partitions():
        if 'loop' in part.device:
            continue
        if part.fstype:
            try:
                usage = psutil.disk_usage(part.mountpoint)
                disk_list.append((
                    part.device,
                    part.mountpoint,
                    usage.percent,
                    usage.used / (1024**3),
                    usage.total / (1024**3)
                ))
            except PermissionError:
                continue

    data = {
        'os_name': os_name,
        'time': now,
        'cpu_percent': cpu_percent,
        'mem_percent': mem_percent,
        'mem_used': mem_used,
        'mem_total': mem_total,
        'disk_list': disk_list,
    }

    if USE_IMAGE:
        try:
            img = _generate_status_image(data)
            img.save(IMAGE_PATH, format='PNG', quality=95, optimize=True)
            log.info("✅ 图片生成成功，已覆盖旧文件。")
        except Exception as e:
            log.error(f"❌ 图片生成失败: {e}")
            await event.reply(f"生成系统状态图片失败，请检查后台日志。\n错误信息: {e}")
            return

        timestamp = int(datetime.now().timestamp())
        image_url_with_ts = f"{IMAGE_URL}?t={timestamp}"
        img_width, img_height = img.size
        md_content = f"![img #{img_width}px #{img_height}px]({image_url_with_ts})"
        buttons = [[{'text': '🔄 刷新状态', 'data': '系统状态', 'enter': True}]]
        await event.reply(md_content, buttons=buttons)
    else:
        text_md = _generate_text_status(data)
        buttons = [[{'text': '🔄 刷新状态', 'data': '系统状态', 'enter': True}]]
        await event.reply(text_md, buttons=buttons)