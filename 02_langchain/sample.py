# from langchain_openai import ChatOpenAI
# from langchain.llms import OpenAI
# from langchain.chains import LLMChain
# from langchain.prompts import PromptTemplate

# import os
# from dotenv import load_dotenv

# load_dotenv()

# # Initialize OpenAI LLM
# # llm = OpenAI(openai_api_key=os.getenv("OPENAI_API_KEY"))
# llm = ChatOpenAI(
#     model="mistralai/mixtral-8x7b-instruct",  # you can also try 'meta-llama/llama-3-70b-instruct' or others
#     openai_api_key=os.getenv("OPENAI_API_KEY"),
#     base_url=os.getenv("OPENAI_BASE_URL"),
#     temperature=0.7
# )

# # Define a simple prompt for the agent
# template = """
# You are an AI assistant with expertise in data analysis and automation. Answer the following question:
# Question: {question}
# """

# # Set up the prompt and LLM chain
# prompt = PromptTemplate(template=template, input_variables=["question"])
# chain = LLMChain(prompt=prompt, llm=llm)

# # Example query
# query = "What is the impact of AI in healthcare?"
# response = chain.run(question=query)
# print(f"Agent Response: {response}")


from langchain_openai import ChatOpenAI
from langchain.prompts import ChatPromptTemplate
from dotenv import load_dotenv
import os

load_dotenv(dotenv_path="/home/guru/Desktop/Desktop/GuruPrasad/GuruDocs/ai_agent/ai_agent/02-langchain/cred.env")

llm = ChatOpenAI(
    model_name="mistralai/mixtral-8x7b-instruct",
    openai_api_key=os.getenv("OPENAI_API_KEY"),
    base_url="https://openrouter.ai/api/v1",
    temperature=0.7
)

prompt = ChatPromptTemplate.from_template("""
You are an AI assistant with expertise in stock trading. Answer the following question:
Question: {question}
""")

chain = prompt | llm

query = "Which is the most profitable stock to invest in right now in india?"
response = chain.invoke({"question": query})

print(f"Agent Response: {response.content}")

