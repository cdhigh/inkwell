// inkwell.go: Kindle终端AI助手（Go语言移植版）
// Author: cdhigh <https://github.com/cdhigh>
// 目标：兼容 ARM v5/v6/v7，不依赖第三方库
package main

import (
	"bufio"
	"bytes"
	"maps"
	"slices"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"crypto/tls"
	"encoding/base64"
	"flag"
	"io"
	"math/rand"
	"net/smtp"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// 所有常量定义
const (
	Version       = "v1.7-go (2025-09-26)"
	ConfigFile    = "config.json"
	HistoryFile   = "history.json"
	PromptsFile   = "prompts.txt"
	KindleDocDir  = "/mnt/us/documents"
	ClippingsTxt  = "My Clippings.txt"
	DefaultTopic  = "New chat"
	DefaultPrompt = `You are a helpful assistant.
- You are to provide clear, concise, and direct responses.
- Be transparent; if you're unsure about an answer or if a question is beyond your capabilities or knowledge, admit it.
- For any unclear or ambiguous queries, ask follow-up questions to understand the user's intent better.
- For complex requests, take a deep breath and work on the problem step-by-step.
- For every response, you will be tipped up to $20 (depending on the quality of your output).
- Keep responses concise and formatted for terminal output.
- Use Markdown for formatting.`

	// 让AI总结此次谈话主题的prompt
	PromptGetTopic = `Please give this conversation a short title.
- Hard limit of 5 words.
- Don't mention yourself in it.
- Don't use any special characters.
- Don't use any capital letters.
- Don't use any punctuation.
- Don't use any symbols.
- Don't use any emojis.
- Don't use any accents.
- Don't use quotes.`

	PromptClips = `I have a few excerpts from my readings. Please analyze them. I may have follow-up questions based on them.
Clippings:
{clips}
{question}`

	DefaultConfigFile = "config.json" // 默认配置文件名

	// 默认配置模板
	DefaultConfig = `{
	"provider": "google",
	"model": "gemini-1.5-flash",
	"api_key": "",
	"api_host": "",
	"display_style": "markdown",
	"chat_type": "multi_turn",
	"token_limit": 4000,
	"max_history": 10,
	"prompt": "default",
	"custom_prompt": "",
	"smtp_sender": "",
	"smtp_host": "",
	"smtp_username": "",
    "smtp_password": "",
    "renew_api_key": ""
}`
)

// 终端颜色代码表
var terminalColors = map[string]int{
	"black":          30,
	"red":            31,
	"green":          32,
	"yellow":         33,
	"blue":           34,
	"magenta":        35,
	"cyan":           36,
	"white":          37,
	"reset":          39,
	"bright_black":   90,
	"bright_red":     91,
	"bright_green":   92,
	"bright_yellow":  93,
	"bright_blue":    94,
	"bright_magenta": 95,
	"bright_cyan":    96,
	"bright_white":   97,
	"grey":           90,
}

// 类型定义，表示带终端格式化的字符串
type Styled string

// AI响应结构体
// 用于封装AI接口的响应
// Success: 是否成功
// Content: 返回内容
// Error: 错误信息
// Host: 当前API主机
type AiResponse struct {
	Content string
	Error   string
	Host    string
	Success bool
}

// 配置文件结构
type AppConfig struct {
	Provider     string `json:"provider"`
	Model        string `json:"model"`
	ApiKey       string `json:"api_key"`
	ApiHost      string `json:"api_host"`
	DisplayStyle string `json:"display_style"`
	ChatType     string `json:"chat_type"`
	Prompt       string `json:"prompt"`
	CustomPrompt string `json:"custom_prompt"`
	SmtpSender   string `json:"smtp_sender"`
	SmtpHost     string `json:"smtp_host"`
	SmtpUsername string `json:"smtp_username"`
	SmtpPassword string `json:"smtp_password"`
	RenewApiKey  string `json:"renew_api_key"`
	TokenLimit   int    `json:"token_limit"`
	MaxHistory   int    `json:"max_history"`
}

// 表示每一条信息
type ChatItem struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

// 历史聊天会话的一个条目
type HistoryItem struct {
	Topic    string     `json:"topic"`
	Prompt   string     `json:"prompt"`
	Messages []ChatItem `json:"messages"`
}

// 主结构体
// 管理配置、历史、对话、菜单、AI请求等
type InkWell struct {
	CfgFile    string
	CurrTopic  string
	PromptName string
	History    []HistoryItem
	Messages   []ChatItem
	Config     *AppConfig
	Provider   *SimpleAiProvider
	Prompts    map[string]string
}

// AI模型结构体
type AiModel struct {
	Name    string
	Rpm     int
	Context int
}

// AI服务商信息结构体
type AiProviderInfo struct {
	Host   string
	Models []AiModel
}

// AI服务商列表
var AIList = map[string]AiProviderInfo{
	"openai": {
		Host: "https://api.openai.com",
		Models: []AiModel{
			{Name: "gpt-5-mini", Rpm: 500, Context: 200000},
			{Name: "gpt-5", Rpm: 500, Context: 200000},
			{Name: "gpt-5-nano", Rpm: 500, Context: 200000},
			{Name: "gpt-4.1", Rpm: 1000, Context: 128000},
			{Name: "gpt-4.1-mini", Rpm: 500, Context: 128000},
			{Name: "gpt-4.1-nano", Rpm: 500, Context: 1000000},
			{Name: "gpt-4-turbo", Rpm: 500, Context: 128000},
			{Name: "gpt-3.5-turbo", Rpm: 3500, Context: 16000},
		},
	},
	"google": {
		Host: "https://generativelanguage.googleapis.com",
		Models: []AiModel{
			{Name: "gemini-1.5-flash", Rpm: 15, Context: 128000},
			{Name: "gemini-1.5-flash-8b", Rpm: 15, Context: 128000},
			{Name: "gemini-1.5-pro", Rpm: 2, Context: 128000},
			{Name: "gemini-2.0-flash", Rpm: 15, Context: 128000},
			{Name: "gemini-2.0-flash-lite", Rpm: 30, Context: 128000},
			{Name: "gemini-2.0-flash-thinking", Rpm: 10, Context: 128000},
			{Name: "gemini-2.0-pro", Rpm: 5, Context: 128000},
		},
	},
	"anthropic": {
		Host: "https://api.anthropic.com",
		Models: []AiModel{
			{Name: "claude-2", Rpm: 60, Context: 100000},
			{Name: "claude-3", Rpm: 60, Context: 200000},
			{Name: "claude-opus-4-0", Rpm: 60, Context: 200000},
			{Name: "claude-sonnet-4-0", Rpm: 60, Context: 200000},
			{Name: "claude-3-7-sonnet-latest", Rpm: 60, Context: 200000},
			{Name: "claude-3-5-sonnet-latest", Rpm: 60, Context: 200000},
			{Name: "claude-3-5-haiku-latest", Rpm: 60, Context: 200000},
			{Name: "claude-2.1", Rpm: 60, Context: 100000},
		},
	},
	"xai": {
		Host: "https://api.x.ai",
		Models: []AiModel{
			{Name: "grok-1", Rpm: 60, Context: 128000},
			{Name: "grok-2", Rpm: 60, Context: 128000},
			{Name: "grok-3", Rpm: 60, Context: 128000},
			{Name: "grok-4", Rpm: 60, Context: 128000},
		},
	},
	"mistral": {
		Host: "https://api.mistral.ai",
		Models: []AiModel{
			{Name: "open-mistral-7b", Rpm: 60, Context: 32000},
			{Name: "mistral-small-latest", Rpm: 60, Context: 32000},
			{Name: "open-mixtral-8x7b", Rpm: 60, Context: 32000},
			{Name: "open-mixtral-8x22b", Rpm: 60, Context: 64000},
			{Name: "mistral-medium-latest", Rpm: 60, Context: 32000},
			{Name: "mistral-large-latest", Rpm: 60, Context: 128000},
			{Name: "pixtral-12b-2409", Rpm: 60, Context: 128000},
			{Name: "codestral-2501", Rpm: 60, Context: 256000},
		},
	},
	"groq": {
		Host: "https://api.groq.com",
		Models: []AiModel{
			{Name: "gemma2-9b-it", Rpm: 30, Context: 8000},
			{Name: "gemma-7b-it", Rpm: 30, Context: 8000},
			{Name: "llama-guard-3-8b", Rpm: 30, Context: 8000},
			{Name: "llama3-70b-8192", Rpm: 30, Context: 8000},
			{Name: "llama3-8b-8192", Rpm: 30, Context: 8000},
			{Name: "mixtral-8x7b-32768", Rpm: 30, Context: 32000},
		},
	},
	"perplexity": {
		Host: "https://api.perplexity.ai",
		Models: []AiModel{
			{Name: "llama-3.1-sonar-small-128k-online", Rpm: 60, Context: 128000},
			{Name: "llama-3.1-sonar-large-128k-online", Rpm: 60, Context: 128000},
			{Name: "llama-3.1-sonar-huge-128k-online", Rpm: 60, Context: 128000},
		},
	},
	"alibaba": {
		Host: "https://dashscope.aliyuncs.com",
		Models: []AiModel{
			{Name: "qwen-turbo", Rpm: 60, Context: 128000},
			{Name: "qwen-plus", Rpm: 60, Context: 128000},
			{Name: "qwen-long", Rpm: 60, Context: 128000},
			{Name: "qwen-max", Rpm: 60, Context: 32000},
		},
	},
}

// AI服务商实例 结构体
type SimpleAiProvider struct {
	Name        string
	Model       string
	ApiKeys     []string
	Hosts       []string
	Client      *http.Client
	ApiKeyIdx   int
	HostIdx     int
	Rpm         int
	ContextSize int
	SingleTurn  bool
}

// 用来获取终端输入的对象，全局创建，节省资源
// 不使用并发，所以不需要锁
var stdinScanner = bufio.NewScanner(os.Stdin)

// 以下是几个格式化字符串为终端显示的函数
// fg: 前景色, bg: 背景色，bold: 粗体，dim: 暗淡，underline: 下划线
// overline: 上划线，italic: 斜体, blink: 闪烁, reverse: 反色
// strikethrough: 删除线, noResetFg/noResetBg: 不重置（保持状态，无\033[0m）
func (s Styled) Fg(color string) Styled {
	return Styled("\033[" + interpretColor(color, 0) + "m" + string(s) + "\033[0m")
}
func (s Styled) Bg(color string) Styled {
	return Styled("\033[" + interpretColor(color, 10) + "m" + string(s) + "\033[0m")
}
func (s Styled) Bold() Styled {
	return Styled("\033[1m" + string(s) + "\033[0m")
}
func (s Styled) Dim() Styled {
	return Styled("\033[2m" + string(s) + "\033[0m")
}
func (s Styled) Underline() Styled {
	return Styled("\033[4m" + string(s) + "\033[0m")
}
func (s Styled) Overline() Styled {
	return Styled("\033[53m" + string(s) + "\033[0m")
}
func (s Styled) Italic() Styled {
	return Styled("\033[3m" + string(s) + "\033[0m")
}
func (s Styled) Blink() Styled {
	return Styled("\033[5m" + string(s) + "\033[0m")
}
func (s Styled) Reverse() Styled {
	return Styled("\033[7m" + string(s) + "\033[0m")
}
func (s Styled) Strikethrough() Styled {
	return Styled("\033[9m" + string(s) + "\033[0m")
}
func (s Styled) NoResetFg(color string) Styled {
	return Styled("\033[" + interpretColor(color, 0) + "m" + string(s))
}
func (s Styled) NoResetBg(color string) Styled {
	return Styled("\033[" + interpretColor(color, 10) + "m" + string(s))
}

// 打印带颜色的字符串
func (s Styled) Println() {
	fmt.Println(string(s))
}
func (s Styled) Print() {
	fmt.Print(string(s))
}
func (s Styled) Printf(args ...any) {
	fmt.Printf(string(s), args...)
}
func (s Styled) Sprintf(args ...any) Styled {
	return Styled(fmt.Sprintf(string(s), args...))
}
func (s Styled) Add(other Styled) Styled {
	return Styled(string(s) + string(other))
}

// 工具函数：颜色解释
func interpretColor(color string, offset int) string {
	code, ok := terminalColors[color]
	if !ok {
		code = 30 // 默认黑色
	}
	return strconv.Itoa(code + offset)
}

// 读取配置文件，若不存在则创建默认配置
// 加载成功返回true
func (iw *InkWell) LoadConfig() bool {
	var cfg AppConfig
	if !fileExists(iw.CfgFile) {
		fmt.Println("")
		Styled("The file %s does not exist").Bold().Printf(iw.CfgFile)
		Styled("Creating a default configuration file with this name...").Bold().Println()
		os.WriteFile(iw.CfgFile, []byte(DefaultConfig), 0644)
		fmt.Println("Edit the file manually or run with the -s option to complete the setup")
		return false
	}

	b, err := os.ReadFile(iw.CfgFile)
	if err != nil {
		return false
	}
	err = json.Unmarshal(b, &cfg)
	if err != nil {
		Styled("Failed to parse config file: %s").Fg("red").Printf(err.Error())
		return false
	}

	// 校验 provider/model 等
	info, ok := AIList[cfg.Provider]
	if !ok {
		cfg.Provider = "google"
		info = AIList["google"]
	}

	if cfg.Model == "" && len(info.Models) > 0 {
		cfg.Model = info.Models[0].Name
	}
	
	if cfg.TokenLimit < 1000 {
		cfg.TokenLimit = 1000
	}

	ds := cfg.DisplayStyle
	if ds != "plaintext" && ds != "markdown" && ds != "markdown_table" {
		cfg.DisplayStyle = "markdown"
	}
	iw.Config = &cfg
	return true
}

// 保存配置到文件
// cfg 如果为nil，则使用iw实例的配置对象
func (iw *InkWell) SaveConfig(cfg *AppConfig) {
	if cfg == nil {
		cfg = iw.Config
	}
	b, _ := json.MarshalIndent(cfg, "", "  ")
	err := os.WriteFile(iw.CfgFile, b, 0644)
	if err != nil {
		Styled("Failed to write %s: %v\n").Printf(Styled(iw.CfgFile).Bold(), err)
	} else {
		Styled("Config have been saved to file: %s\n").Printf(Styled(iw.CfgFile).Bold())
	}
}

// 加载历史记录
func (iw *InkWell) LoadHistory() {
	iw.History = []HistoryItem{}
	if iw.Config == nil || iw.Config.MaxHistory <= 0 { // 禁用了历史对话功能
		return
	}

	hisFile := filepath.Join(filepath.Dir(iw.CfgFile), HistoryFile)
	data, err := os.ReadFile(hisFile)
	if err == nil {
		json.Unmarshal(data, &iw.History)
	}
}

// 保存历史记录
func (iw *InkWell) SaveHistory() {
	path := filepath.Dir(iw.CfgFile)
	hisFile := filepath.Join(path, HistoryFile)
	b, _ := json.Marshal(iw.History)
	os.WriteFile(hisFile, b, 0644)
}

// 读取用户输入
func Input(prompt string) string {
	fmt.Print(prompt)
	if stdinScanner.Scan() {
		return stdinScanner.Text()
	}
	return ""
}

// 解析范围字符串，如 "1,3-5" -> [1,3,4,5]
func ParseRange(txt string) []int {
	var ret []int
	cleanTxt := strings.ReplaceAll(txt, " ", "")
	for _, elem := range strings.Split(cleanTxt, ",") {
		item := strings.SplitN(elem, "-", 2)
		start, end := item[0], item[0]
		if len(item) == 2 {
			end = item[1]
		}
		s, err1 := strconv.Atoi(start)
		e, err2 := strconv.Atoi(end)
		if err1 == nil && err2 == nil {
			// 不允许负数
			if s < 0 {
				s = 0
			}
			if e < 0 {
				e = 0
			}
			if e >= s {
				for i := s; i <= e; i++ {
					ret = append(ret, i)
				}
			} else { // 倒序填充，比如[5-3] -> [5,4,3]
				for i := s; i >= e; i-- {
					ret = append(ret, i)
				}
			}
		}
	}
	return ret
}

// 读取高亮或读书笔记，返回最新的前10条记录
func (iw *InkWell) ReadClippings() [][2]string {
	// 优先查找 Kindle 路径，否则查找本地
	clipPath := filepath.Join(KindleDocDir, ClippingsTxt)
	if !fileExists(clipPath) {
		clipPath = filepath.Join(filepath.Dir(iw.CfgFile), ClippingsTxt)
	}
	if !fileExists(clipPath) {
		Styled("The file %s does not exist.").Fg("red").Printf(ClippingsTxt)
		fmt.Println("")
		return nil
	}

	b, err := os.ReadFile(clipPath)
	if err != nil {
		Styled("Read clippings failed: %s").Fg("red").Printf(err.Error())
		return nil
	}

	clips := strings.Split(string(b), "==========")
	if len(clips) > 10 {
		clips = clips[len(clips)-10:]
	}
	var ret [][2]string
	for _, item := range clips { // 只显示最新摘要，最后一个为空(会被剔除)
		// 每个笔记第一行是书名；第二行使用横杠开头，竖杠分割：笔记类型/页数/位置/时间；之后为具体摘要内容
		lines := strings.SplitN(strings.TrimSpace(item), "\n", 3)
		if len(lines) < 3 {
			continue
		}
		// 书名，笔记内容
		ret = append(ret, [2]string{lines[0], strings.TrimSpace(lines[2])})
	}
	return ret
}

// 交互式配置过程
func (iw *InkWell) Setup() {
	var cfg AppConfig
	providers := slices.Collect(maps.Keys(AIList))
	Styled("Start inkwell config. Press q to abort.").Bold().Println()
	fmt.Println("")
	Styled(" Providers ").Fg("white").Bg("yellow").Bold().Println()
	for i, p := range providers {
		fmt.Printf("%2d. %s\n", i+1, p)
	}

	// 选择provider
	for {
		input := Input("» ")
		if input == "q" || input == "Q" {
			return
		}
		idx, err := strconv.Atoi(input)
		if err == nil && idx >= 1 && idx <= len(providers) {
			cfg.Provider = providers[idx-1]
			break
		}
	}

	// 选择模型
	models := []string{}
	if info, ok := AIList[cfg.Provider]; ok {
		for _, m := range info.Models {
			models = append(models, m.Name)
		}
	}
	fmt.Println("")
	Styled(" Models ").Fg("white").Bg("yellow").Println()
	for i, m := range models {
		fmt.Printf("%2d. %s\n", i+1, m)
	}
	fmt.Printf("%2d. Other\n", len(models)+1)
	for {
		input := Input("» [1] ")
		if input == "q" || input == "Q" {
			return
		} else if input == "" {
			input = "1"
		}
		idx, err := strconv.Atoi(input)
		if err == nil && idx >= 1 && idx <= len(models) {
			cfg.Model = models[idx-1]
			break
		} else if idx == len(models)+1 { // 输入不在列表中的模型名字
			custom := Input("Model Name » ")
			if custom != "" {
				cfg.Model = custom
				break
			}
		}
	}

	// API Key
	fmt.Println("")
	Styled(" Api key (semicolon-separated) ").Fg("white").Bg("yellow").Println()
	for {
		input := Input("» ")
		if input == "q" || input == "Q" {
			return
		} else if input != "" {
			cfg.ApiKey = input
			break
		}
	}

	// API Host
	fmt.Println("")
	Styled(" Api host (optional, semicolon-separated) ").Fg("white").Bg("yellow").Println()
	input := Input("» ")
	if input == "q" || input == "Q" {
		return
	}
	hosts := []string{}
	for _, e := range strings.Split(strings.ReplaceAll(input, " ", ""), ";") {
		if e != "" {
			if !strings.HasPrefix(e, "http") {
				e = "https://" + e
			}
			hosts = append(hosts, e)
		}
	}
	cfg.ApiHost = strings.Join(hosts, ";")

	// Display style
	fmt.Println("")
	Styled(" Display style ").Fg("white").Bg("yellow").Println()
	styles := []string{"markdown", "markdown_table", "plaintext"}
	for i, s := range styles {
		fmt.Printf("%2d. %s\n", i+1, s)
	}
	for {
		input := Input("» [1] ")
		if input == "q" || input == "Q" {
			return
		} else if input == "" {
			input = "1"
		}
		idx, err := strconv.Atoi(input)
		if err == nil && idx >= 1 && idx <= len(styles) {
			cfg.DisplayStyle = styles[idx-1]
			break
		}
	}

	// Chat type
	fmt.Println("")
	Styled(" Chat type ").Fg("white").Bg("yellow").Println()
	turns := []string{"multi_turn (multi-step conversations)", "single_turn (merged history as context)"}
	for i, t := range turns {
		fmt.Printf("%2d. %s\n", i+1, t)
	}
	for {
		input := Input("» [1] ")
		if input == "q" || input == "Q" {
			return
		} else if input == "" || input == "1" {
			cfg.ChatType = "multi_turn"
			break
		} else if input == "2" {
			cfg.ChatType = "single_turn"
			break
		}
	}

	// Token limit
	fmt.Println("")
	Styled(" Context token limit ").Fg("white").Bg("yellow").Println()
	for {
		input := Input("» [4000] ")
		if input == "q" || input == "Q" {
			return
		} else if input == "" {
			input = "4000"
		}
		n, err := strconv.Atoi(input)
		if err == nil {
			if n < 1000 {
				n = 1000
			}
			cfg.TokenLimit = n
			break
		}
	}

	// Max history
	fmt.Println("")
	Styled(" Max history ").Fg("white").Bg("yellow").Println()
	for {
		input := Input("» [10] ")
		if input == "q" || input == "Q" {
			return
		} else if input == "" {
			input = "10"
		}
		n, err := strconv.Atoi(input)
		if err == nil {
			cfg.MaxHistory = n
			break
		}
	}

	// Custom prompt
	fmt.Println("")
	Styled(" Custom prompt (optional) ").Fg("white").Bg("yellow").Println()
	prompts := []string{}
	for {
		line := Input("» ")
		if line == "q" || input == "Q" {
			return
		} else if line == "" {
			break
		}
		prompts = append(prompts, line)
	}
	prompt := strings.Join(prompts, "\n")
	cfg.CustomPrompt = prompt
	if prompt != "" {
		cfg.Prompt = "custom"
	} else {
		cfg.Prompt = "default"
	}

	iw.SaveConfig(&cfg)
}

// 发送AI请求
func (iw *InkWell) FetchAiResponse(messages []ChatItem) AiResponse {
	if iw.Provider == nil {
		fmt.Println("The provider is empty")
		return AiResponse{Success: false, Error: "The provider is empty", Host: ""}
	}

	return iw.Provider.Chat(messages)
}

// 打印聊天气泡
func (iw *InkWell) PrintChatBubble(role, topic string) {
	if topic != "" {
		topic = "(" + topic + ")"
	}
	bg := "cyan"
	if role == "user" {
		bg = "green"
		role = " YOU "
	} else {
		role = " AI "
	}
	charCnt := len(role) + len(topic) + 2
	fmt.Println("")
	Styled("╭" + strings.Repeat("─", charCnt) + "╮").Fg("bright_black").Println()
	fmt.Printf(" %s %s ", Styled(role).Fg("white").Bg(bg), Styled(topic).Fg("bright_black"))
	fmt.Println()
	Styled("╰" + strings.Repeat("─", charCnt) + "╯").Fg("bright_black").Println()
}

// 更新谈话主题
// 如果传入msg，则使用msg前5个单词作为主题，否则让AI进行当前对话的总结
func (iw *InkWell) UpdateTopic(msg string) {
	topic := DefaultTopic
	// 这个 replacer 每次对话最多使用两次，就不提前创建为全局变量了
	titleReplacer := strings.NewReplacer("\n", " ", "\"", " ", "/", " ", "\\", " ", "'", " ", "`", " ")
	if msg != "" {
		// 直接从消息中提取前5个单词作为主题
		words := strings.Fields(titleReplacer.Replace(msg))
		if len(words) > 5 {
			words = words[:5]
		}
		topic = strings.Join(words, " ")
	} else {
		// 让AI总结对话主题
		messages := append(iw.Messages, ChatItem{Role: "user", Content: PromptGetTopic})
		resp := iw.FetchAiResponse(messages)
		if resp.Success {
			topic = titleReplacer.Replace(resp.Content)
		}
	}
	if len(topic) > 40 {
		topic = strings.TrimSpace(topic[:40])
	}
	if topic != "" {
		iw.CurrTopic = topic
	}
}

// 主循环入口
func (iw *InkWell) Start(clippings bool) {
	cfg := iw.Config
	if cfg == nil {
		return
	}

	apiKey := cfg.ApiKey
	if apiKey == "" {
		fmt.Println("")
		Styled("Api key is missing").Bold().Println()
		Styled("Set it in the config file or run with the -s option").Bold().Println()
		fmt.Println("")
		Input("Press return key to quit ")
		return
	}

	provider := cfg.Provider // 启动后已经校验了此配置值的有效性
	model := cfg.Model
	hosts := strings.Split(cfg.ApiHost, ";")
	singleTurn := cfg.ChatType == "single_turn"
	if len(hosts) == 0 { // 使用默认host
		hosts = []string{AIList[provider].Host}
	}

	iw.Provider = &SimpleAiProvider{
		Name:       provider,
		ApiKeys:    strings.Split(apiKey, ";"),
		Model:      model,
		Hosts:      hosts,
		SingleTurn: singleTurn,
		Client:     &http.Client{Timeout: 30 * time.Second},
	}
	iw.LoadHistory()
	iw.StartNewConversation()

	fmt.Printf("Model: %s\n", Styled("%s/%s").Bold().Sprintf(provider, model))
	fmt.Printf("Prompt: %s\n", Styled(iw.PromptName).Bold())
	fmt.Printf("%s send, %s menu, %s clips, %s quit\n",
		Styled(" Enter ").Fg("white").Bg("cyan"),
		Styled(" ? ").Fg("white").Bg("cyan"),
		Styled(" c ").Fg("white").Bg("cyan"),
		Styled(" q ").Fg("white").Bg("cyan"))

	quitRequested := false
	// 如果传入k参数，直接进入选择读书摘要界面，否则显示用户聊天气泡
	if !clippings {
		iw.PrintChatBubble("user", iw.CurrTopic)
	} else if iw.SummarizeClippings() == "quit" {
		quitRequested = true
	}

	msgArr := []string{}
	for !quitRequested {
		input := Input("» ")
		switch input {
		case "q", "Q":
			quitRequested = true
			continue
		case "k", "K":
			iw.RenewApiKey()
		case "c":
			if iw.SummarizeClippings() == "quit" {
				iw.ReplayConversation() // 中断了分享读书摘要，回到原先的对话
			}
		case "?":
			msgArr = nil
			ret := "reshow"
			for ret == "reshow" {
				ret = iw.ProcessMenu()
			}
			if ret == "quit" {
				quitRequested = true
				continue
			}
		default:
			msgCnt := len(msgArr)
			// 输入r重发上一个请求
			if (input == "r" || input == "R") && msgCnt == 0 && len(iw.Messages) > 2 {
				// 开头为背景prompt，最后一个是assistant消息，-2 就是上次的用户消息
				userItem := iw.Messages[len(iw.Messages)-2]
				input = ""
				for _, line := range strings.Split(userItem.Content, "\n") {
					fmt.Printf("» %s\n", line)
				}
				fmt.Println("")
			}

			if input != "" { // 可以输入多行，逐行累加
				msgArr = append(msgArr, input)
			} else if msgCnt > 0 { // 输入一个空行并且之前已经有过输入，发送请求
				msg := strings.Join(msgArr, "\n")
				msgArr = msgArr[:0]
				iw.Messages = append(iw.Messages, ChatItem{Role: "user", Content: msg})
				if len(iw.Messages) == 2 { // 第一次交谈，使用用户输出的开头几个单词做为topic
					iw.UpdateTopic(msg)
				} else if len(iw.Messages) == 4 { // 第三次交谈，使用ai总结谈话内容做为topic
					iw.UpdateTopic("")
				}
				resp := iw.FetchAiResponse(iw.Messages)
				respText := resp.Content
				if !resp.Success {
					respText = "Error: " + resp.Error
				}
				iw.Messages = append(iw.Messages, ChatItem{Role: "assistant", Content: respText})
				iw.PrintAiResponse(resp)
				iw.PrintChatBubble("user", iw.CurrTopic)
			}
		}
	}

	iw.Provider.Close()
	iw.AddCurrentConvToHistory()
}

// 重新输出对话信息，用于切换对话历史
func (iw *InkWell) ReplayConversation() {
	messages := iw.Messages[1:]
	for i, item := range messages {
		content := item.Content
		if item.Role == "user" {
			iw.PrintChatBubble("user", iw.CurrTopic)
			for _, line := range strings.Split(content, "\n") {
				fmt.Printf("» %s\n", line)
			}
		} else {
			iw.PrintChatBubble("assistant", "")
			if iw.Config.DisplayStyle != "plaintext" {
				content = iw.MarkdownToTerm(content)
			}
			fmt.Println(strings.TrimSpace(content))
		}
		if i < len(iw.Messages[1:])-1 {
			fmt.Println()
		}
	}
	iw.PrintChatBubble("user", iw.CurrTopic)
}

// 获取当前prompt文本，并且根据情况修正Inkwell的prompt名字
func (iw *InkWell) GetPromptText() string {
	name := iw.Config.Prompt
	var prompt string
	
	if name == "custom" {
		prompt = iw.Config.CustomPrompt
	} else if name != "default" {
		prompt = iw.LoadPrompts()[name]
	}
	if prompt == "" {
		prompt = DefaultPrompt
		name = "default"
	}
	iw.PromptName = name
	return prompt
}

// 检查文件是否存在
func fileExists(filename string) bool {
	info, err := os.Stat(filename)
	if os.IsNotExist(err) {
		return false
	}
	return !info.IsDir()
}

// 获取当前用户的主目录
/*func getHomeDir() string {
	usr, err := user.Current()
	if err != nil {
		return ""
	}
	return usr.HomeDir
}*/

// 获取下一个ApiKey
func (p *SimpleAiProvider) NextApiKey() string {
	if len(p.ApiKeys) == 0 {
		return ""
	}
	key := p.ApiKeys[p.ApiKeyIdx]
	p.ApiKeyIdx = (p.ApiKeyIdx + 1) % len(p.ApiKeys)
	return key
}

// 获取下一个AI的host地址
func (p *SimpleAiProvider) NextHost() string {
	if len(p.Hosts) == 0 {
		return ""
	}
	host := p.Hosts[p.HostIdx]
	p.HostIdx = (p.HostIdx + 1) % len(p.Hosts)
	return host
}

// AI服务器的聊天接口
func (p *SimpleAiProvider) Chat(messages []ChatItem) AiResponse {
	if len(messages) == 0 {
		return AiResponse{
			Success: false,
			Error:   "Empty messages",
		}
	}
	switch p.Name {
	case "openai":
		return p.openaiChat(messages, "v1/chat/completions")
	case "google":
		return p.googleChat(messages)
	case "xai":
		return p.openaiChat(messages, "v1/chat/completions")
	case "mistral":
		return p.openaiChat(messages, "v1/chat/completions")
	case "groq":
		return p.openaiChat(messages, "openai/v1/chat/completions")
	case "perplexity":
		return p.openaiChat(messages, "chat/completions")
	case "alibaba":
		return p.openaiChat(messages, "compatible-mode/v1/chat/completions")
	default:
		return AiResponse{
			Success: false,
			Error:   fmt.Sprintf("unsupported provider: %s", p.Name),
		}
	}
}

// Openai的聊天接口
func (p *SimpleAiProvider) openaiChat(messages []ChatItem, path string) AiResponse {
	host := p.NextHost()
	if host == "" {
		host = AIList["openai"].Host
	}
	apiKey := p.NextApiKey()
	url, err := url.JoinPath(host, path)
	if err != nil {
		return AiResponse{
			Success: false,
			Error:   fmt.Sprintf("Invalid URL: %v", err),
		}
	}
	// 如果有超过一个host，则返回当前host，否则返回空
	if len(p.Hosts) == 1 {
		host = ""
	}

	// 如果是单轮对话模式，将历史对话合并为单一消息
	if p.SingleTurn && len(messages) > 1 {
		msgArr := []string{"Previous conversations:\n"}
		roleMap := map[string]string{"system": "background", "assistant": "Your response"}
		for _, item := range messages[:len(messages)-1] {
			roleText := roleMap[item.Role]
			if roleText == "" {
				roleText = "I asked"
			}
			msgArr = append(msgArr, fmt.Sprintf("%s:\n%s\n", roleText, item.Content))
		}
		msgArr = append(msgArr, "\nPlease continue this conversation based on the previous information:\n")
		msgArr = append(msgArr, "I ask:")
		msgArr = append(msgArr, messages[len(messages)-1].Content)
		msgArr = append(msgArr, "You Response:\n")
		messages = []ChatItem{{Role: "user", Content: strings.Join(msgArr, "\n")}}
	}

	payload := map[string]any{"model": p.Model, "messages": messages}
	body, err := json.Marshal(payload)
	if err != nil {
		return AiResponse{
			Success: false,
			Error:   fmt.Sprintf("Failed to marshal payload: %v", err),
		}
	}
	req, err := http.NewRequest("POST", url, bytes.NewReader(body))
	if err != nil {
		return AiResponse{
			Success: false,
			Error:   fmt.Sprintf("Failed to create request: %v", err),
		}
	}
	req.Header.Set("Authorization", "Bearer "+apiKey)
	req.Header.Set("Content-Type", "application/json")
	resp, err := p.Client.Do(req)
	if err != nil {
		return AiResponse{Success: false, Content: "", Error: err.Error(), Host: host}
	}
	defer resp.Body.Close()
	respBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		errTxt := fmt.Sprintf("HTTP %d: %s", resp.StatusCode, string(respBody))
		return AiResponse{Success: false, Content: "", Error: errTxt, Host: host}
	}
	var result struct {
		Choices []struct {
			Message struct {
				Content string `json:"content"`
			} `json:"message"`
		} `json:"choices"`
	}
	json.Unmarshal(respBody, &result)
	if len(result.Choices) > 0 {
		return AiResponse{Success: true, Content: result.Choices[0].Message.Content, Error: "", Host: host}
	} else {
		return AiResponse{Success: false, Content: "", Error: "no response", Host: host}
	}
}

// google聊天接口
func (p *SimpleAiProvider) googleChat(messages []ChatItem) AiResponse {
	host := p.NextHost()
	if host == "" {
		host = AIList["google"].Host
	}
	apiKey := p.NextApiKey()
	url := fmt.Sprintf("%s/v1beta/models/%s:generateContent?key=%s", host, p.Model, apiKey)
	// 如果有超过一个host，则返回当前host，否则返回空
	if len(p.Hosts) == 1 {
		host = ""
	}

	// 转换消息格式为Google API格式
	var contents []map[string]any
	for _, item := range messages {
		role := item.Role
		content := item.Content

		// system role 转换为 user，Google API不支持system
		if role == "system" {
			role = "user"
			content = fmt.Sprintf("System Instructions:\n%s", content)
		} else if role == "assistant" { // assistant role 转换为 model
			role = "model"
		}

		contents = append(contents, map[string]any{
			"role": role,
			"parts": []map[string]any{
				{"text": content},
			},
		})
	}

	payload := map[string]any{"contents": contents}
	body, _ := json.Marshal(payload)
	req, _ := http.NewRequest("POST", url, bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	resp, err := p.Client.Do(req)
	if err != nil {
		return AiResponse{Success: false, Content: "", Error: err.Error(), Host: host}
	}
	defer resp.Body.Close()
	respBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		errTxt := fmt.Sprintf("HTTP %d: %s", resp.StatusCode, string(respBody))
		return AiResponse{Success: false, Content: "", Error: errTxt, Host: host}
	}
	var result struct {
		Candidates []struct {
			Content struct {
				Parts []struct {
					Text string `json:"text"`
				} `json:"parts"`
			} `json:"content"`
		} `json:"candidates"`
	}
	json.Unmarshal(respBody, &result)
	if len(result.Candidates) > 0 && len(result.Candidates[0].Content.Parts) > 0 {
		return AiResponse{Success: true, Content: result.Candidates[0].Content.Parts[0].Text, Error: "", Host: host}
	} else {
		return AiResponse{Success: false, Content: "", Error: "no response", Host: host}
	}
}

// 分享一个高亮读书片段给AI，让AI总结和答疑
func (iw *InkWell) SummarizeClippings() string {
	myClips := iw.ReadClippings()
	if len(myClips) == 0 {
		Styled("There is no clippings now").Bold().Println()
		return ""
	}

	fmt.Println()
	Styled(" The latest clippings ").Fg("white").Bg("yellow").Println()
	for i, clip := range myClips {
		title := clip[0]
		frag := clip[1]
		if len(title) > 35 {
			title = title[:35]
		}
		if len(frag) > 35 {
			frag = frag[:35] + "..."
		}
		fmt.Printf("%2d. %s\n    %s\n", i+1, title, Styled(frag).Fg("bright_black"))
	}
	fmt.Println()

	for {
		input := Input("[q, num or range] » ")
		if input == "q" || input == "Q" {
			return "quit"
		}

		nums := ParseRange(input)
		if len(nums) == 0 {
			continue
		}

		// 提取需要的笔记
		var selectedClips [][2]string
		for i := range myClips {
			if contains(nums, i+1) {
				selectedClips = append(selectedClips, myClips[i])
			}
		}
		if len(selectedClips) == 0 {
			continue
		}

		// 收集问题
		var questions []string
		for {
			quest := Input("Question » ")
			if quest == "" {
				break
			}
			if quest == "q" || quest == "Q" {
				return "quit"
			}
			questions = append(questions, quest)
		}

		// 准备消息
		var clipsText strings.Builder
		for _, clip := range selectedClips {
			fmt.Fprintf(&clipsText, "- %s\n%s\n", clip[0], clip[1])
		}

		questionText := "\nQuestion:\n" + strings.Join(questions, "\n")

		// 开始新对话
		iw.Messages = iw.Messages[:1]
		msg := strings.ReplaceAll(PromptClips, "{clips}", clipsText.String())
		msg = strings.ReplaceAll(msg, "{question}", questionText)
		iw.Messages = append(iw.Messages, ChatItem{Role: "user", Content: msg})

		// 打印用户消息
		iw.PrintChatBubble("user", iw.CurrTopic)
		fmt.Println(msg)

		// 获取AI响应
		resp := iw.FetchAiResponse(iw.Messages)
		respText := resp.Content
		if !resp.Success {
			respText = "Error: " + resp.Error
		}

		iw.Messages = append(iw.Messages, ChatItem{Role: "assistant", Content: respText})

		// 打印AI响应
		iw.PrintChatBubble("assistant", resp.Host)
		fmt.Println(respText)

		// 准备下一轮对话
		iw.PrintChatBubble("user", iw.CurrTopic)
		break
	}

	return ""
}

// 检查一个元素是否在切片中
func contains[K comparable](elems []K, target K) bool {
	for _, item := range elems {
		if item == target {
			return true
		}
	}
	return false
}

// 检查一个字符串是否包含任一子串
func strContainsAny(s string, subs ...string) bool {
	for _, sub := range subs {
		if strings.Contains(s, sub) {
			return true
		}
	}
	return false
}

// 将any类型转换为int，出错返回defVal
func toInt(v any, defVal int) int {
	n := defVal
	switch val := v.(type) {
	case int:
		n = val
	case int32:
		n = int(val)
	case int64:
		n = int(val)
	case float32:
		n = int(val)
	case float64:
		n = int(val)
	case string:
		if parsed, err := strconv.Atoi(val); err == nil {
			n = parsed
		}
	}
	return n
}

// 取两个数小的一个
func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// 删除切片中某个元素，返回新的切片
func removeAt[T any](s []T, index int) []T {
	if index < 0 || index >= len(s) {
		return s
	}
	return append(s[:index], s[index+1:]...)
}

// 导出某些历史信息到电子书或发送邮件
// expName: 如果包含@.则发送邮件，否则保存到文件
// indexList: 需要导出的历史索引号列表
func (iw *InkWell) ExportHistory(expName string, indexList []int) {
	// 0 为导出当前会话
	var history []HistoryItem
	for _, index := range indexList {
		if index == 0 {
			history = append(history, HistoryItem{
				Topic:    iw.CurrTopic,
				Prompt:   iw.PromptName,
				Messages: iw.Messages[1:], // 跳过System prompt
			})
		} else if index <= len(iw.History) {
			history = append(history, iw.History[index-1])
		}
	}

	if len(history) == 0 {
		fmt.Println("No conversation match the selected number")
		return
	}

	var emailRegex = regexp.MustCompile(`^[^@]+@[^@]+\.[^@]+$`)
	isEmail := emailRegex.MatchString(expName)
	var bookPath string
	if !isEmail { // 寻找一个最合适的路径
		paths := []struct {
			dir    string
			suffix string
		}{
			{KindleDocDir, ".txt"},
			{appDir(), ".html"},
			{filepath.Dir(iw.CfgFile), ".html"},
			//{getHomeDir(), ".html"},
		}

		for _, path := range paths {
			if isWriteableDir(path.dir) {
				bookPath = path.dir
				// 如果文件名没有后缀，添加默认后缀
				if filepath.Ext(expName) == "" {
					expName += path.suffix
				}
				break
			}
		}

		if bookPath == "" {
			fmt.Println("Cannot find a writeable directory")
			return
		}

		expName = filepath.Join(bookPath, expName)
	}

	// 生成html文件内容
	var htmlContent strings.Builder
	htmlContent.WriteString("<!DOCTYPE html>\n<html>\n<head>\n<meta charset=\"UTF-8\"><title>AI Chat History</title></head><body>\n")

	for _, item := range history {
		htmlContent.WriteString(fmt.Sprintf("<h1>%s</h1><hr/>\n", item.Topic))

		for _, msg := range item.Messages {
			content := iw.MarkdownToHtml(msg.Content, !isEmail)

			if msg.Role == "user" {
				htmlContent.WriteString(fmt.Sprintf(`<div style="margin-bottom:10px;"><strong>YOU:</strong><p style="margin-left:25px;">%s</p></div><hr/>`, content))
			} else {
				htmlContent.WriteString(fmt.Sprintf(`<div style="margin-bottom:10px;"><strong>AI:</strong><p style="margin-left:5px;">%s</p></div><hr/>`, content))
			}
		}
	}
	htmlContent.WriteString("</body></html>")

	content := htmlContent.String()
	var err error
	if isEmail {
		err = iw.SmtpSendMail(expName, content)
	} else {
		err = os.WriteFile(expName, []byte(content), 0644)
	}

	if err != nil {
		fmt.Printf("Could not export to %s: %v\n\n", Styled(expName).Bold(), err)
	} else {
		fmt.Printf("Successfully exported to %s\n\n", Styled(expName).Bold())
	}
}

// 使用smtp发送邮件
func (iw *InkWell) SmtpSendMail(to, content string) error {
	sender := iw.Config.SmtpSender
	host := iw.Config.SmtpHost
	username := iw.Config.SmtpUsername
	password := iw.Config.SmtpPassword

	if sender == "" || host == "" || username == "" || password == "" {
		return fmt.Errorf("some configuration items are missing")
	}

	// 分割主机和端口
	hostParts := strings.Split(host, ":")
	if len(hostParts) != 2 || !isNumeric(hostParts[1]) {
		return fmt.Errorf("invalid smtp host format")
	}

	smtpHost := hostParts[0]
	port, _ := strconv.Atoi(hostParts[1])

	// 准备邮件内容
	msg := []string{
		"From: " + sender,
		"To: " + to,
		"Subject: AI Chat History",
		"MIME-Version: 1.0",
		"Content-Type: multipart/mixed; boundary=boundary123",
		"",
		"--boundary123",
		"Content-Type: text/plain; charset=UTF-8",
		"",
		"This email contains the AI conversation history. The detailed content is in the attachment, sent by Inkwell.",
		"",
		"--boundary123",
		"Content-Type: text/html; charset=UTF-8",
		"Content-Disposition: attachment; filename=\"conversation.html\"",
		"Content-Transfer-Encoding: base64",
		"",
		base64.StdEncoding.EncodeToString([]byte(content)),
		"",
		"--boundary123--",
	}

	// 连接SMTP服务器
	var auth smtp.Auth
	if port == 465 {
		// SSL连接
		tlsConfig := &tls.Config{
			ServerName: smtpHost,
		}
		conn, err := tls.Dial("tcp", fmt.Sprintf("%s:%d", smtpHost, port), tlsConfig)
		if err != nil {
			return fmt.Errorf("failed to connect to SMTP server: %v", err)
		}
		defer conn.Close()

		client, err := smtp.NewClient(conn, smtpHost)
		if err != nil {
			return fmt.Errorf("failed to create SMTP client: %v", err)
		}
		defer client.Close()

		auth = smtp.PlainAuth("", username, password, smtpHost)
		if err := client.Auth(auth); err != nil {
			return fmt.Errorf("SMTP authentication failed: %v", err)
		}

		if err := client.Mail(sender); err != nil {
			return fmt.Errorf("failed to set sender: %v", err)
		}

		if err := client.Rcpt(to); err != nil {
			return fmt.Errorf("failed to set recipient: %v", err)
		}

		w, err := client.Data()
		if err != nil {
			return fmt.Errorf("failed to start mail body: %v", err)
		}

		if _, err := w.Write([]byte(strings.Join(msg, "\r\n"))); err != nil {
			return fmt.Errorf("failed to write mail body: %v", err)
		}

		if err := w.Close(); err != nil {
			return fmt.Errorf("failed to close mail body: %v", err)
		}

		return client.Quit()
	} else {
		// 普通连接或STARTTLS
		auth = smtp.PlainAuth("", username, password, smtpHost)
		return smtp.SendMail(fmt.Sprintf("%s:%d", smtpHost, port), auth, sender, []string{to}, []byte(strings.Join(msg, "\r\n")))
	}
}

// 返回当前执行文件所在的目录（绝对路径）
func appDir() string {
	// 优先使用 os.Executable
	if exePath, err := os.Executable(); err == nil {
		if realPath, err := filepath.EvalSymlinks(exePath); err == nil {
			return filepath.Dir(realPath)
		}
		return filepath.Dir(exePath)
	}

	// 退而求其次使用 os.Args[0]
	if absPath, err := filepath.Abs(os.Args[0]); err == nil {
		return filepath.Dir(absPath)
	}

	// 最后兜底使用当前工作目录
	if cwd, err := os.Getwd(); err == nil {
		return cwd
	}

	// 理论极端情况下
	return os.TempDir()
	//return getHomeDir()
}

// 检查目录是否可写
func isWriteableDir(dir_ string) bool {
	info, err := os.Stat(dir_)
	if err != nil || !info.IsDir() {
		return false
	}

	tempFile := filepath.Join(dir_, ".write_test")
    file, err := os.Create(tempFile)
    if err != nil {
        return false
    }
    file.Close()
    os.Remove(tempFile)
    return true
}

// html转义
func htmlEscape(s string) string {
	// 导出聊天历史才会使用，效率不是很重要，启动速度才重要，就不创建为全局变量了
	var htmlReplacer = strings.NewReplacer(
		"&", "&amp;",
		"<", "&lt;",
		">", "&gt;",
		`"`, "&quot;",
		"'", "&#39;",
	)
	return htmlReplacer.Replace(s)
}

// 检查字符串是否为数字
func isNumeric(s string) bool {
	_, err := strconv.Atoi(s)
	return err == nil
}

// 生成一个唯一标识符
func generateUID() string {
	//return fmt.Sprintf("%d%d", time.Now().UnixNano(), rand.Int63())
	const letters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
	b := make([]byte, 16)
	for i := range b {
		b[i] = letters[rand.Intn(len(letters))]
	}
	return string(b)
}

// 简单的markdown转换为html
// wrapCode: 使用table套在code代码段外模拟一个边框
func (iw *InkWell) MarkdownToHtml(content string, wrapCode bool) string {
	reCodeBlock := regexp.MustCompile("(?s)```(?:([\\w\\-\\+]*)\\n)?(.*?)```")
	reInlineCode := regexp.MustCompile("`([^`]+)`")
	reHeader := regexp.MustCompile("(?m)^(#{1,6})\\s+(.+)$")
	reBold := regexp.MustCompile(`\*\*(.+?)\*\*|__(.+?)__`)
	reItalic := regexp.MustCompile(`\*(.+?)\*|_(.+?)_`)
	reStrike := regexp.MustCompile(`~~(.+?)~~`)
	reUL := regexp.MustCompile(`(?m)^\s*[\*\-]\s+(.+)$`)
	reOL := regexp.MustCompile(`(?m)^\s*(\d+)\.\s+(.+)$`)
	reQuote := regexp.MustCompile(`(?m)^\s*>\s+(.+)$`)
	reLink := regexp.MustCompile(`\[(.+?)\]\((.+?)\)`)

	// 提取代码块
	codeBlocks := make(map[string]struct {
		lang string
		code string
	})
	content = reCodeBlock.ReplaceAllStringFunc(content, func(match string) string {
		parts := reCodeBlock.FindStringSubmatch(match)
		lang := strings.TrimSpace(parts[1])
		code := parts[2]
		uid := fmt.Sprintf("[[CODEBLOCK%s]]", generateUID())
		codeBlocks[uid] = struct {
			lang string
			code string
		}{
			lang: lang,
			code: code,
		}
		return uid
	})

	// 行内代码
	content = reInlineCode.ReplaceAllString(content, "<code>$1</code>")

	// 链接
	content = reLink.ReplaceAllString(content, `<a href="$2">$1</a>`)

	// 标题
	content = reHeader.ReplaceAllStringFunc(content, func(m string) string {
		parts := reHeader.FindStringSubmatch(m)
		level := len(parts[1])
		return fmt.Sprintf("<h%d>%s</h%d>", level, strings.TrimSpace(parts[2]), level)
	})

	// 引用
	content = reQuote.ReplaceAllString(content, `<blockquote>$1</blockquote>`)

	// 无序列表
	content = reUL.ReplaceAllString(content, `<div><strong>• </strong>$1</div>`)

	// 有序列表
	content = reOL.ReplaceAllString(content, `<div><strong>$1. </strong>$2</div>`)

	// 加粗
	content = reBold.ReplaceAllStringFunc(content, func(m string) string {
		parts := reBold.FindStringSubmatch(m)
		if parts[1] != "" {
			return "<strong>" + parts[1] + "</strong>"
		}
		return "<strong>" + parts[2] + "</strong>"
	})

	// 斜体
	content = reItalic.ReplaceAllStringFunc(content, func(m string) string {
		parts := reItalic.FindStringSubmatch(m)
		if parts[1] != "" {
			return "<em>" + parts[1] + "</em>"
		}
		return "<em>" + parts[2] + "</em>"
	})

	// 删除线
	content = reStrike.ReplaceAllString(content, `<s>$1</s>`)

	// 段落包裹（在恢复代码块之前）
	lines := strings.Split(content, "\n")
	for i, line := range lines {
		lineTrim := strings.TrimSpace(line)
		if lineTrim == "" {
			continue
		}
		if strings.HasPrefix(lineTrim, "<h") ||
			strings.HasPrefix(lineTrim, "<pre") ||
			strings.HasPrefix(lineTrim, "<blockquote") ||
			strings.HasPrefix(lineTrim, "<div>") ||
			strings.HasPrefix(lineTrim, "<table") {
			continue
		}
		lines[i] = "<div>" + lineTrim + "</div>"
	}
	content = strings.Join(lines, "\n")

	// 最后恢复代码块
	codeTpl := ""
	if wrapCode {
		codeTpl = `<table border="1" bordercolor="silver" cellspacing="0" width="100%%" style="background-color:#f9f9f9;border:1px solid silver;">
		<tr><td style="padding:10px;"><pre><code class="%s">%s</code></pre></td></tr></table>`
	} else {
		codeTpl = `<pre style="border:1px solid silver;padding:10px;background-color:#f9f9f9;"><code%s>%s</code></pre>`
	}
	for uid, block := range codeBlocks {
		langAttr := block.lang
		if langAttr != "" {
			langAttr = "lang"
		}
		escaped := strings.ReplaceAll(htmlEscape(block.code), " ", "&nbsp;")
		html := fmt.Sprintf(codeTpl, langAttr, escaped)
		content = strings.ReplaceAll(content, uid, html)
	}

	return content
}

// MdTableToHtml markdown里面的表格转换为html格式的表格
func (iw *InkWell) MdTableToHtml(content string) string {
	var (
		currTb []string
		tbHead = true
		ret    []string
		lines  = strings.Split(content, "\n")
	)

	for _, line := range lines {
		trimed := strings.TrimSpace(line)
		if strings.HasPrefix(trimed, "|") && strings.HasSuffix(trimed, "|") && strings.Count(trimed, "|") > 2 {
			if len(currTb) == 0 {
				currTb = append(currTb, `<table border="1" cellspacing="0" width="100%">`)
			}

			tds := strings.Split(strings.Trim(trimed, "|"), "|")
			// 清理每个单元格的内容
			for i := range tds {
				tds[i] = strings.TrimSpace(tds[i])
			}

			// 忽略分割行
			if allEmpty := true; allEmpty {
				for _, td := range tds {
					if strings.Trim(td, ": +-") != "" {
						allEmpty = false
						break
					}
				}
				if allEmpty {
					continue
				}
			}

			currTb = append(currTb, "<tr>")
			for _, td := range tds {
				if tbHead {
					currTb = append(currTb, fmt.Sprintf("<td><strong>%s</strong></td>", td))
				} else {
					currTb = append(currTb, fmt.Sprintf("<td>%s</td>", td))
				}
			}
			currTb = append(currTb, "</tr>")
			tbHead = false
		} else if strings.HasPrefix(trimed, "+") && strings.HasSuffix(trimed, "+") && strings.Trim(trimed, ":+- ") == "" {
			// 另一种分割行
			if len(currTb) == 0 {
				currTb = append(currTb, `<table border="1" cellspacing="0" width="100%">`)
			}
		} else {
			if len(currTb) > 0 {
				// 之前有表格，添加此表格到结果字符串列表
				currTb = append(currTb, "</table>")
				ret = append(ret, strings.Join(currTb, ""))
				currTb = nil
				tbHead = true
			}
			ret = append(ret, line)
		}
	}

	if len(currTb) > 0 {
		currTb = append(currTb, "</table>")
		ret = append(ret, strings.Join(currTb, ""))
	}

	return strings.Join(ret, "\n")
}

// 显示菜单项
func (iw *InkWell) ShowMenu() {
	fmt.Println("")
	Styled(" Current prompt ").Fg("white").Bg("yellow").Println()
	fmt.Println(iw.PromptName)
	fmt.Println("")
	Styled(" Current conversation ").Fg("white").Bg("yellow").Println()
	fmt.Printf(" 0. %s\n", iw.CurrTopic)
	fmt.Println("")
	Styled(" Previous conversations ").Fg("white").Bg("yellow").Println()
	if len(iw.History) == 0 {
		Styled("No previous conversations found!").Fg("bright_black").Println()
	} else {
		for idx, item := range iw.History {
			Styled("%2d. %s\n").Fg("bright_black").Printf(idx+1, item.Topic)
		}
	}
	fmt.Println("")
}

// 显示命令列表和帮助
func (iw *InkWell) ShowCmdList() {
	fmt.Println("")
	Styled(" Commands ").Fg("white").Bg("yellow").Println()
	fmt.Printf("%s: Choose a conversation to continue\n", Styled(" num").Bold())
	fmt.Printf("%s: Start a conversation from reading clip\n", Styled("   c").Bold())
	fmt.Printf("%s: Delete one or a range of conversations\n", Styled("dnum").Bold())
	fmt.Printf("%s: Export one or a range of conversations\n", Styled("enum").Bold())
	fmt.Printf("%s: Switch to another model\n", Styled("   m").Bold())
	fmt.Printf("%s: Start a new conversation\n", Styled("   n").Bold())
	fmt.Printf("%s: Choose another prompt\n", Styled("   p").Bold())
	fmt.Printf("%s: Quit the program\n", Styled("   q").Bold())
	fmt.Printf("%s: Show the command list\n", Styled("   ?").Bold())
}

// 显示菜单，根据用户选择进行相应的处理
func (iw *InkWell) ProcessMenu() string {
	iw.ShowMenu()
	for {
		input := strings.ToLower(Input("[num, c, d, e, m, n, p, q, ?] » "))
		switch input {
		case "q": // 退出
			return "quit"
		case "?": // 显示命令帮助
			iw.ShowCmdList()
		case "m": // 切换model
			iw.SwitchModel()
			iw.ShowMenu()
		case "p": // 选择一个prompt
			iw.SwitchPrompt()
			iw.ShowMenu()
		case "c": // 分享读书笔记给AI，然后提问总结学习
			if iw.SummarizeClippings() == "quit" {
				iw.ReplayConversation() // 中断了分享读书笔记过程，返回当前对话
			}
			return ""
		case "n": // 开始一个新的对话
			iw.StartNewConversation()
			fmt.Println(" NEW CONVERSATION STARTED")
			iw.PrintChatBubble("user", iw.CurrTopic)
			return ""
		default:
			// 处理删除历史命令 (d1, d1-3 等)
			if strings.HasPrefix(input, "d") && len(input) > 1 {
				iw.DeleteHistory(ParseRange(input[1:]))
				iw.SaveHistory()
				return "reshow"
			}

			// 处理导出历史命令 (e1, e1-3 等)
			if strings.HasPrefix(input, "e") && len(input) > 1 {
				fmt.Print("Filename/Email: ")
				if expName := Input(""); expName != "" {
					iw.ExportHistory(expName, ParseRange(input[1:]))
				} else {
					fmt.Println("The filename is empty, canceled")
				}
				continue
			}

			// 处理切换历史命令 (数字)
			if index := toInt(input, -1); index >= 0 {
				if index == 0 { // 返回当前对话
					iw.ReplayConversation()
					return ""
				} else if index <= len(iw.History) { // 切换到历史对话
					historyItem := iw.History[index-1]
					iw.History = removeAt(iw.History, index-1)
					iw.SwitchConversation(historyItem)
					iw.ReplayConversation()
					return ""
				}
			}
		}
	}
}

// 显示菜单，切换当前服务提供商的其他model
func (iw *InkWell) SwitchModel() {
	provider := iw.Config.Provider
	model := iw.Config.Model
	if _, ok := AIList[provider]; !ok {
		fmt.Println("Current provider is invalid")
		return
	}

	models := AIList[provider].Models

	fmt.Println("")
	Styled(" Current model ").Fg("white").Bg("yellow").Bold().Println()
	fmt.Printf("%s/%s\n\n", provider, model)
	Styled(" Available models [add ! to persist] ").Fg("white").Bg("yellow").Bold().Println()
	for idx, item := range models {
		fmt.Printf("%2d. %s\n", idx+1, item.Name)
	}
	fmt.Printf("%d. Other\n", len(models)+1)

	for {
		input := Input("» ")
		if input == "q" || input == "Q" {
			return
		}

		needSave := strings.HasSuffix(input, "!")
		input = strings.TrimSuffix(input, "!")
		index := toInt(input, 0)

		if 1 <= index && index <= len(models) {
			iw.Provider.Model = models[index-1].Name
			iw.Config.Model = iw.Provider.Model
			if needSave {
				iw.SaveConfig(nil)
			}
			break
		} else if index == len(models)+1 {
			if modelName := Input("Model Name » "); modelName != "" {
				iw.Provider.Model = modelName
				iw.Config.Model = modelName
				if needSave {
					iw.SaveConfig(nil)
				}
				break
			}
		}
	}
}

// 显示菜单，选择一个会话使用的prompt
func (iw *InkWell) SwitchPrompt() {
	pmts := iw.LoadPrompts()

	fmt.Println("")
	Styled(" Current prompt ").Fg("white").Bg("yellow").Println()
	fmt.Println(iw.PromptName)
	fmt.Println("")
	Styled(" Available prompts [add ! to persist] ").Fg("white").Bg("yellow").Println()
	promptNames := []string{"default", "custom"}
	for name := range pmts {
		promptNames = append(promptNames, name)
	}
	for idx, item := range promptNames {
		fmt.Printf("%2d. %s\n", idx+1, item)
	}
	fmt.Println("")

	for {
		input := Input("» [1] ")
		if input == "" {
			input = "1"
		}
		if input == "q" || input == "Q" {
			break
		}

		needSave := strings.HasSuffix(input, "!")
		input = strings.TrimSuffix(input, "!")
		index := toInt(input, -1)

		if index == 0 { // 显示当前prompt具体内容
			fmt.Println(iw.Messages[0].Content)
			fmt.Println("")
			continue
		} else if 1 <= index && index <= len(promptNames) {
			var prompt string
			if index == 1 {
				iw.PromptName = "default"
				prompt = DefaultPrompt
			} else if index == 2 {
				prevPrompt := iw.Config.CustomPrompt
				Styled("Current custom prompt:").Bold().Println()
				fmt.Println(prevPrompt)
				fmt.Println("")
				Styled("Provide a new custom prompt or Enter to use current:").Bold().Println()
				var newArr []string
				for {
					text := Input("» ")
					if text == "" {
						break
					}
					newArr = append(newArr, text)
				}
				if len(newArr) > 0 {
					prompt = strings.Join(newArr, "\n")
				} else {
					prompt = prevPrompt
				}
				if prompt != "" {
					iw.PromptName = "custom"
				}
			} else {
				iw.PromptName = promptNames[index-1]
				prompt = pmts[iw.PromptName]
			}

			if prompt != "" {
				if iw.PromptName == "custom" {
					iw.Config.CustomPrompt = prompt
				}
				iw.Config.Prompt = iw.PromptName
				iw.Messages[0].Content = prompt
				if needSave {
					iw.SaveConfig(nil)
				}
				Styled("Prompt set to: %s\n").Bold().Printf(iw.PromptName)
			}
			break
		}
	}
}

// 根据下标列表，删除某些历史信息
func (iw *InkWell) DeleteHistory(indexList []int) {
	newHistory := []HistoryItem{}
	for idx, item := range iw.History {
		if !contains(indexList, idx+1) {
			newHistory = append(newHistory, item)
		}
	}
	iw.History = newHistory

	if contains(indexList, 0) { // 0 表示删除当前对话
		iw.CurrTopic = DefaultTopic
		iw.Messages = iw.Messages[:1]
	}
}

// 开始一个新的会话
func (iw *InkWell) StartNewConversation() {
	iw.AddCurrentConvToHistory()

	// 初始化 Messages
	iw.CurrTopic = DefaultTopic
	iw.PromptName = iw.Config.Prompt
	iw.Messages = []ChatItem{{Role: "system", Content: iw.GetPromptText()}}
}

// 切换到其他会话
func (iw *InkWell) SwitchConversation(item HistoryItem) {
	iw.AddCurrentConvToHistory()

	// 创建新的 Messages，保留 system prompt
	if len(iw.Messages) > 0 {
		iw.Messages = iw.Messages[:1]
	} else { // 如果没有 system prompt，补一个
		iw.Messages = []ChatItem{{Role: "system", Content: iw.GetPromptText()}}
	}
	iw.Messages = append(iw.Messages, item.Messages...)
	iw.CurrTopic = item.Topic
	iw.PromptName = item.Prompt
	iw.Messages[0].Content = iw.GetPromptText() // 更新系统 prompt
}

// 将当前会话添加到历史对话列表
func (iw *InkWell) AddCurrentConvToHistory() {
	maxHistory := iw.Config.MaxHistory
	if maxHistory <= 0 || iw.CurrTopic == "" || iw.CurrTopic == DefaultTopic || len(iw.Messages) < 2 {
		return
	}

	lastTopic := ""
	if len(iw.History) > 0 {
		lastTopic = iw.History[len(iw.History)-1].Topic
	}

	if lastTopic == iw.CurrTopic {
		iw.History[len(iw.History)-1].Messages = iw.Messages[1:] // 第一条消息固定为背景prompt
	} else {
		iw.History = append(iw.History, HistoryItem{
			Topic:    iw.CurrTopic,
			Prompt:   iw.PromptName,
			Messages: iw.Messages[1:],
		})
	}

	if len(iw.History) > maxHistory {
		iw.History = iw.History[len(iw.History)-maxHistory:]
	}
	iw.SaveHistory()
}

// 更新ApiKey
func (iw *InkWell) RenewApiKey() {
	renewUrl := iw.Config.RenewApiKey
	if renewUrl == "" {
		return
	} else if !strings.HasPrefix(renewUrl, "http") {
		fmt.Println("Make sure the renew_api_key field is set in the config file before proceeding")
		return
	}

	resp, err := http.Get(renewUrl)
	if err != nil {
		fmt.Printf("Failed to renew api key: %v\n", err)
		return
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		fmt.Printf("Failed to renew api key: %d: %s\n", resp.StatusCode, resp.Status)
		return
	}

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		fmt.Printf("Failed to read response: %v\n", err)
		return
	}

	var result map[string]any
	if err := json.Unmarshal(body, &result); err != nil {
		fmt.Printf("Failed to parse response: %v\n", err)
		return
	}

	newKey, ok := result["data"].(string)
	if !ok {
		fmt.Println("Unable to renew api key: invalid response format")
		return
	}

	modified, _ := result["modified"].(string)
	if newKey == iw.Config.ApiKey {
		fmt.Printf("Your api key is up to date: %s\n", modified)
		return
	}

	iw.Provider.ApiKeys = []string{newKey}
	iw.Config.ApiKey = newKey
	iw.SaveConfig(nil)
	fmt.Printf("Api key has been renewed successfully: %s\n", modified)
}

// 打印AI返回的内容
func (iw *InkWell) PrintAiResponse(resp AiResponse) {
	host := strings.TrimPrefix(resp.Host, "http://")
	host = strings.TrimPrefix(host, "https://")
	// 循环去掉最后一个点号后的部分，直到小于等于30
	for len(host) > 30 {
		pos := strings.LastIndex(host, ".")
		if pos <= 0 {
			break
		}
		host = host[:pos]
	}
	iw.PrintChatBubble("assistant", host)
	if resp.Success {
		content := resp.Content
		if iw.Config.DisplayStyle != "plaintext" {
			content = iw.MarkdownToTerm(content)
		}
		fmt.Println(strings.TrimSpace(content))
	} else {
		fmt.Println(resp.Error)
		Styled("Press r to resend the last chat").Bold().Println()
		needRenew := strContainsAny(resp.Error, "Unauthorized", "Forbidden", "token_expired")
		if needRenew && iw.Config.RenewApiKey != "" {
			Styled("Press k to renew the api key").Bold().Println()
		}
	}
}

// 加载预置的prompt列表
func (iw *InkWell) LoadPrompts() map[string]string {
	if len(iw.Prompts) > 0 {
		return iw.Prompts
	}

	if iw.Prompts == nil {
		iw.Prompts = make(map[string]string)
	}

	pmtFile := filepath.Join(filepath.Dir(iw.CfgFile), PromptsFile)
	if !fileExists(pmtFile) {
		fmt.Printf("Prompts file %s does not exist\n", Styled(pmtFile).Bg("cyan"))
		return iw.Prompts
	}

	content, err := os.ReadFile(pmtFile)
	if err != nil {
		fmt.Printf("Failed to read %s: %v\n", Styled(pmtFile).Bg("cyan"), err)
		return iw.Prompts
	}
	if len(content) == 0 {
		return iw.Prompts
	}

	entries := strings.Split(string(content), "</>")
	for _, entry := range entries {
		entry = strings.TrimSpace(entry)
		if entry == "" {
			continue
		}

		parts := strings.SplitN(entry, "\n", 2)
		if len(parts) != 2 {
			continue
		}

		name := strings.TrimSpace(parts[0])
		content := strings.TrimSpace(parts[1])
		if name != "" && content != "" {
			iw.Prompts[name] = content
		}
	}
	return iw.Prompts
}

// 关闭所有连接
func (p *SimpleAiProvider) Close() {
	if p.Client != nil {
		p.Client.CloseIdleConnections()
	}
}

// 简单的处理markdown格式，用于在终端显示粗体斜体等效果
func (iw *InkWell) MarkdownToTerm(content string) string {
	// 标题 (# 或 ## 等)，使用粗体
	content = regexp.MustCompile(`(?m)^#{1,6}\s+(.*)$`).ReplaceAllString(content, "\033[1m$1\033[0m")

	// 加粗 **bold**
	content = regexp.MustCompile(`\*\*(.*?)\*\*`).ReplaceAllString(content, "\033[1m$1\033[0m")
	// 加粗 __bold__
	content = regexp.MustCompile(`__(.*?)__`).ReplaceAllString(content, "\033[1m$1\033[0m")

	// 斜体 *italic*
	content = regexp.MustCompile(`\*(.*?)\*`).ReplaceAllString(content, "\033[3m$1\033[0m")
	// 斜体 _italic_
	content = regexp.MustCompile(`_(.*?)_`).ReplaceAllString(content, "\033[3m$1\033[0m")

	// 删除线 ~~text~~
	content = regexp.MustCompile(`~~(.*?)~~`).ReplaceAllString(content, "\033[3m$1\033[0m")

	// 列表项或序号加粗
	content = regexp.MustCompile(`(?m)^(\s*)([*+-] |[1-9]+\. )(.*)$`).ReplaceAllString(content, "$1\033[1m$2\033[0m$3")
	
	// 引用行变灰
	content = regexp.MustCompile(`(?m)^( *>+ .*)$`).ReplaceAllString(content, "\033[90m$1\033[0m")

	// 删除代码块提示行，保留代码块内容
	content = regexp.MustCompile("(?m)^ *```.*$").ReplaceAllString(content, "")

	// 行内代码加粗
	content = regexp.MustCompile("`([^`]+)`").ReplaceAllString(content, "\033[1m$1\033[0m")

	if iw.Config.DisplayStyle == "markdown_table" {
		content = iw.MdTableToTerm(content)
	}
	return content
}

// 处理markdown文本里面的表格，排版对齐以便显示在终端上
func (iw *InkWell) MdTableToTerm(content string) string {
	var (
		table           [][]string // 表格的文本内容 [[col0, col1,...],]，如果不是表格行，则是原行的字符串
		colWidths       []int      // 每列文本字数，用于使用每列最大值进行对齐
		colNums         []int      // 每行的列数，用于判断表格是否有效
		prevTableRowIdx = -1
		lines           = strings.Split(content, "\n")
	)

	for idx, row := range lines {
		if strings.HasPrefix(row, "|") && strings.HasSuffix(row, "|") {
			// 必须要连续
			if prevTableRowIdx >= 0 && (prevTableRowIdx+1) != idx {
				colNums = nil
				break
			}
			prevTableRowIdx = idx
			cells := strings.Split(strings.Trim(row, "|"), "|")
			// 清理每个单元格的内容
			for i := range cells {
				cells[i] = strings.TrimSpace(cells[i])
			}
			table = append(table, cells)

			// 更新列宽
			if len(colWidths) < len(cells) {
				colWidths = make([]int, len(cells))
			}
			for i, cell := range cells {
				if len(cell) > colWidths[i] {
					colWidths[i] = len(cell)
				}
			}
			colNums = append(colNums, len(cells))
		} else {
			table = append(table, []string{row})
		}
	}

	if len(colWidths) == 0 || len(colNums) == 0 {
		return content
	}

	// 如果有一些列数不同，可能为多个表格，为了避免排版混乱，直接返回原结果
	for i := 1; i < len(colNums); i++ {
		if colNums[i] != colNums[0] {
			return content
		}
	}

	// 格式化表格行
	formatRow := func(cells []string, bold bool) string {
		parts := make([]string, len(cells))
		for i, cell := range cells {
			if bold {
				parts[i] = string(Styled(fmt.Sprintf("%-*s", colWidths[i], cell)).Bold())
			} else {
				parts[i] = fmt.Sprintf("%-*s", colWidths[i], cell)
			}
		}
		return "| " + strings.Join(parts, " | ") + " |"
	}

	var result []string
	rowIdx := 0
	for _, row := range table {
		if len(row) == 1 { // 非表格行
			result = append(result, row[0])
			continue
		}

		if allEmpty := true; allEmpty {
			for _, cell := range row {
				if strings.Trim(cell, "- :") != "" {
					allEmpty = false
					break
				}
			}
			if allEmpty { // 分割线
				parts := make([]string, len(row))
				for i := range parts {
					parts[i] = strings.Repeat("-", colWidths[i])
				}
				result = append(result, "+-"+strings.Join(parts, "-+-")+"-+")
				continue
			}
		}

		result = append(result, formatRow(row, rowIdx == 0))
		rowIdx++
	}

	return strings.Join(result, "\n")
}

// 分析命令行参数
func ParseArg() (setup bool, config string, clippings bool) {
	flag.BoolVar(&setup, "s", false, "Start interactive configuration")
	flag.BoolVar(&setup, "setup", false, "Start interactive configuration")
	flag.StringVar(&config, "c", "", "Specify a configuration file")
	flag.StringVar(&config, "config", "", "Specify a configuration file")
	flag.BoolVar(&clippings, "k", false, "Start in clippings mode")
	flag.BoolVar(&clippings, "clippings", false, "Start in clippings mode")
	flag.Parse()

	if config == "" {
		config = DefaultConfigFile
	}

	if !filepath.IsAbs(config) {
		config = filepath.Join(appDir(), config)
	}

	return
}

func main() {
	rand.Seed(time.Now().UnixNano())

	Styled(`  _____         _                    _  _ `).Fg("green").Println()
	Styled(` |_   _|       | |                  | || |`).Fg("green").Println()
	Styled(`   | |   _ __  | | ____      __ ___ | || |`).Fg("green").Println()
	Styled(`   | |  | '_ \ | |/ /\ \ /\ / // _ \| || |`).Fg("green").Println()
	Styled(`  _| |_ | | | ||   <  \ V  V /|  __/| || |`).Fg("green").Println()
	Styled(` |_____||_| |_||_|\_\  \_/\_/  \___||_||_|`).Fg("green").Println()
	Styled(`                                          `).Fg("green").Println()
	fmt.Println(Version)

	setup, cfgFile, clippings := ParseArg()

	// 如果不是初始化则配置文件必须存在
	if !setup && !fileExists(cfgFile) {
		fmt.Println(fmt.Sprintf("The file %s does not exist", Styled(cfgFile).Bold()))
		Input("Press return key to quit ")
		return
	}

	inkwell := &InkWell{CfgFile: cfgFile, CurrTopic: DefaultTopic}
	if setup {
		inkwell.Setup()
	}

	// 加载配置
	if !inkwell.LoadConfig() {
		fmt.Println("Failed to load config")
		Input("Press return key to quit ")
		return
	}

	// 启动主程序
	inkwell.Start(clippings)
}
