from langchain_openai import ChatOpenAI
from langchain.prompts import ChatPromptTemplate
from dotenv import load_dotenv
from tools import *
import os


def run_linux_agent():
    load_dotenv(dotenv_path="/home/guru/Desktop/Desktop/GuruPrasad/GuruDocs/ai_agent/ai_agent/03-linux_agent/cred.env")

    llm = ChatOpenAI(
        model_name="mistralai/mixtral-8x7b-instruct",
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        base_url="https://openrouter.ai/api/v1",
        temperature=0.7
    )

    prompt = ChatPromptTemplate.from_template("""
    You are a Linux expert. Given a question, respond ONLY with the exact Linux command. I repeat I do not want the command explanation. Dont add any extra information in the output except the command. 
    Question: {question}
    """)

    chain = prompt | llm

    while True:
        query = input("\nAsk the Linux agent (or type 'exit'): ")
        if query.lower() == "exit":
            break
        response = chain.invoke({"question": query})
        command = response.content.strip()
        print(f"\nCommand to run: {command}")
        confirm = input("Run this command? (y/n): ")
        if confirm.lower() == "y":
            output = run_command(command)
            print(f"\nOutput:\n{output}")
            log_command_history(query, command, output)
        else:
            print("Command skipped.")            


if __name__ == "__main__":
    run_linux_agent()
