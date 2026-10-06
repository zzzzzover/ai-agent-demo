"""agent-demo：一个手写的工具调用型 CLI Agent。

模块划分：
    config  配置与环境变量
    llm     DeepSeek 客户端（归一化成 LLMResponse）
    safety  路径沙箱与命令白名单
    tools   工具声明、注册与执行
    loop    agent 主循环
    trace   结构化 JSONL trace
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
