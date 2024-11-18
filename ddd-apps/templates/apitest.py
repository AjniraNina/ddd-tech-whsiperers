import openai
import os
from dotenv import load_dotenv

# Load the API key from .env file
load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")

if not api_key:
    print("API key not found. Make sure it is in the .env file as OPENAI_API_KEY.")
    exit(1)

openai.api_key = api_key

try:
    response = openai.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Say hello!"},
        ],
        max_tokens=50,
    )
    print("API call successful! Response:")
    print(response.choices[0].message.content)
except openai.APIError as e:
    print("API call failed:")
    print(e)
