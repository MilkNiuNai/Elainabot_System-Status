import os, sys, subprocess, importlib, psutil, platform, yaml
from datetime import datetime
from aiohttp import web
from PIL import Image, ImageDraw, ImageFont
from core.plugin.decorators import handler, on_load, on_unload
from core.plugin.web_pages import register_route
from core.base.logger import get_logger, PLUGIN

__plugin_meta__ = {
    'name': '系统监控',
    'author': '酸甜牛奶',
    'description': '查询bot所在的系统状态，可查询系统信息、CPU、内存、磁盘。',
    'version': '1.1.0',
    'github': 'https://github.com/MilkNiuNai/Elainabot_System-Status',
    'license': 'MIT',
}

log = get_logger(PLUGIN, '系统监控')

PLUGIN_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(PLUGIN_DIR, 'data')
CONFIG_PATH = os.path.join(DATA_DIR, 'config.yaml')
IMAGE_PATH = os.path.join(DATA_DIR, 'status.png')
BG_IMAGE_PATH = os.path.join(DATA_DIR, 'background.png')

DEFAULT_CONFIG = """\
base_url: "http://127.0.0.1:5200"
use_image: true
use_background: false
use_module_hosting: true
"""

def load_config():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            f.write(DEFAULT_CONFIG)
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}

cfg = load_config()
BASE_URL = cfg.get('base_url', 'http://127.0.0.1:5200')
USE_IMAGE = cfg.get('use_image', True)
USE_BACKGROUND = cfg.get('use_background', False)
USE_MODULE_HOSTING = cfg.get('use_module_hosting', True)
IMAGE_URL = f'{BASE_URL}/api/ext/status/status.png'

# 依赖自动安装
def ensure_deps():
    deps = {'psutil': 'psutil', 'PIL': 'Pillow', 'yaml': 'PyYAML'}
    missing = [pkg for mod, pkg in deps.items() if not importlib.util.find_spec(mod)]
    if missing:
        log.warning(f"缺少依赖: {missing}，尝试安装...")
        try:
            subprocess.check_call([sys.executable, '-m', 'pip', 'install'] + missing, timeout=60)
        except Exception as e:
            log.error(f"自动安装失败: {e}")

# 字体
def find_font():
    local = os.path.join(PLUGIN_DIR, 'font.ttf')
    if os.path.exists(local):
        return local
    for f in os.listdir(PLUGIN_DIR):
        if f.lower().endswith(('.ttf', '.ttc')):
            return os.path.join(PLUGIN_DIR, f)
    paths = {
        "linux": ["/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"],
        "win32": ["C:/Windows/Fonts/msyh.ttc"],
        "darwin": ["/System/Library/Fonts/PingFang.ttc"]
    }
    for p in paths.get(platform.system().lower(), []):
        if os.path.exists(p):
            return p
    return None

FONT_PATH = None

# 获取图床模块实例（框架标准方法）
def _get_hosting():
    if not USE_MODULE_HOSTING:
        return None
    try:
        from core.bot.manager import _bot_manager_ref
        if _bot_manager_ref and _bot_manager_ref.module_manager:
            return _bot_manager_ref.module_manager.get('image_hosting')
    except Exception as e:
        log.warning(f"获取图床模块失败: {e}")
    return None

# 图片生成（保持不变）
WIDTH, MIN_HEIGHT, MAX_HEIGHT = 600, 520, 1200
PAD_T, PAD_B = 20, 20
BG = (30,30,30); TC = (250,250,250); AC = (0,200,255); BB = (60,60,60)
SP = (50,12,36,16,44,16,60,40,12,12,40)

@register_route('GET', '/api/ext/status/status.png', auth=False)
async def serve_status(request):
    if os.path.exists(IMAGE_PATH):
        return web.FileResponse(IMAGE_PATH, headers={'Cache-Control': 'no-cache'})
    return web.Response(status=404, text='Image not found')

def _draw_bar(draw, x, y, w, h, pct, color=AC):
    draw.rectangle([x,y,x+w,y+h], fill=BB)
    if pct>0: draw.rectangle([x,y,x+int(w*pct/100),y+h], fill=color)

def _text_h(font, text):
    return font.getbbox(text)[3]-font.getbbox(text)[1]

def _gen_img(data):
    global FONT_PATH
    if not FONT_PATH: FONT_PATH = find_font()
    try:
        tf = ImageFont.truetype(FONT_PATH, 36)
        lf = ImageFont.truetype(FONT_PATH, 28)
        df = ImageFont.truetype(FONT_PATH, 24)
    except:
        tf = lf = df = ImageFont.load_default()
    lm = 30; pw = 480; ph = 24
    y = PAD_T
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
        if y > MAX_HEIGHT - PAD_B - 40: break
    th = max(y + PAD_B, MIN_HEIGHT)
    img = Image.new('RGB', (WIDTH, th), BG)
    if USE_BACKGROUND and os.path.exists(BG_IMAGE_PATH):
        try:
            bg = Image.open(BG_IMAGE_PATH).convert('RGB')
            if bg.size[0]<WIDTH or bg.size[1]<th:
                bg = bg.resize((WIDTH, th), Image.LANCZOS)
            else:
                left = (bg.size[0]-WIDTH)//2
                top = (bg.size[1]-th)//2
                bg = bg.crop((left, top, left+WIDTH, top+th))
            img.paste(bg, (0,0))
        except: pass
    draw = ImageDraw.Draw(img)
    y = PAD_T
    def dt(text, font, sp):
        nonlocal y
        draw.text((lm, y), text, font=font, fill=TC, anchor='la')
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
        _draw_bar(draw, lm, y, pw-20, dph, pct, (100,200,100))
        y += dph + SP[9]
        dt(f"{used:.1f}GB/{total:.1f}GB", df, SP[10])
    hidden = len(data['disk_list']) - dcount
    if hidden>0:
        draw.text((lm, y+2), f"...还有 {hidden} 个磁盘未显示", font=df, fill=(150,150,150), anchor='la')
    return img

def _gen_text(data):
    disks = '\n'.join(f'> {d[0]} {d[1]}: {d[2]:.1f}% ({d[3]:.1f}/{d[4]:.1f}GB)' for d in data['disk_list'])
    return f"# 📊 系统状态\n> 系统：{data['os_name']}\n> 时间：{data['time']}\n> CPU：{data['cpu_percent']:.1f}%\n> 内存：{data['mem_percent']:.1f}% ({data['mem_used']:.1f}/{data['mem_total']:.1f}GB)\n> 磁盘：\n{disks}"

# 生命周期
@on_load
async def init():
    os.makedirs(DATA_DIR, exist_ok=True)
    ensure_deps()
    try: psutil.cpu_percent(interval=0.1)
    except: pass
    global FONT_PATH
    FONT_PATH = find_font()
    log.info("系统监控插件 v2.4.0 已加载")

@on_unload
def cleanup():
    log.info("系统监控插件已卸载")

# 命令处理器
@handler(r'^/?系统状态$', name='系统状态', desc='查看系统资源占用', priority=5, block=True, owner_only=True)
async def system_status(event, match):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    os_name = platform.system()
    cpu = psutil.cpu_percent(interval=0.5) or 0.0
    mem = psutil.virtual_memory()
    mem_u = mem.used/1024**3; mem_t = mem.total/1024**3
    disks = []
    for p in psutil.disk_partitions():
        if 'loop' in p.device or 'snap' in p.device: continue
        if p.fstype:
            try:
                u = psutil.disk_usage(p.mountpoint)
                disks.append((p.device, p.mountpoint, u.percent, u.used/1024**3, u.total/1024**3))
            except: pass
    data = {'os_name':os_name,'time':now,'cpu_percent':cpu,'mem_percent':mem.percent,'mem_used':mem_u,'mem_total':mem_t,'disk_list':disks}

    if not USE_IMAGE:
        return await event.reply(_gen_text(data), buttons=[[{'text':'🔄 刷新','data':'系统状态','enter':True}]])

    try:
        img = _gen_img(data)
        img.save(IMAGE_PATH, format='PNG', quality=95, optimize=True)
    except Exception as e:
        log.error(f"图片生成失败: {e}")
        return await event.reply(f"生成失败: {e}")

    img_w, img_h = img.size
    timestamp = int(datetime.now().timestamp())
    image_url = f"{IMAGE_URL}?t={timestamp}"

    # 通过图床模块上传
    hosting = _get_hosting()
    if hosting:
        with open(IMAGE_PATH, 'rb') as f:
            img_bytes = f.read()
        log.info("📤 使用图床模块上传...")
        # 按优先级尝试所有已开启图床
        upload_chain = [
            (hosting.upload_nature, 'Nature', hosting.is_nature_available),
            (hosting.upload_bilibili, 'B站', hosting.is_bilibili_available),
            (hosting.upload_chatglm, 'ChatGLM', hosting.is_chatglm_available),
            (hosting.upload_ukaka, 'Ukaka', hosting.is_ukaka_available),
            (hosting.upload_xingye, '星野', hosting.is_xingye_available),
        ]
        uploaded = False
        for func, name, check in upload_chain:
            if not check():
                continue
            log.info(f"  尝试 {name}...")
            try:
                res = await func(img_bytes)
                if isinstance(res, str) and res.startswith('http'):
                    image_url = res
                    log.info(f"✅ {name} 上传成功: {image_url}")
                    uploaded = True
                    break
                else:
                    reason = res[1] if isinstance(res, tuple) else '未知'
                    log.warning(f"  {name} 失败: {reason}")
            except Exception as e:
                log.error(f"  {name} 异常: {e}")
        if not uploaded:
            log.warning("所有已启用图床均上传失败，使用本地图片链接")
    else:
        log.warning("图床模块不可用，使用本地图片链接")

    md = f"![img #{img_w}px #{img_h}px]({image_url})"
    await event.reply(md, buttons=[[{'text':'🔄 刷新状态','data':'系统状态','enter':True}]])
