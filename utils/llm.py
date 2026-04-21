import os
import time
from groq import Groq

from dotenv import load_dotenv

load_dotenv(override=True)


def generate_llm_response(system_prompt: str, user_prompt: str, temperature: float = 0.0) -> str:
    """
    LLM caller that uses Groq (Llama-3.3-70b-versatile).
    Includes basic retry logic for Rate Limits.
    """
    for attempt in range(3):
        try:
            client = Groq(api_key=os.getenv("GROQ_API_KEY"))
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=temperature
            )
            return response.choices[0].message.content.strip()

        except Exception as e:
            if "429" in str(e) or "Rate limit" in str(e):
                print(f"  [Rate limited, waiting 60s before retry {attempt+1}/3...]")
                time.sleep(60)
            else:
                raise e

    return "ERROR: LLM failed to respond after 3 attempts."


