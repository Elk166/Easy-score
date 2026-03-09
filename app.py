import os
import fitz
import uuid
import xml.etree.ElementTree as ET
import tempfile
import shutil
from flask import Flask, request, render_template, send_from_directory
from werkzeug.utils import secure_filename

# 初始化Flask应用
app = Flask(__name__)

# 检测是否在Vercel环境
IS_VERCEL = os.environ.get('VERCEL', False)

# 配置文件夹路径 - Vercel使用临时目录
if IS_VERCEL:
    # Vercel环境使用/tmp目录
    BASE_DIR = '/tmp'
else:
    # 本地环境使用当前目录
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'uploads')
app.config['OUTPUT_FOLDER'] = os.path.join(BASE_DIR, 'output')
app.config['TEMP_IMAGES'] = os.path.join(BASE_DIR, 'temp_images')

# 自动创建文件夹（不存在则创建）
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)
os.makedirs(app.config['TEMP_IMAGES'], exist_ok=True)
# 支持的文件格式
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg'}

def allowed_file(filename):
    """校验文件格式是否合法"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def pdf_to_image(pdf_path):
    """
    PDF多页转高清图片，添加详细日志打印
    :param pdf_path: 上传的PDF文件绝对路径
    :return: 按页码排序的图片路径列表
    """
    try:
        doc = fitz.open(pdf_path)
        page_count = doc.page_count
        img_paths = []
        print(f"\n【PDF转图日志】开始解析PDF文件：{pdf_path}，总页数：{page_count}")
        # 优化转图矩阵：1.5倍（兼顾清晰度和oemer识别率）
        matrix = fitz.Matrix(1.5, 1.5)
        for page_num in range(page_count):
            page = doc[page_num]
            pix = page.get_pixmap(matrix=matrix, alpha=False)  # 关闭透明通道，提升识别率
            # 生成唯一临时文件名，避免冲突
            temp_img_name = f"page_{uuid.uuid4().hex[:8]}_{page_num}.png"
            img_path = os.path.join(app.config['TEMP_IMAGES'], temp_img_name)
            pix.save(img_path)
            img_paths.append(img_path)
            print(f"【PDF转图日志】第{page_num+1}页 → 生成图片：{img_path}")
        doc.close()
        print(f"【PDF转图成功】共生成{len(img_paths)}张图片（预期{page_count}张）")
        return img_paths
    except Exception as e:
        print(f"【PDF转图失败】异常信息：{str(e)}")
        return []

def oemer_to_xml(image_paths, output_dir):
    """
    多图片批量生成单页MusicXML，添加详细日志打印
    :param image_paths: 图片路径列表（按页码排序）
    :param output_dir: 单页XML输出目录
    :return: 按页码排序的单页MusicXML路径列表
    """
    if not image_paths:
        print(f"\n【oemer生成失败】图片路径列表为空，无法生成XML")
        return []
    single_xml_paths = []
    print(f"\n【oemer生成日志】开始光学识别，图片总数：{len(image_paths)}，输出目录：{output_dir}")
    
    # 尝试导入oemer Python API
    try:
        from oemer import ete
        USE_API = True
        print("【oemer生成日志】使用Python API模式")
    except ImportError:
        USE_API = False
        print("【oemer生成日志】使用命令行模式")
    
    for img_idx, img_path in enumerate(image_paths):
        # 生成唯一单页XML文件名
        temp_xml_name = f"single_{uuid.uuid4().hex[:8]}_{img_idx}.musicxml"
        single_xml_path = os.path.join(output_dir, temp_xml_name)
        
        try:
            if USE_API:
                # 使用Python API调用oemer
                import argparse
                args = argparse.Namespace(
                    img_path=img_path,
                    output=single_xml_path,
                    use_tf=False
                )
                ete.main(args)
            else:
                # 调用oemer工具生成XML（命令行模式）
                os.system(f'oemer "{img_path}" --output "{single_xml_path}"')
            
            # 校验XML是否生成成功（非空文件）
            if os.path.exists(single_xml_path):
                file_size = os.path.getsize(single_xml_path)
                if file_size > 0:
                    single_xml_paths.append(single_xml_path)
                    print(f"【oemer生成日志】第{img_idx+1}张图 → 生成XML：{single_xml_path}（文件大小：{file_size}字节）")
                else:
                    print(f"【oemer生成警告】第{img_idx+1}张图 → XML生成为空：{single_xml_path}（oemer未识别到乐谱）")
                    os.remove(single_xml_path)  # 删除空文件
            else:
                print(f"【oemer生成失败】第{img_idx+1}张图 → XML生成失败：{img_path}（oemer调用异常）")
        except Exception as e:
            print(f"【oemer生成异常】第{img_idx+1}张图 → 异常：{str(e)}")
            continue
            
    print(f"【oemer生成结果】共成功生成{len(single_xml_paths)}个有效单页XML（预期{len(image_paths)}个）")
    return single_xml_paths

def merge_musicxml(single_xml_paths, final_xml_path):
    """
    修复命名空间解析缺陷+添加日志+兜底节点定位
    合并多页单页MusicXML为一个标准MusicXML文件（遵循MusicXML 3.0标准）
    :param single_xml_paths: 单页XML路径列表（按页码排序）
    :param final_xml_path: 最终合并后的XML文件保存路径
    :return: 成功返回最终路径，失败返回None
    """
    if not single_xml_paths:
        print(f"\n【XML合并失败】单页XML路径列表为空，无法合并")
        return None

    try:
        # 解析第一页XML作为基础模板
        first_tree = ET.parse(single_xml_paths[0])
        first_root = first_tree.getroot()
        # 提取命名空间（兼容带/不带命名空间的MusicXML）
        ns = {}
        if first_root.tag.startswith('{'):
            ns_uri = first_root.tag.split('{')[1].split('}')[0]
            ns['ns'] = ns_uri
        print(f"\n【XML合并日志】开始合并{len(single_xml_paths)}个单页XML，提取命名空间：{ns}")
        print(f"【XML合并日志】基础模板：{single_xml_paths[0]}")

        # 定位核心乐谱节点score-partwise（三层兜底，确保能找到）
        score_partwise = None
        # 兜底1：带命名空间查询
        if ns:
            score_partwise = first_root.find('.//ns:score-partwise', ns)
        # 兜底2：不带命名空间查询
        if not score_partwise:
            score_partwise = first_root.find('.//score-partwise')
        # 兜底3：遍历所有节点，匹配后缀为score-partwise的节点
        if not score_partwise:
            for elem in first_root.iter():
                if elem.tag.endswith('score-partwise'):
                    score_partwise = elem
                    break
        # 最终校验：未找到核心节点则返回失败
        if not score_partwise:
            print(f"【XML合并失败】未找到MusicXML核心节点：score-partwise")
            return None
        print(f"【XML合并日志】成功定位score-partwise核心节点")

        # 定位part节点（三层兜底，确保能找到）
        part_node = None
        # 兜底1：带命名空间查询
        if ns:
            part_node = score_partwise.find('.//ns:part', ns)
        # 兜底2：不带命名空间查询
        if not part_node:
            part_node = score_partwise.find('.//part')
        # 兜底3：遍历所有节点，匹配后缀为part的节点
        if not part_node:
            for elem in score_partwise.iter():
                if elem.tag.endswith('part'):
                    part_node = elem
                    break
        # 最终校验：未找到part节点则返回失败
        if not part_node:
            print(f"【XML合并失败】未找到MusicXML核心节点：part")
            return None
        print(f"【XML合并日志】成功定位part核心节点，开始追加后续页面小节")

        # 遍历剩余单页XML，提取measure小节并追加
        total_append_measure = 0  # 统计总追加小节数
        for page_idx, xml_path in enumerate(single_xml_paths[1:], 2):
            sub_tree = ET.parse(xml_path)
            sub_root = sub_tree.getroot()
            # 提取当前页的measure小节（三层兜底）
            sub_measures = []
            # 兜底1：带命名空间查询
            if ns:
                sub_measures = sub_root.findall('.//ns:measure', ns)
            # 兜底2：不带命名空间查询
            if not sub_measures:
                sub_measures = sub_root.findall('.//measure')
            # 兜底3：遍历所有节点，匹配后缀为measure的节点
            if not sub_measures:
                for elem in sub_root.iter():
                    if elem.tag.endswith('measure'):
                        sub_measures.append(elem)
            # 追加小节到主节点
            if sub_measures:
                # 在新页面的第一个小节前添加页面分隔信息
                # 创建print元素，设置new-page="yes"
                if ns:
                    print_elem = ET.Element(f"{{{ns['ns']}}}print")
                else:
                    print_elem = ET.Element("print")
                print_elem.set("new-page", "yes")
                part_node.append(print_elem)
                print(f"【XML合并日志】第{page_idx}页 → 添加页面分隔信息")
                
                for measure in sub_measures:
                    part_node.append(measure)
                total_append_measure += len(sub_measures)
                print(f"【XML合并日志】第{page_idx}页 → 成功追加{len(sub_measures)}个小节")
            else:
                print(f"【XML合并警告】第{page_idx}页 → 未找到measure小节节点，跳过该页")
            # 清理临时对象，释放内存
            del sub_tree, sub_root

        # 保存合并后的最终MusicXML文件（UTF-8编码，避免乱码）
        with open(final_xml_path, 'wb') as f:
            # 手动写入XML声明，确保标准格式
            f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
            first_tree.write(f, encoding='utf-8', xml_declaration=False)
        # 校验最终文件
        if os.path.exists(final_xml_path) and os.path.getsize(final_xml_path) > 0:
            print(f"【XML合并成功】最终文件保存至：{final_xml_path}")
            print(f"【XML合并统计】共追加{total_append_measure}个小节，合并完成！")
            return final_xml_path
        else:
            print(f"【XML合并失败】最终生成的文件为空：{final_xml_path}")
            return None
    except Exception as e:
        print(f"【XML合并异常】合并失败，异常信息：{str(e)}")
        return None

@app.route('/', methods=['GET', 'POST'])
def index():
    """首页：文件上传、转换、结果返回（带全环节异常捕获）"""
    result = None
    if request.method == 'POST':
        # 校验是否上传文件
        if 'file' not in request.files:
            return render_template('index.html', result="未上传文件，请选择文件后再提交")
        file = request.files['file']
        # 校验文件格式
        if file.filename == '' or not allowed_file(file.filename):
            return render_template('index.html', result="格式不支持，仅支持 PDF / PNG / JPG / JPEG 格式")
        
        try:
            # 安全处理文件名，保存上传文件
            filename = secure_filename(file.filename)
            upload_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(upload_path)
            file_ext = filename.rsplit('.', 1)[1].lower()
            single_xml_paths = []
            img_paths = []
            print(f"\n======================================")
            print(f"【新任务】开始处理文件：{filename}（格式：{file_ext}）")
            print(f"======================================")

            # 区分PDF和图片文件处理逻辑
            if file_ext == 'pdf':
                # PDF文件：多页转图→生成单页XML
                img_paths = pdf_to_image(upload_path)
                if not img_paths:
                    return render_template('index.html', result="PDF转图失败，请检查文件是否损坏")
                single_xml_paths = oemer_to_xml(img_paths, app.config['OUTPUT_FOLDER'])
            else:
                # 图片文件：直接生成单页XML（向下兼容原逻辑）
                single_xml_paths = oemer_to_xml([upload_path], app.config['OUTPUT_FOLDER'])
            
            # 校验单页XML是否生成成功
            if not single_xml_paths:
                return render_template('index.html', result="oemer识别失败，未生成任何MusicXML（请检查乐谱是否清晰）")

            # 合并所有单页XML为一个最终文件
            xml_filename = f"{filename}.musicxml"
            final_xml_path = os.path.join(app.config['OUTPUT_FOLDER'], xml_filename)
            merge_result = merge_musicxml(single_xml_paths, final_xml_path)
            
            # 校验合并结果
            if not merge_result or not os.path.exists(final_xml_path):
                return render_template('index.html', result="MusicXML文件合并失败，请检查乐谱文件是否清晰")

            # 清理临时文件（单页XML、PDF转的临时图片），避免占满磁盘
            print(f"\n【清理日志】开始清理临时文件...")
            temp_del_count = 0
            for xml_path in single_xml_paths:
                if os.path.exists(xml_path) and xml_path != final_xml_path:
                    os.remove(xml_path)
                    temp_del_count += 1
            for img_path in img_paths:
                if os.path.exists(img_path):
                    os.remove(img_path)
                    temp_del_count += 1
            print(f"【清理日志】共清理{temp_del_count}个临时文件")

            # 转换成功，返回结果
            result = {
                "success": True,
                "filename": filename,
                "xml_filename": xml_filename
            }
            print(f"\n【任务完成】文件{filename}转换成功，最终XML：{xml_filename}")
        except Exception as e:
            # 捕获所有异常，返回具体错误信息
            error_msg = f"转换失败：{str(e)[:60]}，请检查文件是否损坏/清晰"
            print(f"\n【任务失败】{error_msg}")
            return render_template('index.html', result=error_msg)
        return render_template('index.html', result=result)
    # GET请求：返回首页
    return render_template('index.html', result=None)

@app.route('/download/<filename>')
def download(filename):
    """文件下载接口：返回生成的MusicXML文件"""
    return send_from_directory(app.config['OUTPUT_FOLDER'], filename, as_attachment=True)

# Vercel需要这个handler
if __name__ == '__main__':
    # 本地开发模式
    app.run(host='127.0.0.1', port=5000, debug=True)
else:
    # Vercel生产环境
    # 确保目录存在
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)
    os.makedirs(app.config['TEMP_IMAGES'], exist_ok=True)