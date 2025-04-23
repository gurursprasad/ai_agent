from tools import *

# Update the agent's prompt to use the new tool
template = """
You are a finance assistant with access to real-time stock prices.
If the question is about stock prices, use the `get_stock_price` tool provided to get the latest value.

Question: {question}
"""

# Add tool access to the agent's memory
from langchain.tools import Tool

tool = Tool.from_function(get_stock_price, description="Fetch stock price for a given symbol, e.g., AAPL")
chain_with_tool = LLMChain(prompt=PromptTemplate(template=template, input_variables=["question"]), llm=llm, tools=[tool])

# Example query using the tool
query = "What is the current stock price of AAPL?"
response = chain_with_tool.run(question=query)
print(f"Agent Response: {response}")