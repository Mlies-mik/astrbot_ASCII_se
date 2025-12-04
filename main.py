import os
import tempfile
import uuid
from PIL import Image, ImageDraw, ImageFont
import datetime
import asyncio
import time

from astrbot.api import star, AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image as BotImage, Reply, Plain
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


class AsciiArtPlugin(star.Star):
    def __init__(self, context: star.Context, config: AstrBotConfig = None) -> None:
        super().__init__(context)
        self.context = context
        self.config = config or {}
        # 创建插件数据目录在 plugin_data 中
        self.plugin_data_dir = os.path.join(get_astrbot_data_path(), "plugin_data", "ascii_art_plugin")
        os.makedirs(self.plugin_data_dir, exist_ok=True)
        
        # 初始化配置参数
        self._init_config()
        
        # 启动定期清理缓存文件的后台任务
        self._cleanup_task = None
        asyncio.create_task(self._start_cleanup_task())

    def _init_config(self):
        """初始化配置参数，设置默认值"""
        # 参数名配置
        self.width_param = self.config.get("width_param", "--width")  # 仅主参数名，无别名
        self.charset_param = self.config.get("charset_param", "--charset")
        self.chinese_param = self.config.get("chinese_param", "--chinese")
        
        # 默认值配置
        self.default_width = self.config.get("default_width", 0) or None
        self.default_charset = self.config.get("default_charset", "@#S%?*+;:,.")
        self.default_chinese_charset = self.config.get("default_chinese_charset", "爱你喜欢我他她它好美帅酷炫酷帅呆了棒赞优强牛厉害威武霸气萌萌哒赞赞赞顶顶顶神神神")
        
        # 宽度限制配置
        self.min_width = self.config.get("min_width", 50)
        self.max_width = self.config.get("max_width", 300)
        
        # 缓存清理配置（单位：分钟）
        self.cache_cleanup_interval = self.config.get("cache_cleanup_interval", 60)  # 默认每60分钟清理一次
        self.cache_max_age = self.config.get("cache_max_age", 1440)  # 默认保留1440分钟（24小时）
        
        # 帮助信息
        self.help_message = self.config.get("help_message", "请引用一张图片并发送此命令\n\n使用方法:\n  /ascii - 默认转换\n  /ascii width 150 - 指定输出宽度\n  /ascii charset @#$ - 自定义字符集\n  /ascii chinese - 使用中文字符\n\n可以组合使用多个参数")
    
    async def _start_cleanup_task(self):
        """启动定期清理缓存文件的后台任务"""
        try:
            await asyncio.sleep(5)  # 延迟5秒启动，等待插件完全初始化
            while True:
                await asyncio.sleep(self.cache_cleanup_interval * 60)  # 转换为秒
                await self._cleanup_old_cache_files()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[缓存清理] 后台任务出错: {e}")
    
    async def _cleanup_old_cache_files(self):
        """清理过期的缓存文件"""
        try:
            current_time = time.time()
            max_age_seconds = self.cache_max_age * 60  # 转换为秒
            deleted_count = 0
            
            if not os.path.exists(self.plugin_data_dir):
                return
            
            for filename in os.listdir(self.plugin_data_dir):
                file_path = os.path.join(self.plugin_data_dir, filename)
                
                # 仅处理文件，跳过目录
                if not os.path.isfile(file_path):
                    continue
                
                # 检查文件是否以 ascii_result 开头（即缓存文件）
                if not filename.startswith("ascii_result"):
                    continue
                
                try:
                    file_age = current_time - os.path.getmtime(file_path)
                    if file_age > max_age_seconds:
                        os.remove(file_path)
                        deleted_count += 1
                except Exception as e:
                    print(f"[缓存清理] 删除文件 {file_path} 时出错: {e}")
            
            if deleted_count > 0:
                print(f"[缓存清理] 清理了 {deleted_count} 个过期的缓存文件")
        except Exception as e:
            print(f"[缓存清理] 清理过程出错: {e}")
    
    def _parse_command_params(self, message_text: str) -> dict:
        """
        解析命令参数，支持以下格式：
        /ascii --width 150 --chinese --charset @#$
        /ascii c cabd              （c为charset_param，cabd为字符集值，不会被误解为其他参数）
        /ascii --width 150 --charset @#$
        
        参数解析规则：
        - 参数通过"参数名 参数值"的形式传递
        - 参数值被消费后不会被再次当作参数名扫描
        - 中文参数（如--chinese）是"开关"，无需参数值
        """
        params = {
            "width": self.default_width,
            "charset": self.default_charset,
            "use_chinese": False,
            "charset_specified": False  # 标记用户是否明确指定了字符集
        }
        
        # 分割消息为单个单词
        tokens = message_text.split()
        i = 0
        while i < len(tokens):
            token = tokens[i]
            
            # 检查宽度参数 - 需要参数值（数字）
            if token == self.width_param:
                if i + 1 < len(tokens):
                    try:
                        width_val = int(tokens[i + 1])
                        if self.min_width <= width_val <= self.max_width:
                            params["width"] = width_val
                    except ValueError:
                        pass
                    i += 2  # 跳过参数名和参数值，参数值不会被再次扫描
                    continue
            
            # 检查字符集参数 - 需要参数值（字符串）
            if token == self.charset_param:
                if i + 1 < len(tokens):
                    params["charset"] = tokens[i + 1]
                    params["charset_specified"] = True  # 标记用户明确指定了字符集
                    i += 2  # 跳过参数名和参数值，参数值不会被再次扫描
                    continue
            
            # 检查中文参数 - 仅为开关，无需参数值
            if token == self.chinese_param:
                params["use_chinese"] = True
                i += 1  # 仅跳过参数名
                continue
            
            i += 1
        return params

    @filter.command("ascii")
    async def ascii_command(self, event: AstrMessageEvent):
        """
        将引用的图片转换为ASCII艺术
                    if token == self.chinese_param:
        使用方法:
        1. 在QQ中引用一张图片并发送 "/ascii" 指令
        2. 支持自定义参数，具体参数名和别名可在配置面板中设置
        """
        # 获取消息文本（兼容 AstrBotMessage 无 text 属性，仅拼接 Plain 文本）
        message_text = ""
        for comp in getattr(event.message_obj, "message", []):
            if isinstance(comp, Plain):
                message_text += comp.text
        
        # 解析命令参数（使用配置中定义的参数名）
        params = self._parse_command_params(message_text)
        
        # 检查是否有引用消息并且包含图片
        reply_image = None
        for component in event.message_obj.message:
            # 检查直接发送的图片
            if isinstance(component, BotImage):
                reply_image = component
                break
            # 检查引用消息中的图片
            elif isinstance(component, Reply) and component.chain:
                for reply_component in component.chain:
                    if isinstance(reply_component, BotImage):
                        reply_image = reply_component
                        break
                if reply_image:
                    break
                
        if not reply_image:
            event.set_result(event.plain_result(self.help_message))
            return
            
        # 下载图片到临时文件
        temp_dir = tempfile.gettempdir()
        temp_filename = f"ascii_input_{uuid.uuid4().hex}.jpg"
        temp_path = os.path.join(temp_dir, temp_filename)
        
        try:
            # 从URL下载图片
            if reply_image.url:
                await self.download_image(reply_image.url, temp_path)
            elif reply_image.file:
                # 如果已经有本地路径，复制文件
                import shutil
                shutil.copy(reply_image.file, temp_path)
            else:
                event.set_result(event.plain_result("无法获取图片数据"))
                return
            
            # 转换为ASCII艺术
            if params["use_chinese"]:
                # 使用中文模式
                # 如果用户指定了字符集，使用用户指定的；否则使用默认中文字符集
                charset_to_use = params["charset"] if params["charset_specified"] else self.default_chinese_charset
                ascii_result_path = await self.convert_image_to_ascii_chinese(
                    temp_path, 
                    params["width"], 
                    charset_to_use
                )
            else:
                ascii_result_path = await self.convert_image_to_ascii(
                    temp_path, 
                    params["width"], 
                    params["charset"]
                )
            
            # 发送结果
            if ascii_result_path and os.path.exists(ascii_result_path):
                # 确保文件路径是绝对路径
                abs_path = os.path.abspath(ascii_result_path)
                result_image = BotImage.fromFileSystem(abs_path)
                chain = [
                    Plain("图片已转换为ASCII艺术:"),
                    result_image
                ]
                event.set_result(event.chain_result(chain))
            else:
                event.set_result(event.plain_result("转换失败，请稍后重试"))
                
        except Exception as e:
            import traceback
            traceback.print_exc()
            event.set_result(event.plain_result(f"处理过程中发生错误: {str(e)}"))
        finally:
            # 清理临时文件
            if os.path.exists(temp_path):
                os.remove(temp_path)
    
    async def download_image(self, url: str, save_path: str):
        """下载图片到指定路径"""
        import aiohttp
        import aiofiles
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    async with aiofiles.open(save_path, "wb") as f:
                        async for chunk in response.content.iter_chunked(1024):
                            await f.write(chunk)
                else:
                    raise Exception(f"下载图片失败，HTTP状态码: {response.status}")
    
    async def convert_image_to_ascii(self, image_path: str, width: int = None, charset: str = "@#S%?*+;:,.") -> str:
        """将图片转换为ASCII艺术并保存为图片文件"""
        import asyncio
        loop = asyncio.get_event_loop()
        
        # 在线程池中运行CPU密集型任务
        result_path = await loop.run_in_executor(
            None, 
            self._sync_convert_image_to_ascii, 
            image_path,
            width,
            charset
        )
        
        return result_path
    
    async def convert_image_to_ascii_chinese(self, image_path: str, width: int = None, charset: str = None) -> str:
        """将图片转换为中文ASCII艺术并保存为图片文件"""
        import asyncio
        loop = asyncio.get_event_loop()
        
        if charset is None:
            charset = "爱你喜欢我他她它好美帅酷炫酷帅呆了棒赞优强牛厉害威武霸气萌萌哒赞赞赞顶顶顶神神神"
        
        # 在线程池中运行CPU密集型任务
        result_path = await loop.run_in_executor(
            None, 
            self._sync_convert_image_to_ascii_chinese, 
            image_path,
            width,
            charset
        )
        
        return result_path
    
    def _sync_convert_image_to_ascii(self, image_path: str, width: int = None, charset: str = "@#S%?*+;:,.") -> str:
        """同步版本的图片转ASCII艺术（英文字符）"""
        # 打开并处理图片
        img = Image.open(image_path)
        
        # 基于图片宽度自动确定合适的字符宽度
        if width is None:
            width = max(100, min(img.width // 6, 300))
        
        # 计算新尺寸保持宽高比
        aspect_ratio = img.height / img.width
        new_width = width
        new_height = int(aspect_ratio * width)
        
        # 调整图片大小并转换为灰度图
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        img = img.convert("L")  # 转换为灰度图
        
        # 生成ASCII艺术
        ascii_chars = list(charset)
        result = ""
        
        # 计算每个字符代表的灰度值范围
        step = 256 // len(ascii_chars)
        
        for i in range(new_height):
            for j in range(new_width):
                gray_value = img.getpixel((j, i))
                # 根据灰度值选择合适的字符
                char_index = min(int(gray_value / step), len(ascii_chars) - 1)
                result += ascii_chars[char_index]
            result += "\n"
        
        # 保存为图片文件
        output_filename = f"ascii_result_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.png"
        output_path = os.path.join(self.plugin_data_dir, output_filename)
        
        # 创建一个新的图像来绘制ASCII艺术
        font_size = 10
        font = None
        
        # 尝试使用支持中文的字体
        chinese_fonts = [
            "simhei.ttf", "simsun.ttc", "msyh.ttc", "arialuni.ttf",
            "SimHei", "SimSun", "Microsoft YaHei", "Arial Unicode MS"
        ]
        
        for font_name in chinese_fonts:
            try:
                font = ImageFont.truetype(font_name, font_size)
                break
            except:
                continue
        
        # 如果没有找到中文字体，则使用默认字体
        if font is None:
            try:
                # 尝试使用系统等宽字体
                font = ImageFont.truetype("consola.ttf", font_size)
            except:
                try:
                    font = ImageFont.truetype("cour.ttf", font_size)
                except:
                    try:
                        # 在Linux/Mac系统上尝试
                        font = ImageFont.truetype("DejaVuSansMono.ttf", font_size)
                    except:
                        try:
                            font = ImageFont.truetype("Arial.ttf", font_size)
                        except:
                            # 使用默认字体
                            font = ImageFont.load_default()
        
        # 准确测量字符尺寸
        try:
            # 对于较新的PIL版本
            bbox = font.getbbox("A")
            char_width = bbox[2] - bbox[0]
            char_height = bbox[3] - bbox[1]
        except:
            try:
                # 对于较老的PIL版本
                char_width, char_height = font.getsize("A")
            except:
                # 默认值
                char_width, char_height = 8, 12
        
        # 计算图像尺寸
        img_width = new_width * char_width
        img_height = new_height * char_height
        
        # 创建新图像，使用白色背景黑色文字更清晰
        ascii_img = Image.new("RGB", (img_width, img_height), color="white")
        draw = ImageDraw.Draw(ascii_img)
        
        # 绘制ASCII艺术
        lines = result.split("\n")
        for i, line in enumerate(lines):
            if line:  # 忽略空行
                # 使用黑色绘制文字
                draw.text((0, i * char_height), line, fill="black", font=font)
        
        # 保存图像
        ascii_img.save(output_path)
        return output_path
    
    def _sync_convert_image_to_ascii_chinese(self, image_path: str, width: int = None, charset: str = None) -> str:
        """同步版本的图片转ASCII艺术（中文字符）"""
        if charset is None:
            charset = "爱你喜欢我他她它好美帅酷炫酷帅呆了棒赞优强牛厉害威武霸气萌萌哒赞赞赞顶顶顶神神神"
        
        # 打开并处理图片
        img = Image.open(image_path)
        
        # 基于图片宽度自动确定合适的字符宽度
        if width is None:
            # 对于中文字符，使用较小的宽度以适应字符的复杂性
            width = max(50, min(img.width // 10, 150))
        
        # 计算新尺寸保持宽高比
        aspect_ratio = img.height / img.width
        new_width = width
        new_height = int(aspect_ratio * width)
        
        # 调整图片大小并转换为灰度图
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        img = img.convert("L")  # 转换为灰度图
        
        # 生成ASCII艺术
        chinese_chars = list(charset)
        result = ""
        
        # 计算每个字符代表的灰度值范围
        step = 256 // len(chinese_chars)
        
        for i in range(new_height):
            for j in range(new_width):
                gray_value = img.getpixel((j, i))
                # 根据灰度值选择合适的字符
                char_index = min(int(gray_value / step), len(chinese_chars) - 1)
                result += chinese_chars[char_index]
            result += "\n"
        
        # 保存为图片文件
        output_filename = f"ascii_result_chinese_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.png"
        output_path = os.path.join(self.plugin_data_dir, output_filename)
        
        # 创建一个新的图像来绘制ASCII艺术
        font_size = 12
        font = None
        
        # 尝试使用支持中文的字体
        chinese_fonts = [
            "simhei.ttf", "simsun.ttc", "msyh.ttc", "arialuni.ttf",
            "SimHei", "SimSun", "Microsoft YaHei", "Arial Unicode MS"
        ]
        
        for font_name in chinese_fonts:
            try:
                font = ImageFont.truetype(font_name, font_size)
                break
            except:
                continue
        
        # 如果没有找到中文字体，则使用默认字体
        if font is None:
            try:
                font = ImageFont.truetype("consola.ttf", font_size)
            except:
                try:
                    font = ImageFont.truetype("cour.ttf", font_size)
                except:
                    try:
                        font = ImageFont.truetype("DejaVuSansMono.ttf", font_size)
                    except:
                        try:
                            font = ImageFont.truetype("Arial.ttf", font_size)
                        except:
                            font = ImageFont.load_default()
        
        # 准确测量字符尺寸
        try:
            # 对于较新的PIL版本
            bbox = font.getbbox("中")
            char_width = bbox[2] - bbox[0]
            char_height = bbox[3] - bbox[1]
        except:
            try:
                # 对于较老的PIL版本
                char_width, char_height = font.getsize("中")
            except:
                # 默认值
                char_width, char_height = 12, 12
        
        # 计算图像尺寸
        img_width = new_width * char_width
        img_height = new_height * char_height
        
        # 创建新图像，使用白色背景黑色文字更清晰
        ascii_img = Image.new("RGB", (img_width, img_height), color="white")
        draw = ImageDraw.Draw(ascii_img)
        
        # 绘制ASCII艺术
        lines = result.split("\n")
        for i, line in enumerate(lines):
            if line:  # 忽略空行
                # 使用黑色绘制文字
                draw.text((0, i * char_height), line, fill="black", font=font)
        
        # 保存图像
        ascii_img.save(output_path)
        return output_path
    
    async def _start_cleanup_task(self):
        """启动定期清理缓存文件的后台任务"""
        try:
            await asyncio.sleep(5)  # 延迟5秒启动，等待插件完全初始化
            while True:
                await asyncio.sleep(self.cache_cleanup_interval * 60)  # 转换为秒
                await self._cleanup_old_cache_files()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[缓存清理] 后台任务出错: {e}")
    
    async def _cleanup_old_cache_files(self):
        """清理过期的缓存文件"""
        try:
            current_time = time.time()
            max_age_seconds = self.cache_max_age * 60  # 转换为秒
            deleted_count = 0
            
            if not os.path.exists(self.plugin_data_dir):
                return
            
            for filename in os.listdir(self.plugin_data_dir):
                file_path = os.path.join(self.plugin_data_dir, filename)
                
                # 仅处理文件，跳过目录
                if not os.path.isfile(file_path):
                    continue
                
                # 检查文件是否以 ascii_result 开头（即缓存文件）
                if not filename.startswith("ascii_result"):
                    continue
                
                try:
                    file_age = current_time - os.path.getmtime(file_path)
                    if file_age > max_age_seconds:
                        os.remove(file_path)
                        deleted_count += 1
                except Exception as e:
                    print(f"[缓存清理] 删除文件 {file_path} 时出错: {e}")
            
            if deleted_count > 0:
                print(f"[缓存清理] 清理了 {deleted_count} 个过期的缓存文件")
        except Exception as e:
            print(f"[缓存清理] 清理过程出错: {e}")
    
    def _parse_command_params(self, message_text: str) -> dict:
        """
        解析命令参数，支持以下格式：
        /ascii --width 150 --chinese --charset @#$
        /ascii c cabd              （c为charset_param，cabd为字符集值，不会被误解为其他参数）
        /ascii --width 150 --charset @#$
        
        参数解析规则：
        - 参数通过"参数名 参数值"的形式传递
        - 参数值被消费后不会被再次当作参数名扫描
        - 中文参数（如--chinese）是"开关"，无需参数值
        """
        params = {
            "width": self.default_width,
            "charset": self.default_charset,
            "use_chinese": False,
            "charset_specified": False  # 标记用户是否明确指定了字符集
        }
        
        # 分割消息为单个单词
        tokens = message_text.split()
        i = 0
        while i < len(tokens):
            token = tokens[i]
            
            # 检查宽度参数 - 需要参数值（数字）
            if token == self.width_param:
                if i + 1 < len(tokens):
                    try:
                        width_val = int(tokens[i + 1])
                        if self.min_width <= width_val <= self.max_width:
                            params["width"] = width_val
                    except ValueError:
                        pass
                    i += 2  # 跳过参数名和参数值，参数值不会被再次扫描
                    continue
            
            # 检查字符集参数 - 需要参数值（字符串）
            if token == self.charset_param:
                if i + 1 < len(tokens):
                    params["charset"] = tokens[i + 1]
                    params["charset_specified"] = True  # 标记用户明确指定了字符集
                    i += 2  # 跳过参数名和参数值，参数值不会被再次扫描
                    continue
            
            # 检查中文参数 - 仅为开关，无需参数值
            if token == self.chinese_param:
                params["use_chinese"] = True
                i += 1  # 仅跳过参数名
                continue
            
            i += 1
        return params

    @filter.command("ascii")
    async def ascii_command(self, event: AstrMessageEvent):
        """
        将引用的图片转换为ASCII艺术
                    if token == self.chinese_param:
        使用方法:
        1. 在QQ中引用一张图片并发送 "/ascii" 指令
        2. 支持自定义参数，具体参数名和别名可在配置面板中设置
        """
        # 获取消息文本（兼容 AstrBotMessage 无 text 属性，仅拼接 Plain 文本）
        message_text = ""
        for comp in getattr(event.message_obj, "message", []):
            if isinstance(comp, Plain):
                message_text += comp.text
        
        # 解析命令参数（使用配置中定义的参数名）
        params = self._parse_command_params(message_text)
        
        # 检查是否有引用消息并且包含图片
        reply_image = None
        for component in event.message_obj.message:
            # 检查直接发送的图片
            if isinstance(component, BotImage):
                reply_image = component
                break
            # 检查引用消息中的图片
            elif isinstance(component, Reply) and component.chain:
                for reply_component in component.chain:
                    if isinstance(reply_component, BotImage):
                        reply_image = reply_component
                        break
                if reply_image:
                    break
                
        if not reply_image:
            event.set_result(event.plain_result(self.help_message))
            return
            
        # 下载图片到临时文件
        temp_dir = tempfile.gettempdir()
        temp_filename = f"ascii_input_{uuid.uuid4().hex}.jpg"
        temp_path = os.path.join(temp_dir, temp_filename)
        
        try:
            # 从URL下载图片
            if reply_image.url:
                await self.download_image(reply_image.url, temp_path)
            elif reply_image.file:
                # 如果已经有本地路径，复制文件
                import shutil
                shutil.copy(reply_image.file, temp_path)
            else:
                event.set_result(event.plain_result("无法获取图片数据"))
                return
            
            # 转换为ASCII艺术
            if params["use_chinese"]:
                # 使用中文模式
                # 如果用户指定了字符集，使用用户指定的；否则使用默认中文字符集
                charset_to_use = params["charset"] if params["charset_specified"] else self.default_chinese_charset
                ascii_result_path = await self.convert_image_to_ascii_chinese(
                    temp_path, 
                    params["width"], 
                    charset_to_use
                )
            else:
                ascii_result_path = await self.convert_image_to_ascii(
                    temp_path, 
                    params["width"], 
                    params["charset"]
                )
            
            # 发送结果
            if ascii_result_path and os.path.exists(ascii_result_path):
                # 确保文件路径是绝对路径
                abs_path = os.path.abspath(ascii_result_path)
                result_image = BotImage.fromFileSystem(abs_path)
                chain = [
                    Plain("图片已转换为ASCII艺术:"),
                    result_image
                ]
                event.set_result(event.chain_result(chain))
            else:
                event.set_result(event.plain_result("转换失败，请稍后重试"))
                
        except Exception as e:
            import traceback
            traceback.print_exc()
            event.set_result(event.plain_result(f"处理过程中发生错误: {str(e)}"))
        finally:
            # 清理临时文件
            if os.path.exists(temp_path):
                os.remove(temp_path)
    
    async def download_image(self, url: str, save_path: str):
        """下载图片到指定路径"""
        import aiohttp
        import aiofiles
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    async with aiofiles.open(save_path, "wb") as f:
                        async for chunk in response.content.iter_chunked(1024):
                            await f.write(chunk)
                else:
                    raise Exception(f"下载图片失败，HTTP状态码: {response.status}")
    
    async def convert_image_to_ascii(self, image_path: str, width: int = None, charset: str = "@#S%?*+;:,.") -> str:
        """将图片转换为ASCII艺术并保存为图片文件"""
        import asyncio
        loop = asyncio.get_event_loop()
        
        # 在线程池中运行CPU密集型任务
        result_path = await loop.run_in_executor(
            None, 
            self._sync_convert_image_to_ascii, 
            image_path,
            width,
            charset
        )
        
        return result_path
    
    async def convert_image_to_ascii_chinese(self, image_path: str, width: int = None, charset: str = None) -> str:
        """将图片转换为中文ASCII艺术并保存为图片文件"""
        import asyncio
        loop = asyncio.get_event_loop()
        
        if charset is None:
            charset = "爱你喜欢我他她它好美帅酷炫酷帅呆了棒赞优强牛厉害威武霸气萌萌哒赞赞赞顶顶顶神神神"
        
        # 在线程池中运行CPU密集型任务
        result_path = await loop.run_in_executor(
            None, 
            self._sync_convert_image_to_ascii_chinese, 
            image_path,
            width,
            charset
        )
        
        return result_path
    
    def _sync_convert_image_to_ascii(self, image_path: str, width: int = None, charset: str = "@#S%?*+;:,.") -> str:
        """同步版本的图片转ASCII艺术（英文字符）"""
        # 打开并处理图片
        img = Image.open(image_path)
        
        # 基于图片宽度自动确定合适的字符宽度
        if width is None:
            width = max(100, min(img.width // 6, 300))
        
        # 计算新尺寸保持宽高比
        aspect_ratio = img.height / img.width
        new_width = width
        new_height = int(aspect_ratio * width)
        
        # 调整图片大小并转换为灰度图
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        img = img.convert("L")  # 转换为灰度图
        
        # 生成ASCII艺术
        ascii_chars = list(charset)
        result = ""
        
        # 计算每个字符代表的灰度值范围
        step = 256 // len(ascii_chars)
        
        for i in range(new_height):
            for j in range(new_width):
                gray_value = img.getpixel((j, i))
                # 根据灰度值选择合适的字符
                char_index = min(int(gray_value / step), len(ascii_chars) - 1)
                result += ascii_chars[char_index]
            result += "\n"
        
        # 保存为图片文件
        output_filename = f"ascii_result_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.png"
        output_path = os.path.join(self.plugin_data_dir, output_filename)
        
        # 创建一个新的图像来绘制ASCII艺术
        font_size = 10
        font = None
        
        # 尝试使用支持中文的字体
        chinese_fonts = [
            "simhei.ttf", "simsun.ttc", "msyh.ttc", "arialuni.ttf",
            "SimHei", "SimSun", "Microsoft YaHei", "Arial Unicode MS"
        ]
        
        for font_name in chinese_fonts:
            try:
                font = ImageFont.truetype(font_name, font_size)
                break
            except:
                continue
        
        # 如果没有找到中文字体，则使用默认字体
        if font is None:
            try:
                # 尝试使用系统等宽字体
                font = ImageFont.truetype("consola.ttf", font_size)
            except:
                try:
                    font = ImageFont.truetype("cour.ttf", font_size)
                except:
                    try:
                        # 在Linux/Mac系统上尝试
                        font = ImageFont.truetype("DejaVuSansMono.ttf", font_size)
                    except:
                        try:
                            font = ImageFont.truetype("Arial.ttf", font_size)
                        except:
                            # 使用默认字体
                            font = ImageFont.load_default()
        
        # 准确测量字符尺寸
        try:
            # 对于较新的PIL版本
            bbox = font.getbbox("A")
            char_width = bbox[2] - bbox[0]
            char_height = bbox[3] - bbox[1]
        except:
            try:
                # 对于较老的PIL版本
                char_width, char_height = font.getsize("A")
            except:
                # 默认值
                char_width, char_height = 8, 12
        
        # 计算图像尺寸
        img_width = new_width * char_width
        img_height = new_height * char_height
        
        # 创建新图像，使用白色背景黑色文字更清晰
        ascii_img = Image.new("RGB", (img_width, img_height), color="white")
        draw = ImageDraw.Draw(ascii_img)
        
        # 绘制ASCII艺术
        lines = result.split("\n")
        for i, line in enumerate(lines):
            if line:  # 忽略空行
                # 使用黑色绘制文字
                draw.text((0, i * char_height), line, fill="black", font=font)
        
        # 保存图像
        ascii_img.save(output_path)
        return output_path
    
    def _sync_convert_image_to_ascii_chinese(self, image_path: str, width: int = None, charset: str = None) -> str:
        """同步版本的图片转ASCII艺术（中文字符）"""
        if charset is None:
            charset = "爱你喜欢我他她它好美帅酷炫酷帅呆了棒赞优强牛厉害威武霸气萌萌哒赞赞赞顶顶顶神神神"
        
        # 打开并处理图片
        img = Image.open(image_path)
        
        # 基于图片宽度自动确定合适的字符宽度
        if width is None:
            # 对于中文字符，使用较小的宽度以适应字符的复杂性
            width = max(50, min(img.width // 10, 150))
        
        # 计算新尺寸保持宽高比
        aspect_ratio = img.height / img.width
        new_width = width
        new_height = int(aspect_ratio * width)
        
        # 调整图片大小并转换为灰度图
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        img = img.convert("L")  # 转换为灰度图
        
        # 生成ASCII艺术
        chinese_chars = list(charset)
        result = ""
        
        # 计算每个字符代表的灰度值范围
        step = 256 // len(chinese_chars)
        
        for i in range(new_height):
            for j in range(new_width):
                gray_value = img.getpixel((j, i))
                # 根据灰度值选择合适的字符
                char_index = min(int(gray_value / step), len(chinese_chars) - 1)
                result += chinese_chars[char_index]
            result += "\n"
        
        # 保存为图片文件
        output_filename = f"ascii_result_chinese_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.png"
        output_path = os.path.join(self.plugin_data_dir, output_filename)
        
        # 创建一个新的图像来绘制ASCII艺术
        font_size = 12
        font = None
        
        # 尝试使用支持中文的字体
        chinese_fonts = [
            "simhei.ttf", "simsun.ttc", "msyh.ttc", "arialuni.ttf",
            "SimHei", "SimSun", "Microsoft YaHei", "Arial Unicode MS"
        ]
        
        for font_name in chinese_fonts:
            try:
                font = ImageFont.truetype(font_name, font_size)
                break
            except:
                continue
        
        # 如果没有找到中文字体，则使用默认字体
        if font is None:
            try:
                font = ImageFont.truetype("consola.ttf", font_size)
            except:
                try:
                    font = ImageFont.truetype("cour.ttf", font_size)
                except:
                    try:
                        font = ImageFont.truetype("DejaVuSansMono.ttf", font_size)
                    except:
                        try:
                            font = ImageFont.truetype("Arial.ttf", font_size)
                        except:
                            font = ImageFont.load_default()
        
        # 准确测量字符尺寸
        try:
            # 对于较新的PIL版本
            bbox = font.getbbox("中")
            char_width = bbox[2] - bbox[0]
            char_height = bbox[3] - bbox[1]
        except:
            try:
                # 对于较老的PIL版本
                char_width, char_height = font.getsize("中")
            except:
                # 默认值
                char_width, char_height = 12, 12
        
        # 计算图像尺寸
        img_width = new_width * char_width
        img_height = new_height * char_height
        
        # 创建新图像，使用白色背景黑色文字更清晰
        ascii_img = Image.new("RGB", (img_width, img_height), color="white")
        draw = ImageDraw.Draw(ascii_img)
        
        # 绘制ASCII艺术
        lines = result.split("\n")
        for i, line in enumerate(lines):
            if line:  # 忽略空行
                # 使用黑色绘制文字
                draw.text((0, i * char_height), line, fill="black", font=font)
        
        # 保存图像
        ascii_img.save(output_path)
        return output_path
    
    async def _start_cleanup_task(self):
        """启动定期清理缓存文件的后台任务"""
        try:
            await asyncio.sleep(5)  # 延迟5秒启动，等待插件完全初始化
            while True:
                await asyncio.sleep(self.cache_cleanup_interval * 60)  # 转换为秒
                await self._cleanup_old_cache_files()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[缓存清理] 后台任务出错: {e}")
    
    async def _cleanup_old_cache_files(self):
        """清理过期的缓存文件"""
        try:
            current_time = time.time()
            max_age_seconds = self.cache_max_age * 60  # 转换为秒
            deleted_count = 0
            
            if not os.path.exists(self.plugin_data_dir):
                return
            
            for filename in os.listdir(self.plugin_data_dir):
                file_path = os.path.join(self.plugin_data_dir, filename)
                
                # 仅处理文件，跳过目录
                if not os.path.isfile(file_path):
                    continue
                
                # 检查文件是否以 ascii_result 开头（即缓存文件）
                if not filename.startswith("ascii_result"):
                    continue
                
                try:
                    file_age = current_time - os.path.getmtime(file_path)
                    if file_age > max_age_seconds:
                        os.remove(file_path)
                        deleted_count += 1
                except Exception as e:
                    print(f"[缓存清理] 删除文件 {file_path} 时出错: {e}")
            
            if deleted_count > 0:
                print(f"[缓存清理] 清理了 {deleted_count} 个过期的缓存文件")
        except Exception as e:
            print(f"[缓存清理] 清理过程出错: {e}")