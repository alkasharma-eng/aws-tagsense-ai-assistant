import os
from dotenv import load_dotenv
from openai import OpenAI
from openai.types.chat import ChatCompletionUserMessageParam, ChatCompletionSystemMessageParam

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

test_response = client.chat.completions.create(
    model="gpt-3.5-turbo",
    messages = [
        ChatCompletionSystemMessageParam(role="system", content="You are a helpful assistant."),
        ChatCompletionUserMessageParam(role="user", content="Hello! What is tagging in AWS?")
    ],
    temperature=0.5
)

print(test_response.choices[0].message.content.strip())

models = client.models.list()
for m in models.data:
    print(m.id)

def ask_gpt(prompt: str, context: str = "", model: str = "gpt-3.5-turbo", temperature: float = 0.3) -> str:
    try:
        messages = [
            ChatCompletionSystemMessageParam(role="system", content="You are a helpful assistant."),
            ChatCompletionUserMessageParam(role="user", content=prompt)
        ]

        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        return f"⚠️ Error: {str(e)}"
