#!/usr/bin/env python3
# -*- coding:utf-8 -*-
#Author: cdhigh <https://github.com/cdhigh>
"""运行于终端的Ai助手，主要为Kindle设计，也适用于其他系统的终端
1. Python >= 3.8
2. 不依赖任何第三方库
用法：
1. 使用命令行参数 --setup 开始交互式的初始化和配置
2. 不带参数执行，自动使用同一目录下的配置文件 config.json，如果没有则自动新建一个默认模板
3. 如果需要不同的配置，可以传入参数 python inkwell.py --config path/to/config.json
"""
import os, sys, re, json, itertools, ssl, argparse
import http.client

__Version__ = 'v1.0 (2024-12-02)'
BASE_PATH = os.path.dirname(os.path.abspath(__file__))
CONFIG_JSON = f"{BASE_PATH}/config.json"
HISTORY_JSON = "history.json"

#默认的AI角色配置
CUSTOM_INSTRUCTIONS = """You are a helpful personal assistant.
- Please note that your answers will be displayed on the terminal.
- So keep answers short as possible and use a suitable format for printing on a terminal."""

#获取谈话主题的prompt
PROMPT_GET_TOPIC = """Please give this conversation a short title.
- Hard limit of 4 words.
- Don't mention yourself in it.
- Don't use any special characters.
- Don't use any capital letters.
- Don't use any punctuation.
- Don't use any symbols.
- Don't use any emojis.
- Don't use any accents.
- Don't use quotes."""

#终端的颜色代码表
_TERMINAL_COLORS = {"black": 30, "red": 31, "green": 32, "yellow": 33, "blue": 34, "magenta": 35,
    "cyan": 36, "white": 37, "reset": 39, "bright_black": 90, "bright_red": 91, "bright_green": 92,
    "bright_yellow": 93, "bright_blue": 94, "bright_magenta": 95, "bright_cyan": 96, "bright_white": 97,
    "orange": (255,165,0), "grey": 90}

DEFAULT_TOPIC = 'new conversation'

#为简化代码，设置几个全局变量
g_cfgFile = ''
g_config = {}
g_currTopic = ''
g_messages = []
g_history = []
g_tokenNum = 0

#AI响应的结构封装
class AiResponse:
    def __init__(self, success, content="", error=""):
        self.success = success
        self.content = content
        self.error = error

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

#获取配置数据，这个函数返回的配置字典是经过校验的，里面的数据都是合法的
def getConfig(cfgFile=None):
    global g_cfgFile, g_config
    g_cfgFile = cfgFile or CONFIG_JSON
    cfg = {}
    if not os.path.isfile(g_cfgFile):
        sprint(f'\n\nThe file {g_cfgFile} does not exist. A default file will be created', fg='red', bg='white')
        sprint('Please complete the file using a text editor\n\n', fg='red', bg='white')
        try:
            with open(g_cfgFile, 'w', encoding='utf-8') as f:
                json.dump({"provider": "gemini", "model": "gemini-1.5-flash", "api_key": "", "api_host": "", 
                    "display_style": "markdown", "token_limit": 4000, "max_history": 10, "custom_instructions": ""}, f, indent=2)
        except Exception as e:
            print(f'Failed to write {g_cfgFile}: {e}')

    if os.path.isfile(g_cfgFile):
        with open(g_cfgFile, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
    if not isinstance(cfg, dict):
        cfg = {}

    #校验和设置几个全局变量
    provider = cfg.get('provider', '').lower()
    if provider not in AI_LIST:
        provider = 'google'
        cfg['provider'] = provider
    models = [item['name'] for item in AI_LIST[provider]['models']]
    model = cfg.get('model')
    if model not in models:
        cfg['model'] = models[0]
    if cfg.get("token_limit", 4000) < 1000:
        cfg['token_limit'] = 1000
    displayStyle = cfg.get('display_style')
    if displayStyle not in ('plaintext', 'markdown', 'markdown_table'):
        displayStyle = 'markdown'
    cfg['display_style'] = displayStyle
    g_config = cfg
    return cfg

#获取历史对话信息
def getHistory():
    global g_history, g_config
    history = []
    if g_config.get('max_history', 10) <= 0:
        return history

    hisPath = os.path.dirname(g_cfgFile)
    hisFile = os.path.join(hisPath, HISTORY_JSON)
    if os.path.isfile(hisFile):
        try:
            with open(hisFile, 'r', encoding='utf-8') as f:
                history = json.load(f)
            if not isinstance(history, list):
                history = []
        except:
            pass
    return history

#将一条数据添加到历史对话
def addHistory(topic, messages):
    global g_history, g_config
    maxHisotry = g_config.get('max_history', 10)
    if maxHisotry <= 0:
        return

    if g_history and g_history[-1]['topic'] == topic:
        g_history[-1]['messages'] = messages[1:] #第一条消息为背景prompt
    else:
        g_history.append({'topic': topic, 'messages': messages[1:]})
    if len(g_history) > maxHisotry:
        g_history = g_history[-maxHisotry:]
    saveHistory(g_history)

#保存历史对话信息
def saveHistory(history):
    global g_cfgFile, g_config
    if g_config.get('max_history', 10) <= 0:
        return

    hisPath = os.path.dirname(g_cfgFile)
    hisFile = os.path.join(hisPath, HISTORY_JSON)
    try:
        if not os.path.isdir(hisPath):
            os.mkdir(hisPath)
        with open(hisFile, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print('Failed to save history file {}: {}'.format(style(hisFile, bold=True), str(e)))

#根据下标列表，删除某些历史信息
def deleteHistory(indexList):
    global g_history
    g_history = [item for idx, item in enumerate(g_history, 1) if idx not in indexList]

#导出某些历史信息到电子书
def exportHistory(expName, indexList):
    global g_history, g_cfgFile
    # [0] 为导出当前会话
    if len(indexList) == 1 and indexList[0] == 0:
        history = [{'topic': g_currTopic, 'messages': g_messages[1:]}]
    else:
        history = [item for idx, item in enumerate(g_history, 1) if idx in indexList]
    if not history:
        print('No conversation match the selected number')
        return

    def dir_available(dir_):
        return os.path.isdir(dir_) and os.access(dir_, os.W_OK)

    #寻找一个最合适的路径
    suffix = '.html'
    if dir_available('/mnt/us/documents'):
        bookPath = '/mnt/us/documents'
        suffix = '.txt'
    elif dir_available(BASE_PATH):
        bookPath = BASE_PATH
    elif dir_available(os.path.dirname(g_cfgFile)):
        bookPath = os.path.dirname(g_cfgFile)
    elif dir_available(os.path.expanduser('~')):
        bookPath = os.path.expanduser('~')
    else:
        print('Cannot find a writeable directory')
        return
    
    if os.path.splitext(expName)[-1] == suffix:
        suffix = ''
    expFileName = f"{bookPath}/{expName}{suffix}"
    try:
        with open(expFileName, 'w', encoding='utf-8') as f:
            f.write('<!DOCTYPE html>\n<html lang="zh">\n<head><meta charset="UTF-8"><title>AI Chat History</title></head><body>')
            for idx, item in enumerate(history, 1):
                f.write(f"<h1>Topic: {item['topic']}</h1><hr/>\n")
                for msg in item['messages']:
                    content = markdownToHtml(msg["content"])
                    if msg['role'] == 'user':
                        f.write(f'<div style="margin-bottom: 10px;"><strong>User:</strong><p style="margin-left: 25px;">{content}</p></div><hr/>\n')
                    else:
                        f.write(f'<div style="margin-bottom: 10px;"><strong>AI:</strong><p style="margin-left: 5px;">{content}</p></div><hr/>\n')
            f.write('</body></html>')
    except Exception as e:
        print('Could not export to {}: {}\n'.format(style(expFileName, bold=True), str(e)))
    else:
        print("Successfully exported to {}\n".format(style(expFileName, bold=True)))

#简单的markdown转换为html，只转换常用的几个格式
#不严谨，可能会排版混乱，但是应付AI聊天的场景应该足够
def markdownToHtml(content):
    import uuid
    #先把多行代码块中的文本提取出来，避免下面其他的处理搞乱代码
    codes = {}
    for mat in re.finditer(r'```(\w+)?\n(.*?)```', content, flags=re.DOTALL):
        id_ = '{{' + str(uuid.uuid4()) + '}}'
        codes[id_] = (mat.group(1), mat.group(2)) #语言标识，代码块
        content = content.replace(mat.group(0), id_)

    #行内代码 (`code`)
    content = re.sub(r'`([^`]+)`', r'<code>\1</code>', content)

    content = mdTableToHtml(content)

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

    #链接 [text](url)
    content = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', content)
    
    #段落 (保持换行)
    content = re.sub(r'([^\n]+)', r'<div>\1</div>', content)

    #恢复多行代码块，Kindle不支持div边框，所以在代码块外套一个table，使用table的外框
    tpl = ('<table border="1" cellspacing="0" width="100%" style="background-color:#f9f9f9;">'
        '<tr><td><pre><code class="{lang}">{code}</code></pre></td></tr></table>')
    for id_, (lang, code) in codes.items():
        code = code.replace(' ', '&nbsp;')
        content = content.replace(id_, tpl.format(lang=lang, code=code))
        
    return content

#markdown里面的表格转换为html格式的表格
def mdTableToHtml(content):
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
def parseRange(txt):
    ret = []
    arr = [item.split('-', 1) for item in txt.replace(' ', '').split(',')]
    for item in arr:
        if len(item) >= 2:
            if item[0].isdigit() and item[1].isdigit():
                ret.extend(range(int(item[0]), int(item[1]) + 1))
        elif item[0].isdigit():
            ret.append(int(item[0]))
    return ret

#显示菜单，根据用户选择进行相应的处理
def showMenu():
    global g_currTopic, g_messages, g_history
    print('')
    sprint(' Current conversation ', fg='white', bg='yellow', bold=True)
    print(g_currTopic)
    print('')
    sprint(' Previous conversations ', fg='white', bg='yellow', bold=True)
    if not g_history:
        sprint('No previous conversations found!', fg='bright_black')
    else:
        for idx, item in enumerate(g_history, 1):
            sprint('{:2d}. {}'.format(idx, item.get('topic', 'Unknown topic'), fg='bright_black'))

    print('')
    sprint(' Choose a conversation to continue, OR ', fg='white', bg='yellow', bold=True)
    print('[   0   ] {}'.format(style('start a new conversation', fg='bright_black')))
    print('[ dnum  ] {}'.format(style('delete one or range conversations', fg='bright_black')))
    print('[ enum  ] {}'.format(style('export one or range conversations', fg='bright_black')))
    print('[ Enter ] {}'.format(style('return to the current one', fg='bright_black')))
    while True:
        userInput = input('» ')
        inputLen = len(userInput)
        if not userInput:
            replayConversation()
            break
        elif userInput == 'q':
            return 'quit'
        elif inputLen > 1 and userInput[0] == 'd' and userInput[1].isdigit(): #删除历史数据
            deleteHistory(parseRange(userInput[1:]))
            saveHistory(g_history)
            return 'reshow'
        elif inputLen > 1 and userInput[0] == 'e' and userInput[1].isdigit(): #将历史数据导出为电子书
            expName = input(f'Filename: ')
            if expName:
                exportHistory(expName, parseRange(userInput[1:]))
            else:
                print('The filename is empty, canceled')
        elif userInput == '0': #开始一个新的对话
            if g_currTopic != DEFAULT_TOPIC:
                addHistory(g_currTopic, g_messages)
            g_messages = g_messages[:1] #第一个元素是背景信息
            g_currTopic = DEFAULT_TOPIC
            g_tokenNum = 0
            print(' NEW CONVERSATION STARTED')
            printChatBubble('user', g_currTopic)
            break
        elif userInput.isdigit(): #切换到其他对话
            index = int(userInput) #根据用户直觉，这个数值从1开始
            if 1 <= index <= len(g_history):
                msg = g_history.pop(index - 1)
                if g_currTopic != DEFAULT_TOPIC:
                    addHistory(g_currTopic, g_messages)
                g_messages = g_messages[:1] + msg.get('messages', [])
                g_currTopic = msg.get('topic', DEFAULT_TOPIC)
                g_tokenNum = calculateToken(g_messages)
                replayConversation()
                break
            else:
                print('The conversation number is out of range')

#重新输出对话信息，用于切换对话历史
def replayConversation():
    global g_currTopic, g_messages
    for item in g_messages[1:]:
        if item.get('role') == 'user':
            printUserMessage(item.get('content'))
        else:
            printAiResponse(AiResponse(success=True, content=item.get('content')))
    
    printChatBubble('user', g_currTopic)

#打印用户输入内容
def printUserMessage(content):
    global g_currTopic
    printChatBubble('user', g_currTopic)
    for line in (e for e in content.splitlines() if e):
        print(f'» {line}')

#打印AI返回的内容
def printAiResponse(resp):
    global g_config
    printChatBubble('assistant')
    if resp.success:
        disStyle = g_config.get('display_style', 'markdown')
        content = resp.content if disStyle == 'plaintext' else markdownToTerm(resp.content)
        print(content.replace('\n\n', '\n').strip('\n'))
    else:
        sprint('Error: ', fg='red', bold=True)
        print(resp.error)

#简单的处理markdown格式，用于在终端显示粗体斜体等效果
def markdownToTerm(content):
    global g_config
    #标题 (# 或 ## 等)，使用粗体
    content = re.sub(r'^(#{1,6})\s+?(.*)$', r'\033[1m\2\033[0m', content, flags=re.MULTILINE)
    
    #加粗 (**bold** 或 __bold__)
    content = re.sub(r'(\*\*|__)(.*?)\1', r'\033[1m\2\033[0m', content)
    
    #斜体 (*italic* 或 _italic_)
    content = re.sub(r'(\*|_)(.*?)\1', r'\033[3m\2\033[0m', content)

    #删除线 (~~text~~), 大部分的终端不支持删除线，先取消此功能
    #content = re.sub(r'(~{1,2})(.*?)\1', r'\033[9m\2\033[0m', content)

    #列表项或序号加粗
    content = re.sub(r'^( *)(\* |\+ |- |[1-9]+\. )(.*)$', r'\1\033[1m\2\033[0m\3', content, flags=re.MULTILINE)

    #引用行变灰
    content = re.sub(r'^( *>+ .*)$', r'\033[90m\1\033[0m', content, flags=re.MULTILINE)
    
    #删除代码块提示行，保留代码块内容
    content = re.sub(r'^ *```.*$', '', content, flags=re.MULTILINE)

    #行内代码加粗
    content = re.sub(r'(`)(.*?)\1', r'\033[1m\2\033[0m', content)

    if g_config.get('display_style', 'markdown') == 'markdown_table':
        content = mdTableToTerm(content)
    return content

#处理markdown文本里面的表格，排版对齐以便显示在终端上
#在电脑上效果还可以
#但在kindle实测效果不好，因为kindle屏幕太小，排版容易乱
def mdTableToTerm(content):
    #假定里面只有一个表格
    colWidths = []
    colNums = []
    table = []
    prevTableRowIdx = -1
    lines = content.splitlines()
    for idx, row in enumerate(lines):
        if row.startswith('|') and row.endswith('|'):
            #必须要连续
            if prevTableRowIdx >= 0 and prevTableRowIdx + 1 != idx:
                colNums = []
                break
            prevTableRowIdx = idx
            rowArr = [cell.strip() for cell in row.strip('|').split('|')]
            table.append(rowArr)
            colWidths.append([len(cell) for cell in rowArr]) #当前行每列的宽度
            colNums.append(len(rowArr))
        else:
            table.append(row)
    
    #有一些列数不同，为了避免排版混乱，直接返回原结果
    if not colNums or any(x != colNums[0] for x in colNums):
        return '\n'.join(lines)

    colMaxWidths = [max(row[i] for row in colWidths) for i in range(colNums[0])] #每列的最大长度

    def format_row(row, bold=False):
        return " | ".join(style(cell.ljust(width), bold=bold) for cell, width in zip(row, colMaxWidths))
    
    rowIdx = 0
    for idx in range(len(table)):
        row = table[idx]
        if isinstance(row, list):
            if rowIdx == 0: #表头
                table[idx] = f'| {format_row(row, bold=True)} |'
            elif all(not cell.strip('-') for cell in row): #分割线
                table[idx] = '| ' + "-+-".join('-' * width for width in colMaxWidths) + ' |'
            else: #内容行
                table[idx] = f'| {format_row(row)} |'
            rowIdx += 1
    return '\n'.join(table)

#在终端打印对话泡泡，显示角色和对话主题
def printChatBubble(role, topic=''):
    if role == 'user':
        role, fg, bg, bubFg = ' YOU ', 'white', 'green', 'bright_black'
        topic = f' ({topic})' if topic else ''
    else:
        role, fg, bg, bubFg = ' AI ', 'white', 'cyan', 'bright_black'
        topic = ''
    #暂时不打印 ╞ ╡，避免不同的终端字体不同而不对齐
    txt = " {}{} ".format(style(role, fg=fg, bg=bg, bold=True), style(topic, fg=bubFg))
    charCnt = len(role) + len(topic) + 2
    sprint('\n╭{}╮'.format('─' * charCnt), fg=bubFg)
    print(txt)
    sprint('╰{}╯'.format('─' * charCnt), fg=bubFg)

#更新谈话主题
def updateTopic(client, msg=None):
    global g_currTopic
    if msg:
        words = msg.replace('\n', ' ').replace('"', ' ').replace("'", ' ').split(' ')[:4]
        g_currTopic = ' '.join(words)[:30].strip() #限制总长度不超过30字节
    else: #让AI总结
        messages = g_messages + [{"role": "user", "content": PROMPT_GET_TOPIC}]
        resp = fetchAiResponse(client, messages)
        if resp.success:
            g_currTopic = resp.content.replace('`', '').replace('"', '').replace('\n', '')

#给AI发请求，返回 AiResponse
def fetchAiResponse(client, messages):
    try:
        respTxt = client.chat(messages)
    except:
        return AiResponse(success=False, error=loc_exc_pos('Error'))
    else:
        return AiResponse(success=True, content=respTxt)

#统计当前的token数，（现在计算的是字节数）
def calculateToken(messages):
    return sum(len(item['content']) for item in messages[1:])

#添加一条消息到消息历史
def addMessage(role, msg):
    global g_tokenNum, g_config
    g_messages.append({"role": role, "content": msg})
    g_tokenNum += len(msg)
    limit = g_config.get("token_limit", 4000)
    if g_tokenNum > limit:
        while (g_tokenNum := calculateToken(g_messages)) > limit:
            g_messages.pop(1)

#主程序入口
#cfgFile: json配置文件名，为空则使用默认值
def start(cfgFile):
    global g_currTopic, g_messages, g_history
    
    #获取配置数据
    cfg = getConfig(cfgFile)
    apiKey = cfg.get('api_key')
    if not apiKey:
        print('')
        sprint('Api key is missing', bold=True)
        sprint('Set it in the config file or run with the -s option', bold=True)
        return

    provider = cfg.get('provider')
    model = cfg.get('model')
    client = SimpleAiProvider(provider, apiKey=apiKey, model=model, apiHost=cfg.get('api_host'))
    print('Model: {}'.format(style(f'{provider}/{model}', bold=True)))
    print('Empty line to send, ? to menu, q to quit')
    
    g_history = getHistory()
    g_currTopic = DEFAULT_TOPIC
    #role 有三种可能值：system, user, assistant
    g_messages = [{"role": "system", "content": cfg.get('custom_instructions') or CUSTOM_INSTRUCTIONS}]
    quitRequested = False
    while not quitRequested:
        msgArr = []
        printChatBubble('user', g_currTopic)
        while not quitRequested:
            sys.stdin.flush()
            userInput = input("» ")
            if userInput == 'q':
                quitRequested = True
                break
            elif userInput == '?':
                msgArr = []
                ret = 'reshow'
                while ret == 'reshow':
                    ret = showMenu()
                if ret == 'quit':
                    quitRequested = True
                    break
            elif userInput: #可以输入多行，逐行累加
                msgArr.append(userInput)
            elif msgArr: #输入一个空行并且之前已经有过输入，发送请求
                msg = '\n'.join(msgArr)
                msgArr = []
                addMessage('user', msg)
                if len(g_messages) == 2: #第一次交谈，使用用户输出的开头四个单词做为topic
                    updateTopic(client, msg)
                elif len(g_messages) == 4: #第三次交谈，使用ai总结谈话内容做为topic
                    updateTopic(client)
                resp = fetchAiResponse(client, g_messages)
                addMessage('assistant', resp.content.strip() if resp.success else ('Error: ' + resp.error))
                printAiResponse(resp)
                printChatBubble('user', g_currTopic)

    #保存当前记录
    if g_currTopic != DEFAULT_TOPIC:
        addHistory(g_currTopic, g_messages)

#交互式配置过程
def setup(cfgFile):
    cfg = {}
    providers = list(AI_LIST.keys())
    print('')
    sprint(' Providers ', fg='white', bg='yellow', bold=True)
    print('\n'.join(f'{idx:2d}. {item}' for idx, item in enumerate(providers, 1)))
    models = []
    while True:
        if '1' <= (userInput := input('» ')) <= str(len(providers)):
            cfg['provider'] = provider = providers[int(userInput) - 1]
            models = [item['name'] for item in AI_LIST[provider]['models']]
            break

    #模型
    print('')
    sprint(' Models ', fg='white', bg='yellow', bold=True)
    print('\n'.join(f'{idx:2d}. {item}' for idx, item in enumerate(models, 1)))
    while True:
        if '1' <= (userInput := input('» ')) <= str(len(models)):
            cfg['model'] = models[int(userInput) - 1]
            break

    #Api key
    print('')
    sprint(' Api key ', fg='white', bg='yellow', bold=True)
    while True:
        if userInput := input('» '):
            cfg['api_key'] = userInput
            break

    #Api host
    print('')
    sprint(' Api host (optional) ', fg='white', bg='yellow', bold=True)
    userInput = input('» ')
    cfg['api_host'] = userInput

    #Display style
    print('')
    sprint(' Display style ', fg='white', bg='yellow', bold=True)
    styles = ['markdown', 'markdown_table', 'plaintext']
    print('\n'.join(f'{idx:2d}. {item}' for idx, item in enumerate(styles, 1)))
    while True:
        if '1' <= (userInput := input('» ')) <= str(len(styles)):
            cfg['display_style'] = styles[int(userInput) - 1]
            break
    
    #Conversation token limit
    print('')
    sprint(' Conversation token limit ', fg='white', bg='yellow', bold=True)
    while True:
        userInput = input('» [4000] ') or '4000'
        if userInput.isdigit():
            cfg['token_limit'] = int(userInput)
            if cfg['token_limit'] < 1000:
                cfg['token_limit'] = 1000
            break
    
    #Max history
    print('')
    sprint(' Max history ', fg='white', bg='yellow', bold=True)
    while True:
        userInput = input('» [10] ') or '10'
        if userInput.isdigit():
            cfg['max_history'] = int(userInput)
            break

    #custom_instructions
    print('')
    sprint(' Custom instructions (optional) ', fg='white', bg='yellow', bold=True)
    userInput = input('» ')
    cfg['custom_instructions'] = userInput.replace('\\n', '\n')

    try:
        with open(cfgFile, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print('Failed to write {}: {}\n'.format(style(cfgFile, bold=True), str(e)))
    else:
        print('Setup finished, config file: {}\n'.format(style(cfgFile, bold=True)))

#------------------------------------------------------------
#开始为AI适配器类
#------------------------------------------------------------
#支持的AI服务商列表，models里面的第一项请设置为默认要使用的model
#rpm(requests per minute)是针对免费用户的，如果是付费用户，一般会高很多，可以自己修改
#大语言模型发展迅速，估计没多久这些数据会全部过时
AI_LIST = {
    'google': {'host': 'generativelanguage.googleapis.com', 'models': [
        {'name': 'gemini-1.5-flash', 'rpm': 15, 'context': 128000}, #其实支持100万
        {'name': 'gemini-1.5-flash-8b', 'rpm': 15, 'context': 128000}, 
        {'name': 'gemini-1.5-pro', 'rpm': 2, 'context': 128000},],},
    'openai': {'host': 'api.openai.com', 'models': [
        {'name': 'gpt-4o-mini', 'rpm': 3, 'context': 128000},
        {'name': 'gpt-4o', 'rpm': 3, 'context': 128000},
        {'name': 'gpt-4-turbo', 'rpm': 3, 'context': 128000},
        {'name': 'gpt-3.5-turbo', 'rpm': 3, 'context': 16000},
        {'name': 'gpt-3.5-turbo-instruct', 'rpm': 3, 'context': 4000},],},
    'anthropic': {'host': 'api.anthropic.com', 'models': [
        {'name': 'claude-2', 'rpm': 5, 'context': 100000},
        {'name': 'claude-3', 'rpm': 5, 'context': 200000},
        {'name': 'claude-2.1', 'rpm': 5, 'context': 100000},],},
    'xai': {'host': 'api.x.ai', 'models': [
        {'name': 'grok-beta', 'rpm': 60, 'context': 128000},],},
    'mistral': {'host': 'api.mistral.ai', 'models': [
        {'name': 'open-mistral-7b', 'rpm': 60, 'context': 32000},
        {'name': 'mistral-small-latest', 'rpm': 60, 'context': 32000},
        {'name': 'open-mixtral-8x7b', 'rpm': 60, 'context': 32000},
        {'name': 'open-mixtral-8x22b', 'rpm': 60, 'context': 64000},
        {'name': 'mistral-medium-latest', 'rpm': 60, 'context': 32000},
        {'name': 'mistral-large-latest', 'rpm': 60, 'context': 128000},
        {'name': 'pixtral-12b-2409', 'rpm': 60, 'context': 128000},],},
    'groq': {'host': 'api.groq.com', 'models': [
        {'name': 'gemma2-9b-it', 'rpm': 30, 'context': 8000},
        {'name': 'gemma-7b-it', 'rpm': 30, 'context': 8000},
        {'name': 'llama-guard-3-8b', 'rpm': 30, 'context': 8000},
        {'name': 'llama3-70b-8192', 'rpm': 30, 'context': 8000},
        {'name': 'llama3-8b-8192', 'rpm': 30, 'context': 8000},
        {'name': 'mixtral-8x7b-32768', 'rpm': 30, 'context': 32000},],},
    'alibaba': {'host': 'dashscope.aliyuncs.com', 'models': [
        {'name': 'qwen-turbo', 'rpm': 60, 'context': 128000}, #其实支持100万
        {'name': 'qwen-plus', 'rpm': 60, 'context': 128000},
        {'name': 'qwen-long', 'rpm': 60, 'context': 128000},
        {'name': 'qwen-max', 'rpm': 60, 'context': 32000},],},
}

#自定义HTTP响应错误异常
class HttpResponseError(Exception):
    def __init__(self, status, reason, body=None):
        super().__init__(f"HTTP {status}: {reason}")
        self.status = status
        self.reason = reason
        self.body = body

class SimpleAiProvider:
    #name: AI提供商的名字
    def __init__(self, name, apiKey, model=None, apiHost=None):
        name = name.lower()
        if name not in AI_LIST:
            raise ValueError(f"Unsupported provider: {name}")
        self.name = name
        self.apiKey = apiKey
        
        index = 0
        for idx, item in enumerate(AI_LIST[name]['models']):
            if model == item['name']:
                index = idx
                break
        
        item = AI_LIST[name]['models'][index]
        self.model = item['name']
        self.rpm = item['rpm']
        self.context_size = item['context']
        if self.rpm <= 0:
            self.rpm = 2
        if self.context_size < 4000:
            self.context_size = 4000
        self.apiHost = apiHost
        self.conn = None
        self.createConnection()

    #创建长连接
    def createConnection(self):
        host = self.apiHost or AI_LIST[self.name]['host']
        if (index := host.find('//')) >= 0:
            host = host[index + 2:]
        if (index := host.find('/')) > 0:
            host = host[:index]
        self.close()
        self.conn = http.client.HTTPSConnection(host, timeout=30, context=ssl._create_unverified_context())

    #发起一个网络请求，返回json数据
    def post(self, path, payload, headers, toJson=True):
        retried = 0
        while retried < 2:
            try:
                self.conn.request("POST", path, json.dumps(payload), headers)
                resp = self.conn.getresponse()
                body = resp.read().decode("utf-8")
                if not (200 <= resp.status < 300):
                    raise HttpResponseError(resp.status, resp.reason, body)
                return json.loads(body) if toJson else body
            except (http.client.CannotSendRequest, http.client.RemoteDisconnected) as e:
                if retried:
                    raise
                print("Connection issue, retrying:", e)
                self.createConnection()
                retried += 1

    #关闭连接
    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def __repr__(self):
        return f'{self.name}({self.model})'

    #外部调用此函数即可调用简单聊天功能
    #message: 如果是文本，则使用各项默认参数
    #传入 list/dict 可以定制 role 等参数
    def chat(self, message):
        if not self.apiKey:
            raise ValueError(f'The api key is empty')
        name = self.name
        if name == "openai":
            return self._openai_chat(message)
        elif name == "anthropic":
            return self._anthropic_chat(message)
        elif name == "google":
            return self._gemini_chat(message)
        elif name == "xai":
            return self._grok_chat(message)
        elif name == "mistral":
            return self._mistral_chat(message)
        elif name == 'groq':
            return self._groq_chat(message)
        elif name == "alibaba":
            return self._alibaba_chat(message)
        else:
            raise ValueError(f"Unsupported provider: {name}")

    #openai的chat接口
    def _openai_chat(self, message, path='/v1/chat/completions'):
        headers = {'Authorization': f"Bearer {self.apiKey}",
            'Content-Type': 'application/json'}
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": message}] if isinstance(message, str) else message
        }
        data = self.post(path, payload, headers)
        return data["choices"][0]["message"]["content"]

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
            payload = {"prompt": prompt, "model": self.model, "max_tokens_to_sample": 256}
        elif isinstance(message, dict):
            payload = message
        else:
            prompt = f"\n\nHuman: {message}\n\nAssistant:"
            payload = {"prompt": prompt, "model": self.model, "max_tokens_to_sample": 256}
        
        data = self.post('/v1/complete', payload, headers)
        return data["completion"]

    #gemini的chat接口
    def _gemini_chat(self, message):
        url = f'/v1beta/models/{self.model}:generateContent?key={self.apiKey}'
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
        data = self.post(url, payload, headers)
        contents = data["candidates"][0]["content"]
        return contents['parts'][0]['text']

    #grok的chat接口
    def _grok_chat(self, message):
        return self._openai_chat(message, path='/v1/chat/completions')

    #mistral的chat接口
    def _mistral_chat(self, message):
        return self._openai_chat(message, path='/v1/chat/completions')

    #groq的chat接口
    def _groq_chat(self, message):
        return self._openai_chat(message, path='/openai/v1/chat/completions')

    #通义千问
    def _alibaba_chat(self, message):
        return self._openai_chat(message, path='/compatible-mode/v1/chat/completions')

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
    else:
        cfgFile = cfgFile or CONFIG_JSON
        if args.setup:
            setup(cfgFile)

        start(cfgFile)