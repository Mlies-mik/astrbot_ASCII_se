import os
import tempfile
import uuid
from PIL import Image, ImageDraw, ImageFont
import datetime
import asyncio
import time
import shutil
import aiohttp
import aiofiles
import logging

from astrbot.api import star, AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image as BotImage, Reply, Plain, At
from astrbot.api.star import StarTools


class AsciiArtPlugin(star.Star):
    """尝试硬编码一个限制，像素上限 (6000px)
    不出意外的话，这个限制比配置文件中的限制更优先,避免图片过大"""
    ABSOLUTE_MAX_DIMENSION = 6000

    def __init__(self, context: star.Context, config: AstrBotConfig = None) -> None:
        super().__init__(context)
        self.context = context
        self.config = config or {}
        # 使用 StarTools 获取插件数据目录
        self.plugin_data_dir = StarTools.get_data_dir("ascii_art_plugin")
        os.makedirs(self.plugin_data_dir, exist_ok=True)

        # 初始化配置参数
        self._init_config()

        # 设置日志记录器
        self.logger = logging.getLogger("AsciiArtPlugin")

        # 启动定期清理缓存文件的后台任务
        self._cleanup_task = None
        asyncio.create_task(self._start_cleanup_task())

    def _init_config(self):
        """初始化配置参数，设置默认值及安全限制"""
        # 参数名配置
        self.scale_param = self.config.get("scale_param", "--scale")
        self.charset_param = self.config.get("charset_param", "--charset")
        self.chinese_param = self.config.get("chinese_param", "--chinese")

        # 默认值配置
        self.default_scale = self.config.get("default_scale", 1.0)
        self.default_charset = self.config.get("default_charset", "@#S%?*+;:,.")
        self.default_chinese_charset = self.config.get("default_chinese_charset",
                                                       "爱你喜欢我他她它好美帅酷炫酷帅呆了棒赞优强牛厉害威武霸气萌萌哒赞赞赞顶顶顶神神神")

        # 倍数限制配置
        self.min_scale = self.config.get("min_scale", 0.1)
        self.max_scale = self.config.get("max_scale", 10.0)  # 放宽最大倍数限制

        user_max_dim = self.config.get("max_dimension", 6000) # 默认调大到6000以适应高画质
        self.effective_max_dim = min(user_max_dim, self.ABSOLUTE_MAX_DIMENSION)

        self.cache_cleanup_interval = self.config.get("cache_cleanup_interval", 60)
        self.cache_max_age = self.config.get("cache_max_age", 1440)

        self.help_message = self.config.get("help_message",
                                            "请引用一张图片并发送此命令\n\n"
                                            "使用方法:\n"
                                            "  /ascii - 默认清晰度(1.0倍)\n"
                                            "  /ascii --scale 3.0 - 提高清晰度(推荐2-5倍)\n"
                                            "  /ascii --scale 0.8 - 降低清晰度\n"
                                            "  /ascii --charset @#$ - 自定义字符集\n"
                                            "  /ascii --chinese - 使用中文字符\n\n"
                                            f"注意：输出图片长宽限制为 {self.effective_max_dim}px"
                                            )

        self.result_message = self.config.get("result_message", "图片已转换为ASCII艺术:")

    async def _start_cleanup_task(self):
        """启动定期清理缓存文件的后台任务"""
        try:
            await asyncio.sleep(5)
            while True:
                await asyncio.sleep(self.cache_cleanup_interval * 60)
                await self._cleanup_old_cache_files()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error("[缓存清理] 后台任务出错: %s", e, exc_info=True)

    async def _cleanup_old_cache_files(self):
        """清理过期的缓存文件"""
        try:
            current_time = time.time()
            max_age_seconds = self.cache_max_age * 60
            deleted_count = 0

            if not os.path.exists(self.plugin_data_dir):
                return

            for filename in os.listdir(self.plugin_data_dir):
                file_path = os.path.join(self.plugin_data_dir, filename)
                if not os.path.isfile(file_path): continue
                if not filename.startswith("ascii_result"): continue

                try:
                    file_age = current_time - os.path.getmtime(file_path)
                    if file_age > max_age_seconds:
                        os.remove(file_path)
                        deleted_count += 1
                except Exception as e:
                    self.logger.error("[缓存清理] 删除文件 %s 时出错: %s", file_path, e, exc_info=True)

            if deleted_count > 0:
                self.logger.info("[缓存清理] 清理了 %d 个过期的缓存文件", deleted_count)
        except Exception as e:
            self.logger.error("[缓存清理] 清理过程出错: %s", e, exc_info=True)

    async def _download_image(self, url: str) -> bytes | None:
        """下载图片"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    return await resp.read()
        except Exception as e:
            self.logger.error(f"下载图片失败: {url}, 错误: {e}")
            return None

    async def _get_avatar(self, user_id: str) -> bytes | None:
        """获取用户QQ头像"""
        if not user_id.isdigit():
            return None

        avatar_url = f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"
        return await self._download_image(avatar_url)

    def _parse_command_params(self, message_text: str) -> dict:
        """解析命令参数"""
        params = {
            "scale": self.default_scale,
            "charset": self.default_charset,
            "use_chinese": False,
            "charset_specified": False,
            "scale_adjusted": False,
            "adjusted_scale": None
        }

        tokens = message_text.split()
        i = 0
        while i < len(tokens):
            token = tokens[i]

            if token == self.scale_param:
                if i + 1 < len(tokens):
                    try:
                        scale_val = float(tokens[i + 1])
                        if scale_val < self.min_scale:
                            params["scale"] = self.min_scale
                            params["scale_adjusted"] = True
                            params["adjusted_scale"] = self.min_scale
                        elif scale_val > self.max_scale:
                            params["scale"] = self.max_scale
                            params["scale_adjusted"] = True
                            params["adjusted_scale"] = self.max_scale
                        else:
                            params["scale"] = scale_val
                    except ValueError:
                        pass
                    i += 2
                    continue

            if token == self.charset_param:
                if i + 1 < len(tokens):
                    params["charset"] = tokens[i + 1]
                    params["charset_specified"] = True
                    i += 2
                    continue

            if token == self.chinese_param:
                params["use_chinese"] = True
                i += 1
                continue

            i += 1
        return params

    async def _get_images(self, event: AstrMessageEvent) -> bytes | None:
        """获取图片数据，支持从消息、回复和@用户头像中获取"""
        # 查找直接发送的图片或回复中的图片
        for component in event.message_obj.message:
            if isinstance(component, BotImage):
                if component.url:
                    return await self._download_image(component.url)
                elif component.file:
                    return open(component.file, 'rb').read()
            elif isinstance(component, Reply) and component.chain:
                for reply_component in component.chain:
                    if isinstance(reply_component, BotImage):
                        if reply_component.url:
                            return await self._download_image(reply_component.url)
                        elif reply_component.file:
                            return open(reply_component.file, 'rb').read()
        
        # 查找@用户并获取其头像
        for component in event.message_obj.message:
            if isinstance(component, At):
                avatar_data = await self._get_avatar(str(component.qq))
                if avatar_data:
                    return avatar_data
                    
        return None

    @filter.command("ascii")
    async def ascii_command(self, event: AstrMessageEvent):
        """主命令入口"""
        # 提取纯文本内容用于参数解析
        message_text = ""
        for comp in getattr(event.message_obj, "message", []):
            if isinstance(comp, Plain):
                message_text += comp.text

        # 解析命令参数
        params = self._parse_command_params(message_text)

        # 获取图片数据（从消息、回复或@用户头像）
        image_data = await self._get_images(event)
        
        if not image_data:
            event.set_result(event.plain_result(self.help_message))
            return

        temp_dir = tempfile.gettempdir()
        temp_filename = f"ascii_input_{uuid.uuid4().hex}.jpg"
        temp_path = os.path.join(temp_dir, temp_filename)

        try:
            # 保存图片数据到临时文件
            with open(temp_path, "wb") as f:
                f.write(image_data)

            # 区分中文/英文模式
            use_chinese = params["use_chinese"]
            charset = params["charset"] if params["charset_specified"] else (
                self.default_chinese_charset if use_chinese else params["charset"]
            )

            # 执行转换，捕获可能的尺寸超限错误
            try:
                ascii_result_path = await self.convert_image_to_ascii(
                    image_path=temp_path,
                    scale=params["scale"],
                    charset=charset,
                    use_chinese=use_chinese
                )

                if ascii_result_path and os.path.exists(ascii_result_path):
                    abs_path = os.path.abspath(ascii_result_path)
                    result_image = BotImage.fromFileSystem(abs_path)
                    chain = [Plain(self.result_message), result_image]

                    if params.get("scale_adjusted"):
                        adjustment_msg = f"\n\n倍数超出配置范围，已自动调整为 {params['adjusted_scale']}"
                        chain.insert(1, Plain(adjustment_msg))

                    event.set_result(event.chain_result(chain))
                else:
                    event.set_result(event.plain_result("转换失败，未知错误"))

            except ValueError as ve:
                # 捕获在转换函数中抛出的尺寸过大异常
                event.set_result(event.plain_result(f"生成失败: {str(ve)}"))

        except Exception as e:
            self.logger.error("处理过程中发生错误: %s", e, exc_info=True)
            event.set_result(event.plain_result(f"系统错误: {str(e)}"))
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    async def download_image(self, url: str, save_path: str):
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    async with aiofiles.open(save_path, "wb") as f:
                        async for chunk in response.content.iter_chunked(1024):
                            await f.write(chunk)
                else:
                    raise Exception(f"下载失败，状态码: {response.status}")

    async def convert_image_to_ascii(self, image_path: str, scale: float = 1.0, charset: str = "@#S%?*+;:,.",
                                     use_chinese: bool = False) -> str:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._sync_convert_image_to_ascii,
            image_path, scale, charset, use_chinese
        )

    def _sync_convert_image_to_ascii(self, image_path: str, scale: float = 1.0, charset: str = "@#S%?*+;:,.",
                                     use_chinese: bool = False) -> str:
        """
        同步转换逻辑
        修复了字体测量方式，确保画布尺寸能容纳绘制的文字
        """
        img = Image.open(image_path)

        # F1 字体加载
        font_size = 12 if use_chinese else 10
        font = None

        # 优先尝试常见的等宽中文字体
        chinese_fonts = [
            "simhei.ttf", "msyh.ttc", "simsun.ttc", 
            "SimHei", "Microsoft YaHei", "SimSun", 
            "wqy-zenhei.ttc", "wqy-microhei.ttc"  # Linux常见
        ]
        
        # 英文模式优先尝试等宽字体
        ascii_fonts = [
            "consola.ttf", "cour.ttf", "lucon.ttf",
            "DejaVuSansMono.ttf", "Courier New", "arial.ttf"
        ]

        font_list = chinese_fonts if use_chinese else ascii_fonts

        for font_name in font_list:
            try:
                font = ImageFont.truetype(font_name, font_size)
                break
            except:
                continue

        if font is None:
            # 加载默认字体（load_default通常不是等宽的，效果可能一般，但在容器内可能没得选）
            font = ImageFont.load_default()

        # F2 字体测量修正 (选择回滚到更紧凑的计算方式，消除间隙)
        try:
            # 尝试测试保证画布足够大
            test_char = "中" if use_chinese else "A"
            
            # 使用 getbbox 获取墨迹宽度，而不是步进宽度 getlength
            # 这样会应该会让字符排布更紧密，恢复旧版的高密度细节效果
            l, t, r, b = font.getbbox(test_char)
            char_width = r - l
            char_height = b - t
            
            # 如果测量失败，尝试回退到默认值
            if char_width == 0: 
                char_width = font_size
            if char_height == 0: 
                char_height = font_size

        except Exception as e:
            self.logger.warning(f"字体测量失败，使用默认值: {e}")
            char_width, char_height = (font_size, font_size)

        # F3 计算网格尺寸
        # 提高基础网格上限，以支持更高的清晰度
        if use_chinese:
            base_grid_width = max(50, min(img.width // 10, 120))
        else:
            base_grid_width = max(80, min(img.width // 6, 500))

        # 应用倍数
        new_grid_width = int(base_grid_width * scale)

        # 计算高度 (应用字符比例修正)
        aspect_ratio = img.height / img.width
        correction_factor = char_width / char_height
        new_grid_height = int(aspect_ratio * new_grid_width * correction_factor)

        # F4 安全检查
        final_pixel_width = new_grid_width * char_width
        final_pixel_height = new_grid_height * char_height

        if final_pixel_width > self.effective_max_dim or final_pixel_height > self.effective_max_dim:
            raise ValueError(
                f"输出尺寸 ({final_pixel_width}x{final_pixel_height}) 超过限制 ({self.effective_max_dim}px)。"
                f"当前倍数: {scale}, 请尝试减小 --scale 参数。"
            )

        # F5 图片处理
        img = img.resize((new_grid_width, new_grid_height), Image.Resampling.LANCZOS)
        img = img.convert("L")

        # F6 字符映射
        ascii_chars = list(charset)
        step = 256 // len(ascii_chars)
        
        # 预计算字符索引，加速循环
        len_chars_minus_1 = len(ascii_chars) - 1
        
        lines = []
        for i in range(new_grid_height):
            line_chars = []
            for j in range(new_grid_width):
                gray_value = img.getpixel((j, i))
                # 简单的映射逻辑
                char_index = int(gray_value / step)
                if char_index > len_chars_minus_1:
                    char_index = len_chars_minus_1
                line_chars.append(ascii_chars[char_index])
            lines.append("".join(line_chars))

        # F7 绘图保存
        if use_chinese:
            suffix = f"chinese_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}.png"
        else:
            suffix = f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}.png"
            
        output_filename = f"ascii_result_{suffix}"
        output_path = os.path.join(self.plugin_data_dir, output_filename)

        # 创建白色背景画布
        # 这里尝试 final_pixel_width 确保画布足够放下所有文字
        ascii_img = Image.new("RGB", (final_pixel_width, final_pixel_height), color="white")
        draw = ImageDraw.Draw(ascii_img)

        for i, line in enumerate(lines):
            # 绘制每一行
            # y 坐标按照 i * char_height 计算
            draw.text((0, i * char_height), line, fill="black", font=font)

        ascii_img.save(output_path)
        return output_path