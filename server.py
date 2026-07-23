# -*- coding: utf-8 -*-
"""
论文库爬虫后端 server.py
给前端论文库(index.html)提供 /crawl 接口：
  输入一个论文页面 URL → 找 PDF → 下载到 pdfs/ → 解析标题/摘要 → 返回元数据

运行： python server.py   （起在 http://localhost:5000）
然后去前端点"🕷 爬取论文"按钮。
"""
import io
import os
import re
import time
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import pdfplumber

app = Flask(__name__)
CORS(app)  # 允许前端(file:// 或别的端口)跨域调用

# PDF 存放目录（和 index.html 同级的 pdfs/ 文件夹）
PDF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pdfs')
os.makedirs(PDF_DIR, exist_ok=True)

# 伪装成浏览器，避免一些网站拒绝
UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}


def find_pdf_url(url, soup):
    """从页面里找出 PDF 的下载地址"""
    # 1. arxiv abs 页面特殊处理：/abs/xxx → /pdf/xxx.pdf
    if 'arxiv.org/abs/' in url:
        return url.replace('/abs/', '/pdf/') + '.pdf'
    # 2. 找页面里第一个 .pdf 链接
    for a in soup.find_all('a', href=True):
        h = a['href']
        if h.lower().endswith('.pdf'):
            if h.startswith('http'):
                return h
            if h.startswith('/'):
                return url.rstrip('/') + h
            return url.rsplit('/', 1)[0] + '/' + h
    # 3. 用户直接给的就是 PDF 链接
    if url.lower().endswith('.pdf'):
        return url
    return None


def parse_pdf(pdf_bytes):
    """用 pdfplumber 提取前 3 页文本，从中正则匹配 Abstract"""
    text = ''
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages[:3]:
            text += (page.extract_text() or '') + '\n'
    # 优先：Abstract 到常见终止符（Keywords/Introduction/Comments/Subjects 等）
    m = re.search(
        r'Abstract[::\s]*([\s\S]{30,1500}?)'
        r'\n\s*(?:Keywords|Index Terms|1\.?\s*Introduction|CCS|Categories|Comments|Subjects|Cite as|摘要)',
        text, re.I)
    if m:
        return m.group(1).strip()[:600]
    # 兜底：Abstract 后直接取前 600 字
    m2 = re.search(r'Abstract[:\s]*([\s\S]{30,600})', text, re.I)
    if m2:
        return m2.group(1).strip()[:600]
    return ''


@app.post('/crawl')
def crawl():
    data = request.get_json() or {}
    url = (data.get('url') or '').strip()
    if not url:
        return jsonify({'error': '请提供 url'}), 400
    try:
        # ① 抓页面
        resp = requests.get(url, headers=UA, timeout=20)
        soup = BeautifulSoup(resp.text, 'html.parser')

        # ② 找 PDF
        pdf_url = find_pdf_url(url, soup)
        if not pdf_url:
            return jsonify({'error': '页面没找到 PDF 链接'}), 404

        # ③ 下载 PDF
        pr = requests.get(pdf_url, headers=UA, timeout=60)
        pdf_bytes = pr.content

        # ④ 存到本地 pdfs/
        safe = re.sub(r'[^\w\-]', '_', url.split('//')[-1].split('/')[-1])[:40] or 'paper'
        filename = f"{int(time.time())}_{safe}.pdf"
        with open(os.path.join(PDF_DIR, filename), 'wb') as f:
            f.write(pdf_bytes)

        # ⑤ 解析元数据：摘要优先从 HTML 页面取（干净），取不到再解析 PDF
        abstract = ''
        for sel in [('meta', {'name': 'citation_abstract'}),
                    ('meta', {'property': 'og:description'})]:
            tag = soup.find(*sel)
            if tag and tag.get('content'):
                abstract = re.sub(r'^Abstract[:\s]*', '', tag['content'].strip(), flags=re.I)
                break
        if not abstract:
            bq = soup.find('blockquote', class_='abstract')
            if bq:
                abstract = bq.get_text(' ', strip=True).replace('Abstract:', '', 1).strip()
        if not abstract:
            abstract = parse_pdf(pdf_bytes)
        title = (soup.title.string.strip() if soup.title and soup.title.string
                 else url.split('/')[-1])
        title = re.sub(r'\s*[-|·]\s*(arXiv|IEEE XPLORER|ScienceDirect).*$', '',
                       title, flags=re.I)[:200]

        return jsonify({
            'title': title,
            'abs': abstract or '(未自动提取到摘要，可手动补)',
            'src': url,
            'pdf_file': 'pdfs/' + filename
        })
    except Exception as e:
        return jsonify({'error': f'爬取失败: {e}'}), 500


@app.get('/pdf/<path:f>')
def get_pdf(f):
    """让前端能通过 /pdf/文件名 在线看 PDF"""
    return send_from_directory(PDF_DIR, f)


if __name__ == '__main__':
    print('=' * 50)
    print('爬虫后端已启动: http://localhost:5000')
    print('现在去打开 index.html，点"🕷 爬取论文"按钮')
    print('=' * 50)
    app.run(port=5000, debug=True)
