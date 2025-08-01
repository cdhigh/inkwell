#!/usr/bin/env python3
# -*- coding:utf-8 -*-
#Author: cdhigh <https://github.com/cdhigh>
"""运行于终端的Ai助手，主要为Kindle设计，也适用于其他系统的终端
1. Python >= 3.8
2. 单文件设计，不依赖任何第三方库
3. 支持多个api key自动轮换
4. 支持多个api服务器自动轮换
5. 支持终端显示格式化后的markdown文本
6. 支持对读书摘要笔记(My Clippings)进行AI总结和提问学习
7. 支持将会话历史导出为格式良好的电子书或发送至邮件
用法：
1. 使用命令行参数 -s 或 --setup 开始交互式的初始化和配置
2. 不带参数执行，自动使用同一目录下的配置文件 config.json，如果没有则自动新建一个默认模板
3. 如果需要不同的配置，可以传入参数 python inkwell.py --config path/to/config.json
4. 在kindle上使用时可以在kterm的menu.json里面添加一个或多个项目，action值为：
bin/kterm.sh -e 'python3 /mnt/us/extensions/kterm/ai/inkwell.py --config /mnt/us/extensions/kterm/ai/google.json
5. 如果需要自动开关wifi，可以在kterm.sh的 `${EXTENSION}/bin/kterm ${PARAM} "$@"` 行前后添加
lipc-set-prop com.lab126.cmd wirelessEnable 1
lipc-set-prop com.lab126.cmd wirelessEnable 0
"""
import os, sys, re, json, ssl, argparse
import http.client
from urllib.parse import urlsplit

__Version__ = 'v1.6.2 (2025-08-01)'
BASE_PATH = os.path.dirname(os.path.abspath(__file__))
CONFIG_JSON = f"{BASE_PATH}/config.json"
HISTORY_JSON = "history.json" #历史文件会自动跟随程序传入的配置文件路径
PROMPTS_FILE = f"{BASE_PATH}/prompts.txt"
KINDLE_DOC_DIR = '/mnt/us/documents'
CLIPPINGS_FILE = os.path.join(KINDLE_DOC_DIR, 'My Clippings.txt')
if not os.path.isfile(CLIPPINGS_FILE) and os.path.isfile(os.path.join(BASE_PATH, 'My Clippings.txt')):
    CLIPPINGS_FILE = os.path.join(BASE_PATH, 'My Clippings.txt')

#默认的AI角色配置
DEFAULT_PROMPT = """You are a helpful assistant.
- You are to provide clear, concise, and direct responses.
- Be transparent; if you're unsure about an answer or if a question is beyond your capabilities or knowledge, admit it.
- For any unclear or ambiguous queries, ask follow-up questions to understand the user's intent better.
- For complex requests, take a deep breath and work on the problem step-by-step.
- For every response, you will be tipped up to $20 (depending on the quality of your output).
- Keep responses concise and formatted for terminal output.
- Use Markdown for formatting."""

#让AI总结此次谈话主题的prompt
PROMPT_GET_TOPIC = """Please give this conversation a short title.
- Hard limit of 5 words.
- Don't mention yourself in it.
- Don't use any special characters.
- Don't use any capital letters.
- Don't use any punctuation.
- Don't use any symbols.
- Don't use any emojis.
- Don't use any accents.
- Don't use quotes."""

#发送读书笔记的prompt
CLIPS_PROMPT = """I have a few excerpts from my readings. Please analyze them. I may have follow-up questions based on them.
Clippings:
{clips}
{question}
"""

#终端的颜色代码表
_TERMINAL_COLORS = {"black": 30, "red": 31, "green": 32, "yellow": 33, "blue": 34, "magenta": 35,
    "cyan": 36, "white": 37, "reset": 39, "bright_black": 90, "bright_red": 91, "bright_green": 92,
    "bright_yellow": 93, "bright_blue": 94, "bright_magenta": 95, "bright_cyan": 96, "bright_white": 97,
    "orange": (255,165,0), "grey": 90}

DEFAULT_TOPIC = 'new conversation'
DEFAULT_CFG = {"provider": "", "model": "", "api_key": "", "api_host": "", 
    "display_style": "markdown", "chat_type": "multi_turn", "token_limit": 4000, "max_history": 10, 
    "prompt": "default", "custom_prompt": "", "smtp_sender": "", "smtp_host": "", "smtp_username": "",
    "smtp_password": "", "renew_api_key": ""}

#AI响应的结构封装
class AiResponse:
    def __init__(self, success, content='', error='', host=''):
        self.success = success
        self.content = content
        self.error = error
        self.host = host

#翻译颜色代码为终端转义字符串
#color: 支持 列表[R, G, B]/字符串"red"
#offset: =0 设置前景色，=10 设置背景色
def interpretColor(color, offset=0):
    if isinstance(color, int):
        return f"{38 + offset};5;{code:d}"
    code = _TERMINAL_COLORS.get(color, 30) if isinstance(color, str) else color
    if isinstance(code, (tuple, list)): #RGB
        return f"{38 + offset};2;{code[0]:d};{code[1]:d};{code[2]:d}"
    else:
        return str(code + offset)

#返回着色格式化后的字符串，用于终端显示字体
def style(text, fg=None, bg=None, bold=None, dim=None, underline=None, overline=None,
    italic=None, blink=None, reverse=None, strikethrough=None, reset=True):
    parts = []
    if fg:
        parts.append(f"\033[{interpretColor(fg)}m")
    if bg:
        parts.append(f"\033[{interpretColor(bg, 10)}m")
    if bold is not None:
        parts.append(f"\033[{1 if bold else 22}m")
    if dim is not None:
        parts.append(f"\033[{2 if dim else 22}m")
    if underline is not None:
        parts.append(f"\033[{4 if underline else 24}m")
    if overline is not None:
        parts.append(f"\033[{53 if overline else 55}m")
    if italic is not None:
        parts.append(f"\033[{3 if italic else 23}m")
    if blink is not None:
        parts.append(f"\033[{5 if blink else 25}m")
    if reverse is not None:
        parts.append(f"\033[{7 if reverse else 27}m")
    if strikethrough is not None:
        parts.append(f"\033[{9 if strikethrough else 29}m")
    parts.append(text)
    if reset:
        parts.append("\033[0m")
    return "".join(parts)

#向终端输出带颜色的字符串
def sprint(txt, **kwargs):
    print(style(txt, **kwargs))

#字符串转整数，出错则返回default
def str_to_int(txt, default=0):
    try:
        return int(txt)
    except:
        return default

#主类
class InkWell:
    def __init__(self, cfgFile):
        self.cfgFile = cfgFile or CONFIG_JSON
        self.currTopic = ''
        self.prompts = {}
        self.currPrompt = ''
        self.history = []
        self.messages = [{"role": "system", "content": ''}] #role: system, user, assistant
        self.config = self.loadConfig()
        
    #获取配置数据，这个函数返回的配置字典是经过校验的，里面的数据都是合法的
    def loadConfig(self):
        cfg = {}
        if not os.path.isfile(self.cfgFile):
            print('\n')
            sprint(f'The file {self.cfgFile} does not exist', bold=True)
            sprint('Creating a default configuration file with this name...', bold=True)
            print('Edit the file manually or run with the -s option to complete the setup')
            print('')
            try:
                with open(self.cfgFile, 'w', encoding='utf-8') as f:
                    json.dump(DEFAULT_CFG, f, indent=2)
            except Exception as e:
                print(f'Failed to write {self.cfgFile}: {e}')
            input('Press return key to quit ')
            return None

        if os.path.isfile(self.cfgFile):
            with open(self.cfgFile, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
        if not isinstance(cfg, dict):
            cfg = {}

        #校验和设置几个全局变量
        provider = cfg.get('provider', '').lower()
        if provider not in AI_LIST:
            provider = 'google'
            cfg['provider'] = provider
        if not cfg.get('model'):
            cfg['model'] = next(item['name'] for item in AI_LIST[provider]['models'])
        if cfg.get("token_limit", 4000) < 1000:
            cfg['token_limit'] = 1000
        displayStyle = cfg.get('display_style')
        if displayStyle not in ('plaintext', 'markdown', 'markdown_table'):
            displayStyle = 'markdown'
        cfg['display_style'] = displayStyle
        return cfg

    #将配置保存到配置文件
    def saveConfig(self, cfg):
        try:
            with open(self.cfgFile, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print('Failed to write {}: {}\n'.format(style(self.cfgFile, bold=True), str(e)))
        else:
            print('Config have been saved to file: {}\n'.format(style(self.cfgFile, bold=True)))

    #加载预置的prompt列表
    def loadPrompts(self):
        if self.prompts or not os.path.isfile(PROMPTS_FILE):
            return self.prompts

        self.prompts = {}
        try:
            with open(PROMPTS_FILE, 'r', encoding='utf-8') as f:
                entries = [entry.partition('\n') for e in f.read().split('</>') if (entry := e.strip())]
                for name, _, content in entries:
                    name = name.strip()
                    content = content.strip()
                    if name and content:
                        self.prompts[name] = content
        except Exception as e:
            print(f'Failed to read {style(PROMPTS_FILE, bold=True)}: {e}')
        return self.prompts

    #加载历史对话信息，返回历史列表
    def loadHistory(self):
        if self.config.get('max_history', 10) <= 0: #禁用了历史对话功能
            return []

        hisPath = os.path.dirname(self.cfgFile)
        hisFile = os.path.join(hisPath, HISTORY_JSON)
        history = []
        if os.path.isfile(hisFile):
            try:
                with open(hisFile, 'r', encoding='utf-8') as f:
                    history = json.load(f)
                if not isinstance(history, list):
                    history = []
            except:
                pass
        return history

    #将当前会话添加到历史对话列表
    def addCurrentConvToHistory(self):
        maxHisotry = self.config.get('max_history', 10)
        if maxHisotry <= 0 or not self.currTopic or self.currTopic == DEFAULT_TOPIC:
            return

        if self.history and self.history[-1]['topic'] == self.currTopic:
            self.history[-1]['messages'] = self.messages[1:] #第一条消息固定为背景prompt
        else:
            self.history.append({'topic': self.currTopic, 'prompt': self.currPrompt, 'messages': self.messages[1:]})
        if len(self.history) > maxHisotry:
            self.history = self.history[-maxHisotry:]
        self.saveHistory()

    #保存历史对话信息到文件
    def saveHistory(self):
        if self.config.get('max_history', 10) <= 0:
            return

        hisPath = os.path.dirname(self.cfgFile)
        hisFile = os.path.join(hisPath, HISTORY_JSON)
        try:
            os.makedirs(hisPath, exist_ok=True)
            with open(hisFile, 'w', encoding='utf-8') as f:
                json.dump(self.history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print('Failed to save history file {}: {}'.format(style(hisFile, bold=True), str(e)))

    #根据下标列表，删除某些历史信息
    def deleteHistory(self, indexList):
        self.history = [item for idx, item in enumerate(self.history, 1) 
            if idx not in indexList]
        if 0 in indexList:  # 0 表示当前对话
            self.currTopic = DEFAULT_TOPIC
            self.messages = self.messages[:1]

    #导出某些历史信息到电子书
    #如果expName为电子邮件地址，则发送邮件，否则保存到文件
    #indexList: 需要导出的历史索引号列表
    def exportHistory(self, expName, indexList):
        # 0 为导出当前会话
        history = [self.history[index - 1] if index else {'topic': self.currTopic, 'messages': self.messages[1:]}
            for index in indexList if index <= len(self.history)]

        if not history:
            print('No conversation match the selected number')
            return
        
        isEmail = bool('@' in expName)
        if not isEmail: #寻找一个最合适的路径
            _writeable = lambda dir_: os.path.isdir(dir_) and os.access(dir_, os.W_OK)
            paths = [(KINDLE_DOC_DIR, '.txt'), (BASE_PATH, '.html'),
                (os.path.dirname(self.cfgFile), '.html'), (os.path.expanduser('~'), '.html')]
            for path, suffix in paths:
                if _writeable(path):
                    bookPath = path
                    break
            else:
                print('Cannot find a writeable directory')
                return
            
            suffix = '' if os.path.splitext(expName)[-1].lower() == suffix else suffix
            expName = f"{bookPath}/{expName}{suffix}"

        #生成html文件内容
        htmlContent = ['<!DOCTYPE html>\n<html>\n<head>\n<meta charset="UTF-8"><title>AI Chat History</title></head><body>']
        for idx, item in enumerate(history, 1):
            htmlContent.append(f"<h1>{item['topic']}</h1><hr/>")
            for msg in item['messages']:
                content = self.markdownToHtml(msg["content"], wrapCode=not isEmail)
                if msg['role'] == 'user':
                    htmlContent.append(f'<div style="margin-bottom:10px;"><strong>YOU:</strong><p style="margin-left:25px;">{content}</p></div><hr/>')
                else:
                    htmlContent.append(f'<div style="margin-bottom:10px;"><strong>AI:</strong><p style="margin-left:5px;">{content}</p></div><hr/>')
        htmlContent.append('</body></html>')
        try:
            if isEmail:
                self.smtpSendMail(expName, '\n'.join(htmlContent))
            else:
                with open(expName, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(htmlContent))
        except Exception as e:
            print('Could not export to {}: {}\n'.format(style(expName, bold=True), str(e)))
        else:
            print("Successfully exported to {}\n".format(style(expName, bold=True)))

    #使用smtp发送邮件，此函数可能会抛出异常
    def smtpSendMail(self, to, content):
        import smtplib
        from email.mime.base import MIMEBase
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        from email.encoders import encode_base64

        sender = self.config.get('smtp_sender', '')
        host = self.config.get('smtp_host', '')
        username = self.config.get('smtp_username', '')
        password = self.config.get('smtp_password', '')
        if not all([sender, host, username, password, host.split(':')[-1].isdigit()]):
            raise ValueError('Some configuration items are missing')

        host, port = host.split(':')
        port = int(port)
        
        to = [to] if isinstance(to, str) else to
        message = MIMEMultipart()
        message['Subject'] = 'AI Chat History'
        message['From'] = sender
        message['To'] = ', '.join(to)
        body = 'This email contains the AI conversation history. The detailed content is in the attachment, sent by Inkwell.'
        message.attach(MIMEText(body, 'plain', _charset='utf-8'))
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(content.encode('utf-8'))
        part.add_header('Content-Disposition', 'attachment', filename=('utf-8', '', 'conversation.html'))
        encode_base64(part)
        message.attach(part)

        klass = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
        with klass(host=host, port=port) as server:
            server.set_debuglevel(0) #0-no debug info, 1-base, 2- verbose
            server.connect(host, port)
            server.ehlo()
            if port == 587:
                server.starttls()
                server.ehlo()
            server.login(user=username, password=password)
            server.sendmail(sender, to, message.as_string())

    #简单的markdown转换为html，只转换常用的几个格式
    #不严谨，可能会排版混乱，但是应付AI聊天的场景应该足够
    #wrapCode: 使用table套在code代码段外模拟一个边框
    def markdownToHtml(self, content, wrapCode=True):
        import uuid
        
        #先把多行代码块中的文本提取出来，避免下面其他的处理搞乱代码
        codes = {}
        for mat in re.finditer(r'```(\w+)?\n(.*?)```', content, flags=re.DOTALL):
            id_ = '{{' + str(uuid.uuid4()) + '}}'
            codes[id_] = (mat.group(1), mat.group(2)) #语言标识，代码块
            content = content.replace(mat.group(0), id_)

        #行内代码 (`code`)
        content = re.sub(r'`([^`]+)`', r'<code>\1</code>', content)

        #表格
        content = self.mdTableToHtml(content)

        #标题 (# 或 ## 等)
        content = re.sub(r'^(#{1,6})\s+?(.*)$', lambda m: f'<h{len(m.group(1))}>{m.group(2).strip()}</h{len(m.group(1))}>', content, flags=re.MULTILINE)
        
        #加粗 (**bold** 或 __bold__)
        content = re.sub(r'(\*\*|__)(.*?)\1', r'<strong>\2</strong>', content)
        
        #斜体 (*italic* 或 _italic_)
        content = re.sub(r'(\*|_)(.*?)\1', r'<em>\2</em>', content)

        #删除线 (~~text~~)
        content = re.sub(r'(~{1,2})(.*?)\1', r'<s>\2</s>', content)

        #无序列表 (- 或 * 开头)
        content = re.sub(r'^ *[\*\-]\s+?(.*)$', r'<div><strong>• </strong>\1</div>', content, flags=re.MULTILINE)
        
        #有序列表 (数字加点开头)
        content = re.sub(r'^ *(\d+\.\s+?)(.*)$', r'<div><strong>\1</strong>\2</div>', content, flags=re.MULTILINE)

        #引用 (大于号开头)
        content = re.sub(r'^\s*>+\s+?(.*)$', r'<blockquote>\1</blockquote>', content, flags=re.MULTILINE)

        #链接 [text](url)
        content = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', content)
        
        #段落 (保持换行)
        content = re.sub(r'([^\n]+)', r'<div>\1</div>', content)

        #恢复多行代码块，Kindle不支持div边框，所以在代码块外套一个table，使用table的外框
        if wrapCode:
            tpl = ('<table border="1" bordercolor="silver" cellspacing="0" width="100%" style="background-color:#f9f9f9;border:1px solid silver;">'
                '<tr><td style="padding:5px;"><pre><code class="{lang}">{code}</code></pre></td></tr></table>')
        else:
            tpl = '<pre style="border:1px solid #555555;padding:10px;background-color:#f9f9f9;"><code class="{lang}">{code}</code></pre>'
        for id_, (lang, code) in codes.items():
            code = code.replace(' ', '&nbsp;')
            content = content.replace(id_, tpl.format(lang=lang or "lang", code=code))
            
        return content

    #markdown里面的表格转换为html格式的表格
    def mdTableToHtml(self, content):
        currTb = []
        tbHead = True
        ret = []
        for idx, line in enumerate(content.splitlines()):
            trimed = line.strip()
            if trimed.startswith('|') and trimed.endswith('|') and trimed.count('|') > 2:
                if not currTb: #表格开始
                    currTb.append('<table border="1" cellspacing="0" width="100%">')
                tds = [td.strip() for td in trimed.strip('|').split('|')]
                if ''.join([td.strip(':+- ') for td in tds]): #忽略分割行
                    currTb.append('<tr>')
                    currTb.append(''.join([f'<td><strong>{td}</strong></td>' if tbHead else f'<td>{td}</td>' for td in tds]))
                    currTb.append('</tr>')
                    tbHead = False
            elif trimed.startswith('+') and trimed.endswith('+') and not trimed.strip(':+- '): #另一种分割行
                if not currTb: #表格开始
                    currTb.append('<table border="1" cellspacing="0" width="100%">')
            elif currTb: #之前有表格，添加此表格到结果字符串列表
                currTb.append('</table>')
                ret.append(''.join(currTb))
                currTb = []
                tbHead = True
            else:
                ret.append(line)

        if currTb:
            currTb.append('</table>')
            ret.append(''.join(currTb))
        return '\n'.join(ret)

    #分析数值范围，返回一个列表，为了符合用户直觉，范围为前闭后闭，
    #1 -> [1]; 1-3 -> [1, 2, 3]; 1,3-5 -> [1, 3, 4, 5]
    def parseRange(self, txt):
        ret = []
        for e in txt.replace(' ', '').split(','):
            item = e.split('-', 1)
            start, end = (item[0], item[0]) if len(item) == 1 else item
            if start.isdigit() and end.isdigit():
                start, end = int(start), int(end)
                ret.extend(range(start, end + 1) if end >= start else range(start, end - 1, -1))
        return ret

    #显示菜单项
    def showMenu(self):
        print('')
        sprint(' Current prompt ', fg='white', bg='yellow', bold=True)
        print(self.currPrompt)
        print('')
        sprint(' Current conversation ', fg='white', bg='yellow', bold=True)
        print(f' 0. {self.currTopic}')
        print('')
        sprint(' Previous conversations ', fg='white', bg='yellow', bold=True)
        if not self.history:
            sprint('No previous conversations found!', fg='bright_black')
        else:
            for idx, item in enumerate(self.history, 1):
                sprint('{:2d}. {}'.format(idx, item.get('topic', 'Unknown topic'), fg='bright_black'))
        print('')

    #显示菜单，根据用户选择进行相应的处理
    def processMenu(self):
        self.showMenu()
        while True:
            input_ = input('[num, c, d, e, m, n, p, q, ?] » ').lower()
            if input_ == 'q': #退出
                return 'quit'
            elif input_ == '?': #显示命令帮助
                self.showCmdList()
            elif input_ == 'm': #切换model
                self.switchModel()
                self.showMenu()
            elif input_ == 'p': #选择一个prompt
                self.switchPrompt()
                self.showMenu()
            elif input_ == 'c': #分享读书笔记给AI，然后提问总结学习
                if self.summarizeClippings() == 'quit':
                    self.replayConversation() #中断了分享读书笔记过程，返回当前对话
                break
            elif input_[:1] == 'd' and input_[1:2].isdigit(): #删除历史数据
                self.deleteHistory(self.parseRange(input_[1:]))
                self.saveHistory()
                return 'reshow'
            elif input_[:1] == 'e' and input_[1:2].isdigit(): #将历史数据导出为电子书
                if expName := input(f'Filename/Email: '):
                    self.exportHistory(expName, self.parseRange(input_[1:]))
                else:
                    print('The filename is empty, canceled')
            elif input_ == 'n': #开始一个新的对话
                self.startNewConversation()
                print(' NEW CONVERSATION STARTED')
                self.printChatBubble('user', self.currTopic)
                break
            elif 0 <= (index := str_to_int(input_, -1)) <= len(self.history): #回到当前对话或切换到其他对话
                if index > 0:
                    self.switchConversation(self.history.pop(index - 1))
                self.replayConversation()
                break

    #开始一个新的会话
    def startNewConversation(self):
        self.addCurrentConvToHistory()
        self.messages = self.messages[:1] #第一个元素是系统Prompt，要一直保留
        self.currTopic = DEFAULT_TOPIC
        self.currPrompt = self.config.get('prompt', 'default')
        promptText = self.getPromptText(self.currPrompt)
        if promptText == DEFAULT_PROMPT:
            self.currPrompt = 'default'
        elif promptText == self.config.get('custom_prompt'):
            self.currPrompt = 'custom'
        self.messages[0]['content'] = promptText
    
    #切换到其他会话
    #msg: 目的消息字典
    def switchConversation(self, msg):
        self.addCurrentConvToHistory()
        self.messages = self.messages[:1] + msg.get('messages', [])
        self.currTopic = msg.get('topic', DEFAULT_TOPIC)
        self.currPrompt = msg.get('prompt', 'default')
        promptText = self.getPromptText(self.currPrompt)
        if promptText == DEFAULT_PROMPT:
            self.currPrompt = 'default'
        elif promptText == self.config.get('custom_prompt'):
            self.currPrompt = 'custom'
        self.messages[0]['content'] = promptText
    
    #根据prompt名字，返回prompt具体文本
    def getPromptText(self, promptName):
        prompt = ''
        if (not promptName or promptName == 'custom') and (customPrompt := self.config.get('custom_prompt')):
            prompt = customPrompt
        elif promptName != 'default':
            prompt = self.loadPrompts().get(promptName)

        return prompt if prompt else DEFAULT_PROMPT

    #显示菜单，切换当前服务提供商的其他model
    def switchModel(self):
        provider = self.config.get('provider')
        model = self.config.get('model')
        if provider not in AI_LIST:
            print('Current provider is invalid')
            return

        models = [item['name'] for item in AI_LIST[provider]['models']]
        print('')
        sprint(' Current model ', fg='white', bg='yellow', bold=True)
        print(f'{provider}/{model}')
        print('')
        sprint(' Available models [add ! to persist] ', fg='white', bg='yellow', bold=True)
        print('\n'.join(f'{idx:2d}. {item}' for idx, item in enumerate(models, 1)))
        print('')
        while True:
            if (input_ := input('» ')) == 'q':
                return
            needSave = input_.endswith('!')
            input_ = input_.rstrip('!')
            if 1 <= (index := str_to_int(input_)) <= len(models):
                self.client.model = models[index - 1]
                self.config['model'] = self.client.model
                if needSave:
                    self.saveConfig(self.config)
                break

    #显示菜单，选择一个会话使用的prompt
    def switchPrompt(self):
        self.loadPrompts()

        print('')
        sprint(' Current prompt ', fg='white', bg='yellow', bold=True)
        print(self.currPrompt)
        print('')
        sprint(' Available prompts [add ! to persist] ', fg='white', bg='yellow', bold=True)
        promptNames = ['default', 'custom', *self.prompts.keys()]
        print('\n'.join(f'{idx:2d}. {item}' for idx, item in enumerate(promptNames, 1)))
        print('')
        while True:
            if (input_ := input('[q 0 num] » ')) == 'q':
                break
            needSave = input_.endswith('!')
            input_ = input_.rstrip('!')
            index = str_to_int(input_, -1)
            if index == 0: #显示当前prompt具体内容
                print(self.messages[0]['content'])
                print('')
                continue
            elif 1 <= index <= len(promptNames):
                if index == 1:
                    self.currPrompt = 'default'
                    prompt = DEFAULT_PROMPT
                elif index == 2:
                    prevPrompt = self.config.get('custom_prompt', '')
                    sprint('Current custom prompt:', bold=True)
                    print(prevPrompt)
                    print('')
                    sprint('Provide a new curstom prompt or Enter to use current:', bold=True)
                    newArr = []
                    while (text := input('» ')):
                        newArr.append(text)
                    prompt = '\n'.join(newArr) or prevPrompt
                    if prompt:
                        self.currPrompt = 'custom'
                else:
                    self.currPrompt = promptNames[index - 1]
                    prompt = self.prompts.get(self.currPrompt)

                if prompt: #消息列表第一项为系统prompt
                    if self.currPrompt == 'custom':
                        self.config['custom_prompt'] = prompt
                    self.config['prompt'] = self.currPrompt
                    self.messages[0]['content'] = prompt
                    if needSave:
                        self.saveConfig(self.config)
                    sprint(f'Prompt set to: {self.currPrompt}', bold=True)
                break

    #读取高亮或读书笔记，返回最新的前10条记录
    def readClippings(self):
        if not os.path.isfile(CLIPPINGS_FILE):
            print('The file {} does not exist.'.format(style(CLIPPINGS_FILE, bold=True)))
            return []

        try:
            with open(CLIPPINGS_FILE, 'r', encoding='utf-8') as f:
                clips = f.read().split('==========')
        except Exception as e:
            print(f'Read clippings failed: {str(e)}')
            return []

        ret = []
        for item in clips[-10:]: #只显示最新的9个摘要，因为最后一个为空
            #每个笔记第一行是书名；第二行使用横杠开头，竖杠分割：笔记类型/页数/位置/时间；之后为具体摘要内容
            lines = item.strip().split('\n', 2)
            if len(lines) < 3:
                continue
            ret.append((lines[0], lines[-1].strip())) #书名，笔记内容
        return ret

    #分享一个高亮读书片段给AI，让AI总结和答疑
    def summarizeClippings(self):
        myClips = self.readClippings()
        if not myClips:
            sprint('There is no clippings now', bold=True)
            return

        print('')
        sprint(' The latest clippings ', fg='white', bg='yellow', bold=True)
        toDisplay = []
        for idx, item in enumerate(myClips, 1):
            frag = (item[1][:35] + '...') if len(item[1]) > 35 else item[1]
            toDisplay.append(f'{idx:2d}. {item[0][:30]}\n    {{}}'.format(style(frag, fg='bright_black')))
        print('\n'.join(toDisplay))
        print('')
        while True:
            if (input_ := input('[q, num or range] » ')) == 'q':
                return 'quit'
            if not (nums := self.parseRange(input_)):
                continue
            #提取需要的笔记
            clips = [myClips[idx] for idx in range(len(myClips)) if (idx + 1) in nums]
            if not clips:
                continue
            questMsg = []
            while (quest := input('Question » ')) not in ('', 'q', 'Q'):
                questMsg.append(quest)
            if quest in ('q', 'Q'):
                return 'quit'
            question = '\nQuestion:\n{}'.format('\n'.join(questMsg)) if questMsg else ''

            self.startNewConversation()
            msg = CLIPS_PROMPT.format(clips='\n'.join([f'- {e[0]}\n{e[1]}' for e in clips]), question=question)
            self.messages.append({"role": 'user', "content": msg})
            self.printUserMessage(msg)
            resp = self.fetchAiResponse(self.messages)
            respText = resp.content.strip() if resp.success else ('Error: ' + resp.error)
            self.messages.append({"role": 'assistant', "content": respText})
            self.printAiResponse(resp)
            self.printChatBubble('user', self.currTopic) #准备下一轮对话
            break

    #显示命令列表和帮助
    def showCmdList(self):
        print('')
        sprint(' Commands ', fg='white', bg='yellow', bold=True)
        print('{}: Choose a conversation to continue'.format(style(' num', bold=True)))
        print('{}: Start a conversation from reading clip'.format(style('   c', bold=True)))
        print('{}: Delete one or a range of conversations'.format(style('dnum', bold=True)))
        print('{}: Export one or a range of conversations'.format(style('enum', bold=True)))
        print('{}: Switch to another model'.format(style('   m', bold=True)))
        print('{}: Start a new conversation'.format(style('   n', bold=True)))
        print('{}: Choose another prompt'.format(style('   p', bold=True)))
        print('{}: Quit the program'.format(style('   q', bold=True)))
        print('{}: Show the command list'.format(style('   ?', bold=True)))

    #重新输出对话信息，用于切换对话历史
    def replayConversation(self):
        for item in self.messages[1:]:
            if item.get('role') == 'user':
                self.printUserMessage(item.get('content'))
            else:
                self.printAiResponse(AiResponse(success=True, content=item.get('content')))
        
        self.printChatBubble('user', self.currTopic)

    #打印用户输入内容
    def printUserMessage(self, content):
        self.printChatBubble('user', self.currTopic)
        for line in (e for e in content.splitlines() if e):
            print(f'» {line}')

    #打印AI返回的内容
    def printAiResponse(self, resp):
        host = resp.host
        if host.startswith('http://'):
            host = host[7:]
        elif host.startswith('https://'):
            host = host[8:]
        while len(host) > 30: # 循环去掉最后一个点号后的部分，直到小于等于30
            pos = host.rfind('.')
            if pos <= 0:
                break
            host = host[:pos]

        self.printChatBubble('assistant', host)
        if resp.success:
            disStyle = self.config.get('display_style', 'markdown')
            content = resp.content if disStyle == 'plaintext' else self.markdownToTerm(resp.content)
            print(content.strip())
        else:
            print(resp.error)
            sprint('Press r to resend the last chat', bold=True)
            if (any(s in resp.error for s in ('Unauthorized', 'Forbidden', 'token_expired'))
                and self.config.get('renew_api_key')):
                sprint('Press k to renew the api key', bold=True)

    #简单的处理markdown格式，用于在终端显示粗体斜体等效果
    def markdownToTerm(self, content):
        #标题 (# 或 ## 等)，使用粗体
        content = re.sub(r'^(#{1,6})\s+?(.*)$', r'\033[1m\2\033[0m', content, flags=re.MULTILINE)
        
        #加粗 (**bold** 或 __bold__)
        content = re.sub(r'(\*\*|__)(.*?)\1', r'\033[1m\2\033[0m', content)
        
        #斜体 (*italic* 或 _italic_)
        content = re.sub(r'(\*|_)(.*?)\1', r'\033[3m\2\033[0m', content)

        #删除线 (~~text~~), 大部分的终端不支持删除线，使用斜体代替
        #content = re.sub(r'(~{1,2})(.*?)\1', r'\033[9m\2\033[0m', content)
        content = re.sub(r'(~{1,2})(.*?)\1', r'\033[3m\2\033[0m', content)

        #列表项或序号加粗
        content = re.sub(r'^( *)(\* |\+ |- |[1-9]+\. )(.*)$', r'\1\033[1m\2\033[0m\3', content, flags=re.MULTILINE)

        #引用行变灰
        content = re.sub(r'^( *>+ .*)$', r'\033[90m\1\033[0m', content, flags=re.MULTILINE)
        
        #删除代码块提示行，保留代码块内容
        content = re.sub(r'^ *```.*$', '', content, flags=re.MULTILINE)

        #行内代码加粗
        content = re.sub(r'(`)(.*?)\1', r'\033[1m\2\033[0m', content)

        if self.config.get('display_style', 'markdown') == 'markdown_table':
            content = self.mdTableToTerm(content)
        return content

    #处理markdown文本里面的表格，排版对齐以便显示在终端上
    #在电脑上效果还可以
    #但在kindle实测效果不好，因为kindle屏幕太小，排版容易乱
    #返回排版后的字符串
    def mdTableToTerm(self, content):
        #假定里面只有一个表格，如果有多个表格，直接返回
        table = [] #表格的文本内容 [[col0, col1,...],]，如果不是表格行，则是原行的字符串
        colWidths = [] #每列文本字数 [[cnt0, cnt1,...],]，用于使用每列最大值进行对齐
        colNums = [] #每行的列数 [num0, num1,...]，用于判断表格是否有效
        prevTableRowIdx = -1
        lines = content.splitlines()
        for idx, row in enumerate(lines):
            if row.startswith('|') and row.endswith('|'):
                #必须要连续
                if prevTableRowIdx >= 0 and (prevTableRowIdx + 1) != idx:
                    colNums = []
                    break
                prevTableRowIdx = idx
                rowArr = [cell.strip() for cell in row.strip('|').split('|')]
                table.append(rowArr)
                colWidths.append([len(cell) for cell in rowArr]) #当前行每列的宽度
                colNums.append(len(rowArr))
            else:
                table.append(row)
        
        #如果有一些列数不同，可能为多个表格，为了避免排版混乱，直接返回原结果
        if not colNums or any(x != colNums[0] for x in colNums):
            return content

        colMaxWidths = [max(row[i] for row in colWidths) for i in range(colNums[0])] #每列的最大长度

        #内嵌函数，统一左对齐
        def format_row(row, bold):
            return "| {} |".format(" | ".join(style(cell.ljust(width), bold=bold) 
                            for cell, width in zip(row, colMaxWidths)))
        
        rowIdx = 0
        for idx in range(len(table)):
            row = table[idx]
            if isinstance(row, list):
                if all(not cell.strip('-:') for cell in row): #分割线
                    table[idx] = '| ' + "-+-".join(('-' * width) for width in colMaxWidths) + ' |'
                else: #内容行
                    table[idx] = format_row(row, bold=bool(rowIdx == 0))
                rowIdx += 1
        return '\n'.join(table)

    #在终端打印对话泡泡，显示角色和对话主题
    def printChatBubble(self, role, topic=''):
        topic = f' ({topic})' if topic else ''
        if role == 'user':
            role, fg, bg, bubFg = ' YOU ', 'white', 'green', 'bright_black'
        else:
            role, fg, bg, bubFg = ' AI ', 'white', 'cyan', 'bright_black'
        #不打印中间行左右的 ╞ ╡，避免不同的终端字体不同而不对齐
        txt = " {}{} ".format(style(role, fg=fg, bg=bg, bold=True), style(topic, fg=bubFg))
        charCnt = len(role) + len(topic) + 2
        sprint('\n╭{}╮'.format('─' * charCnt), fg=bubFg)
        print(txt)
        sprint('╰{}╯'.format('─' * charCnt), fg=bubFg)

    #更新谈话主题
    def updateTopic(self, msg=None):
        if msg: #直接在msg字符串上截取
            words = msg.replace('\n', ' ').replace('"', ' ').replace("'", ' ').split(' ')[:5]
            self.currTopic = ' '.join(words)[:30].strip() #限制总长度不超过30字节
        else: #让AI总结
            messages = self.messages + [{"role": "user", "content": PROMPT_GET_TOPIC}]
            resp = self.fetchAiResponse(messages)
            if resp.success:
                self.currTopic = resp.content.replace('`', '').replace('"', '').replace('\n', '')[:30]

    #给AI发请求，返回 AiResponse
    def fetchAiResponse(self, messages):
        try:
            respTxt = self.client.chat(self.getTrimmedChat(messages))
        except:
            return AiResponse(success=False, error=loc_exc_pos('Error'), host=self.client.tag)
        else:
            return AiResponse(success=True, content=respTxt, host=self.client.tag)

    #从消息历史中截取符合token长度要求的最近一部分会话，用于发送给AI服务器
    #返回一个新的列表
    def getTrimmedChat(self, messages: list):
        if not messages:
            return messages
        limit = int(self.config.get("token_limit", 4000) * 3) #简化token计算公式：字节数/3
        currLen = len(messages[0]['content']) + 20 # 20='role'/'system'/symbols:[]{},""
        newMsgs = []
        for idx in range(len(messages) - 1, 0, -1):
            role = messages[idx]['role']
            content = messages[idx]['content']
            if content.startswith('Error: '): #把谈话上下文里面的错误信息剔除
                content = ''
            currLen += len(content) + 20
            if currLen > limit:
                break
            newMsgs.append({'role': role, 'content': content})
        return messages[:1] + newMsgs[::-1]

    #更新ApiKey
    def renewApiKey(self):
        url = self.config.get('renew_api_key', '')
        if not url.startswith('http'):
            print('Make sure the "renew_api_key" field is set in the config file before proceeding')
            return

        parts = urlsplit(url)
        kC = http.client.HTTPSConnection if parts.scheme == 'https' else http.client.HTTPConnection
        conn = kC(parts.netloc, timeout=60)

        url = f"{parts.path}?{parts.query}" if parts.query else parts.path
        conn.request('GET', url)
        resp = conn.getresponse()
        try:
            body = json.loads(resp.read().decode("utf-8"))
            newKey = body.get('data')
            modified = body.get('modified', '')
        except:
            body = newKey = modified = None
        if not (200 <= resp.status < 300) or not body:
            print(f'Failed to renew api key: {resp.status}: {resp.reason}: {body}')
        elif newKey == self.config.get('api_key'):
            print(f'Your api key is up to date: {modified}')
        elif newKey:
            self.client.apiKey = newKey
            self.config['api_key'] = newKey
            self.saveConfig(self.config)
            print(f'Api key has been renewed successfully: {modified}')
        else:
            print('Unable to renew api key: empty result received')

        conn.close()

    #主循环入口
    #clippings: 为True则直接进入选择摘要模式，否则默认新建一个对话
    def start(self, clippings=False):
        cfg = self.config
        if cfg is None:
            return

        apiKey = cfg.get('api_key')
        if not apiKey:
            print('')
            sprint('Api key is missing', bold=True)
            sprint('Set it in the config file or run with the -s option', bold=True)
            print('')
            input_ = input('Press return key to quit ')
            return

        provider = cfg.get('provider')
        model = cfg.get('model')
        singleTurn = bool(cfg.get('chat_type') == 'single_turn')

        self.client = SimpleAiProvider(provider, apiKey=apiKey, model=model, apiHost=cfg.get('api_host'),
            singleTurn=singleTurn)
        self.history = self.loadHistory()
        self.startNewConversation()

        print('Model: {}'.format(style(f'{provider}/{model}', bold=True)))
        print('Prompt: {}'.format(style(self.currPrompt, bold=True)))
        print('{} send, {} menu, {} clips, {} quit'.format(style(' Enter ', fg='white', bg='cyan'),
            style(' ? ', fg='white', bg='cyan'), style(' c ', fg='white', bg='cyan'),
            style(' q ', fg='white', bg='cyan')))
        #print('Empty line to send, ? to menu, q to quit')

        quitRequested = False
        #直接进入选择读书摘要界面
        if not clippings:
            self.printChatBubble('user', self.currTopic)
        elif self.summarizeClippings() == 'quit':
            quitRequested = True
        
        while not quitRequested:
            msgArr = []
            while not quitRequested:
                sys.stdin.flush()
                input_ = input("» ")
                if input_ in ('q', 'Q'):
                    quitRequested = True
                    break
                elif input_ in ('k', 'K') and cfg.get('renew_api_key'): #更新api_key
                    self.renewApiKey()
                elif input_ == 'c': #进入选择读书摘要界面
                    if self.summarizeClippings() == 'quit':
                        self.replayConversation() #中断了分享读书摘要，回到原先的对话
                elif input_ == '?':
                    msgArr = []
                    ret = 'reshow'
                    while ret == 'reshow':
                        ret = self.processMenu()
                    if ret == 'quit':
                        quitRequested = True
                        break
                else:
                    msg = '\n'.join(msgArr).strip()
                    #输入r重发上一个请求
                    if input_ in ('r', 'R') and not msg and len(self.messages) > 2:
                        userItem = self.messages[-2] #开头为背景prompt，之后user/assistant交替
                        msg = userItem.get('content', '')
                        input_ = ''
                        for line in msg.splitlines():
                            print(f'» {line}')
                        print('')

                    if input_: #可以输入多行，逐行累加
                        msgArr.append(input_)
                    elif msg: #输入一个空行并且之前已经有过输入，发送请求
                        msgArr = []
                        self.messages.append({"role": 'user', "content": msg})
                        if len(self.messages) == 2: #第一次交谈，使用用户输出的开头四个单词做为topic
                            self.updateTopic(msg)
                        elif len(self.messages) == 4: #第三次交谈，使用ai总结谈话内容做为topic
                            self.updateTopic()
                        resp = self.fetchAiResponse(self.messages)
                        respText = resp.content.strip() if resp.success else ('Error: ' + resp.error)
                        self.messages.append({"role": 'assistant', "content": respText})
                        self.printAiResponse(resp)
                        self.printChatBubble('user', self.currTopic)

        self.client.close()
        self.addCurrentConvToHistory()

    #交互式配置过程
    def setup(self):
        cfg = {}
        providers = list(AI_LIST.keys())
        print('')
        sprint('Start inkwell config. Press q to abort.', bold=True)
        print('')
        sprint(' Providers ', fg='white', bg='yellow', bold=True)
        print('\n'.join(f'{idx:2d}. {item}' for idx, item in enumerate(providers, 1)))
        models = []
        while True:
            if (input_ := input('» ')) in ('q', 'Q'):
                return
            if 1 <= (index := str_to_int(input_)) <= len(providers):
                cfg['provider'] = provider = providers[index - 1]
                models = [item['name'] for item in AI_LIST[provider]['models']]
                break

        #模型Model
        print('')
        sprint(' Models ', fg='white', bg='yellow', bold=True)
        print('\n'.join(f'{idx:2d}. {item}' for idx, item in enumerate(models, 1)))
        print(f'{len(models) + 1:2d}. Other')
        while True:
            if (input_ := (input('» [1] ') or '1')) in ('q', 'Q'):
                return
            index = str_to_int(input_)
            if 1 <= index <= len(models):
                cfg['model'] = models[index - 1]
                break
            elif (index == len(models) + 1) and (input_ := input('Model Name » ')):
                cfg['model'] = input_
                break

        #Api key
        print('')
        sprint(' Api key (semicolon-separated) ', fg='white', bg='yellow', bold=True)
        while True:
            if (input_ := input('» ')) in ('q', 'Q'):
                return
            if input_:
                cfg['api_key'] = input_
                break

        #主机地址，可以为多个，使用分号分割
        print('')
        sprint(' Api host (optional, semicolon-separated) ', fg='white', bg='yellow', bold=True)
        if (input_ := input('» ')) in ('q', 'Q'):
            return
        cfg['api_host'] = ';'.join([e if e.startswith('http') else 'https://' + e 
            for e in input_.replace(' ', '').split(';') if e])

        #Display style
        print('')
        sprint(' Display style ', fg='white', bg='yellow', bold=True)
        styles = ['markdown', 'markdown_table', 'plaintext']
        print('\n'.join(f'{idx:2d}. {item}' for idx, item in enumerate(styles, 1)))
        while True:
            if (input_ := (input('» [1] ') or '1')) in ('q', 'Q'):
                return
            if 1 <= (index := str_to_int(input_)) <= len(styles):
                cfg['display_style'] = styles[index - 1]
                break

        #是否支持上下文多轮对话
        print('')
        sprint(' Chat type ', fg='white', bg='yellow', bold=True)
        turns = ['multi_turn (multi-step conversations)', 'single_turn (merged history as context)']
        print('\n'.join(f'{idx:2d}. {item}' for idx, item in enumerate(turns, 1)))
        while True:
            if (input_ := (input('» [1] ') or '1')) in ('q', 'Q'):
                return
            if input_ in ('1', '2'):
                cfg['chat_type'] = 'multi_turn' if (input_ == '1') else 'single_turn'
                break
        
        #Conversation token limit
        print('')
        sprint(' Context token limit ', fg='white', bg='yellow', bold=True)
        while True:
            if (input_ := (input('» [4000] ') or '4000')) in ('q', 'Q'):
                return
            if input_.isdigit():
                cfg['token_limit'] = int(input_)
                if cfg['token_limit'] < 1000:
                    cfg['token_limit'] = 1000
                break
        
        #Max history
        print('')
        sprint(' Max history ', fg='white', bg='yellow', bold=True)
        while True:
            if (input_ := (input('» [10] ') or '10')) in ('q', 'Q'):
                return
            if input_.isdigit():
                cfg['max_history'] = int(input_)
                break

        #配置自定义的prompt
        print('')
        sprint(' Custom prompt (optional) ', fg='white', bg='yellow', bold=True)
        prompts = []
        while (input_ := input('» ')) not in ('', 'q', 'Q'):
            prompts.append(input_)
        if input_ in ('q', 'Q'):
            return
        prompt = '\n'.join(prompts).strip()
        #prompt是prompts.txt里面某一个prompt的标题
        cfg['prompt'] = 'custom' if prompt else 'default'
        cfg['custom_prompt'] = prompt
        
        self.saveConfig(cfg)

#------------------------------------------------------------
#开始为AI适配器类
#------------------------------------------------------------
#支持的AI服务商列表，models里面的第一项请设置为默认要使用的model
#context: 输入上下文长度，因为程序采用估计法，建议设小一些。注意：一般的AI的输出长度较短，大约4k/8k
#rpm(requests per minute)是针对免费用户的，如果是付费用户，一般会高很多，可以自己修改
#大语言模型发展迅速，估计没多久这些数据会全部过时
#https://docs.anthropic.com/en/docs/about-claude/models/overview
AI_LIST = {
    'openai': {'host': 'https://api.openai.com', 'models': [
        {'name': 'gpt-4o-mini', 'rpm': 1000, 'context': 128000},
        {'name': 'gpt-4o', 'rpm': 500, 'context': 128000},
        {'name': 'o1', 'rpm': 500, 'context': 200000},
        {'name': 'o1-mini', 'rpm': 500, 'context': 200000},
        {'name': 'o1-pro', 'rpm': 500, 'context': 200000},
        {'name': 'gpt-4.1', 'rpm': 500, 'context': 1000000},
        {'name': 'o3', 'rpm': 500, 'context': 200000},
        {'name': 'o3-mini', 'rpm': 1000, 'context': 200000},
        {'name': 'o4-mini', 'rpm': 1000, 'context': 200000},
        {'name': 'gpt-4-turbo', 'rpm': 500, 'context': 128000},
        {'name': 'gpt-3.5-turbo', 'rpm': 3500, 'context': 16000},],},
    'google': {'host': 'https://generativelanguage.googleapis.com', 'models': [
        {'name': 'gemini-1.5-flash', 'rpm': 15, 'context': 128000}, #其实支持100万
        {'name': 'gemini-1.5-flash-8b', 'rpm': 15, 'context': 128000},
        {'name': 'gemini-1.5-pro', 'rpm': 2, 'context': 128000},
        {'name': 'gemini-2.0-flash', 'rpm': 15, 'context': 128000},
        {'name': 'gemini-2.0-flash-lite', 'rpm': 30, 'context': 128000},
        {'name': 'gemini-2.0-flash-thinking', 'rpm': 10, 'context': 128000},
        {'name': 'gemini-2.0-pro', 'rpm': 5, 'context': 128000},],},
    'anthropic': {'host': 'https://api.anthropic.com', 'models': [
        {'name': 'claude-2', 'rpm': 5, 'context': 100000},
        {'name': 'claude-3', 'rpm': 5, 'context': 200000},
        {'name': 'claude-opus-4-0', 'rpm': 60, 'context': 200000},
        {'name': 'claude-sonnet-4-0', 'rpm': 60, 'context': 200000},
        {'name': 'claude-3-7-sonnet-latest', 'rpm': 60, 'context': 200000},
        {'name': 'claude-3-5-sonnet-latest', 'rpm': 60, 'context': 200000},
        {'name': 'claude-3-5-haiku-latest', 'rpm': 60, 'context': 200000},
        {'name': 'claude-2.1', 'rpm': 5, 'context': 100000},],},
    'xai': {'host': 'https://api.x.ai', 'models': [
        {'name': 'grok-1', 'rpm': 60, 'context': 128000},
        {'name': 'grok-2', 'rpm': 60, 'context': 128000},
        {'name': 'grok-3', 'rpm': 60, 'context': 128000},
        {'name': 'grok-4', 'rpm': 60, 'context': 128000},],},
    'mistral': {'host': 'https://api.mistral.ai', 'models': [
        {'name': 'open-mistral-7b', 'rpm': 60, 'context': 32000},
        {'name': 'mistral-small-latest', 'rpm': 60, 'context': 32000},
        {'name': 'open-mixtral-8x7b', 'rpm': 60, 'context': 32000},
        {'name': 'open-mixtral-8x22b', 'rpm': 60, 'context': 64000},
        {'name': 'mistral-medium-latest', 'rpm': 60, 'context': 32000},
        {'name': 'mistral-large-latest', 'rpm': 60, 'context': 128000},
        {'name': 'pixtral-12b-2409', 'rpm': 60, 'context': 128000},
        {'name': 'codestral-2501', 'rpm': 60, 'context': 256000},],},
    'groq': {'host': 'https://api.groq.com', 'models': [
        {'name': 'gemma2-9b-it', 'rpm': 30, 'context': 8000},
        {'name': 'gemma-7b-it', 'rpm': 30, 'context': 8000},
        {'name': 'llama-guard-3-8b', 'rpm': 30, 'context': 8000},
        {'name': 'llama3-70b-8192', 'rpm': 30, 'context': 8000},
        {'name': 'llama3-8b-8192', 'rpm': 30, 'context': 8000},
        {'name': 'mixtral-8x7b-32768', 'rpm': 30, 'context': 32000},],},
    'perplexity': {'host': 'https://api.perplexity.ai', 'models': [
        {'name': 'llama-3.1-sonar-small-128k-online', 'rpm': 60, 'context': 128000},
        {'name': 'llama-3.1-sonar-large-128k-online', 'rpm': 60, 'context': 128000},
        {'name': 'llama-3.1-sonar-huge-128k-online', 'rpm': 60, 'context': 128000},],},
    'alibaba': {'host': 'https://dashscope.aliyuncs.com', 'models': [
        {'name': 'qwen-turbo', 'rpm': 60, 'context': 128000}, #其实支持100万
        {'name': 'qwen-plus', 'rpm': 60, 'context': 128000},
        {'name': 'qwen-long', 'rpm': 60, 'context': 128000},
        {'name': 'qwen-max', 'rpm': 60, 'context': 32000},],},
}

#自定义HTTP响应错误异常
class HttpResponseError(Exception):
    def __init__(self, status, reason, body=None):
        super().__init__(f"{status}: {reason}")
        self.status = status
        self.reason = reason
        self.body = body

class SimpleAiProvider:
    #name: AI提供商的名字
    #apiKey: 如需要多个Key，以分号分割，逐个使用
    #apiHost: 支持自搭建的API转发服务器，如传入以分号分割的地址列表字符串，则逐个使用
    #singleTurn: 一些API转发服务不支持多轮对话模式，设置此标识，当前仅支持 openai
    def __init__(self, name, apiKey, model=None, apiHost=None, singleTurn=False):
        name = name.lower()
        if name not in AI_LIST:
            raise ValueError(f"Unsupported provider: {name}")
        self.name = name
        self.apiKeys = apiKey.split(';')
        self.apiKeyIdx = 0
        self.singleTurn = singleTurn
        self._models = AI_LIST[name]['models']
        
        #如果传入的model不在列表中，默认使用第一个的参数
        item = next((m for m in self._models if m['name'] == model), self._models[0])
        #self.model = item['name']
        self.model = model or item['name']
        self._rpm = item['rpm']
        self.context_size = item['context']
        if self._rpm <= 0:
            self._rpm = 2
        if self.context_size < 1000:
            self.context_size = 1000
        #分析主机和url，保存为 SplitResult(scheme,netloc,path,query,frament)元祖
        #connPools每个元素为 [host_tuple, conn_obj]
        self.connPools = [[urlsplit(e if e.startswith('http') else ('https://' + e)), None]
            for e in (apiHost or AI_LIST[name]['host']).replace(' ', '').split(';')]
        self.host = '' #当前正在使用的 netloc
        self.connIdx = 0
        self.createConnections()

    #返回速率限制，如果有多个host或key，则速率可以倍数放大
    @property
    def rpm(self):
        return int(self._rpm * max([len(self.connPools), len(self.apiKeys)]))
    #用于界面显示的host，如果是多个的话，显示当前使用的host，否则返回空
    @property
    def tag(self):
        return self.host if len(self.connPools) > 1 else ""

    #自动获取下一个ApiKey
    @property
    def apiKey(self):
        ret = self.apiKeys[self.apiKeyIdx]
        self.apiKeyIdx = (self.apiKeyIdx + 1) % len(self.apiKeys)
        return ret
    @apiKey.setter
    def apiKey(self, value):
        self.apiKeys = value.split(';')
        self.apiKeyIdx = 0
    
    #自动获取列表中下一个连接对象，返回 (index, host tuple, con obj)
    def nextConnection(self):
        index = self.connIdx
        host, conn = self.connPools[index]
        self.connIdx = (self.connIdx + 1) % len(self.connPools)
        return index, host, conn

    #创建长连接
    #index: 如果传入一个整型，则只重新创建此索引的连接实例
    def createConnections(self):
        for index in range(len(self.connPools)):
            self.createOneConnection(index)

        #尽量不修改connIdx，保证能轮询每个host
        if self.connIdx >= len(self.connPools):
            self.connIdx = 0

    #创建一个对应索引的连接对象
    def createOneConnection(self, index):
        if not (0 <= index < len(self.connPools)):
            return

        host, e = self.connPools[index]
        if e:
            e.close()
        #使用HTTPSConnection有一个好处是短时间多次对话只需要一次握手
        if host.scheme == 'https':
            sslCtx = ssl._create_unverified_context()
            conn = http.client.HTTPSConnection(host.netloc, timeout=60, context=sslCtx)
        else:
            conn = http.client.HTTPConnection(host.netloc, timeout=60)
        self.connPools[index][1] = conn

    #发起一个网络请求，返回json数据
    def _send(self, path, headers=None, payload=None, toJson=True, method='POST'):
        if payload:
            payload = json.dumps(payload)
        retried = 0
        while retried < 2:
            try:
                index, host, conn = self.nextConnection() #(index, host_tuple, conn_obj)
                self.host = host.netloc
                #拼接路径，避免一些边界条件出错
                url = '/' + host.path.strip('/') + (('?' + host.query) if host.query else '') + path.lstrip('/')
                conn.request(method, url, payload, headers)
                resp = conn.getresponse()
                body = resp.read().decode("utf-8")
                #print(resp.reason, ', ', body) #TODO
                if not (200 <= resp.status < 300):
                    raise HttpResponseError(resp.status, resp.reason, body)
                return json.loads(body) if toJson else body
            except (http.client.CannotSendRequest, http.client.RemoteDisconnected) as e:
                if retried:
                    raise
                #print("Connection issue, retrying:", e)
                self.createOneConnection(index)
                retried += 1

    #关闭连接
    #index: 如果传入一个整型，则只关闭对应索引的连接
    def close(self, index=None):
        connNum = len(self.connPools)
        if isinstance(index, int) and (0 <= index < connNum):
            host, e = self.connPools[index]
            if e:
                e.close()
                self.connPools[index][1] = None

        for index in range(connNum):
            host, e = self.connPools[index] #[host_tuple, conn_obj]
            if e:
                e.close()
                self.connPools[index][1] = None

    def __repr__(self):
        return f'{self.name}/{self.model}'

    #外部调用此函数即可调用简单聊天功能
    #message: 如果是文本，则使用各项默认参数
    #传入 list/dict 可以定制 role 等参数
    #返回 respTxt
    def chat(self, message):
        if not self.apiKey:
            raise ValueError(f'The api key is empty')
        name = self.name
        if name == "openai":
            return self._openai_chat(message)
        elif name == "anthropic":
            return self._anthropic_chat(message)
        elif name == "google":
            return self._google_chat(message)
        elif name == "xai":
            return self._xai_chat(message)
        elif name == "mistral":
            return self._mistral_chat(message)
        elif name == 'groq':
            return self._groq_chat(message)
        elif name == 'perplexity':
            return self._perplexity_chat(message)
        elif name == "alibaba":
            return self._alibaba_chat(message)
        else:
            raise ValueError(f"Unsupported provider: {name}")

    #返回当前服务提供商支持的models列表
    def models(self, prebuild=True):
        if self.name in ('openai', 'xai'):
            return self._openai_models()
        elif self.name == 'google':
            return self._google_models()
        else:
            return [item['name'] for item in self._models]

    #openai的chat接口
    def _openai_chat(self, message, path='v1/chat/completions'):
        headers = {'Authorization': f'Bearer {self.apiKey}', 'Content-Type': 'application/json'}
        if isinstance(message, str):
            msg = [{"role": "user", "content": message}]
        elif self.singleTurn and (len(message) > 1): #将多轮对话手动拼接为单一轮对话
            msgArr = ['Previous conversions:\n']
            roleMap = {'system': 'background', 'assistant': 'Your responsed'}
            msgArr.extend([f'{roleMap.get(e["role"], "I asked")}:\n{e["content"]}\n' for e in message[:-1]])
            msgArr.append(f'\nPlease continue this conversation based on the previous information:\n')
            msgArr.append("I ask:")
            msgArr.append(message[-1]['content'])
            msgArr.append("You Response:\n")
            msg = [{"role": "user", "content": '\n'.join(msgArr)}]
        else:
            msg = message
        payload = {"model": self.model, "messages": msg}
        data = self._send(path, headers=headers, payload=payload, method='POST')
        return data["choices"][0]["message"]["content"]

    #openai的models接口
    def _openai_models(self):
        headers = {'Authorization': f'Bearer {self.apiKey}', 'Content-Type': 'application/json'}
        data = self._send('v1/models', headers=headers, method='GET')
        return [item['id'] for item in data['data']]

    #anthropic的chat接口
    def _anthropic_chat(self, message):
        headers = {'Accept': 'application/json', 'Anthropic-Version': '2023-06-01',
            'Content-Type': 'application/json', 'x-api-key': self.apiKey}

        if isinstance(message, list): #将openai的payload格式转换为anthropic的格式
            msg = []
            for item in message:
                role = 'Human' if (item.get('role') != 'assistant') else 'Assistant'
                content = item.get('content', '')
                msg.append(f"\n\n{role}: {content}")
            prompt = ''.join(msg) + "\n\nAssistant:"
            payload = {"prompt": prompt, "model": self.model, "max_tokens_to_sample": 1024}
        elif isinstance(message, dict):
            payload = message
        else:
            prompt = f"\n\nHuman: {message}\n\nAssistant:"
            payload = {"prompt": prompt, "model": self.model, "max_tokens_to_sample": 1024}
        
        data = self._send('v1/complete', headers=headers, payload=payload, method='POST')
        return data["completion"]

    #gemini的chat接口
    def _google_chat(self, message):
        url = f'v1beta/models/{self.model}:generateContent?key={self.apiKey}'
        headers = {'Content-Type': 'application/json'}
        if isinstance(message, list): #将openai的payload格式转换为gemini的格式
            msg = []
            for item in message:
                role = 'user' if (item.get('role') != 'assistant') else 'model'
                content = item.get('content', '')
                msg.append({'role': role, 'parts': [{'text': content}]})
            payload = {'contents': msg}
        elif isinstance(message, dict):
            payload = message
        else:
            payload = {'contents': [{'role': 'user', 'parts': [{'text': message}]}]}
        data = self._send(url, headers=headers, payload=payload, method='POST')
        contents = data["candidates"][0]["content"]
        return contents['parts'][0]['text']

    #google的models接口
    def _google_models(self):
        url = f'v1beta/models?key={self.apiKey}&pageSize=100'
        headers = {'Content-Type': 'application/json'}
        data = self._send(url, headers=headers, method='GET')
        _trim = lambda x: x[7:] if x.startswith('models/') else x
        return [_trim(item['name']) for item in data['models']]

    #xai的chat接口
    def _xai_chat(self, message):
        return self._openai_chat(message, path='v1/chat/completions')

    #mistral的chat接口
    def _mistral_chat(self, message):
        return self._openai_chat(message, path='v1/chat/completions')

    #groq的chat接口
    def _groq_chat(self, message):
        return self._openai_chat(message, path='openai/v1/chat/completions')

    #perplexity的chat接口
    def _perplexity_chat(self, message):
        return self._openai_chat(message, path='chat/completions')

    #通义千问
    def _alibaba_chat(self, message):
        return self._openai_chat(message, path='compatible-mode/v1/chat/completions')

#获取发生异常时的文件名和行号，添加到自定义错误信息后面
#此函数必须要在异常后调用才有意义，否则只是简单的返回传入的参数
def loc_exc_pos(msg: str):
    klass, e, excTb = sys.exc_info()
    if excTb:
        import traceback
        stacks = traceback.extract_tb(excTb) #StackSummary instance, a list
        if len(stacks) == 0:
            return msg

        top = stacks[0]
        bottom2 = stacks[-2] if len(stacks) > 1 else stacks[-1]
        bottom1 = stacks[-1]
        tF = os.path.basename(top.filename)
        tLn = top.lineno
        b1F = os.path.basename(bottom1.filename)
        b1Ln = bottom1.lineno
        b2F = os.path.basename(bottom2.filename)
        b2Ln = bottom2.lineno
        typeName = klass.__name__ if klass else ''
        return f'{msg}: {typeName} {e} [{tF}:{tLn}->...->{b2F}:{b2Ln}->{b1F}:{b1Ln}]'
    else:
        return msg

#分析命令行参数
def getArg():
    parser = argparse.ArgumentParser()
    parser.add_argument("-s", "--setup", action="store_true", help="Start interactive configuration")
    parser.add_argument("-c", "--config", metavar="FILE", help="Specify a configuration file")
    parser.add_argument("-k", "--clippings", action="store_true", help="Start in clippings")
    return parser.parse_args()

if __name__ == "__main__":
    print(style(r'''  _____         _                    _  _ ''', fg='green'))
    print(style(r''' |_   _|       | |                  | || |''', fg='green'))
    print(style(r'''   | |   _ __  | | ____      __ ___ | || |''', fg='green'))
    print(style(r'''   | |  | '_ \ | |/ /\ \ /\ / // _ \| || |''', fg='green'))
    print(style(r'''  _| |_ | | | ||   <  \ V  V /|  __/| || |''', fg='green'))
    print(style(r''' |_____||_| |_||_|\_\  \_/\_/  \___||_||_|''', fg='green'))
    print(style(r'''                                          ''', fg='green'))
    print(__Version__)

    args = getArg()
    cfgFile = args.config
    if cfgFile:
        cfgFile = os.path.abspath(cfgFile)

    #如果不是初始化并且指定了配置文件，则配置文件必须存在
    if not args.setup and cfgFile and not os.path.isfile(cfgFile):
        print('The file {} does not exist'.format(style(cfgFile, bold=True)))
        print('')
        input_ = input('Press return key to quit ')
    else:
        inkwell = InkWell(cfgFile)
        if args.setup:
            inkwell.setup()

        inkwell.start(args.clippings)
